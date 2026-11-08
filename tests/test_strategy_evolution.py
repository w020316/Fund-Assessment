"""策略自进化测试（B7/B8, 设计文档 docs/strategy-evolution-design-2026-11-07.md §7 全部 10 类用例）

覆盖:
1. 变异归一化  2. 评估器正确性  3. 预算收割  4. 503 三路径
5. 失败零写盘  6. 激活写 yaml  7. T+1/风控隔离  8. 鉴权
9. B2 接线回归(快照)  10. 单飞互斥(双向)

CI 红线(见 conftest): 不触碰 web/user_fund_positions.json(只读不写);
代际存储与 config/strategies.yaml 全部重定向到 pytest tmp_path。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.strategies.evolution as evolution
import src.strategies.trading_quant as tq
from src.strategies.trading_quant import WEIGHTS, SIGNAL_THRESHOLDS, TradingQuant


# ---------------------------------------------------------------------------
# fixtures: 存储重定向(真实 data/、config/ 一律不碰)
# ---------------------------------------------------------------------------

PARENT_PARAMS = {
    "weights": {"technical": 0.25, "capital": 0.30, "fundamental": 0.10, "news": 0.20, "sentiment": 0.15},
    "signals": {"strong_buy": 80.0, "buy": 65.0, "watch": 50.0, "hold": 35.0, "sell": 20.0},
}


@pytest.fixture()
def evolution_store(tmp_path, monkeypatch):
    """把代际存储整体重定向到 tmp(真实 data/strategy_evolution 零触碰)。"""
    data_dir = tmp_path / "strategy_evolution"
    monkeypatch.setattr(evolution, "DATA_DIR", data_dir)
    monkeypatch.setattr(evolution, "GENERATIONS_DIR", data_dir / "generations")
    monkeypatch.setattr(evolution, "LINEAGE_FILE", data_dir / "lineage.json")
    monkeypatch.setattr(evolution, "ACTIVE_FILE", data_dir / "active.json")
    # 2026-11-10 CI greening: POSITIONS_FILE 一并重定向。原实现缺省读
    # web/user_fund_positions.json(用户真实数据, gitignore, CI 上不存在),
    # 导致本用例"本地 200 / CI 503"的环境依赖假绿; 改为受控持仓文件。
    positions_file = tmp_path / "positions.json"
    positions_file.write_text(
        json.dumps({"positions": [{"fund_code": "000001"}, {"fund_code": "000002"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(evolution, "POSITIONS_FILE", positions_file)
    return data_dir


@pytest.fixture()
def strategies_yaml(tmp_path, monkeypatch):
    """把 strategies.yaml 重定向到 tmp(真实 config/ 零触碰)。"""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    monkeypatch.setattr(tq, "STRATEGIES_YAML_PATH", cfg_dir / "strategies.yaml")
    import web.routes.config as config_route

    monkeypatch.setattr(config_route, "_CONFIG_DIR", cfg_dir)
    return cfg_dir / "strategies.yaml"


# 单飞锁泄漏源（由 _no_leaked_guard 在 teardown 记录, 供后续用例的 setup 报出）
_GUARD_LEAK_SOURCE: dict[str, str | None] = {"nodeid": None}


@pytest.fixture(autouse=True)
def _no_leaked_guard(request):
    """单飞锁在用例前后必须空闲（L-04）。

    单飞锁是**模块级** threading.Lock, 一旦某个用例泄漏租约, 后面不相干的用例会
    成串变红（qa-lead 复跑看到的"4 条随机红"里, TestWriteGuard 那 2 条就是被
    前一个用例的泄漏带崩的: 它在 setup 阶段 acquire 直接失败）。

    两条断言的分工:
    - teardown 断言 → 在**泄漏源**用例上失败, 并记下它的 nodeid;
    - setup 断言    → 后续用例失败时**直接报出泄漏源的 nodeid**, 让它一眼可定位,
      而不是变成一串"看起来各自独立"的红灯。级联本身不掩盖（泄漏源已经红了）,
      但必须指认清楚。
    只做断言, 不强行释放——强解他人持有的锁会让真正的泄漏被掩盖。
    """
    from web.routes import strategy as strategy_route

    lock = strategy_route._BACKTEST_GUARD
    if lock.locked():
        source = _GUARD_LEAK_SOURCE["nodeid"] or "未知用例（可能来自其他测试模块）"
        pytest.fail(
            f"单飞锁在进入 {request.node.nodeid} 前仍被占用。"
            f"泄漏源: {source}。本用例未做任何改动, 此失败是级联信号——请修泄漏源"
        )
    yield
    if lock.locked():
        _GUARD_LEAK_SOURCE["nodeid"] = request.node.nodeid
        pytest.fail(
            f"{request.node.nodeid} 结束时单飞锁仍被占用: 租约泄漏"
            f"（检查 try/finally 中的 finish() 收尾与工作线程是否已 join）"
        )


def _write_yaml(path: Path, section: dict | None) -> None:
    import yaml

    data = {} if section is None else {"trading_quant": section}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


def _fake_loader_factory(slope: float = 0.005, days: int = 100):
    """合成净值装载器(每日稳定斜率, 可注入涨/跌)。"""

    def loader(code: str, period: str) -> list[dict]:
        import numpy as np

        drift = slope * (1.0 + (abs(hash(code)) % 3) * 0.1)
        return [{"date": f"d{j}", "nav": float(1.0 + j * drift)} for j in range(days)]

    return loader


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _tree_snapshot(root: Path) -> dict[str, str | None]:
    return {str(p.relative_to(root)): _sha256(p) for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------
# 1. 变异归一化
# ---------------------------------------------------------------------------

class TestMutation:
    def test_weights_sum_and_clip_and_reproducible(self, evolution_store):
        rng = evolution.rng_from_seed("gen1-test")
        seen = []
        for _ in range(1000):
            cand = evolution.mutate_params(PARENT_PARAMS, rng)
            vals = [cand["weights"][k] for k in evolution.DIM_KEYS]
            assert sum(vals) == pytest.approx(1.0, abs=1e-9)
            for v in vals:
                assert evolution.WEIGHT_CLIP[0] <= v <= evolution.WEIGHT_CLIP[1]
            sig = [cand["signals"][k] for k in evolution.SIGNAL_ORDER]
            assert all(sig[i] > sig[i + 1] for i in range(4)), sig
            seen.append(cand)
        # 种子可复现: 同种子重放得到逐位相同序列
        rng2 = evolution.rng_from_seed("gen1-test")
        for first in seen[:50]:
            assert evolution.mutate_params(PARENT_PARAMS, rng2) == first

    def test_signal_limits_respected(self, evolution_store):
        rng = evolution.rng_from_seed("gen-limits")
        for _ in range(500):
            cand = evolution.mutate_params(PARENT_PARAMS, rng)
            for k, (lo, hi) in evolution.SIGNAL_LIMITS.items():
                assert lo <= cand["signals"][k] <= hi, (k, cand["signals"][k])


# ---------------------------------------------------------------------------
# 2. 评估器正确性
# ---------------------------------------------------------------------------

class TestEvaluator:
    def test_uptrend_fitness_components(self, evolution_store):
        import numpy as np

        navs = {f"f{i}": np.linspace(1.0, 2.0, 80) for i in range(3)}
        proxy, rets, _ = evolution.build_proxy_matrix(navs)
        m = evolution.evaluate_candidate(proxy, rets, PARENT_PARAMS["weights"],
                                         {"strong_buy": 80, "buy": 60, "watch": 50, "hold": 40, "sell": 30})
        assert m["annual_return"] > 0
        assert m["win_rate"] > 0.9
        assert m["max_drawdown"] <= 0
        assert m["fitness"] > 0

    def test_buy_sell_equal_boundary_no_flap(self, evolution_store):
        """buy=sell 边界: 状态机无保持带, 但持仓由当日判定且 T+1 错位正确。"""
        import numpy as np

        navs = {f"f{i}": np.linspace(1.0, 1.2, 60) for i in range(2)}
        proxy, rets, _ = evolution.build_proxy_matrix(navs)
        weights = PARENT_PARAMS["weights"]
        m = evolution.evaluate_candidate(proxy, rets, weights,
                                         {"strong_buy": 80, "buy": 50, "watch": 50, "hold": 50, "sell": 50})
        # 手工重算 T+1: position=1(sig>=50) → r[d]=pos[d-1]*combo_ret[d]
        w = [weights[k] for k in evolution.DIM_KEYS]
        sig = (np.tensordot(proxy, w, axes=([2], [0]))).mean(axis=0)
        pos = (sig >= 50).astype(float)
        expect_r = np.zeros(len(sig))
        expect_r[1:] = pos[:-1] * rets.mean(axis=0)[1:]
        assert m["win_rate"] == pytest.approx(round(float((expect_r[1:] > 0).sum()) / (len(sig) - 1), 4), abs=1e-9)

    def test_equal_weight_composite(self, evolution_store):
        import numpy as np

        navs = {"a": np.linspace(1.0, 1.1, 40)}
        proxy, _, _ = evolution.build_proxy_matrix(navs)
        # 任一权重组合的 composite 均等于 Σ w_k·s_k(等权由 mean_f 实现, 此处验合成)
        weights = {"technical": 0.5, "capital": 0.2, "fundamental": 0.1, "news": 0.1, "sentiment": 0.1}
        c = np.tensordot(proxy, [weights[k] for k in evolution.DIM_KEYS], axes=([2], [0]))
        manual = sum(proxy[0, 10, i] * weights[evolution.DIM_KEYS[i]] for i in range(5))
        assert c[0, 10] == pytest.approx(manual, abs=1e-9)


# ---------------------------------------------------------------------------
# 3. 预算收割
# ---------------------------------------------------------------------------

class TestBudgetHarvest:
    def test_slow_evaluation_harvests_partial(self, evolution_store):
        import time

        counter = {"n": 0}

        def slow_mutate(params, rng):
            counter["n"] += 1
            return {"weights": dict(params["weights"]),
                    "signals": {"strong_buy": 95.0, "buy": 92.0, "watch": 60.0, "hold": 40.0, "sell": 10.0}}

        orig_mutate, orig_eval = evolution.mutate_params, evolution.evaluate_candidate

        def slow_eval(proxy, rets, w, s):
            time.sleep(0.12)
            return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0,
                    "win_rate": 0.0, "fitness": float(counter["n"])}

        evolution.mutate_params, evolution.evaluate_candidate = slow_mutate, slow_eval
        try:
            res = evolution.run_evolution(candidates=10, budget_seconds=0.3,
                                          nav_loader=_fake_loader_factory(), fund_codes=["a", "b", "c"])
        finally:
            evolution.mutate_params, evolution.evaluate_candidate = orig_mutate, orig_eval
        assert 2 <= res["candidates_evaluated"] < 10
        assert res["elapsed_ms"] >= 300


# ---------------------------------------------------------------------------
# 4/5. 503 三路径 + 失败零写盘（web 层）
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(evolution_store, strategies_yaml):
    from web.api import app

    return TestClient(app)


def _seed_generation(gen_id: int, fitness: float) -> None:
    lineage = evolution.load_lineage()
    record = {
        "version": 1, "generation_id": gen_id, "created_at": evolution.now_iso(),
        "parent_id": gen_id - 1 if gen_id > 1 else None, "origin": "evolution",
        "params": PARENT_PARAMS,
        "metrics": {"annual_return": 0.1, "max_drawdown": -0.05, "sharpe": 1.0,
                    "win_rate": 0.5, "fitness": fitness},
        "parent_fitness": fitness - 0.01, "improved_over_parent": True,
    }
    filename = evolution.save_generation(record)
    evolution.append_lineage(lineage, {
        "generation_id": gen_id, "created_at": record["created_at"], "parent_id": record["parent_id"],
        "fitness": fitness, "improved": True, "origin": "evolution", "file": filename,
        "params_digest": evolution.params_digest(PARENT_PARAMS),
    })


class TestErrorPathsAndZeroWrite:
    def test_busy_503_and_lock_released(self, client, evolution_store):
        from web.routes import strategy as strategy_route

        assert strategy_route._BACKTEST_GUARD.acquire(blocking=False)
        try:
            resp = client.post("/api/strategy/evolution/run", json={})
            assert resp.status_code == 503
            assert resp.json()["detail"]["code"] == "EVOLUTION_BUSY"
        finally:
            strategy_route._BACKTEST_GUARD.release()
        # 锁正确释放
        assert not strategy_route._BACKTEST_GUARD.locked()

    def test_timeout_503_and_lock_released(self, client, evolution_store, monkeypatch):
        import asyncio
        import web.routes.evolution as evolution_route

        monkeypatch.setattr(evolution_route, "EVOLUTION_TIMEOUT_SECONDS", 0.2)

        def slow_run(*args, **kwargs):
            time.sleep(1.0)
            return {}

        monkeypatch.setattr(evolution, "run_evolution", slow_run)
        before = _tree_snapshot(evolution_store)
        resp = client.post("/api/strategy/evolution/run", json={})
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "EVOLUTION_TIMEOUT"
        # wait_for 之后锁仍正确释放
        from web.routes import strategy as strategy_route

        assert not strategy_route._BACKTEST_GUARD.locked()
        # 超时路径零写盘
        assert _tree_snapshot(evolution_store) == before

    def test_data_unavailable_503_and_zero_write(self, client, evolution_store):
        monkey_loader = lambda code, period: [{"date": "d1", "nav": 1.0}]  # 样本 <30 日

        orig_loader = evolution._default_nav_loader
        evolution._default_nav_loader = monkey_loader
        try:
            before = _tree_snapshot(evolution_store)
            resp = client.post("/api/strategy/evolution/run", json={})
            assert resp.status_code == 503
            assert resp.json()["detail"]["code"] == "EVOLUTION_DATA_UNAVAILABLE"
            assert _tree_snapshot(evolution_store) == before
        finally:
            evolution._default_nav_loader = orig_loader

    def test_run_exception_zero_write(self, client, evolution_store, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(evolution, "run_evolution", boom)
        before = _tree_snapshot(evolution_store)
        resp = client.post("/api/strategy/evolution/run", json={})
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "EVOLUTION_FAILED"
        assert _tree_snapshot(evolution_store) == before


# ---------------------------------------------------------------------------
# 6. 激活写 yaml
# ---------------------------------------------------------------------------

class TestActivate:
    def test_activate_writes_yaml_then_pointer(self, client, evolution_store, strategies_yaml):
        # 预写一份 yaml(出厂参数), 保证激活时产生 .bak 备份
        _write_yaml(strategies_yaml, {"weights": dict(WEIGHTS), "signals": dict(SIGNAL_THRESHOLDS)})
        _seed_generation(1, fitness=0.42)
        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["activated"] is True and body["yaml_updated"] is True
        assert body["generation_id"] == 1 and body["prev_generation_id"] is None
        # yaml trading_quant 段 == 该代参数
        import yaml as yaml_lib

        with open(strategies_yaml, "r", encoding="utf-8") as f:
            data = yaml_lib.safe_load(f)
        assert data["trading_quant"]["weights"] == PARENT_PARAMS["weights"]
        assert data["trading_quant"]["signals"] == PARENT_PARAMS["signals"]
        # .bak 存在; active.json 指针更新
        assert strategies_yaml.with_suffix(".yaml.bak").exists()
        active = evolution.load_active()
        assert active["generation_id"] == 1
        assert active["params_snapshot"]["weights"] == PARENT_PARAMS["weights"]
        # B2 接线: 下一次实例化即读到激活参数
        quant = TradingQuant()
        assert quant.weights == PARENT_PARAMS["weights"]
        assert quant.signals == {k: float(v) for k, v in PARENT_PARAMS["signals"].items()}

    def test_reactivate_old_generation_rolls_back(self, client, evolution_store, strategies_yaml):
        _seed_generation(1, fitness=0.30)
        _seed_generation(2, fitness=0.50)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 2}).status_code == 200
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        active = evolution.load_active()
        assert active["generation_id"] == 1
        import yaml as yaml_lib

        with open(strategies_yaml, "r", encoding="utf-8") as f:
            assert yaml_lib.safe_load(f)["trading_quant"]["weights"] == PARENT_PARAMS["weights"]

    def test_yaml_write_failure_keeps_pointer_untouched(self, client, evolution_store, strategies_yaml, monkeypatch):
        import web.routes.evolution as evolution_route

        _seed_generation(1, fitness=0.42)
        before = _sha256(evolution.ACTIVE_FILE)

        def boom(data):
            raise OSError("disk full")

        # 路由按名绑定 config 的 save_strategies_yaml, 须在其自身命名空间打补丁
        monkeypatch.setattr(evolution_route, "save_strategies_yaml", boom)
        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 1})
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "ACTIVATE_WRITE_FAILED"
        # active.json 逐字节不变
        assert _sha256(evolution.ACTIVE_FILE) == before


# ---------------------------------------------------------------------------
# 7. T+1 / 风控隔离（B7）
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_import_closure_no_executor_no_fastapi(self):
        """evolution.py 的 import 语句不含 executor/risk_manager/TradeExecutor/fastapi/web。"""
        source = Path(evolution.__file__).read_text(encoding="utf-8")
        banned = ("executor", "risk_manager", "fastapi")
        for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w., \[\]]+)|import\s+([\w., ]+))", source, re.M):
            module = (m.group(1) or "") + (m.group(3) or "")
            names = m.group(2) or ""
            for text in (module.lower(), names.lower()):
                for b in banned:
                    assert b not in text, f"evolution.py import 闭包出现禁止引用: {m.group(0)!r}"
            assert "web" != module.strip().lower() and not module.lower().startswith("web."), m.group(0)

    def test_trade_and_risk_db_untouched(self, client, evolution_store):
        """跑一次完整进化, trade.db/risk.db mtime 不变。"""
        trade_db = Path("data/trade.db")
        risk_db = Path("data/risk.db")
        before = {p: p.stat().st_mtime_ns for p in (trade_db, risk_db) if p.exists()}
        # 用受控 loader 走真实 run_evolution 路径(不触碰交易/风控任何模块)
        orig_loader = evolution._default_nav_loader
        evolution._default_nav_loader = _fake_loader_factory(slope=0.004)
        try:
            resp = client.post("/api/strategy/evolution/run", json={"candidates": 2})
        finally:
            evolution._default_nav_loader = orig_loader
        assert resp.status_code == 200
        after = {p: p.stat().st_mtime_ns for p in (trade_db, risk_db) if p.exists()}
        assert before == after


# ---------------------------------------------------------------------------
# 8. 鉴权：6 端点无 token 全 401（参数化）
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("post", "/api/strategy/evolution/run", {}),
    ("get", "/api/strategy/evolution/lineage", None),
    ("get", "/api/strategy/evolution/generations/1", None),
    ("get", "/api/strategy/evolution/compare", None),
    ("post", "/api/strategy/evolution/activate", {"generation_id": 1}),
    ("get", "/api/strategy/evolution/status", None),
]


class TestAuth:
    def test_all_endpoints_require_admin(self, client, evolution_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        for method, path, body in ENDPOINTS:
            resp = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
            assert resp.status_code == 401, (method, path, resp.status_code)
        # 带正确 token 则放行(status 只读端点验证)
        resp = client.get("/api/strategy/evolution/status", headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 9. B2 接线回归（快照: yaml 无段/损坏时与旧实现逐位一致）
# ---------------------------------------------------------------------------

class TestParamWiring:
    def test_yaml_missing_section_falls_back_to_constants(self, evolution_store, strategies_yaml):
        _write_yaml(strategies_yaml, None)  # 无 trading_quant 段
        quant = TradingQuant()
        assert quant.weights == dict(WEIGHTS)
        assert quant.signals == dict(SIGNAL_THRESHOLDS)
        assert sum(quant.weights.values()) == pytest.approx(1.0)

    def test_yaml_corrupted_falls_back_to_constants(self, evolution_store, strategies_yaml):
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text("trading_quant: [broken", encoding="utf-8")
        quant = TradingQuant()
        assert quant.weights == dict(WEIGHTS)
        assert quant.signals == dict(SIGNAL_THRESHOLDS)

    def test_yaml_section_activates_new_params(self, evolution_store, strategies_yaml):
        section = {
            "weights": {"technical": 0.4, "capital": 0.2, "fundamental": 0.2, "news": 0.1, "sentiment": 0.1},
            "signals": {"strong_buy": 85, "buy": 70, "watch": 55, "hold": 40, "sell": 15},
        }
        _write_yaml(strategies_yaml, section)
        quant = TradingQuant()
        assert quant.weights == {k: float(v) for k, v in section["weights"].items()}
        assert quant.signals == {k: float(v) for k, v in section["signals"].items()}

    def test_output_bitwise_identical_to_legacy(self, evolution_store, strategies_yaml, monkeypatch):
        """yaml 有段(=出厂值)时, stock_analysis 输出与旧实现逐位一致。"""
        _write_yaml(strategies_yaml, {
            "weights": dict(WEIGHTS),
            "signals": dict(SIGNAL_THRESHOLDS),
        })
        quant = TradingQuant()
        monkeypatch.setattr(quant, "_score_technical", lambda code: 72.0)
        monkeypatch.setattr(quant, "_score_capital", lambda code: 61.0)
        monkeypatch.setattr(quant, "_score_fundamental", lambda code: 44.0)
        monkeypatch.setattr(quant, "_score_news", lambda code: 55.0)
        monkeypatch.setattr(quant, "_score_sentiment", lambda code: 63.0)
        result = quant.stock_analysis("600519")
        # 旧实现: composite = Σ score×WEIGHTS(模块常量), 信号按原硬编码阈值
        legacy_composite = (
            72.0 * WEIGHTS["technical"] + 61.0 * WEIGHTS["capital"] + 44.0 * WEIGHTS["fundamental"]
            + 55.0 * WEIGHTS["news"] + 63.0 * WEIGHTS["sentiment"]
        )
        legacy_signal = TradingQuant._signal_from_score(legacy_composite)
        assert result["composite"] == round(legacy_composite, 2)
        assert result["signal"] == legacy_signal.value
        # 静态调用(缺省阈值)行为与旧实现一致
        assert TradingQuant._signal_from_score(80.0).value == "STRONG_BUY"
        assert TradingQuant._signal_from_score(19.0).value == "STRONG_SELL"


# ---------------------------------------------------------------------------
# 10. 单飞互斥（双向复用同一把锁）
# ---------------------------------------------------------------------------

class TestMutualExclusion:
    def test_evolution_running_backtest_gets_503(self, client, evolution_store):
        """evolution 持锁期间, /api/strategy/backtest → 503(反方向)。"""
        from web.routes import strategy as strategy_route

        assert strategy_route._BACKTEST_GUARD.acquire(blocking=False)
        try:
            resp = client.post("/api/strategy/backtest", json={"strategy": "bspro_quant"})
            assert resp.status_code == 503
        finally:
            strategy_route._BACKTEST_GUARD.release()

    def test_backtest_running_evolution_gets_503(self, client, evolution_store):
        """回测持锁期间, /run → 503 EVOLUTION_BUSY(正方向, 与 BUSY 用例互为验证)。"""
        from web.routes import strategy as strategy_route

        assert strategy_route._BACKTEST_GUARD.acquire(blocking=False)
        try:
            resp = client.post("/api/strategy/evolution/run", json={})
            assert resp.status_code == 503
            assert resp.json()["detail"]["code"] == "EVOLUTION_BUSY"
        finally:
            strategy_route._BACKTEST_GUARD.release()

    def test_status_reflects_shared_lock(self, client, evolution_store):
        from web.routes import strategy as strategy_route

        resp = client.get("/api/strategy/evolution/status")
        assert resp.json()["running"] is False
        assert strategy_route._BACKTEST_GUARD.acquire(blocking=False)
        try:
            resp = client.get("/api/strategy/evolution/status")
            assert resp.json()["running"] is True
            assert resp.json()["lock_holder"] == "backtest_or_evolution"
        finally:
            strategy_route._BACKTEST_GUARD.release()


# ---------------------------------------------------------------------------
# 存储层补充: 50 代滚动清理 / .bak 回退（B1 验收标准）
# ---------------------------------------------------------------------------

class TestStorage:
    def test_rolling_prune_keeps_50(self, evolution_store):
        for gid in range(1, 53):
            lineage = evolution.load_lineage()
            filename = evolution.save_generation({
                "version": 1, "generation_id": gid, "created_at": evolution.now_iso(),
                "params": PARENT_PARAMS, "metrics": {"fitness": 0.1},
            })
            evolution.append_lineage(lineage, {
                "generation_id": gid, "created_at": evolution.now_iso(), "parent_id": None,
                "fitness": 0.1, "improved": True, "origin": "evolution", "file": filename,
                "params_digest": evolution.params_digest(PARENT_PARAMS),
            })
        assert len(evolution.load_lineage()["generations"]) == 50
        gen_files = list((evolution.GENERATIONS_DIR).glob("gen_*.json"))
        assert len(gen_files) == 50

    def test_corrupt_lineage_recovers_from_bak(self, evolution_store):
        # 两次写入: 第二次写时才会产生 .bak(首写无旧文件可备份)
        _seed_generation(1, fitness=0.4)
        _seed_generation(2, fitness=0.5)
        evolution.LINEAGE_FILE.write_text("{corrupted", encoding="utf-8")
        lineage = evolution.load_lineage()
        # .bak 滞后一次写: 恢复出 gen1(写入序前一代), 主文件由 safe_read_json 自愈
        assert lineage["next_gen_id"] == 2
        assert {g["generation_id"] for g in lineage["generations"]} == {1}


# ===========================================================================
# 11. QF3-03 · 样本外验证闸门（训练段选优 + 验证段证伪）
#
# 缺陷事实: 旧实现父代只评 1 次、候选取 N 个里的最大、且两者共用同一全样本窗口
# → "候选的最大值 > 父代单点"即便毫无真实改进也大概率成立（多重比较 + 无留出）,
# 假阳性率随候选数单调上升。以下用例把"只样本内变好必须被拒"钉成回归锁。
# ===========================================================================

# 候选信号: 唯一区别是把 buy 从父代 65 降到 55(更容易入场), 其余阈值严格递降
CAND_SIGNALS_BUY55 = {
    "strong_buy": 80.0, "buy": 55.0, "watch": 50.0, "hold": 35.0, "sell": 20.0,
}


def _synthetic_proxy(train_ret: float, val_ret: float, split: int = 80, total: int = 100):
    """构造可直接控制的代理矩阵/收益矩阵（绕过代理公式, 精确控制"市场走势"）。

    proxy 全段恒为 60 分 → 组合信号 sig 恒为 60（权重和为 1）, 于是:
    - 父代 buy=65 → sig<buy 且 sig>sell → 始终空仓 → 收益 0, fitness 0;
    - 候选 buy=55 → 始终持仓 → 收益完全由段内日漂移符号决定。
    这样"训练段变好 / 验证段变差"只取决于 train_ret 与 val_ret 的符号,
    与代理公式的细节无关, 用例可读且确定。
    """
    import numpy as np

    proxy = np.full((2, total, 5), 60.0, dtype=float)
    rets = np.zeros((2, total), dtype=float)
    rets[:, :split] = train_ret
    rets[:, split:] = val_ret
    return proxy, rets, ["a", "b"]


class TestOutOfSampleGate:
    def _run_with(self, monkeypatch, proxy, rets, candidates: int = 3):
        """固定候选参数跑一次 run_evolution（数据装载与代理矩阵用受控替身）。"""
        monkeypatch.setattr(evolution, "build_proxy_matrix", lambda navs: (proxy, rets, ["a", "b"]))
        monkeypatch.setattr(
            evolution, "mutate_params",
            lambda parent, rng: {"weights": dict(parent["weights"]), "signals": dict(CAND_SIGNALS_BUY55)},
        )
        return evolution.run_evolution(
            candidates=candidates,
            budget_seconds=30.0,
            nav_loader=_fake_loader_factory(days=100),
            fund_codes=["a", "b"],
        )

    def test_train_only_gain_rejected_and_zero_write(self, evolution_store, strategies_yaml, monkeypatch):
        """QF3-03 回归锁: 训练段变好但验证段变差 → improved=False 且零写盘。"""
        # 训练段(前 80 日)上涨 / 验证段(后 20 日)下跌
        proxy, rets, _ = _synthetic_proxy(train_ret=0.004, val_ret=-0.004)
        res = self._run_with(monkeypatch, proxy, rets)

        assert res["improved"] is False
        assert res["generation_id"] is None
        # 样本内确有"改善" —— 正是旧实现会误判为 improved=True 的那种情形
        assert res["new_fitness"] > res["parent_fitness"]
        # 样本外反向（验证段是**后 20 日**下跌段, 该不等式同时锁住"按时间顺序切分"）
        assert res["new_val_fitness"] < res["parent_val_fitness"]
        assert res["split_index"] == 80
        assert res["train_days"] == 80 and res["val_days"] == 20
        assert "过拟合" in res["message"]
        # 零写盘: 谱系索引与代文件都不产生
        assert not evolution.LINEAGE_FILE.exists()
        assert not (evolution.GENERATIONS_DIR.exists() and list(evolution.GENERATIONS_DIR.glob("gen_*.json")))

    def test_both_segments_gain_accepted_with_audit_fields(self, evolution_store, strategies_yaml, monkeypatch):
        """两段同时变好 → improved=True, 且 gen 记录带 val_metrics/split_index 可审计。"""
        proxy, rets, _ = _synthetic_proxy(train_ret=0.004, val_ret=0.002)
        res = self._run_with(monkeypatch, proxy, rets)

        assert res["improved"] is True
        gen_id = res["generation_id"]
        assert gen_id == 1
        record = evolution.load_generation(gen_id)
        assert record is not None
        # 训练段指标在 metrics, 验证段指标在 val_metrics, 切分点单独落 split_index
        assert record["split_index"] == 80
        assert record["metrics"]["segment"] == "train"
        assert record["val_metrics"]["segment"] == "validation"
        assert record["metrics"]["fitness"] > record["parent_fitness"]
        assert record["val_metrics"]["fitness"] > record["parent_val_fitness"]
        assert record["metrics"]["eval_window_days"] == 79      # 80 日 - 首日无 T-1
        assert record["val_metrics"]["eval_window_days"] == 19  # 20 日 - 首日无 T-1
        assert record["val_metrics"]["fund_codes"] == ["a", "b"]
        # 谱系摘要也带上验证段 fitness（列表页即可看出是否过了闸门）
        entry = evolution.lineage_entry(evolution.load_lineage(), gen_id)
        assert entry["val_fitness"] == record["val_metrics"]["fitness"]

    def test_insufficient_days_raises_and_zero_write(self, evolution_store, strategies_yaml):
        """总交易日 <100 → 抛 EvolutionDataUnavailable, 不静默降级为全样本评估。"""
        with pytest.raises(evolution.EvolutionDataUnavailable) as ei:
            evolution.run_evolution(
                candidates=2, budget_seconds=30.0,
                nav_loader=_fake_loader_factory(days=99), fund_codes=["a", "b"],
            )
        msg = str(ei.value)
        assert "99" in msg and "验证段" in msg
        assert not evolution.LINEAGE_FILE.exists()
        assert not (evolution.GENERATIONS_DIR.exists() and list(evolution.GENERATIONS_DIR.glob("gen_*.json")))

    def test_insufficient_days_503_via_route(self, client, evolution_store):
        """路由层把样本不足映射为 503 EVOLUTION_DATA_UNAVAILABLE（不是 200 假成功）。"""
        orig_loader = evolution._default_nav_loader
        evolution._default_nav_loader = _fake_loader_factory(days=99)
        try:
            resp = client.post("/api/strategy/evolution/run", json={"candidates": 2})
        finally:
            evolution._default_nav_loader = orig_loader
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "EVOLUTION_DATA_UNAVAILABLE"
        assert not evolution.LINEAGE_FILE.exists()

    def test_run_response_split_fields_contract(self, evolution_store, strategies_yaml, monkeypatch):
        """QF3-29: /run 响应切分字段契约，**覆盖 improved=True 分支**。

        缺口背景：res["split_index"] / train_days / val_days 三个字段前端零消费，
        此前断言只覆盖 improved=False 分支（:644/:645），改进分支响应侧零断言。
        而真实数据下父代 fitness 恒 0、几乎恒走 improved=False，故本用例是该分支
        这三字段的**首次验证** —— 实现漂移时这里是唯一会红的地方。

        ⚠️ 不要写 res["metrics"]["eval_window_days"]：该键在**任何分支都不存在**。
        /run 响应的 metrics 是 evaluate_candidate 的原始 5 字段
        （annual_return/max_drawdown/sharpe/win_rate/fitness），未经富化；
        富化版（含 segment/split_index/eval_window_days）只进 record。
        故：切分点只能取响应顶层，富化指标只能取 record（GET /generations/{id}）。

        ⚠️ record 也没有 train_days / val_days（只有响应有），总天数只能取响应侧。
        """
        # 训练段与验证段同时改善 → 稳定产出 improved=True
        proxy, rets, _ = _synthetic_proxy(train_ret=0.004, val_ret=0.002)
        res = self._run_with(monkeypatch, proxy, rets)

        # 前置守卫：必须真的进入改进分支，否则下面的 record 断言无意义
        assert res["improved"] is True, "未进入 improved=True 分支, 本用例失去意义"
        assert res["generation_id"] is not None

        # ① 响应层自洽：train_days 与 split_index 同源
        assert res["train_days"] == res["split_index"]
        # ② 两段均非空
        assert res["train_days"] >= 1 and res["val_days"] >= 1
        total = res["train_days"] + res["val_days"]

        record = evolution.load_generation(res["generation_id"])
        assert record is not None

        # ③ record 三处 split_index 镜像不漂移
        assert (record["split_index"]
                == record["metrics"]["split_index"]
                == record["val_metrics"]["split_index"])

        # ④ 跨 payload 守恒：两段可评估交易日 + 2 == 响应给出的总天数
        #    （每段首日无 T-1 持仓不计, 故各少 1）
        #    100 日 fixture: 79 + 19 + 2 == 80 + 20 == 100
        #    写成恒等式而非硬编码 100 —— 恒等式才是防腐化的东西
        assert (record["metrics"]["eval_window_days"]
                + record["val_metrics"]["eval_window_days"] + 2
                == total)


# ===========================================================================
# 12. evaluate_candidate 的 day_slice（样本外评估的唯一窗口入口）
# ===========================================================================

class TestDaySlice:
    def test_day_slice_uses_only_window_data(self):
        import numpy as np

        # 前半段上涨 / 后半段下跌: 两段的指标必须符号相反, 才能证明切片真的生效
        proxy = np.full((2, 100, 5), 60.0, dtype=float)
        rets = np.zeros((2, 100), dtype=float)
        rets[:, :50] = 0.004
        rets[:, 50:] = -0.004
        weights = PARENT_PARAMS["weights"]
        signals = dict(CAND_SIGNALS_BUY55)  # sig=60 ≥ buy=55 → 始终持仓

        full = evolution.evaluate_candidate(proxy, rets, weights, signals)
        train = evolution.evaluate_candidate(proxy, rets, weights, signals, (0, 50))
        val = evolution.evaluate_candidate(proxy, rets, weights, signals, (50, 100))

        assert train["annual_return"] > 0 > val["annual_return"]
        assert train["win_rate"] == 1.0 and val["win_rate"] == 0.0
        # 与手工预切片逐位一致（两条路径共用 _slice_window）
        assert train == evolution.evaluate_candidate(proxy[:, :50, :], rets[:, :50], weights, signals)
        assert val == evolution.evaluate_candidate(proxy[:, 50:, :], rets[:, 50:], weights, signals)
        # 向后兼容: day_slice=None 与既有 4 参数调用逐位相同
        assert full == evolution.evaluate_candidate(proxy, rets, weights, signals, None)
        assert full == evolution.evaluate_candidate(proxy, rets, weights, signals)
        # 全样本指标不等于任一单段 → 切片未被忽略
        assert full["annual_return"] != train["annual_return"]

    def test_split_index_is_time_ordered(self):
        # 80/20 且必须严格时序: 100 → 80/20, 250 → 200/50, 下限 100 恰好可用
        assert evolution.train_val_split_index(100) == 80
        assert evolution.train_val_split_index(250) == 200
        for total in (99, 30, 0):
            with pytest.raises(evolution.EvolutionDataUnavailable):
                evolution.train_val_split_index(total)


# ===========================================================================
# 13. QF3-02 · 激活前备份与回滚
# ===========================================================================

class TestActivateBackupAndRollback:
    def test_activate_snapshots_yaml_before_write(self, client, evolution_store, strategies_yaml):
        import web.routes.evolution as evolution_route

        # 带注释的原文: 只有逐字节复制才能保留, yaml.dump 会丢掉注释
        original = (
            "# 出厂参数（手工注释，回滚必须保留）\n"
            "trading_quant:\n"
            "  weights: {technical: 0.25, capital: 0.30, fundamental: 0.10, news: 0.20, sentiment: 0.15}\n"
            "  signals: {strong_buy: 80, buy: 65, watch: 50, hold: 35, sell: 20}\n"
        )
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.42)

        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 1})
        assert resp.status_code == 200
        body = resp.json()
        backup_rel = body["backup_path"]
        assert re.fullmatch(r"backup/strategies_\d{8}_\d{6}(_\d+)?\.yaml", backup_rel), backup_rel
        backup_path = evolution_route._resolve_backup_path(backup_rel)
        assert backup_path.is_file()
        # 备份内容 == 激活前的 yaml 原文（逐字节, 含注释）
        assert backup_path.read_text(encoding="utf-8") == original
        # active.json 记录回滚点与"回滚后应回指的代号"
        active = evolution.load_active()
        assert active["backup_path"] == backup_rel
        assert active["restored_from"] is None       # 首次激活: 激活前为出厂参数
        assert body["restored_from"] is None
        # 真源确实被改写（注释丢失 = 走了 yaml.dump 路径, 说明激活生效）
        assert strategies_yaml.read_text(encoding="utf-8") != original
        assert "手工注释" not in strategies_yaml.read_text(encoding="utf-8")

    def test_rollback_restores_backup_and_pointer(self, client, evolution_store, strategies_yaml):
        import web.routes.evolution as evolution_route

        original = "trading_quant:\n  weights: {technical: 0.9}\n"
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.30)
        _seed_generation(2, fitness=0.50)

        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        backup_of_1 = evolution.load_active()["backup_path"]
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 2}).status_code == 200
        active2 = evolution.load_active()
        assert active2["restored_from"] == 1
        backup_of_2 = active2["backup_path"]
        # 每次激活都新建一个回滚点（同秒激活也不能互相覆盖）
        assert backup_of_2 != backup_of_1
        assert evolution_route._resolve_backup_path(backup_of_1).is_file()

        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rolled_back"] is True and body["yaml_restored"] is True
        assert body["restored_from"] == 1
        assert body["generation_id"] == 1
        # yaml 内容 == 备份原文
        assert (
            strategies_yaml.read_text(encoding="utf-8")
            == evolution_route._resolve_backup_path(backup_of_2).read_text(encoding="utf-8")
        )
        # 指针回指 restored_from, 且回滚点被消费
        active = evolution.load_active()
        assert active["generation_id"] == 1
        assert active["backup_path"] is None
        assert active["params_snapshot"]["weights"] == PARENT_PARAMS["weights"]
        assert active["rolled_back_to_backup"] == backup_of_2
        # 回滚点已消费 → 再回滚必须 409（不允许 200 假装成功）
        again = client.post("/api/strategy/evolution/rollback")
        assert again.status_code == 409
        assert again.json()["detail"]["code"] == "NO_BACKUP_AVAILABLE"

    def test_rollback_without_backup_returns_409(self, client, evolution_store, strategies_yaml):
        import web.routes.evolution as evolution_route

        # 场景 1: 从未激活（active.json 不存在）→ 无 backup_path
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NO_BACKUP_AVAILABLE"
        assert resp.json()["detail"]["retryable"] is False

        # 场景 2: 备份文件被清理 → 同样 409, 且真源零改动
        original = "trading_quant:\n  weights: {technical: 0.25}\n"
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        backup_rel = evolution.load_active()["backup_path"]
        evolution_route._resolve_backup_path(backup_rel).unlink()
        before = strategies_yaml.read_bytes()
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NO_BACKUP_AVAILABLE"
        assert strategies_yaml.read_bytes() == before

    def test_backup_failure_aborts_before_touching_yaml(self, client, evolution_store, strategies_yaml, monkeypatch):
        import web.routes.evolution as evolution_route

        original = "trading_quant:\n  weights: {technical: 0.25}\n"
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.42)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(evolution_route, "_backup_strategies_yaml", boom)
        before_active = _sha256(evolution.ACTIVE_FILE)
        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 1})
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "ACTIVATE_BACKUP_FAILED"
        # 备份失败 → 真源与指针都零改动（绝不产生"无法回退的激活"）
        assert strategies_yaml.read_text(encoding="utf-8") == original
        assert _sha256(evolution.ACTIVE_FILE) == before_active

    def test_rollback_requires_admin(self, client, evolution_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 401

    def test_rollback_to_factory_state(self, client, evolution_store, strategies_yaml):
        """restored_from=None（首次激活前=出厂参数）时回滚: 指针回到 None, 快照为 None。"""
        original = "trading_quant:\n  weights: {technical: 0.25}\n"
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 200
        assert resp.json()["restored_from"] is None
        active = evolution.load_active()
        assert active["generation_id"] is None
        assert active["params_snapshot"] is None
        assert strategies_yaml.read_text(encoding="utf-8") == original

    def test_rollback_rejects_invalid_backup_yaml(self, client, evolution_store, strategies_yaml):
        import web.routes.evolution as evolution_route

        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text("trading_quant:\n  weights: {technical: 0.25}\n", encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        backup_path = evolution_route._resolve_backup_path(evolution.load_active()["backup_path"])
        before = strategies_yaml.read_bytes()

        # 非合法 YAML → 409, 真源零改动
        backup_path.write_text("trading_quant: [broken", encoding="utf-8")
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NO_BACKUP_AVAILABLE"
        assert strategies_yaml.read_bytes() == before

        # 合法 YAML 但不是映射（顶层是字符串）→ 同样拒绝
        backup_path.write_text("just a string\n", encoding="utf-8")
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NO_BACKUP_AVAILABLE"
        assert strategies_yaml.read_bytes() == before

    def test_rollback_write_failure_returns_503(self, client, evolution_store, strategies_yaml, monkeypatch):
        import web.routes.evolution as evolution_route

        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text("trading_quant:\n  weights: {technical: 0.25}\n", encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        before = strategies_yaml.read_bytes()

        def boom(*args, **kwargs):
            raise OSError("read-only fs")

        monkeypatch.setattr(evolution_route, "_restore_yaml_atomic", boom)
        resp = client.post("/api/strategy/evolution/rollback")
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "ROLLBACK_WRITE_FAILED"
        # 还原失败 → 真源未被改动, 回滚点仍在（可重试）
        assert strategies_yaml.read_bytes() == before
        assert evolution.load_active()["backup_path"]


# ===========================================================================
# 14. 读端点与激活/回滚边界（补齐 web/routes/evolution.py 的既有覆盖空洞）
# ===========================================================================

def _seed_corrupt_generation(gen_id: int) -> None:
    """在谱系里登记一个指向不存在文件的代号（模拟代文件被清理/损坏）。"""
    lineage = evolution.load_lineage()
    evolution.append_lineage(lineage, {
        "generation_id": gen_id, "created_at": evolution.now_iso(), "parent_id": None,
        "fitness": 0.1, "improved": True, "origin": "evolution",
        "file": f"gen_{gen_id}_missing.json",
        "params_digest": evolution.params_digest(PARENT_PARAMS),
    })


class TestReadEndpoints:
    def test_lineage_and_generation_reads(self, client, evolution_store):
        _seed_generation(1, fitness=0.30)
        _seed_generation(2, fitness=0.50)

        resp = client.get("/api/strategy/evolution/lineage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["active_generation_id"] is None
        # 按代号倒序
        assert [g["generation_id"] for g in body["generations"]] == [2, 1]

        resp = client.get("/api/strategy/evolution/generations/1")
        assert resp.status_code == 200
        assert resp.json()["params"]["weights"] == PARENT_PARAMS["weights"]

        assert client.get("/api/strategy/evolution/generations/0").json()["detail"]["code"] == "INVALID_GEN_ID"
        resp = client.get("/api/strategy/evolution/generations/99")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "GEN_NOT_FOUND"

    def test_corrupted_generation_file_503(self, client, evolution_store):
        _seed_corrupt_generation(5)
        resp = client.get("/api/strategy/evolution/generations/5")
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "EVOLUTION_CORRUPTED"

    def test_compare_requires_active_then_returns_diff(self, client, evolution_store, strategies_yaml):
        # 无激活代 → 404 NO_ACTIVE_GENERATION
        resp = client.get("/api/strategy/evolution/compare")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "NO_ACTIVE_GENERATION"

        _seed_generation(1, fitness=0.30)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        _seed_generation(2, fitness=0.50)

        resp = client.get("/api/strategy/evolution/compare?gen=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"]["generation_id"] == 1
        assert body["candidate"]["generation_id"] == 2
        assert set(body["param_diff"]) == {"weights", "signals"}
        assert set(body["metric_diff"]) == {"annual_return", "max_drawdown", "sharpe", "win_rate", "fitness"}
        # 缺省 gen → 取最新代
        assert client.get("/api/strategy/evolution/compare").json()["candidate"]["generation_id"] == 2
        # 不存在的代 → 404
        resp = client.get("/api/strategy/evolution/compare?gen=99")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "GEN_NOT_FOUND"

    def test_compare_with_active_pointer_but_empty_lineage(self, client, evolution_store):
        """active.json 有指针而谱系为空（人工/异常态）→ 404 而非 500。"""
        evolution.save_active({
            "generation_id": 7, "activated_at": evolution.now_iso(), "activated_by": "admin",
            "params_snapshot": PARENT_PARAMS,
        })
        resp = client.get("/api/strategy/evolution/compare")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "GEN_NOT_FOUND"
        assert "谱系为空" in resp.json()["detail"]["message"]

    def test_activate_missing_and_corrupted_generation(self, client, evolution_store, strategies_yaml):
        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 99})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "GEN_NOT_FOUND"

        _seed_corrupt_generation(5)
        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 5})
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "EVOLUTION_CORRUPTED"

    def test_activate_pointer_write_failure_reports_yaml_already_updated(self, client, evolution_store,
                                                                        strategies_yaml, monkeypatch):
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text("trading_quant:\n  weights: {technical: 0.25}\n", encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        before_yaml = strategies_yaml.read_bytes()

        def boom(record):
            raise OSError("disk full")

        monkeypatch.setattr(evolution, "save_active", boom)
        resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 1})
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "ACTIVATE_WRITE_FAILED"
        # 报错口径要说明"真源已更新", 并给出可回退的备份路径
        assert "已更新" in detail["message"] and "备份" in detail["message"]
        assert strategies_yaml.read_bytes() != before_yaml


# ===========================================================================
# 15. QF3-10 · 超时后单飞锁的生命周期必须覆盖到工作线程结束
#
# 缺陷事实: wait_for + 线程池的组合不保证线程随超时结束。旧实现把锁释放在依赖
# 的 finally（=响应返回时）, 超时后线程仍在 save_generation/append_lineage,
# 而锁已空 → 下一个 /run 与在途线程并发读-改-写 lineage（丢代）。
# 以下用例锁住三件事: (1) 在途线程的写盘不被丢弃且只发生一次; (2) 在途期间
# 第二次 /run 被拒、**不启动第二个线程**; (3) 线程收尾后锁才释放（不泄漏）。
#
# 确定性约定（L-04）: 本组用例**不使用 sleep 做时序断言**。全量负载下 sleep
# 窗口会失准（qa-lead 复跑实测 4 条随机红）, 加大 sleep 只是降低触发概率。
# 所有"应该已经发生"都换成 Event.wait(), 所有"还不能发生"都换成状态断言。
# ===========================================================================

# 事件等待上限: 不是 sleep —— 事件在正常路径上立刻发生, 上限只在异常时兜底。
WAIT_TIMEOUT = 30.0
JOIN_TIMEOUT = 60.0


def _write_one_generation() -> int:
    """模拟 run_evolution 的真实尾部: 读 lineage → 写 gen 文件 → 追加索引。"""
    lineage = evolution.load_lineage()
    gen_id = int(lineage.get("next_gen_id", 1))
    record = {
        "version": 1, "generation_id": gen_id, "created_at": evolution.now_iso(),
        "parent_id": None, "origin": "evolution", "params": PARENT_PARAMS,
        "metrics": {"fitness": 0.2}, "improved_over_parent": True,
    }
    filename = evolution.save_generation(record)
    evolution.append_lineage(lineage, {
        "generation_id": gen_id, "created_at": record["created_at"], "parent_id": None,
        "fitness": 0.2, "improved": True, "origin": "evolution", "file": filename,
        "params_digest": evolution.params_digest(PARENT_PARAMS),
    })
    return gen_id


class _InFlightRun:
    """在后台线程里发一次 /run, 用 Event 精确控制工作线程的生命周期。

    三个锚点全部是事件, 没有任何 sleep:
    - ``worker_entered``: 工作线程已进入 run_evolution（⇒ 单飞锁已持有）
    - ``drain_entered``:  路由的 wait_for 已超时、正处在"等线程收尾"路径上
    - ``request_done``:   后台请求已返回（此时锁必已由工作线程释放）

    ``drain_entered`` 来自对 ``_drain_worker`` 的 spy: 它是超时路径的**第一个**
    可观测动作, 用它替代"sleep 到超时点之后"这种时间窗口断言。若该函数不存在,
    说明超时收尾路径被移除（QF3-10 回归）, 直接 fail 而不是静默降级。
    """

    def __init__(self, client, monkeypatch, *, timeout: float = 0.1, on_finish=None):
        import web.routes.evolution as evolution_route

        real_drain = getattr(evolution_route, "_drain_worker", None)
        if real_drain is None:
            pytest.fail("路由缺少 _drain_worker: 超时收尾路径不存在（QF3-10 回归）")

        self.worker_entered = threading.Event()
        self.may_finish = threading.Event()
        self.drain_entered = threading.Event()
        self.request_done = threading.Event()
        self.calls = {"n": 0}
        self.response = None
        self.error: BaseException | None = None
        self._on_finish = on_finish
        self._client = client
        self._thread: threading.Thread | None = None

        monkeypatch.setattr(evolution_route, "EVOLUTION_TIMEOUT_SECONDS", timeout)

        async def spy_drain(finished: threading.Event) -> bool:
            self.drain_entered.set()
            return await real_drain(finished)

        monkeypatch.setattr(evolution_route, "_drain_worker", spy_drain)

    def run_evolution_stub(self, *args, **kwargs):
        """替代 evolution.run_evolution: 卡到测试放行为止（模拟不可取消的在途线程）。"""
        self.calls["n"] += 1
        self.worker_entered.set()
        self.may_finish.wait(WAIT_TIMEOUT)
        if self._on_finish is not None:
            self._on_finish()
        return {"improved": False, "generation_id": None, "metrics": {},
                "candidates_evaluated": 0, "elapsed_ms": 0, "message": "stub"}

    def start(self) -> threading.Thread:
        def request():
            try:
                self.response = self._client.post("/api/strategy/evolution/run", json={})
            except BaseException as e:  # noqa: BLE001 - 收集给测试线程断言, 不吞掉
                self.error = e
            finally:
                self.request_done.set()

        self._thread = threading.Thread(target=request, name="inflight-run")
        self._thread.start()
        return self._thread

    def finish(self) -> None:
        """放行工作线程并等后台请求返回: 保证不残留线程、不泄漏锁（finally 必调）。"""
        self.may_finish.set()
        if self._thread is not None:
            self._thread.join(JOIN_TIMEOUT)
        self.request_done.wait(WAIT_TIMEOUT)


class TestRunLockLifetime:
    def test_lock_held_until_inflight_thread_finishes(self, client, evolution_store, monkeypatch):
        """超时后锁**仍被在途线程持有**（旧实现在响应时即释放, 此处为 False）。"""
        from web.routes import strategy as strategy_route

        run = _InFlightRun(client, monkeypatch)
        monkeypatch.setattr(evolution, "run_evolution", run.run_evolution_stub)
        t = run.start()
        try:
            assert run.worker_entered.wait(WAIT_TIMEOUT), "工作线程未启动"
            # 超时已发生且路由进入收尾路径 —— 此刻工作线程必然还活着（被测试卡住）
            assert run.drain_entered.wait(WAIT_TIMEOUT), "路由未进入超时收尾路径"
            assert t.is_alive(), "后台请求不应在收尾完成前返回"
            # 核心不变式: 超时后锁仍被在途线程持有（旧实现此处已释放）
            assert strategy_route._BACKTEST_GUARD.locked(), "超时后锁不得提前释放"
        finally:
            run.finish()
        assert run.error is None, f"后台请求异常: {run.error!r}"
        assert run.response.status_code == 503
        assert run.response.json()["detail"]["code"] == "EVOLUTION_TIMEOUT"
        assert run.calls["n"] == 1
        # 线程真正结束后才释放（否则单飞锁永久泄漏, 进化/回测全被拒）
        assert not strategy_route._BACKTEST_GUARD.locked()

    def test_second_run_rejected_while_inflight_thread_alive(self, client, evolution_store, monkeypatch):
        """在途窗口内第二次 /run 必须被拒, 且**不启动第二个工作线程**。"""
        from web.routes import strategy as strategy_route

        run = _InFlightRun(client, monkeypatch)
        monkeypatch.setattr(evolution, "run_evolution", run.run_evolution_stub)
        run.start()
        try:
            assert run.worker_entered.wait(WAIT_TIMEOUT), "工作线程未启动"
            # 超时已发生（drain 已进入）而线程仍活着 → 旧实现此时锁已空
            assert run.drain_entered.wait(WAIT_TIMEOUT), "路由未进入超时收尾路径"
            second = client.post("/api/strategy/evolution/run", json={})
            assert second.status_code == 503
            assert second.json()["detail"]["code"] == "EVOLUTION_BUSY"
            assert run.calls["n"] == 1, "第二次请求不得启动并行的进化线程"
        finally:
            run.finish()
        assert run.error is None, f"后台请求异常: {run.error!r}"
        assert run.response.status_code == 503
        assert run.response.json()["detail"]["code"] == "EVOLUTION_TIMEOUT"
        assert not strategy_route._BACKTEST_GUARD.locked()

    def test_timed_out_write_not_duplicated_by_next_run(self, client, evolution_store, monkeypatch):
        """超时那次写盘 + 下一次运行 → 代号顺延(1,2); 且写盘确实发生在**超时之后**。"""
        for expected_gen in (1, 2):
            run = _InFlightRun(client, monkeypatch, on_finish=_write_one_generation)
            monkeypatch.setattr(evolution, "run_evolution", run.run_evolution_stub)
            run.start()
            try:
                assert run.worker_entered.wait(WAIT_TIMEOUT), "工作线程未启动"
                assert run.drain_entered.wait(WAIT_TIMEOUT), "路由未进入超时收尾路径"
                # 超时已发生、线程仍被卡住 → 谱系里还没有这一代（写盘是"晚写"）
                assert len(evolution.load_lineage()["generations"]) == expected_gen - 1
            finally:
                run.finish()
            assert run.error is None, f"后台请求异常: {run.error!r}"
            assert run.response.status_code == 503
            assert run.response.json()["detail"]["code"] == "EVOLUTION_TIMEOUT"
        gens = evolution.load_lineage()["generations"]
        assert [g["generation_id"] for g in gens] == [1, 2]
        # 两代文件都在（没有被并发写覆盖成一份）
        assert len(list(evolution.GENERATIONS_DIR.glob("gen_*.json"))) == 2


# ===========================================================================
# 16. QF3-22 · /activate 与 /rollback 纳入同一把单飞锁
#
# 缺陷事实: 两个写端点原先不取锁, 可与 /run、/backtest 并发写
# strategies.yaml / active.json（读-改-写交错 → 参数与指针可能互相覆盖）。
# ===========================================================================

class TestWriteGuard:
    def test_rollback_rejected_when_lock_held(self, client, evolution_store, strategies_yaml):
        from web.routes import strategy as strategy_route

        original = "trading_quant:\n  weights: {technical: 0.25}\n"
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        yaml_before = strategies_yaml.read_bytes()
        backup_before = evolution.load_active()["backup_path"]
        assert backup_before

        assert strategy_route._BACKTEST_GUARD.acquire(blocking=False)
        try:
            resp = client.post("/api/strategy/evolution/rollback")
        finally:
            strategy_route._BACKTEST_GUARD.release()
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "EVOLUTION_LOCKED"
        # 409 的语义是"未做任何改动": 真源与指针都不许动
        assert strategies_yaml.read_bytes() == yaml_before
        assert evolution.load_active()["backup_path"] == backup_before
        # 锁释放后回滚可用（409 路径没有把锁泄漏成永久占用）
        assert client.post("/api/strategy/evolution/rollback").status_code == 200
        assert strategies_yaml.read_text(encoding="utf-8") == original

    def test_activate_rejected_when_lock_held(self, client, evolution_store, strategies_yaml):
        import web.routes.evolution as evolution_route
        from web.routes import strategy as strategy_route

        original = "trading_quant:\n  weights: {technical: 0.25}\n"
        strategies_yaml.parent.mkdir(parents=True, exist_ok=True)
        strategies_yaml.write_text(original, encoding="utf-8")
        _seed_generation(1, fitness=0.42)
        active_before = _sha256(evolution.ACTIVE_FILE)

        assert strategy_route._BACKTEST_GUARD.acquire(blocking=False)
        try:
            resp = client.post("/api/strategy/evolution/activate", json={"generation_id": 1})
        finally:
            strategy_route._BACKTEST_GUARD.release()
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "EVOLUTION_LOCKED"
        # 被拒的激活连备份都不该产生（写前就退出了）
        assert strategies_yaml.read_text(encoding="utf-8") == original
        assert _sha256(evolution.ACTIVE_FILE) == active_before
        assert not list(evolution_route._backup_dir().glob("strategies_*.yaml"))
        # 锁释放后激活正常
        assert client.post("/api/strategy/evolution/activate", json={"generation_id": 1}).status_code == 200
        assert strategies_yaml.read_text(encoding="utf-8") != original

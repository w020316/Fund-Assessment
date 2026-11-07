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

"""tests/test_prediction_service.py — T02 编排服务验收(mock 数据源 + LLM)。

红线测试:
- R4.1: LLM mock 返回编造标的/超额条目时, 最终推荐不含该标的
- 非交易日 skipped
- 验证缺失占位(最终时点后)与 15:40 首跑跳过
- 样本 <5 → hit_rate=None
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core import data_source_v2 as ds2  # noqa: E402
from src.core import prediction_service as svc  # noqa: E402
from src.core import prediction_signals as sig  # noqa: E402
from src.core.prediction_store import PredictionStore  # noqa: E402
from tests.conftest import cst_on, trading_days_ago  # noqa: E402

_CST = _dt.timezone(_dt.timedelta(hours=8))


def _cst(y: int, m: int, d: int, hh: int = 15, mm: int = 45) -> _dt.datetime:
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_CST)


class _FakeLLM:
    """可编程 LLM 假体: 按档位返回预设文本; raise_error=True 模拟 LLM 故障。"""

    def __init__(self, deep_json: str = "", light_text: str = "",
                 raise_error: bool = False) -> None:
        self.deep_json = deep_json
        self.light_text = light_text
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"kwargs": kwargs, "messages": messages})
        if self.raise_error:
            raise RuntimeError("LLM 不可用(模拟)")
        tier = kwargs.get("task_tier", "standard")
        content = self.deep_json if tier == "deep" else self.light_text
        return SimpleNamespace(content=content, provider="fake", model="fake")


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离环境: tmp 库 + 可编程 LLM + 默认 trading-day 探针放行。"""
    store = PredictionStore(tmp_path / "prediction.db")
    store.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: store)
    fake = _FakeLLM(raise_error=True)  # 默认 LLM 故障(降级路径)
    monkeypatch.setattr(
        "src.core.llm_router.get_llm_router", lambda: fake
    )
    # 交易日探针默认放行(018957 净值日期 = 传入 now 的日期由各用例自行 mock)

    class _Env:
        def __init__(self) -> None:
            self.store = store
            self.llm = fake

        def set_trading_day(self, now: _dt.datetime) -> None:
            monkeypatch.setattr(
                ds2, "get_fund_realtime_tencent",
                lambda codes, **kw: [
                    {"code": "018957", "update_time": now.strftime("%Y-%m-%d 15:00")}
                ],
            )

        def set_non_trading_day(self) -> None:
            monkeypatch.setattr(
                ds2, "get_fund_realtime_tencent",
                lambda codes, **kw: [
                    {"code": "018957", "update_time": "2000-01-01 15:00"}
                ],
            )

    return _Env()


def _patch_market(monkeypatch: pytest.MonkeyPatch, indices=None, sentiment=None, sectors=None,
                  index_error: Exception | None = None) -> None:
    def _idx():
        if index_error:
            raise index_error
        return indices or [
            {"code": "000300", "name": "沪深300", "price": 4000.0, "change_pct": 0.6},
        ]

    monkeypatch.setattr(ds2, "get_index_realtime", _idx)
    monkeypatch.setattr(
        ds2, "get_market_sentiment",
        lambda: sentiment or {"rise_count": 3200, "fall_count": 1500,
                              "flat_count": 300, "sentiment": "偏多", "ratio": 0.64},
    )
    monkeypatch.setattr(
        ds2, "get_sector_ranking",
        lambda: sectors if sectors is not None else [
            {"name": "半导体", "change_pct": 2.4},
            {"name": "证券", "change_pct": 1.6},
        ],
    )


# ===== 快照采集 =====


def test_collect_snapshot_non_trading_day_skipped(env, monkeypatch) -> None:
    env.set_non_trading_day()
    out = svc.collect_snapshot("morning", now=_cst(2026, 11, 9, 9, 35))
    assert out["skipped"] is True and out["reason"] == "non_trading_day"
    assert env.store.get_snapshot("2026-11-09", "morning") is None


def test_collect_snapshot_happy_path_llm_down(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch)
    out = svc.collect_snapshot("morning", now=_cst(2026, 11, 9, 9, 35))
    assert out["skipped"] is False
    assert out["rule_summary"]  # R1.2: rule_summary 必有
    assert out["llm_summary"] == ""  # LLM 故障 → 空串, 不阻塞
    assert out["missing_fields"] == []
    assert env.store.get_snapshot("2026-11-09", "morning") is not None


def test_collect_snapshot_partial_source_failure(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch, index_error=RuntimeError("上游挂了"))
    out = svc.collect_snapshot("close", now=_cst(2026, 11, 9, 15, 40))
    assert "indices" in out["missing_fields"]  # R1.1: 字段置空不崩溃
    assert out["rule_summary"]


def test_collect_snapshot_idempotent_within_5min(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    calls = {"n": 0}

    def _counting_sectors():
        calls["n"] += 1
        return [{"name": "半导体", "change_pct": 2.4}]

    monkeypatch.setattr(ds2, "get_sector_ranking", _counting_sectors)
    first = svc.collect_snapshot("noon", now=_cst(2026, 11, 9, 13, 5))
    assert first.get("idempotent") is not True
    second = svc.collect_snapshot("noon", now=_cst(2026, 11, 9, 13, 7))
    assert second.get("idempotent") is True  # R1.4: 5 分钟内幂等
    assert calls["n"] == 1  # 未重新采集


def test_collect_snapshot_invalid_session(env) -> None:
    with pytest.raises(ValueError):
        svc.collect_snapshot("evening", now=_cst(2026, 11, 9))


# ===== 次日预测 =====


def test_generate_prediction_non_trading_day(env) -> None:
    env.set_non_trading_day()
    out = svc.generate_prediction(now=_cst(2026, 11, 9))
    assert out["skipped"] is True


def test_generate_prediction_rule_wins_on_conflict(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch)
    # LLM deep 给出与规则相反的方向 + 编造理由
    env.llm.deep_json = json.dumps(
        {"direction": "down", "confidence": 88, "reason": "LLM 看空(编造)"},
        ensure_ascii=False,
    )
    env.llm.raise_error = False
    out = svc.generate_prediction(now=_cst(2026, 11, 9, 15, 40))
    assert out["skipped"] is False
    assert out["direction"] == "up"  # 规则结论 = 最终结论(R3.1)
    assert out["llm_direction"] == "down"
    assert out["conflict_flag"] == 1  # 冲突标注
    assert out["target_date"] == "2026-11-10"  # 周一 → 次交易日周二


def test_generate_prediction_llm_down_falls_back(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch)
    out = svc.generate_prediction(now=_cst(2026, 11, 9, 15, 40))
    assert out["llm_direction"] is None
    assert out["llm_reason"] == ""
    assert out["conflict_flag"] == 0
    assert out["direction"] in ("up", "down", "flat")


def test_generate_prediction_idempotent_overwrite(env, monkeypatch) -> None:
    # 日期取真实今天的相对基准: list_predictions(days=30) 以 date('now') 为基准,
    # 硬编码日期会随时间滑出窗口(日期炸弹)。本用例只验证同日幂等覆盖, 与具体日历日无关。
    now = cst_on(trading_days_ago(0))
    env.set_trading_day(now)
    _patch_market(monkeypatch)
    first = svc.generate_prediction(now=now)
    second = svc.generate_prediction(now=now.replace(minute=50))
    assert first["id"] == second["id"]  # R3.3 同日幂等覆盖
    rows = env.store.list_predictions(days=30)
    assert len(rows) == 1


# ===== 推荐生成(R4.1 红线) =====


def _cfg_with_candidates() -> dict:
    import copy
    cfg = copy.deepcopy(sig.DEFAULT_CONFIG)
    cfg["sector_candidates"] = {
        "半导体": [{"symbol": "512480", "kind": "stock"}],
        "证券": [{"symbol": "512000", "kind": "stock"}],
    }
    return cfg


def test_generate_recommendations_llm_cannot_add_symbols(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch)
    monkeypatch.setattr(svc.sig, "load_signal_config", lambda path=None: _cfg_with_candidates())
    monkeypatch.setattr(
        ds2, "get_realtime_quote_tencent",
        lambda codes, **kw: [
            {"code": "512480", "name": "半导体ETF", "change_pct": 2.4},
            {"code": "512000", "name": "券商ETF", "change_pct": 1.6},
        ],
    )
    # LLM 润色: 返回超额条目并塞入编造标的文本
    env.llm.light_text = json.dumps(
        ["理由A", "理由B", "强烈推荐 600519 贵州茅台(编造)"],
        ensure_ascii=False,
    )
    env.llm.raise_error = False
    out = svc.generate_recommendations(now=_cst(2026, 11, 9, 15, 40))
    symbols = [it["symbol"] for it in out["items"]]
    assert symbols == ["512480", "512000"]  # R4.1 红线: 编造标的未进入推荐
    assert out["items"][0]["source_signal"].startswith("sector_momentum:半导体")
    assert out["items"][0]["name"] == "半导体ETF"  # 名称来自行情上游
    rows = env.store.list_recommendations(trade_date="2026-11-09")
    assert len(rows) == 2


def test_generate_recommendations_empty_when_no_signal(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    monkeypatch.setattr(svc.sig, "load_signal_config", lambda path=None: _cfg_with_candidates())
    _patch_market(monkeypatch, sectors=[{"name": "无映射板块", "change_pct": 3.0}])
    out = svc.generate_recommendations(now=_cst(2026, 11, 9, 15, 40))
    assert out["items"] == []  # PRD 允许: 今日无满足条件的推荐
    assert env.store.list_recommendations(trade_date="2026-11-09") == []


def test_generate_recommendations_quote_missing_dropped(env, monkeypatch) -> None:
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch)
    monkeypatch.setattr(svc.sig, "load_signal_config", lambda path=None: _cfg_with_candidates())
    monkeypatch.setattr(ds2, "get_realtime_quote_tencent", lambda codes, **kw: [])
    out = svc.generate_recommendations(now=_cst(2026, 11, 9, 15, 40))
    assert out["items"] == []  # 行情取不到 → 丢弃并告警, 不落库


# ===== 每日验证(R6.1) =====


def _seed_targets(store: PredictionStore, target_date: str) -> tuple[int, int]:
    """按修复后口径 seed: trade_date=上一交易日(周五 2026-11-06), target_date=验证日今日。

    周末边界: 周五 15:40 生成的预测 target_date=next_trading_date(周五)=周一,
    周一 15:40 verify_daily 的验证目标恰为今日(周一)。
    """
    p = store.upsert_prediction(
        trade_date="2026-11-06", target_date=target_date, benchmark="HS300",
        direction="up", confidence=70.0, rule_direction="up", rule_confidence=70.0,
        rule_reasons=[], llm_direction=None, llm_confidence=None, llm_reason="",
        conflict_flag=0,
    )
    n = store.replace_recommendations("2026-11-06", target_date, [
        {"symbol": "512480", "name": "半导体ETF", "kind": "stock", "direction": "up",
         "confidence": 70, "reason": "r", "source_signal": "sector_momentum:半导体|change_pct=+2.40"},
    ])
    assert n == 1
    rec = store.list_recommendations_for_target(target_date)[0]
    assert p is not None
    return p["id"], rec["id"]


def test_verify_daily_hit(env, monkeypatch) -> None:
    # 周五(2026-11-06)生成的预测 target=周一(2026-11-09); 周一 15:40 验证今日
    env.set_trading_day(_cst(2026, 11, 9))
    pred_id, rec_id = _seed_targets(env.store, "2026-11-09")
    _patch_market(monkeypatch)  # 沪深300 +0.6% → up 命中(>0.3)
    monkeypatch.setattr(
        ds2, "get_realtime_quote_tencent",
        lambda codes, **kw: [{"code": "512480", "name": "半导体ETF", "change_pct": 1.2}],
    )
    out = svc.verify_daily(now=_cst(2026, 11, 9, 15, 40))
    assert out["skipped"] is False
    assert out["verified"] == 2 and out["missing"] == 0
    assert out["target_date"] == "2026-11-09"
    hits = {d["target_id"]: d.get("hit") for d in out["detail"]}
    assert hits[pred_id] == 1 and hits[rec_id] == 1
    v = env.store.get_verification("2026-11-09", "prediction", pred_id)
    assert v["hit"] == 1 and v["actual_return"] == pytest.approx(0.6)
    # 验证行口径: trade_date == target_date == 验证日今日
    assert v["trade_date"] == "2026-11-09"


def test_verify_daily_data_missing_first_run_skips(env, monkeypatch) -> None:
    """15:40 首跑取不到数据 → 不写占位, 等 16:30。"""
    env.set_trading_day(_cst(2026, 11, 9))
    _seed_targets(env.store, "2026-11-09")
    _patch_market(monkeypatch, index_error=RuntimeError("取不到"))
    out = svc.verify_daily(now=_cst(2026, 11, 9, 15, 40))
    assert out["verified"] == 0 and out["missing"] == 0
    assert env.store.list_verifications(days=30) == []


def test_verify_daily_missing_placeholder_after_final(env, monkeypatch) -> None:
    """16:30 重试仍取不到 → 写 missing=1 占位(R6.1 拍板口径)。"""
    env.set_trading_day(_cst(2026, 11, 9))
    _seed_targets(env.store, "2026-11-09")
    _patch_market(monkeypatch, index_error=RuntimeError("仍取不到"))
    out = svc.verify_daily(now=_cst(2026, 11, 9, 16, 30))
    assert out["missing"] == 2 and out["verified"] == 0
    rows = env.store.list_verifications(days=30)
    assert len(rows) == 2 and all(r["missing"] == 1 for r in rows)
    # 统计口径: missing 行被排除
    assert env.store.list_verifications_between("2000-01-01", "2100-01-01") == []


def test_verify_daily_non_trading_day(env) -> None:
    env.set_non_trading_day()
    out = svc.verify_daily(now=_cst(2026, 11, 9))
    assert out["skipped"] is True


def test_verify_daily_idempotent_no_double_count(env, monkeypatch) -> None:
    """16:30 重试时已验证过的目标不重复计数。"""
    env.set_trading_day(_cst(2026, 11, 9))
    _seed_targets(env.store, "2026-11-09")
    _patch_market(monkeypatch)
    monkeypatch.setattr(
        ds2, "get_realtime_quote_tencent",
        lambda codes, **kw: [{"code": "512480", "name": "半导体ETF", "change_pct": 1.2}],
    )
    first = svc.verify_daily(now=_cst(2026, 11, 9, 15, 40))
    second = svc.verify_daily(now=_cst(2026, 11, 9, 16, 30))
    assert first["verified"] == 2
    assert second["verified"] == 0 and second["missing"] == 0  # 幂等 upsert


def test_verify_daily_weekend_boundary_friday_generated(env, monkeypatch) -> None:
    """QA mock 场景自证(周末边界): 周五 15:40 生成预测(target=下周一, 由
    next_trading_date 跨周末顺推) → 周一 15:40 验证 verified=1, 且验证行
    trade_date == target_date == 周一(今日实时涨跌即目标日最终涨跌)。"""
    # 周五(2026-11-06) 15:40: 生成次日预测
    env.set_trading_day(_cst(2026, 11, 6))
    _patch_market(monkeypatch)
    gen = svc.generate_prediction(now=_cst(2026, 11, 6, 15, 40))
    assert gen["skipped"] is False
    assert gen["trade_date"] == "2026-11-06"
    assert gen["target_date"] == "2026-11-09"  # 周五 → 下一交易日周一(跨周末)

    # 周一(2026-11-09) 15:40: 验证 target=周一 的预测
    env.set_trading_day(_cst(2026, 11, 9))
    _patch_market(monkeypatch)  # 沪深300 +0.6% → up 命中(>0.3)
    out = svc.verify_daily(now=_cst(2026, 11, 9, 15, 40))
    assert out["skipped"] is False
    assert out["verified"] == 1 and out["missing"] == 0
    assert out["target_date"] == "2026-11-09"
    v = env.store.get_verification("2026-11-09", "prediction", gen["id"])
    assert v is not None
    assert v["trade_date"] == "2026-11-09" == gen["target_date"]  # 验证日=目标日=周一
    assert v["hit"] == 1 and v["actual_return"] == pytest.approx(0.6)


# ===== 准确率统计(R6.2/R6.3, 对账口径) =====


def test_compute_accuracy_reconciliation(tmp_path: Path) -> None:
    """看板数字 == 库内重算(对账测试锚点): 5 样本命中 4 → 0.8; <5 → None。"""
    store = PredictionStore(tmp_path / "p.db")
    store.init_db()
    today = svc.beijing_today_str()
    # 修复后验证口径: verify_daily 在目标日当日验证, 验证行 trade_date == target_date == today
    target = today
    # 5 条预测验证: 4 命中
    for i in range(5):
        p = store.upsert_prediction(
            trade_date=today, target_date=target, benchmark=f"B{i}",
            direction="up", confidence=60, rule_direction="up", rule_confidence=60,
            rule_reasons=[], llm_direction="down" if i == 4 else "up",
            llm_confidence=60, llm_reason="", conflict_flag=0,
        )
        assert p is not None
        ret = 0.8 if i < 4 else -0.8
        store.upsert_verification(target, "prediction", p["id"], "up", "up",
                                  1 if i < 4 else 0, ret)
    # 4 条推荐验证(样本 <5): 直接以 SQL 追加, 避开 replace 的整日覆盖语义
    from src.core.prediction_store import _LOCK as _store_lock

    ids = []
    with _store_lock:
        conn = store._connection()
        for i in range(4):
            cur = conn.execute(
                "INSERT INTO recommendations (trade_date, target_date, symbol, name, kind,"
                " direction, confidence, reason, source_signal, created_at)"
                " VALUES (?, ?, ?, ?, 'stock', 'up', 60, '', ?, 't')",
                (today, target, f"51030{i}", f"基金{i}", f"src{i}"),
            )
            ids.append(cur.lastrowid)
        conn.commit()
    for i, rid in enumerate(ids):
        store.upsert_verification(target, "recommendation", rid, "up", "up",
                                  1 if i < 3 else 0, 0.9)

    dash = svc.compute_accuracy(store=store, stat_date=today)
    # 30d 窗口: 预测 rule 4/5 → hit_rate 0.8; 推荐 3/4 → 样本不足 None
    rule30 = dash["windows"]["30d"]["prediction"]["rule"]
    assert rule30["hits"] == 4 and rule30["total"] == 5
    assert rule30["hit_rate"] == pytest.approx(0.8)
    rec30 = dash["windows"]["30d"]["recommendation"]["rule"]
    assert rec30["total"] == 4 and rec30["hit_rate"] is None  # 样本<5 → None
    assert dash["insufficient_sample"] is True
    # llm 来源: 5 条 LLM 结论均与实际一致(前 4 条 up+0.8 命中, 第 5 条 down-0.8 命中)
    llm30 = dash["windows"]["30d"]["prediction"]["llm"]
    assert llm30["total"] == 5 and llm30["hits"] == 5
    assert llm30["hit_rate"] == pytest.approx(1.0)
    # 落库对账: accuracy_stats 与内存口径一致
    stats = store.list_accuracy_stats(stat_date=today)
    pair = {(s["window"], s["target_kind"], s["source"]): s for s in stats}
    assert pair[("30d", "prediction", "rule")]["hit_rate"] == pytest.approx(0.8)
    assert pair[("30d", "recommendation", "rule")]["hit_rate"] is None
    # 走势: 仅一个验证日, total=9, hit_rate=(4+3)/9
    assert len(dash["trend_30d"]) == 1
    assert dash["trend_30d"][0]["total"] == 9
    assert dash["trend_30d"][0]["hit_rate"] == pytest.approx(7 / 9, abs=1e-3)

"""tests/test_prediction_orphan_target.py — QF3-05 孤儿目标日验收。

缺陷(QF3-05): next_trading_date() 只跳周末(仓库无交易日历)。节前最后一个交易日
生成的预测, target_date 会落在节假日; 而 verify_daily 用 target_date == today
精确查, 查不到 → 样本既没被验证也没被标记, 连日志都没有 —— 静默消失。

修复口径: verify_daily 正常流程之后做孤儿检测(回溯窗口
cfg["verify"]["orphan_lookback_days"], 默认 10 天), 命中的写 missing=1
并带 error_code="orphan_target_date"; 超窗只告警不回填; 响应增加 orphaned 计数。
"""
from __future__ import annotations

import copy
import datetime as _dt
import sys
from pathlib import Path

import pytest
from loguru import logger as _logger

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core import data_source_v2 as ds2  # noqa: E402
from src.core import prediction_service as svc  # noqa: E402
from src.core.prediction_store import PredictionStore  # noqa: E402

_CST = _dt.timezone(_dt.timedelta(hours=8))
# 2026-11-09 是周一(既有用例口径), 故 11-10 周二、11-06 周五、11-05 周四
_TODAY = "2026-11-10"


def _cst(y: int, m: int, d: int, hh: int = 15, mm: int = 40) -> _dt.datetime:
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_CST)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = PredictionStore(tmp_path / "prediction.db")
    store.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: store)
    monkeypatch.setattr(svc, "beijing_now", lambda: _cst(2026, 11, 10))
    # 交易日探针: 今日(11-10)为交易日
    monkeypatch.setattr(
        ds2, "get_fund_realtime_tencent",
        lambda codes, **kw: [{"code": "018957", "update_time": f"{_TODAY} 15:00"}],
    )
    monkeypatch.setattr(
        ds2, "get_index_realtime",
        lambda: [{"code": "000300", "name": "沪深300", "price": 4000.0, "change_pct": 0.8}],
    )

    class _Env:
        def __init__(self) -> None:
            self.store = store

        def seed(self, trade_date: str, target_date: str, benchmark: str) -> int:
            p = self.store.upsert_prediction(
                trade_date=trade_date, target_date=target_date, benchmark=benchmark,
                direction="up", confidence=60.0, rule_direction="up",
                rule_confidence=60.0, rule_reasons=[], llm_direction=None,
                llm_confidence=None, llm_reason="", conflict_flag=0,
            )
            assert p is not None
            return int(p["id"])

    return _Env()


def _cfg_with_lookback(days: int) -> dict:
    cfg = copy.deepcopy(svc.sig.load_signal_config())
    cfg["verify"]["orphan_lookback_days"] = days
    return cfg


def test_orphan_marked_missing_and_excluded_from_accuracy(env, monkeypatch) -> None:
    """孤儿(节假日前生成, target_date 已过期且无验证行) → 写 missing=1, orphaned=1。"""
    # 周四 11-05 生成, 目标日 11-06(节假日, 从未被验证) → 孤儿
    orphan_id = env.seed("2026-11-05", "2026-11-06", "ORPHAN")
    # 今日正常预测, 用于确认正常流程未受影响
    normal_id = env.seed(_TODAY, _TODAY, "NORMAL")

    out = svc.verify_daily(now=_cst(2026, 11, 10))

    assert out["skipped"] is False
    assert out["verified"] == 1  # 只验证今日那条
    assert out["orphaned"] == 1

    v = env.store.get_verification("2026-11-06", "prediction", orphan_id)
    assert v is not None and v["missing"] == 1, "孤儿未被标记为 missing"
    assert v["hit"] is None and v["actual_return"] is None

    # detail 中列出, 并带 error_code
    orphan_entries = [d for d in out["detail"] if d.get("error_code") == "orphan_target_date"]
    assert len(orphan_entries) == 1
    assert orphan_entries[0]["target_id"] == orphan_id
    assert orphan_entries[0]["target_date"] == "2026-11-06"

    # 不进入准确率分子分母(missing 行被排除)
    counted = env.store.list_verifications_between("2026-01-01", "2026-12-31")
    assert orphan_id not in {r["target_id"] for r in counted if r["target_kind"] == "prediction"}
    assert normal_id in {r["target_id"] for r in counted if r["target_kind"] == "prediction"}


def test_orphan_beyond_lookback_not_backfilled_but_warned(env, monkeypatch) -> None:
    """超窗孤儿 → 不写验证行, 但有汇总 warning。"""
    cfg = _cfg_with_lookback(2)  # 先构造(此时 load_signal_config 尚未被替换)
    monkeypatch.setattr(svc.sig, "load_signal_config", lambda path=None: cfg)
    old_id = env.seed("2026-11-05", "2026-11-06", "OLD")  # 距 today 4 天 > 窗口 2 天

    records: list[str] = []
    sink_id = _logger.add(lambda m: records.append(m), level="WARNING")
    try:
        out = svc.verify_daily(now=_cst(2026, 11, 10))
    finally:
        _logger.remove(sink_id)

    assert out["orphaned"] == 0
    assert env.store.get_verification("2026-11-06", "prediction", old_id) is None
    joined = "\n".join(records)
    assert "超出回溯窗口" in joined
    assert "不自动回填" in joined


def test_no_orphan_normal_verification_unaffected(env, monkeypatch) -> None:
    """无孤儿 → orphaned=0, 正常验证照常进行。"""
    normal_id = env.seed(_TODAY, _TODAY, "NORMAL")

    out = svc.verify_daily(now=_cst(2026, 11, 10))

    assert out["orphaned"] == 0
    assert out["verified"] == 1
    v = env.store.get_verification(_TODAY, "prediction", normal_id)
    assert v is not None and v["missing"] == 0 and v["hit"] == 1


def test_orphan_detection_idempotent(env, monkeypatch) -> None:
    """重复运行不重复标记(已有验证行即视为非孤儿)。"""
    orphan_id = env.seed("2026-11-05", "2026-11-06", "ORPHAN")

    first = svc.verify_daily(now=_cst(2026, 11, 10))
    second = svc.verify_daily(now=_cst(2026, 11, 10))

    assert first["orphaned"] == 1
    assert second["orphaned"] == 0  # 已标记过, 不再重复
    v = env.store.get_verification("2026-11-06", "prediction", orphan_id)
    assert v is not None and v["missing"] == 1


def test_recommendation_orphan_also_marked(env, monkeypatch) -> None:
    """推荐同样会被标记为孤儿(不只预测)。"""
    n = env.store.replace_recommendations("2026-11-05", "2026-11-06", [
        {"symbol": "512480", "name": "半导体ETF", "kind": "stock", "direction": "up",
         "confidence": 70.0, "reason": "r", "source_signal": "sector_momentum:半导体|change_pct=+2.40"},
    ])
    assert n == 1
    rec_id = env.store.list_recommendations_for_target("2026-11-06")[0]["id"]

    out = svc.verify_daily(now=_cst(2026, 11, 10))

    assert out["orphaned"] == 1
    v = env.store.get_verification("2026-11-06", "recommendation", rec_id)
    assert v is not None and v["missing"] == 1

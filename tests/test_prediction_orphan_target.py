"""tests/test_prediction_orphan_target.py — QF3-05 孤儿目标日验收。

缺陷(QF3-05): next_trading_date() 只跳周末(仓库无交易日历)。节前最后一个交易日
生成的预测, target_date 会落在节假日; 而 verify_daily 用 target_date == today
精确查, 查不到 → 样本既没被验证也没被标记, 连日志都没有 —— 静默消失。

修复口径: verify_daily 正常流程之后做孤儿检测(回溯窗口
cfg["verify"]["orphan_lookback_days"], 默认 10 天), 命中的写 missing=1
并带 error_code="orphan_target_date"; 超窗只告警不回填; 响应增加 orphaned 计数。

确定性说明: _mark_orphans 通过 list_predictions(days=N)/list_recommendations(days=N)
取数, 其过滤基准是 SQLite 的 date('now')(真实时钟)。因此本文件的 seed 日期
一律用 conftest.trading_days_ago() 取相对真实今天的偏移, 且注入的 now 与 seed
日期来自同一基准 —— 不写死任何日历日, 避免"日期炸弹"。
"""
from __future__ import annotations

import copy
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
from tests.conftest import cst_on, trading_days_ago  # noqa: E402


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = PredictionStore(tmp_path / "prediction.db")
    store.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: store)

    # 注入的 now 与 seed 日期同源: 都取"真实今天的相对基准"
    today = trading_days_ago(0)
    monkeypatch.setattr(svc, "beijing_now", lambda: cst_on(today))
    monkeypatch.setattr(
        ds2, "get_fund_realtime_tencent",
        lambda codes, **kw: [{"code": "018957", "update_time": f"{today} 15:00"}],
    )
    monkeypatch.setattr(
        ds2, "get_index_realtime",
        lambda: [{"code": "000300", "name": "沪深300", "price": 4000.0, "change_pct": 0.8}],
    )

    class _Env:
        def __init__(self) -> None:
            self.store = store
            self.today = today

        @property
        def now(self):
            return cst_on(today)

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
    """孤儿(target_date 已过期且无验证行) → 写 missing=1, orphaned=1。"""
    orphan_target = trading_days_ago(1)
    # 几天前生成, 目标日已过期且从未被验证 → 孤儿
    orphan_id = env.seed(trading_days_ago(5), orphan_target, "ORPHAN")
    # 今日正常预测, 用于确认正常流程未受影响
    normal_id = env.seed(env.today, env.today, "NORMAL")

    out = svc.verify_daily(now=env.now)

    assert out["skipped"] is False
    assert out["verified"] == 1  # 只验证今日那条
    assert out["orphaned"] == 1

    v = env.store.get_verification(orphan_target, "prediction", orphan_id)
    assert v is not None and v["missing"] == 1, "孤儿未被标记为 missing"
    assert v["hit"] is None and v["actual_return"] is None

    # detail 中列出, 并带 error_code
    orphan_entries = [d for d in out["detail"] if d.get("error_code") == "orphan_target_date"]
    assert len(orphan_entries) == 1
    assert orphan_entries[0]["target_id"] == orphan_id
    assert orphan_entries[0]["target_date"] == orphan_target

    # 不进入准确率分子分母(missing 行被排除)
    counted = env.store.list_verifications_between("2000-01-01", "2100-01-01")
    counted_ids = {r["target_id"] for r in counted if r["target_kind"] == "prediction"}
    assert orphan_id not in counted_ids
    assert normal_id in counted_ids


def test_orphan_beyond_lookback_not_backfilled_but_warned(env, monkeypatch) -> None:
    """超窗孤儿 → 不写验证行, 但有汇总 warning。"""
    cfg = _cfg_with_lookback(2)  # 先构造(此时 load_signal_config 尚未被替换)
    monkeypatch.setattr(svc.sig, "load_signal_config", lambda path=None: cfg)

    orphan_target = trading_days_ago(5)  # 至少 5 个日历日前 > 窗口 2 天
    old_id = env.seed(trading_days_ago(10), orphan_target, "OLD")

    records: list[str] = []
    sink_id = _logger.add(lambda m: records.append(m), level="WARNING")
    try:
        out = svc.verify_daily(now=env.now)
    finally:
        _logger.remove(sink_id)

    assert out["orphaned"] == 0
    assert env.store.get_verification(orphan_target, "prediction", old_id) is None
    joined = "\n".join(records)
    assert "超出回溯窗口" in joined
    assert "不自动回填" in joined


def test_no_orphan_normal_verification_unaffected(env, monkeypatch) -> None:
    """无孤儿 → orphaned=0, 正常验证照常进行。"""
    normal_id = env.seed(env.today, env.today, "NORMAL")

    out = svc.verify_daily(now=env.now)

    assert out["orphaned"] == 0
    assert out["verified"] == 1
    v = env.store.get_verification(env.today, "prediction", normal_id)
    assert v is not None and v["missing"] == 0 and v["hit"] == 1


def test_orphan_detection_idempotent(env, monkeypatch) -> None:
    """重复运行不重复标记(已有验证行即视为非孤儿)。"""
    orphan_target = trading_days_ago(1)
    orphan_id = env.seed(trading_days_ago(5), orphan_target, "ORPHAN")

    first = svc.verify_daily(now=env.now)
    second = svc.verify_daily(now=env.now)

    assert first["orphaned"] == 1
    assert second["orphaned"] == 0  # 已标记过, 不再重复
    v = env.store.get_verification(orphan_target, "prediction", orphan_id)
    assert v is not None and v["missing"] == 1


def test_recommendation_orphan_also_marked(env, monkeypatch) -> None:
    """推荐同样会被标记为孤儿(不只预测)。"""
    orphan_target = trading_days_ago(1)
    n = env.store.replace_recommendations(trading_days_ago(5), orphan_target, [
        {"symbol": "512480", "name": "半导体ETF", "kind": "stock", "direction": "up",
         "confidence": 70.0, "reason": "r", "source_signal": "sector_momentum:半导体|change_pct=+2.40"},
    ])
    assert n == 1
    rec_id = env.store.list_recommendations_for_target(orphan_target)[0]["id"]

    out = svc.verify_daily(now=env.now)

    assert out["orphaned"] == 1
    v = env.store.get_verification(orphan_target, "recommendation", rec_id)
    assert v is not None and v["missing"] == 1

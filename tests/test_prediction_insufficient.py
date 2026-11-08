"""tests/test_prediction_insufficient.py — P0 QF3-01 数据不足守卫验收。

缺陷(QF3-01): 三信号(breadth_ratio / sentiment_score / sector_momentum)全缺失时,
synthesize() 用 composite=0.0 落到 else 分支输出 direction="flat"、confidence=50.0,
generate_prediction() 再无条件落库 —— 一条"全盲却确定"的预测进入数据库, 随后被
/verify 正常验证并计入准确率分子分母, 造成数据真实性缺陷。

修复口径:
- synthesize() 返回 valid_signals / insufficient; 全缺失时 direction/confidence/
  rule_direction/rule_confidence 全为 None(不再用 flat/50 兜底),
  composite 与 reasons 保持原样以保留可追溯性。
- generate_prediction() 在 insufficient 时不调 LLM、不落库, 返回 skipped=True。

本文件用真实 synthesize() 验证(不 mock 被测函数)。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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


def _cst(y: int, m: int, d: int, hh: int = 15, mm: int = 40) -> _dt.datetime:
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_CST)


class _FakeLLM:
    """可编程 LLM 假体: 记录每次调用, 用于断言"数据不足时未消耗额度"。"""

    def __init__(self, deep_json: str = "") -> None:
        self.deep_json = deep_json
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"kwargs": kwargs, "messages": messages})
        return SimpleNamespace(content=self.deep_json, provider="fake", model="fake")


# ===== 纯函数层: synthesize() 的数据不足口径 =====


def test_synthesize_all_missing_returns_none_not_flat() -> None:
    """三信号全缺失 → insufficient=True 且四个结论字段全为 None(不得 flat/50 兜底)。"""
    out = sig.synthesize(None, None, None, sig.DEFAULT_CONFIG)

    assert out["insufficient"] is True
    assert out["valid_signals"] == 0
    assert out["direction"] is None
    assert out["confidence"] is None
    assert out["rule_direction"] is None
    assert out["rule_confidence"] is None

    # 可追溯性保留: composite 与 reasons 原样(便于回溯"为何判定数据不足")
    assert out["composite"] == 0.0
    assert len(out["reasons"]) == 3
    assert all(r["verdict"] == "missing" for r in out["reasons"])
    assert {r["signal"] for r in out["reasons"]} == {
        "breadth_ratio", "sentiment_score", "sector_momentum",
    }


def test_synthesize_all_missing_never_emits_flat_or_fifty() -> None:
    """回归护栏: 任何"全缺失"输入组合都不得产出 flat / 50.0。"""
    for sentiment, indices, sectors in (
        (None, None, None),
        ({}, None, []),
        ({"rise_count": 0, "fall_count": 0}, [], []),
        (None, [], None),
    ):
        out = sig.synthesize(sentiment, indices, sectors, sig.DEFAULT_CONFIG)
        assert out["insufficient"] is True, f"输入 {sentiment!r} 应判为数据不足"
        assert out["direction"] != "flat"
        assert out["confidence"] != 50.0
        assert out["direction"] is None and out["confidence"] is None


def test_synthesize_single_valid_signal_not_insufficient() -> None:
    """仅 1 个信号有效(板块动量) → insufficient=False, 走正常方向判定。"""
    out = sig.synthesize(
        None,
        None,
        [{"name": "半导体", "change_pct": 2.4}, {"name": "证券", "change_pct": 1.6}],
        sig.DEFAULT_CONFIG,
    )
    assert out["insufficient"] is False
    assert out["valid_signals"] == 1
    assert out["direction"] == "up"  # 板块净 +2 → +10 > 阈值 8
    assert out["rule_direction"] == "up"
    assert isinstance(out["confidence"], float)
    assert out["confidence"] > 0


def test_synthesize_two_valid_signals_not_insufficient() -> None:
    """2 个信号有效(广度+情绪) → insufficient=False, 走正常方向判定。"""
    sentiment = {
        "rise_count": 3200, "fall_count": 1500, "flat_count": 300,
        "sentiment": "偏多", "ratio": 3200 / 5000,
    }
    out = sig.synthesize(sentiment, None, None, sig.DEFAULT_CONFIG)
    assert out["insufficient"] is False
    assert out["valid_signals"] == 2
    assert out["direction"] == "up"
    assert out["confidence"] > 50
    # 缺失信号仍如实记录, 便于追溯
    missing = [r["signal"] for r in out["reasons"] if r["verdict"] == "missing"]
    assert missing == ["sector_momentum"]


# ===== 编排层: generate_prediction() 的数据不足守卫 =====


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离环境: tmp 库 + 假 LLM; 真实 PredictionStore, upsert_prediction 包一层 Mock。"""
    store = PredictionStore(tmp_path / "prediction.db")
    store.init_db()
    upsert_spy = Mock(wraps=store.upsert_prediction)
    monkeypatch.setattr(store, "upsert_prediction", upsert_spy)
    monkeypatch.setattr(svc, "get_store", lambda: store)

    fake = _FakeLLM()
    monkeypatch.setattr("src.core.llm_router.get_llm_router", lambda: fake)

    class _Env:
        def __init__(self) -> None:
            self.store = store
            self.llm = fake
            self.upsert_spy = upsert_spy

        def set_trading_day(self, now: _dt.datetime) -> None:
            monkeypatch.setattr(
                ds2, "get_fund_realtime_tencent",
                lambda codes, **kw: [
                    {"code": "018957", "update_time": now.strftime("%Y-%m-%d 15:00")}
                ],
            )

        def set_all_sources_missing(self) -> None:
            """三信号全缺失: 情绪源抛错 → sentiment={} → 传 None; 板块空 → 传 None。"""
            def _boom():
                raise RuntimeError("情绪源不可用(模拟)")

            monkeypatch.setattr(ds2, "get_market_sentiment", _boom)
            monkeypatch.setattr(ds2, "get_index_realtime", lambda: [])
            monkeypatch.setattr(ds2, "get_sector_ranking", lambda: [])

    return _Env()


def test_generate_prediction_insufficient_skips_without_persisting(env, monkeypatch) -> None:
    """三信号全缺失 → skipped=True, 不调 LLM, 不调用 store.upsert_prediction。"""
    # 日期取真实今天的相对基准: list_predictions(days=30) 以 date('now') 为基准,
    # 硬编码日期会随时间滑出窗口(日期炸弹)。
    today = trading_days_ago(0)
    now = cst_on(today)
    env.set_trading_day(now)
    env.set_all_sources_missing()

    out = svc.generate_prediction(now=now)

    # 返回口径
    assert out["skipped"] is True
    assert out["reason"] == "insufficient_data"
    assert out["trade_date"] == today
    assert out["missing_fields"] == ["sentiment"]  # 逐源容错仍如实记录
    assert "direction" not in out  # 绝不返回兜底方向

    # 不落库(核心红线)
    assert env.upsert_spy.call_count == 0
    assert env.store.list_predictions(days=30) == []

    # 不消耗 LLM 额度
    assert env.llm.calls == []


def test_generate_prediction_insufficient_logs_warning(env, caplog) -> None:
    """守卫触发时必须有可观测告警, 且含 trade_date 与 missing。"""
    from loguru import logger as _loguru_logger

    env.set_trading_day(_cst(2026, 11, 9))
    env.set_all_sources_missing()

    records: list[str] = []
    sink_id = _loguru_logger.add(lambda m: records.append(m), level="WARNING")
    try:
        svc.generate_prediction(now=_cst(2026, 11, 9, 15, 40))
    finally:
        _loguru_logger.remove(sink_id)

    joined = "\n".join(records)
    assert "数据不足" in joined
    assert "trade_date=2026-11-09" in joined
    assert "missing=" in joined


def test_generate_prediction_one_valid_signal_still_persists(env, monkeypatch) -> None:
    """仅 1 个信号有效 → 正常路径: 调 LLM 且落库一次。"""
    # 同上: 日期取真实今天的相对基准, 避免 list_predictions(days=30) 的窗口漂移。
    today = trading_days_ago(0)
    now = cst_on(today)
    env.set_trading_day(now)

    def _boom():
        raise RuntimeError("情绪源不可用(模拟)")

    monkeypatch.setattr(ds2, "get_market_sentiment", _boom)
    monkeypatch.setattr(ds2, "get_index_realtime", lambda: [])
    monkeypatch.setattr(
        ds2, "get_sector_ranking",
        lambda: [{"name": "半导体", "change_pct": 2.4}, {"name": "证券", "change_pct": 1.6}],
    )

    out = svc.generate_prediction(now=now)

    assert out["skipped"] is False
    assert out["direction"] == "up"
    # 目标日独立校验: 晚于今日且为工作日(不拿被测函数当 oracle)
    assert out["target_date"] > today
    assert _dt.date.fromisoformat(out["target_date"]).weekday() < 5
    assert env.upsert_spy.call_count == 1
    assert len(env.store.list_predictions(days=30)) == 1
    assert len(env.llm.calls) == 1  # deep 档正常调用

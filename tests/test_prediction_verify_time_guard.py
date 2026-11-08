"""tests/test_prediction_verify_time_guard.py — QF3-04 `/verify` 时点守卫验收。

缺陷(QF3-04): verify_daily() 只判断"是否交易日", 不判断现在几点。
_hs300_change_pct() 取的是**实时**涨跌, 若在盘中(如 10:00)被调用, 会把分钟级
波动当作目标日最终涨跌写入并 missing=0; 15:40 正式跑时 _pending() 返回 False,
不再覆盖 —— 错误结果被永久固化进准确率。

修复口径: now 早于当日 earliest_hhmm(默认 15:00)时直接返回
skipped=True / reason="too_early", 且不读库不写库; 16:30 重试路径(_is_verify_final)不受影响。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core import data_source_v2 as ds2  # noqa: E402
from src.core import prediction_service as svc  # noqa: E402
from src.core.prediction_store import PredictionStore  # noqa: E402

_CST = _dt.timezone(_dt.timedelta(hours=8))
_DAY = (2026, 11, 9)


def _cst(hh: int, mm: int = 0, day: tuple[int, int, int] = _DAY) -> _dt.datetime:
    return _dt.datetime(day[0], day[1], day[2], hh, mm, tzinfo=_CST)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = PredictionStore(tmp_path / "prediction.db")
    store.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: store)

    class _Env:
        def __init__(self) -> None:
            self.store = store

        def set_trading_day(self, now: _dt.datetime) -> None:
            monkeypatch.setattr(
                ds2, "get_fund_realtime_tencent",
                lambda codes, **kw: [
                    {"code": "018957", "update_time": now.strftime("%Y-%m-%d 15:00")}
                ],
            )

        def set_market(self, change_pct: float = 0.8) -> None:
            monkeypatch.setattr(
                ds2, "get_index_realtime",
                lambda: [{"code": "000300", "name": "沪深300",
                          "price": 4000.0, "change_pct": change_pct}],
            )

        def seed_predictions(self, target_date: str, n: int = 2) -> None:
            for i in range(n):
                p = self.store.upsert_prediction(
                    trade_date=target_date, target_date=target_date, benchmark=f"B{i}",
                    direction="up", confidence=60.0, rule_direction="up",
                    rule_confidence=60.0, rule_reasons=[], llm_direction=None,
                    llm_confidence=None, llm_reason="", conflict_flag=0,
                )
                assert p is not None

    return _Env()


def test_verify_too_early_skips_without_touching_db(env) -> None:
    """14:59 → too_early, 且 verification 表零新增(核心断言)。"""
    env.set_trading_day(_cst(15))
    env.set_market()
    env.seed_predictions("2026-11-09", n=2)

    out = svc.verify_daily(now=_cst(14, 59))

    assert out["skipped"] is True
    assert out["reason"] == "too_early"
    assert out["verified"] == 0 and out["missing"] == 0
    assert out["detail"] == []
    assert env.store.list_verifications(days=30) == [], "时点守卫未生效, 盘中写入了验证行"


def test_verify_at_earliest_boundary_proceeds(env) -> None:
    """恰好 15:00 → 正常进入验证流程(边界含等号)。"""
    env.set_trading_day(_cst(15))
    env.set_market(change_pct=0.8)
    env.seed_predictions("2026-11-09", n=2)

    out = svc.verify_daily(now=_cst(15, 0))

    assert out["skipped"] is False
    assert out.get("reason") != "too_early"
    assert out["verified"] == 2
    rows = env.store.list_verifications(days=30)
    assert len(rows) == 2
    assert all(r["hit"] == 1 for r in rows)  # up 预测 vs +0.8% → 命中


def test_verify_at_scheduled_time_proceeds(env) -> None:
    """15:40 正式跑 → 正常进入, 不受时点守卫影响。"""
    env.set_trading_day(_cst(15))
    env.set_market(change_pct=0.8)
    env.seed_predictions("2026-11-09", n=2)

    out = svc.verify_daily(now=_cst(15, 40))

    assert out["skipped"] is False
    assert out["verified"] == 2
    assert len(env.store.list_verifications(days=30)) == 2


def test_retry_after_final_still_works(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """16:30 重试路径(_is_verify_final)不受时点守卫影响, 仍写 missing 占位。"""
    env.set_trading_day(_cst(15))

    def _boom():
        raise RuntimeError("行情取不到(模拟)")

    monkeypatch.setattr(ds2, "get_index_realtime", _boom)
    env.seed_predictions("2026-11-09", n=1)

    out = svc.verify_daily(now=_cst(16, 30))

    assert out["skipped"] is False
    assert out["missing"] == 1 and out["verified"] == 0
    rows = env.store.list_verifications(days=30)
    assert len(rows) == 1 and rows[0]["missing"] == 1


def test_earliest_hhmm_configurable(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """earliest_hhmm 可由配置调整: 改成 09:30 后, 10:00 不再被判 too_early。"""
    env.set_trading_day(_cst(10))
    env.set_market(change_pct=0.8)
    env.seed_predictions("2026-11-09", n=1)

    import copy
    cfg = copy.deepcopy(svc.sig.load_signal_config())
    cfg["verify"]["earliest_hhmm"] = "0930"
    monkeypatch.setattr(svc.sig, "load_signal_config", lambda path=None: cfg)

    out = svc.verify_daily(now=_cst(10, 0))
    assert out["skipped"] is False
    assert out["verified"] == 1


def test_earliest_hhmm_accepts_colon_format(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置兼容 "1500" 与 "15:00" 两种写法。"""
    cfg = {"verify": {"earliest_hhmm": "15:00"}}
    assert svc._verify_earliest(_cst(10), cfg).strftime("%H:%M") == "15:00"
    cfg2 = {"verify": {"earliest_hhmm": "1500"}}
    assert svc._verify_earliest(_cst(10), cfg2).strftime("%H:%M") == "15:00"


def test_earliest_hhmm_invalid_falls_back_to_1500() -> None:
    """配置非法 → 回退 15:00, 不抛异常。"""
    for bad in ("", "abc", "9999", "25:00", None):
        got = svc._verify_earliest(_cst(10), {"verify": {"earliest_hhmm": bad}})
        assert got.strftime("%H:%M") == "15:00", f"非法配置 {bad!r} 未回退"

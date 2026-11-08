"""tests/test_prediction_probe_tristate.py — QF3-06 交易日探针三态验收。

缺陷(QF3-06): check_trading_day() 是 bool, 取数异常与"无返回数据"都 return False
(=非交易日)。于是上游一挂, 采集/预测/推荐/验证全部静默跳过, 调度任务拿到 200
记为成功 —— 故障被伪装成"今天休市", 可能持续多日无人发现。

修复口径:
- probe_trading_day() 返回 "trading" / "non_trading" / "probe_failed";
- check_trading_day() 保留为薄包装(probe == "trading"), 既有调用方零行为变化;
- 编排层 probe_failed → skipped=True, reason="probe_failed"(不再是 non_trading_day);
- 路由层 probe_failed → 503 PREDICTION_PROBE_UNAVAILABLE, non_trading_day 维持 200。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core import data_source_v2 as ds2  # noqa: E402
from src.core import prediction_service as svc  # noqa: E402
from src.core.prediction_store import PredictionStore  # noqa: E402
from web.api import app  # noqa: E402

client = TestClient(app)
_ADMIN_TOKEN = "probe-tristate-token"
_CST = _dt.timezone(_dt.timedelta(hours=8))
_NOW = _dt.datetime(2026, 11, 9, 15, 40, tzinfo=_CST)  # 周一 15:40(避开 verify 时点守卫)


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PredictionStore:
    s = PredictionStore(tmp_path / "prediction.db")
    s.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: s)
    monkeypatch.setenv("ADMIN_TOKEN", _ADMIN_TOKEN)
    # 固定时钟, 避免依赖真实时间
    monkeypatch.setattr(svc, "beijing_now", lambda: _NOW)
    return s


def _probe_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(codes, **kw):
        raise RuntimeError("上游数据源不可用(模拟)")

    monkeypatch.setattr(ds2, "get_fund_realtime_tencent", _boom)


def _probe_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds2, "get_fund_realtime_tencent", lambda codes, **kw: [])


def _probe_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针返回昨日日期 → 明确判定为非交易日。"""
    monkeypatch.setattr(
        ds2, "get_fund_realtime_tencent",
        lambda codes, **kw: [{"code": "018957", "update_time": "2026-11-08 15:00"}],
    )


def _probe_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ds2, "get_fund_realtime_tencent",
        lambda codes, **kw: [{"code": "018957", "update_time": "2026-11-09 15:00"}],
    )


# ===== 纯函数三态 =====


def test_probe_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_trading(monkeypatch)
    assert svc.probe_trading_day(_NOW) == "trading"


def test_probe_non_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_stale(monkeypatch)
    assert svc.probe_trading_day(_NOW) == "non_trading"


def test_probe_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针抛异常 → probe_failed(不再折叠成非交易日)。"""
    _probe_raises(monkeypatch)
    assert svc.probe_trading_day(_NOW) == "probe_failed"


def test_probe_failed_on_empty_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针返回空列表 → probe_failed(不再折叠成非交易日)。"""
    _probe_empty(monkeypatch)
    assert svc.probe_trading_day(_NOW) == "probe_failed"


def test_check_trading_day_wrapper_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """薄包装语义不变: 只有 trading 才为 True(既有调用方零行为变化)。"""
    _probe_trading(monkeypatch)
    assert svc.check_trading_day(_NOW) is True

    _probe_stale(monkeypatch)
    assert svc.check_trading_day(_NOW) is False

    _probe_raises(monkeypatch)
    assert svc.check_trading_day(_NOW) is False  # 故障仍 fail-closed 为 False


# ===== 编排层: probe_failed 不等于休市 =====


def test_generate_prediction_probe_failed_reason(store, monkeypatch: pytest.MonkeyPatch) -> None:
    """探针故障 → skipped=True 且 reason="probe_failed"(不是 non_trading_day)。"""
    _probe_raises(monkeypatch)

    out = svc.generate_prediction(now=_NOW)

    assert out["skipped"] is True
    assert out["reason"] == "probe_failed"
    assert store.list_predictions(days=30) == []


def test_generate_recommendations_probe_failed_reason(store, monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_empty(monkeypatch)

    out = svc.generate_recommendations(now=_NOW)

    assert out["skipped"] is True
    assert out["reason"] == "probe_failed"


def test_verify_daily_probe_failed_reason(store, monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_raises(monkeypatch)

    out = svc.verify_daily(now=_NOW)

    assert out["skipped"] is True
    assert out["reason"] == "probe_failed"
    assert out["verified"] == 0 and out["missing"] == 0


def test_collect_snapshot_probe_failed_reason(store, monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_raises(monkeypatch)

    out = svc.collect_snapshot("morning", now=_NOW)

    assert out["skipped"] is True
    assert out["reason"] == "probe_failed"
    assert store.get_snapshot("2026-11-09", "morning") is None


def test_probe_failed_distinct_from_non_trading(store, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一入口下, 休市与故障必须给出不同 reason —— 这是本次修复的核心区分。"""
    _probe_stale(monkeypatch)
    assert svc.generate_prediction(now=_NOW)["reason"] == "non_trading_day"

    _probe_raises(monkeypatch)
    assert svc.generate_prediction(now=_NOW)["reason"] == "probe_failed"


# ===== 路由层: 故障让调度变红, 休市维持 200 =====


def test_route_probe_failed_returns_503(store, monkeypatch: pytest.MonkeyPatch) -> None:
    """探针故障 → 503 + PREDICTION_PROBE_UNAVAILABLE(让 GitHub Action 变红)。"""
    _probe_raises(monkeypatch)

    resp = client.post(
        "/api/prediction/predictions/generate",
        headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
    )

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "PREDICTION_PROBE_UNAVAILABLE"
    assert "探针" in detail["message"]


def test_route_non_trading_returns_200(store, monkeypatch: pytest.MonkeyPatch) -> None:
    """探针明确判定非交易日 → 200(正常业务状态, 不应让调度变红)。"""
    _probe_stale(monkeypatch)

    resp = client.post(
        "/api/prediction/predictions/generate",
        headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["skipped"] is True
    assert body["data"]["reason"] == "non_trading_day"


def test_route_verify_probe_failed_returns_503(store, monkeypatch: pytest.MonkeyPatch) -> None:
    """verify 端点同样升级为 503(时点守卫在前, 故用 15:40 时钟)。"""
    _probe_raises(monkeypatch)

    resp = client.post(
        "/api/prediction/verify", headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"}
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "PREDICTION_PROBE_UNAVAILABLE"


def test_route_collect_probe_failed_returns_503(store, monkeypatch: pytest.MonkeyPatch) -> None:
    _probe_raises(monkeypatch)

    resp = client.post(
        "/api/prediction/snapshots/collect",
        json={"session": "morning"},
        headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "PREDICTION_PROBE_UNAVAILABLE"

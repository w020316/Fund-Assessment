"""tests/test_prediction_routes.py — T03 路由验收。

覆盖:
- openapi.json 含全部 8+1 条 /api/prediction 路径(防漏挂 P0)
- 写端点无 token → 401; 公开端点匿名可达
- 统一响应结构 {code, message, data}
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from web.api import app  # noqa: E402
from web.routes import prediction as prediction_routes  # noqa: E402

client = TestClient(app)

# 设计文档 A1~A8 + export(主理人补充)
EXPECTED_PATHS = {
    "/api/prediction/snapshots",
    "/api/prediction/snapshots/collect",
    "/api/prediction/predictions",
    "/api/prediction/predictions/generate",
    "/api/prediction/recommendations",
    "/api/prediction/recommendations/generate",
    "/api/prediction/accuracy",
    "/api/prediction/verify",
    "/api/prediction/export",
}


def test_openapi_contains_all_prediction_paths() -> None:
    """防漏挂 P0: include_router 后全部路径必须进 openapi。"""
    paths = set(app.openapi()["paths"].keys())
    missing = EXPECTED_PATHS - paths
    assert not missing, f"漏挂的路由: {missing}"


def test_write_endpoints_require_admin_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """写端点逐个挂了 require_admin: 配置 token 后无头请求 → 401。"""
    monkeypatch.setenv("ADMIN_TOKEN", "route-test-token")
    for method, path in (
        ("post", "/api/prediction/snapshots/collect"),
        ("post", "/api/prediction/predictions/generate"),
        ("post", "/api/prediction/recommendations/generate"),
        ("post", "/api/prediction/verify"),
        ("get", "/api/prediction/export"),
    ):
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} 匿名未拦截"


def test_export_with_wrong_token_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "route-test-token")
    resp = client.get(
        "/api/prediction/export", headers={"Authorization": "Bearer wrong-token"}
    )
    assert resp.status_code == 401


def test_get_snapshots_response_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(trade_date, days):
        return [{
            "trade_date": "2026-11-09", "session": "morning",
            "collected_at": "2026-11-09T01:35:12Z", "payload": {},
            "missing_fields": [], "rule_summary": "s", "llm_summary": "",
        }]

    monkeypatch.setattr(prediction_routes.svc, "get_snapshots", _fake)
    resp = client.get("/api/prediction/snapshots?days=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "success"
    assert body["data"]["count"] == 1
    assert body["data"]["snapshots"][0]["session"] == "morning"


def test_get_accuracy_response_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake():
        return {
            "windows": {"day": {"prediction": {"rule": {"hits": 0, "total": 0, "hit_rate": None}},
                                "recommendation": {"rule": {"hits": 0, "total": 0, "hit_rate": None},
                                                   "llm": {"hits": 0, "total": 0, "hit_rate": None}}}},
            "insufficient_sample": True,
            "trend_30d": [],
            "stat_date": "2026-11-09",
        }

    monkeypatch.setattr(prediction_routes.svc, "async_accuracy_dashboard", _fake)
    resp = client.get("/api/prediction/accuracy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["insufficient_sample"] is True
    assert "trend_30d" in body["data"]


def test_collect_invalid_session_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 模式无 token 放行(require_admin 放行), session 非法 → code=-1 业务失败。"""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    async def _raise(_session):
        raise ValueError("session 必须是 morning/noon/close 之一")

    monkeypatch.setattr(prediction_routes.svc, "async_collect_snapshot", _raise)
    resp = client.post(
        "/api/prediction/snapshots/collect", json={"session": "evening"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == -1
    assert "session" in body["message"]


def test_generate_prediction_service_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    async def _boom():
        raise RuntimeError("落库失败(模拟)")

    monkeypatch.setattr(prediction_routes.svc, "async_generate_prediction", _boom)
    resp = client.post("/api/prediction/predictions/generate")
    assert resp.status_code == 200
    assert resp.json()["code"] == -1


# ---------------------------------------------------------------------------
# COV-01（第四轮）：补齐公开读 GET /predictions、/recommendations，写端点
# /recommendations/generate 成功+异常，/export 成功+库不可读，以及 collect/verify
# 的兜底异常分支（上游全失败也不 500，统一 code=-1）。
# ---------------------------------------------------------------------------

def test_get_predictions_response_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(days):
        assert days == 30
        return [{"trade_date": "2026-11-06", "target_date": "2026-11-07", "direction": "up"}]

    monkeypatch.setattr(prediction_routes.svc, "get_predictions", _fake)
    resp = client.get("/api/prediction/predictions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "success"
    assert body["data"]["count"] == 1
    assert body["data"]["predictions"][0]["direction"] == "up"


def test_get_recommendations_response_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(trade_date, days):
        assert trade_date is None  # date 空 → None
        return [{"fund_code": "002891", "action": "buy", "rule_signal": "s"}]

    monkeypatch.setattr(prediction_routes.svc, "get_recommendations", _fake)
    resp = client.get("/api/prediction/recommendations?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["count"] == 1
    assert body["data"]["recommendations"][0]["fund_code"] == "002891"


def test_generate_recommendations_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    async def _fake():
        return {"trade_date": "2026-11-09", "generated": 3}

    monkeypatch.setattr(prediction_routes.svc, "async_generate_recommendations", _fake)
    resp = client.post("/api/prediction/recommendations/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["generated"] == 3


def test_generate_recommendations_service_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    async def _boom():
        raise RuntimeError("落库失败(模拟)")

    monkeypatch.setattr(prediction_routes.svc, "async_generate_recommendations", _boom)
    resp = client.post("/api/prediction/recommendations/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == -1
    assert "推荐生成失败" in body["message"]


def test_collect_snapshot_upstream_failure_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 ValueError 的上游异常走兜底分支：code=-1，不 500（区别于非法 session）。"""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    async def _boom(_session):
        raise RuntimeError("上游全部失败(模拟)")

    monkeypatch.setattr(prediction_routes.svc, "async_collect_snapshot", _boom)
    resp = client.post("/api/prediction/snapshots/collect", json={"session": "morning"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == -1
    assert "快照采集失败" in body["message"]


def test_verify_service_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    async def _boom():
        raise RuntimeError("验证落库失败(模拟)")

    monkeypatch.setattr(prediction_routes.svc, "async_verify_daily", _boom)
    resp = client.post("/api/prediction/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == -1
    assert "验证失败" in body["message"]


def test_export_db_success_streams_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    import src.core.prediction_store as store_mod

    class _FakeStore:
        def export_bytes(self):
            return b"SQLITE-CONSISTENT-SNAPSHOT"

    monkeypatch.setattr(store_mod, "get_store", lambda: _FakeStore())
    resp = client.get("/api/prediction/export")
    assert resp.status_code == 200
    assert resp.content == b"SQLITE-CONSISTENT-SNAPSHOT"
    assert resp.headers["content-type"].startswith("application/octet-stream")
    assert "prediction_snapshot.db" in resp.headers["content-disposition"]


def test_export_db_unreadable_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """export_bytes 返回 None（库不可读）→ code=-1 业务失败，不抛 500、不返回空字节流。"""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    import src.core.prediction_store as store_mod

    class _FakeStore:
        def export_bytes(self):
            return None

    monkeypatch.setattr(store_mod, "get_store", lambda: _FakeStore())
    resp = client.get("/api/prediction/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == -1
    assert "导出失败" in body["message"]

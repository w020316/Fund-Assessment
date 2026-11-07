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

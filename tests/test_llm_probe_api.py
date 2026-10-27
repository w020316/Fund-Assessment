"""LLM 连通性自检端点测试(2026-10-27)。

覆盖 POST /api/health/llm/probe:
- 鉴权:未带令牌 / 令牌错误 → 401
- 正常探测:返回 ok_count/latency/provider
- 未知 Provider → 404
- GET /api/health/llm 暴露 free_providers

铁律:全部离线 —— 用 monkeypatch 替换 Router 单例与 _call_provider,不发真实请求。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.core.llm_router as llm_mod
from src.core.llm_router import LLMResponse, LLMRouter

# 真实 .env 里的 Key 一律不参与测试,否则探针会打到外部服务
_LLM_ENV_VARS = [
    "TTAPI_API_KEY", "AGNES_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL",
    "ZHIPU_API_KEY", "ZHIPU_API_KEYS", "SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS",
    "SPARK_API_KEY", "SPARK_API_KEYS", "DASHSCOPE_API_KEY", "DASHSCOPE_API_KEYS",
    "QIANFAN_API_KEY", "QIANFAN_API_KEYS", "GROQ_API_KEY", "GROQ_API_KEYS",
    "CEREBRAS_API_KEY", "CEREBRAS_API_KEYS", "OPENROUTER_API_KEY", "OPENROUTER_API_KEYS",
    "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS", "CF_ACCOUNT_ID", "CF_API_TOKEN",
]


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for key in _LLM_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def probe_client(monkeypatch):
    """把 web.api 使用的 LLM Router 单例替换为离线测试实例。"""
    from web.api import app

    router = LLMRouter()
    monkeypatch.setattr(llm_mod, "_router", router)
    return TestClient(app), router


class TestProbeEndpoint:
    def test_requires_admin_token(self, monkeypatch, probe_client):
        client, _ = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        assert client.post("/api/health/llm/probe").status_code == 401
        assert client.post(
            "/api/health/llm/probe", headers={"Authorization": "Bearer wrong-token"}
        ).status_code == 401

    def test_probe_all_with_admin(self, monkeypatch, probe_client):
        client, router = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        router._load_from_env()
        monkeypatch.setattr(
            router, "_call_provider",
            lambda p, *a, **kw: LLMResponse(content="pong", provider=p.name, model=p.model, latency_ms=3.0),
        )
        resp = client.post(
            "/api/health/llm/probe", headers={"Authorization": "Bearer test-admin-token"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok_count"] == 1
        assert body["results"][0]["provider"] == "zhipu_glm"

    def test_unknown_provider_returns_404(self, monkeypatch, probe_client):
        client, _ = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        resp = client.post(
            "/api/health/llm/probe?provider=nope",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 404

    def test_health_llm_lists_free_providers(self, probe_client):
        client, _ = probe_client
        resp = client.get("/api/health/llm")
        assert resp.status_code == 200
        body = resp.json()
        assert "free_providers" in body
        assert body["free_providers"] == []

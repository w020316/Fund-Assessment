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
    "LY77625_API_KEY", "LY77625_BASE_URL", "LY77625_MODEL",
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
        # 一个 ZHIPU_API_KEY 会加载两个免费模型槽位(4.7-Flash + 4.5-Flash 兜底)
        assert body["ok_count"] == 2
        assert [r["provider"] for r in body["results"]] == ["zhipu_glm", "zhipu_glm_45"]

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


# ===== T04：可观测面（routing 段 + probe?tier=）2026-11-07 增量，只追加 =====

_ROUTING_ENV = {
    "ZHIPU_API_KEY": "zk-secret-value",
    "AGNES_API_KEY": "ak-secret-value",
    "LY77625_API_KEY": "lk-secret-value",
}


class TestRoutingHealthEndpoint:
    def test_health_llm_exposes_routing_tiers(self, monkeypatch, probe_client):
        """AC9/AC11：routing.tiers 含 deep/standard/light 三键，值为有序 provider 名数组。"""
        client, router = probe_client
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()

        resp = client.get("/api/health/llm")
        assert resp.status_code == 200
        body = resp.json()

        routing = body["routing"]
        assert set(routing["tiers"]) == {"deep", "standard", "light"}
        assert routing["tiers"]["deep"] == ["zhipu_glm", "agnes", "ly77625"]
        assert routing["tiers"]["light"] == ["zhipu_glm", "agnes"]
        # standard 与 providers 全链一致
        assert routing["tiers"]["standard"] == list(body["providers"].keys())

    def test_health_llm_exposes_budgets_and_flags(self, monkeypatch, probe_client):
        client, router = probe_client
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()

        body = client.get("/api/health/llm").json()
        routing = body["routing"]
        assert routing["budgets_seconds"]["deep"] == 60.0
        assert routing["budgets_seconds"]["light"] == 15.0
        assert routing["enabled"] is True
        assert routing["same_upstream_groups"] == {"zhipu_glm": ["zhipu_glm_45"]}
        # 逐跳明细含 available 与 same_upstream_as
        deep = routing["routes"]["deep"]
        assert [r["provider"] for r in deep] == ["zhipu_glm", "agnes", "ly77625"]
        assert deep[1]["model"] == "agnes-3.0-flash"
        assert deep[1]["label"] == "agnes_30"
        assert deep[0]["available"] is True
        assert deep[0]["same_upstream_as"] is None

    def test_health_llm_never_leaks_api_keys(self, monkeypatch, probe_client):
        """AC10：响应体绝不含 api_key / sk- / Bearer 片段。"""
        client, router = probe_client
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()

        raw = client.get("/api/health/llm").text
        assert "api_key" not in raw.lower()
        assert "sk-" not in raw
        assert "Bearer" not in raw
        assert "zk-secret-value" not in raw
        assert "ak-secret-value" not in raw
        assert "lk-secret-value" not in raw

    def test_health_llm_keeps_existing_shape(self, probe_client):
        """既有两键 providers/free_providers 结构不变。"""
        client, _router = probe_client
        body = client.get("/api/health/llm").json()
        for key in ("status", "total_providers", "available_providers", "free_providers", "providers", "response_cache"):
            assert key in body
        assert body["status"] == "ok"

    def test_health_llm_last_routing_after_chat(self, monkeypatch, probe_client):
        """AC12：deep 业务触发后 routing.last_routing.tier == "deep"。"""
        from src.core.llm_router import _recorder

        client, router = probe_client
        _recorder.reset()  # 全局单例：先清空，避免其他用例的计数干扰
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()

        def fake_call(provider, messages, **kwargs):
            return LLMResponse(
                content="ok", provider=provider.name,
                model=kwargs.get("model") or provider.model, latency_ms=2.0,
            )

        monkeypatch.setattr(router, "_call_provider", fake_call)
        router.chat([{"role": "user", "content": "deep-routing-probe"}], task_tier="deep")

        routing = client.get("/api/health/llm").json()["routing"]
        assert routing["last_routing"]["tier"] == "deep"
        assert routing["last_routing"]["provider"] == "zhipu_glm"
        assert routing["counters"]["deep"]["calls"] == 1
        assert routing["counters"]["deep"]["success"] == 1


class TestProbeByTier:
    def test_probe_tier_deep_returns_three_candidates(self, monkeypatch, probe_client):
        """AC11：带 ADMIN_TOKEN 的 POST /probe?tier=deep 返回 3 条候选。"""
        client, router = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()
        monkeypatch.setattr(
            router, "_call_provider",
            lambda p, *a, **kw: LLMResponse(
                content="pong", provider=p.name, model=kw.get("model") or p.model, latency_ms=3.0
            ),
        )

        resp = client.post(
            "/api/health/llm/probe?tier=deep",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["tier"] == "deep"
        assert body["total"] == 3
        assert [r["provider"] for r in body["results"]] == ["zhipu_glm", "agnes", "ly77625"]
        # agnes 探活用的是档位覆盖模型，而不是 Provider 默认值
        assert body["results"][1]["model"] == "agnes-3.0-flash"

    def test_probe_tier_light_returns_two_candidates(self, monkeypatch, probe_client):
        client, router = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()
        monkeypatch.setattr(
            router, "_call_provider",
            lambda p, *a, **kw: LLMResponse(content="pong", provider=p.name, model=p.model, latency_ms=1.0),
        )

        body = client.post(
            "/api/health/llm/probe?tier=light",
            headers={"Authorization": "Bearer test-admin-token"},
        ).json()
        assert body["total"] == 2
        assert [r["provider"] for r in body["results"]] == ["zhipu_glm", "agnes"]

    def test_probe_tier_requires_admin_token(self, monkeypatch, probe_client):
        """鉴权行为不变：未带 token 仍 401。"""
        client, _router = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        assert client.post("/api/health/llm/probe?tier=deep").status_code == 401

    def test_probe_without_tier_still_probes_all(self, monkeypatch, probe_client):
        """兼容性：不传 tier 的 probe 仍是"全 Provider 探活"。"""
        client, router = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()
        monkeypatch.setattr(
            router, "_call_provider",
            lambda p, *a, **kw: LLMResponse(content="pong", provider=p.name, model=p.model, latency_ms=1.0),
        )

        body = client.post(
            "/api/health/llm/probe",
            headers={"Authorization": "Bearer test-admin-token"},
        ).json()
        assert body["total"] == 4
        assert "tier" not in body

    def test_probe_provider_param_still_wins_over_tier(self, monkeypatch, probe_client):
        client, router = probe_client
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        for key, value in _ROUTING_ENV.items():
            monkeypatch.setenv(key, value)
        router._load_from_env()
        monkeypatch.setattr(
            router, "_call_provider",
            lambda p, *a, **kw: LLMResponse(content="pong", provider=p.name, model=p.model, latency_ms=1.0),
        )

        body = client.post(
            "/api/health/llm/probe?provider=agnes&tier=deep",
            headers={"Authorization": "Bearer test-admin-token"},
        ).json()
        assert body["total"] == 1
        assert body["results"][0]["provider"] == "agnes"

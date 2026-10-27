"""免费 LLM Provider 接入测试(2026-10-27)。

覆盖:
- 新增插槽:讯飞星火 Lite / 百度千帆 / Cerebras 的加载、默认模型、优先级、限流
- 默认模型纠偏:智谱 glm-4.7-flash、硅基流动 Qwen/Qwen2-7B-Instruct、OpenRouter 免费模型
- 多 Key 解析(XXX_API_KEYS 优先于 XXX_API_KEY)
- probe():成功 / 失败(脱敏 + 熔断计数) / 未知 Provider / 未配置任何 Provider

铁律:全部离线,不发真实网络请求;不读取真实 .env 中的凭据。
"""
from __future__ import annotations

import pytest

from src.core.llm_router import LLMProvider, LLMResponse, LLMRouter, ProviderType

ALL_LLM_ENV = [
    "TTAPI_API_KEY", "TTAPI_BASE_URL", "TTAPI_MODEL",
    "AGNES_API_KEY", "AGNES_BASE_URL", "AGNES_MODEL",
    "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "ZHIPU_API_KEY", "ZHIPU_API_KEYS", "ZHIPU_MODEL", "ZHIPU_MODEL_FALLBACK", "ZHIPU_BASE_URL",
    "SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS", "SILICONFLOW_MODEL", "SILICONFLOW_BASE_URL",
    "SPARK_API_KEY", "SPARK_API_KEYS", "SPARK_MODEL", "SPARK_BASE_URL",
    "DASHSCOPE_API_KEY", "DASHSCOPE_API_KEYS", "DASHSCOPE_MODEL", "DASHSCOPE_BASE_URL",
    "QIANFAN_API_KEY", "QIANFAN_API_KEYS", "QIANFAN_MODEL", "QIANFAN_BASE_URL",
    "GROQ_API_KEY", "GROQ_API_KEYS", "GROQ_MODEL", "GROQ_BASE_URL",
    "CEREBRAS_API_KEY", "CEREBRAS_API_KEYS", "CEREBRAS_MODEL", "CEREBRAS_BASE_URL",
    "OPENROUTER_API_KEY", "OPENROUTER_API_KEYS", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
    "CF_ACCOUNT_ID", "CF_API_TOKEN", "CF_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for key in ALL_LLM_ENV:
        monkeypatch.delenv(key, raising=False)


def _build(monkeypatch, env: dict) -> LLMRouter:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return LLMRouter()


def _only(router: LLMRouter, name: str) -> LLMProvider:
    found = [p for p in router._providers if p.name == name]
    assert len(found) == 1, f"期望恰好 1 个 {name},实际 {[p.name for p in router._providers]}"
    return found[0]


class TestNewProviderLoading:
    def test_spark_lite_loading(self, monkeypatch):
        router = _build(monkeypatch, {"SPARK_API_KEY": "spark-test-key"})
        provider = _only(router, "spark_lite")
        assert provider.base_url == "https://spark-api-open.xf-yun.com/v1"
        assert provider.model == "lite"
        assert provider.priority == 88
        assert provider.free_tier is True
        assert provider.rpm == 30

    def test_qianfan_loading(self, monkeypatch):
        router = _build(monkeypatch, {"QIANFAN_API_KEY": "qianfan-test-key"})
        provider = _only(router, "qianfan_ernie")
        assert provider.base_url == "https://qianfan.baidubce.com/v2"
        assert provider.model == "ernie-speed-128k"
        assert provider.priority == 82
        assert provider.free_tier is True

    def test_cerebras_loading(self, monkeypatch):
        router = _build(monkeypatch, {"CEREBRAS_API_KEY": "cerebras-test-key"})
        provider = _only(router, "cerebras")
        assert provider.base_url == "https://api.cerebras.ai/v1"
        assert provider.model == "gpt-oss-120b"
        assert provider.priority == 78
        assert provider.rpm == 5  # 免费层 5 RPM
        assert provider.free_tier is True

    def test_new_providers_absent_without_keys(self, monkeypatch):
        router = _build(monkeypatch, {})
        assert router._providers == []


class TestCorrectedDefaults:
    def test_zhipu_default_is_free_flash(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "k"})
        assert _only(router, "zhipu_glm").model == "glm-4.7-flash"

    def test_siliconflow_default_is_free_model(self, monkeypatch):
        router = _build(monkeypatch, {"SILICONFLOW_API_KEY": "k"})
        assert _only(router, "siliconflow").model == "Qwen/Qwen2-7B-Instruct"

    def test_openrouter_default_is_free_model(self, monkeypatch):
        router = _build(monkeypatch, {"OPENROUTER_API_KEY": "k"})
        assert _only(router, "openrouter").model == "google/gemma-4-31b-it:free"

    def test_model_env_overrides_default(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "k", "ZHIPU_MODEL": "glm-4.5-flash"})
        assert _only(router, "zhipu_glm").model == "glm-4.5-flash"


class TestMultiKeyAndPriority:
    def test_multi_keys_take_precedence(self, monkeypatch):
        router = _build(monkeypatch, {
            "SPARK_API_KEY": "single-key",
            "SPARK_API_KEYS": "key-a, key-b ,key-c",
        })
        provider = _only(router, "spark_lite")
        assert provider.api_keys == ["key-a", "key-b", "key-c"]
        assert provider.api_key == "key-a"
        assert router.health_check()["spark_lite"]["has_multi_keys"] is True

    def test_free_providers_sorted_by_priority(self, monkeypatch):
        env = {
            "ZHIPU_API_KEY": "k", "SILICONFLOW_API_KEY": "k", "SPARK_API_KEY": "k",
            "DASHSCOPE_API_KEY": "k", "QIANFAN_API_KEY": "k", "GROQ_API_KEY": "k",
            "CEREBRAS_API_KEY": "k", "OPENROUTER_API_KEY": "k",
        }
        router = _build(monkeypatch, env)
        names = [p.name for p in router._providers]
        assert names == [
            "zhipu_glm", "zhipu_glm_45", "openrouter", "cerebras", "groq",
            "qianfan_ernie", "aliyun_qwen", "spark_lite", "siliconflow",
        ]
        assert [p.priority for p in router._providers] == sorted(p.priority for p in router._providers)

    def test_zhipu_glm_45_loading(self, monkeypatch):
        """GLM-4.5-Flash 与 4.7-Flash 共用 Key,作为同平台限流兜底。"""
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "zhipu-test-key"})
        provider = _only(router, "zhipu_glm_45")
        assert provider.model == "glm-4.5-flash"
        assert provider.priority == 1
        assert provider.free_tier is True
        assert provider.api_key == "zhipu-test-key"
        assert provider.rpm == 30

    def test_zhipu_fallback_model_env_override(self, monkeypatch):
        router = _build(monkeypatch, {
            "ZHIPU_API_KEY": "k", "ZHIPU_MODEL_FALLBACK": "glm-4.5-air",
        })
        assert _only(router, "zhipu_glm_45").model == "glm-4.5-air"

    def test_free_tier_visible_in_health_check(self, monkeypatch):
        router = _build(monkeypatch, {
            "ZHIPU_API_KEY": "k", "DEEPSEEK_API_KEY": "k",
        })
        health = router.health_check()
        assert health["zhipu_glm"]["free_tier"] is True
        assert health["deepseek"]["free_tier"] is False


class TestProbe:
    def _ok_response(self, provider: LLMProvider) -> LLMResponse:
        return LLMResponse(content="pong", provider=provider.name, model=provider.model, latency_ms=12.5)

    def test_probe_all_success(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "k", "GROQ_API_KEY": "k"})
        monkeypatch.setattr(router, "_call_provider", lambda p, *a, **kw: self._ok_response(p))
        result = router.probe()
        assert result["status"] == "ok"
        # 智谱会加载两个免费模型槽位(zhipu_glm + zhipu_glm_45),加 groq 共 3 个
        assert result["total"] == 3
        assert result["ok_count"] == 3
        assert [r["provider"] for r in result["results"]] == ["zhipu_glm", "zhipu_glm_45", "groq"]
        assert all(r["latency_ms"] == 12.5 for r in result["results"])
        assert {r["sample"] for r in result["results"]} == {"pong"}

    def test_probe_single_provider(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "k", "GROQ_API_KEY": "k"})
        called = []
        monkeypatch.setattr(router, "_call_provider", lambda p, *a, **kw: called.append(p.name) or self._ok_response(p))
        result = router.probe(name="groq")
        assert result["total"] == 1
        assert result["results"][0]["provider"] == "groq"
        assert called == ["groq"]

    def test_probe_unknown_provider(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "k"})
        result = router.probe(name="not_exist")
        assert result["status"] == "error"
        assert "not_exist" in result["message"]
        assert result["results"] == []

    def test_probe_without_any_provider(self, monkeypatch):
        router = _build(monkeypatch, {})
        assert router.probe()["status"] == "error"

    def test_probe_failure_sanitizes_key_and_counts(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "sk-secret-1234567890"})
        provider = _only(router, "zhipu_glm")

        def boom(p, *a, **kw):
            raise RuntimeError("401 Unauthorized for key sk-secret-1234567890")

        monkeypatch.setattr(router, "_call_provider", boom)
        result = router.probe(name="zhipu_glm")
        item = result["results"][0]
        assert item["ok"] is False
        assert "sk-secret-1234567890" not in item["error"]
        assert "***" in item["error"]
        assert provider._fail_count == 1
        assert result["ok_count"] == 0


class TestBaseUrlNormalization:
    """线上实测:ZHIPU_BASE_URL 默认值带尾斜杠时拼出 .../v4//chat/completions。"""

    def test_trailing_slash_stripped_on_init(self):
        provider = LLMProvider(
            name="t", provider_type=ProviderType.OPENAI,
            base_url="https://example.com/v1/", model="m",
        )
        assert provider.base_url == "https://example.com/v1"

    def test_zhipu_default_has_no_trailing_slash(self, monkeypatch):
        router = _build(monkeypatch, {"ZHIPU_API_KEY": "zhipu-test-key"})
        assert _only(router, "zhipu_glm").base_url == "https://open.bigmodel.cn/api/paas/v4"

    def test_openai_request_url_has_no_double_slash(self, monkeypatch):
        captured = {}

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

        def _fake_post(url, **kwargs):
            captured["url"] = url
            return _Resp()

        monkeypatch.setattr("src.core.llm_router.requests.post", _fake_post)
        provider = LLMProvider(
            name="t", provider_type=ProviderType.OPENAI,
            base_url="https://open.bigmodel.cn/api/paas/v4/",
            api_key="k", model="glm-4.7-flash",
        )
        router = _build(monkeypatch, {})
        router._call_openai_compatible(
            provider, [{"role": "user", "content": "hi"}], "glm-4.7-flash", 0.7, False, 10,
        )
        assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        assert "//" not in captured["url"].split("://", 1)[1]

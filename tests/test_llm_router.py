"""LLM路由器测试"""
import pytest
from unittest.mock import patch, MagicMock
from src.core.llm_router import LLMRouter, LLMProvider, ProviderType, LLMResponse


class TestLLMProvider:
    """LLMProvider 测试"""

    def test_provider_creation(self):
        provider = LLMProvider(
            name="test",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.test.com/v1",
            api_key="test-key",
            model="gpt-4o",
            priority=0,
        )
        assert provider.name == "test"
        assert provider.is_available
        assert provider.enabled

    def test_circuit_breaker(self):
        """测试熔断器机制"""
        provider = LLMProvider(
            name="test",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.test.com/v1",
            api_key="test-key",
        )
        assert provider.is_available

        # 连续3次失败触发熔断
        provider.record_failure()
        provider.record_failure()
        assert provider.is_available  # 还未熔断

        provider.record_failure()
        assert not provider.is_available  # 熔断

    def test_circuit_breaker_recovery(self):
        """测试熔断器恢复"""
        import time
        provider = LLMProvider(
            name="test",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.test.com/v1",
            api_key="test-key",
        )
        provider.record_failure()
        provider.record_failure()
        provider.record_failure()
        assert not provider.is_available

        # 模拟30秒后恢复
        provider._last_fail_time = time.monotonic() - 31
        assert provider.is_available  # 半开状态

    def test_record_success_resets_failures(self):
        """测试成功调用重置失败计数"""
        provider = LLMProvider(
            name="test",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.test.com/v1",
            api_key="test-key",
        )
        provider.record_failure()
        provider.record_failure()
        provider.record_success()
        assert provider._fail_count == 0
        assert not provider._circuit_open

    def test_disabled_provider(self):
        """测试禁用的Provider"""
        provider = LLMProvider(
            name="test",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.test.com/v1",
            api_key="test-key",
            enabled=False,
        )
        assert not provider.is_available


class TestLLMRouter:
    """LLMRouter 测试"""

    def test_router_creation_no_env(self):
        """测试无环境变量时创建路由器"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
            assert len(router._providers) == 0
            assert router.available_providers == []

    def test_router_with_ttapi(self):
        """测试TTAPI Provider加载"""
        with patch.dict("os.environ", {"TTAPI_API_KEY": "test-key"}, clear=False):
            router = LLMRouter()
            ttapi_providers = [p for p in router._providers if p.name == "ttapi"]
            assert len(ttapi_providers) == 1
            assert ttapi_providers[0].provider_type == ProviderType.OPENAI

    def test_router_priority_sorting(self):
        """测试Provider按优先级排序"""
        with patch.dict("os.environ", {
            "TTAPI_API_KEY": "key1",
            "DEEPSEEK_API_KEY": "key2",
        }, clear=False):
            router = LLMRouter()
            if len(router._providers) >= 2:
                assert router._providers[0].priority <= router._providers[1].priority

    def test_chat_all_providers_fail(self):
        """测试所有Provider失败"""
        router = LLMRouter()
        # 添加一个会失败的Provider
        provider = LLMProvider(
            name="fail_provider",
            provider_type=ProviderType.OPENAI,
            base_url="https://nonexistent.invalid/v1",
            api_key="invalid-key",
            model="test",
        )
        router.add_provider(provider)

        with pytest.raises(RuntimeError, match="所有LLM Provider均不可用"):
            router.chat([{"role": "user", "content": "test"}])

    def test_chat_with_mock_success(self):
        """测试成功调用（mock）"""
        # 创建空Router（不加载环境变量中的Provider）
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider = LLMProvider(
            name="mock_provider",
            provider_type=ProviderType.OPENAI,
            base_url="https://mock.test.com/v1",
            api_key="mock-key",
            model="gpt-4o",
        )
        router.add_provider(provider)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "测试回复"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            result = router.chat([{"role": "user", "content": "test"}])
            assert result.content == "测试回复"
            assert result.provider == "mock_provider"

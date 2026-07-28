"""LLM路由器测试"""
import pytest
import requests
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
            "AGNES_API_KEY": "key2",
        }, clear=False):
            router = LLMRouter()
            if len(router._providers) >= 2:
                assert router._providers[0].priority <= router._providers[1].priority

    def test_chat_all_providers_fail(self):
        """测试所有Provider失败"""
        # 隔离 env, 避免加载真实 Provider(如 agnes)导致调用成功
        with patch.dict("os.environ", {}, clear=True):
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


class TestTokenBucket:
    """令牌桶限流器测试"""

    def test_unlimited_bucket(self):
        """rpm=0 表示不限流,应立即可用"""
        from src.core.llm_router import TokenBucket
        bucket = TokenBucket(rpm=0)
        assert bucket.acquire(timeout=0.1) is True
        # 不限流时多次获取都应成功
        for _ in range(100):
            assert bucket.acquire(timeout=0.1) is True

    def test_acquire_single_token(self):
        """rpm>0 时首次获取令牌应成功"""
        from src.core.llm_router import TokenBucket
        bucket = TokenBucket(rpm=60)  # 1 令牌/秒
        assert bucket.acquire(timeout=1.0) is True

    def test_acquire_timeout_when_exhausted(self):
        """令牌耗尽时获取应超时返回 False"""
        from src.core.llm_router import TokenBucket
        # rpm=1 表示每分钟1个令牌,初始化时只有1个令牌
        bucket = TokenBucket(rpm=1)
        assert bucket.acquire(timeout=0.1) is True  # 消耗唯一令牌
        # 再获取应超时(令牌补充需60秒,timeout=0.1不够)
        assert bucket.acquire(timeout=0.1) is False

    def test_bucket_refill(self):
        """令牌桶应按时间补充令牌"""
        import time as _time
        from src.core.llm_router import TokenBucket
        # rpm=600: 初始600令牌,每秒补充10个
        bucket = TokenBucket(rpm=600)
        # 消耗初始令牌(留少量避免边界问题)
        for _ in range(590):
            bucket.acquire(timeout=0.01)
        # 等待补充(0.3s 应补充约3个令牌)
        _time.sleep(0.3)
        # 补充后应能成功获取
        assert bucket.acquire(timeout=1.0) is True


class TestMultiKeyRotation:
    """多 Key 轮换测试"""

    def test_current_api_key_rotation(self):
        """多 Key 应轮换使用"""
        provider = LLMProvider(
            name="test_multi",
            provider_type=ProviderType.OPENAI,
            base_url="https://test.com/v1",
            api_key="single-key",
            api_keys=["key1", "key2", "key3"],
        )
        # 第一次调用应使用 key1
        assert provider.current_api_key == "key1"
        # 第二次应使用 key2
        assert provider.current_api_key == "key2"
        # 第三次应使用 key3
        assert provider.current_api_key == "key3"
        # 第四次应回到 key1
        assert provider.current_api_key == "key1"

    def test_fallback_to_single_key(self):
        """无 api_keys 时应回退到 api_key"""
        provider = LLMProvider(
            name="test_single",
            provider_type=ProviderType.OPENAI,
            base_url="https://test.com/v1",
            api_key="only-key",
        )
        assert provider.current_api_key == "only-key"
        # 多次调用应始终返回同一 key
        assert provider.current_api_key == "only-key"

    def test_parse_api_keys_multi(self):
        """测试多 Key 解析(逗号分隔)"""
        with patch.dict("os.environ", {
            "TEST_PROVIDER_API_KEYS": "k1, k2, k3",
            "TEST_PROVIDER_API_KEY": "fallback",
        }, clear=False):
            router = LLMRouter()
            keys = router._parse_api_keys("TEST_PROVIDER_API_KEY", "TEST_PROVIDER_API_KEYS")
            assert keys == ["k1", "k2", "k3"]

    def test_parse_api_keys_single_fallback(self):
        """无多 Key 时应回退到单 Key"""
        with patch.dict("os.environ", {
            "TEST_PROVIDER_API_KEY": "single-key",
            "TEST_PROVIDER_API_KEYS": "",
        }, clear=False):
            router = LLMRouter()
            keys = router._parse_api_keys("TEST_PROVIDER_API_KEY", "TEST_PROVIDER_API_KEYS")
            assert keys == ["single-key"]

    def test_parse_api_keys_empty(self):
        """无任何 Key 时应返回空列表"""
        with patch.dict("os.environ", {
            "TEST_PROVIDER_API_KEY": "",
            "TEST_PROVIDER_API_KEYS": "",
        }, clear=False):
            router = LLMRouter()
            keys = router._parse_api_keys("TEST_PROVIDER_API_KEY", "TEST_PROVIDER_API_KEYS")
            assert keys == []


class TestFreeProviderLoading:
    """8个免费 Provider 加载测试"""

    def _clear_llm_env(self):
        """清除所有 LLM 相关环境变量"""
        import os
        llm_envs = [
            "TTAPI_API_KEY", "TTAPI_BASE_URL", "TTAPI_MODEL",
            "AGNES_API_KEY", "AGNES_BASE_URL", "AGNES_MODEL",
            "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL",
            "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
            "OLLAMA_BASE_URL", "OLLAMA_MODEL",
            "ZHIPU_API_KEY", "ZHIPU_API_KEYS", "ZHIPU_MODEL", "ZHIPU_BASE_URL",
            "SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS", "SILICONFLOW_MODEL", "SILICONFLOW_BASE_URL",
            "DASHSCOPE_API_KEY", "DASHSCOPE_API_KEYS", "DASHSCOPE_MODEL", "DASHSCOPE_BASE_URL",
            "GROQ_API_KEY", "GROQ_API_KEYS", "GROQ_MODEL", "GROQ_BASE_URL",
            "OPENROUTER_API_KEY", "OPENROUTER_API_KEYS", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL",
            "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
            "CF_ACCOUNT_ID", "CF_API_TOKEN", "CF_MODEL",
        ]
        for k in llm_envs:
            os.environ.pop(k, None)

    def test_zhipu_glm_loading(self):
        """智谱 GLM 加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"ZHIPU_API_KEY": "zhipu-test-key"}, clear=False):
            router = LLMRouter()
            zhipu = [p for p in router._providers if p.name == "zhipu_glm"]
            assert len(zhipu) == 1
            assert zhipu[0].model == "glm-4-flash"
            assert zhipu[0].rpm == 30
            assert zhipu[0].priority == 100
            assert "zhipu_glm" in router._token_buckets  # 已创建令牌桶

    def test_siliconflow_loading(self):
        """硅基流动加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"SILICONFLOW_API_KEY": "sf-test-key"}, clear=False):
            router = LLMRouter()
            sf = [p for p in router._providers if p.name == "siliconflow"]
            assert len(sf) == 1
            assert sf[0].model == "Qwen/Qwen2.5-7B-Instruct"
            assert sf[0].rpm == 1000
            assert sf[0].priority == 95

    def test_dashscope_loading(self):
        """阿里云百炼加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "ds-test-key"}, clear=False):
            router = LLMRouter()
            ds = [p for p in router._providers if p.name == "aliyun_qwen"]
            assert len(ds) == 1
            assert ds[0].model == "qwen-turbo"
            assert ds[0].priority == 85

    def test_groq_loading(self):
        """Groq 加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key"}, clear=False):
            router = LLMRouter()
            groq = [p for p in router._providers if p.name == "groq"]
            assert len(groq) == 1
            assert groq[0].rpm == 30
            assert groq[0].priority == 80

    def test_openrouter_loading(self):
        """OpenRouter 加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "or-test-key"}, clear=False):
            router = LLMRouter()
            or_p = [p for p in router._providers if p.name == "openrouter"]
            assert len(or_p) == 1
            assert or_p[0].rpm == 20
            assert or_p[0].priority == 75

    def test_deepseek_loading(self):
        """DeepSeek 加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "ds-test-key"}, clear=False):
            router = LLMRouter()
            ds = [p for p in router._providers if p.name == "deepseek"]
            assert len(ds) == 1
            assert ds[0].priority == 70
            assert ds[0].rpm == 0  # 未设置限流

    def test_cloudflare_loading(self):
        """Cloudflare Workers AI 加载测试(需要 account_id + token)"""
        self._clear_llm_env()
        with patch.dict("os.environ", {
            "CF_ACCOUNT_ID": "test-account",
            "CF_API_TOKEN": "test-token",
        }, clear=False):
            router = LLMRouter()
            cf = [p for p in router._providers if p.name == "cloudflare_ai"]
            assert len(cf) == 1
            assert cf[0].priority == 65
            assert "test-account" in cf[0].base_url

    def test_cloudflare_requires_both_envs(self):
        """Cloudflare 需同时配置 account_id 和 token"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"CF_ACCOUNT_ID": "test-account"}, clear=False):
            router = LLMRouter()
            cf = [p for p in router._providers if p.name == "cloudflare_ai"]
            assert len(cf) == 0  # 缺少 token,不应加载

    def test_multi_keys_loading(self):
        """多 Key 配置应正确加载到 api_keys 列表"""
        self._clear_llm_env()
        with patch.dict("os.environ", {
            "ZHIPU_API_KEYS": "key1,key2,key3",
        }, clear=False):
            router = LLMRouter()
            zhipu = [p for p in router._providers if p.name == "zhipu_glm"][0]
            assert len(zhipu.api_keys) == 3
            assert zhipu.api_keys == ["key1", "key2", "key3"]

    def test_priority_ordering(self):
        """所有免费 Provider 应按 priority 升序排列"""
        self._clear_llm_env()
        with patch.dict("os.environ", {
            "ZHIPU_API_KEY": "k1",
            "SILICONFLOW_API_KEY": "k2",
            "DASHSCOPE_API_KEY": "k3",
            "GROQ_API_KEY": "k4",
            "OPENROUTER_API_KEY": "k5",
            "DEEPSEEK_API_KEY": "k6",
            "CF_ACCOUNT_ID": "acc",
            "CF_API_TOKEN": "tok",
        }, clear=False):
            router = LLMRouter()
            priorities = [p.priority for p in router._providers]
            assert priorities == sorted(priorities)
            # priority: cloudflare=65 < deepseek=70 < openrouter=75 < groq=80
            #          < aliyun=85 < siliconflow=95 < zhipu=100
            assert router._providers[0].name == "cloudflare_ai"
            assert router._providers[0].priority == 65
            assert router._providers[-1].name == "zhipu_glm"
            assert router._providers[-1].priority == 100


class TestChatWithRateLimit:
    """chat 方法的限流集成测试"""

    def test_chat_skips_rate_limited_provider(self):
        """令牌桶耗尽时应跳过该 Provider,尝试下一个"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()

        # Provider A: rpm=1, 限流后会跳过
        provider_a = LLMProvider(
            name="limited_provider",
            provider_type=ProviderType.OPENAI,
            base_url="https://a.test.com/v1",
            api_key="key-a",
            model="m-a",
            rpm=1,  # 仅1个令牌
            priority=0,
        )
        # Provider B: 不限流,作为兜底
        provider_b = LLMProvider(
            name="unlimited_provider",
            provider_type=ProviderType.OPENAI,
            base_url="https://b.test.com/v1",
            api_key="key-b",
            model="m-b",
            priority=1,
        )
        router.add_provider(provider_a)
        router.add_provider(provider_b)

        # 用 Mock 替换 A 的令牌桶,使其 acquire 永远返回 False(模拟限流超时)
        router._token_buckets["limited_provider"] = MagicMock()
        router._token_buckets["limited_provider"].acquire.return_value = False
        # B 无令牌桶(不限流),不会被替换

        # mock requests.post,只在调用 B 的 URL 时返回成功
        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if "b.test.com" in url:
                resp.json.return_value = {
                    "choices": [{"message": {"content": "from-b"}}],
                    "usage": {},
                }
            else:
                # A 的 URL 不应被调用
                resp.json.return_value = {"choices": [{"message": {"content": "from-a"}}]}
            return resp

        with patch("requests.post", side_effect=mock_post):
            result = router.chat([{"role": "user", "content": "test"}])
            assert result.provider == "unlimited_provider"
            # A 的令牌桶 acquire 被调用过
            router._token_buckets["limited_provider"].acquire.assert_called_once()

    def test_chat_uses_multi_key_rotation(self):
        """chat 调用应使用多 Key 轮换"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider = LLMProvider(
            name="multi_key_provider",
            provider_type=ProviderType.OPENAI,
            base_url="https://multi.test.com/v1",
            api_keys=["k1", "k2", "k3"],
            model="m",
        )
        router.add_provider(provider)

        captured_auths = []

        def mock_post(url, headers=None, **kwargs):
            captured_auths.append(headers.get("Authorization", ""))
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {},
            }
            return resp

        with patch("requests.post", side_effect=mock_post):
            router.chat([{"role": "user", "content": "m1"}])
            router.chat([{"role": "user", "content": "m2"}])
            router.chat([{"role": "user", "content": "m3"}])

        # 应分别使用 k1, k2, k3
        assert captured_auths == ["Bearer k1", "Bearer k2", "Bearer k3"]

    def test_health_check_returns_all_fields(self):
        """健康检查应返回完整字段"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        router.add_provider(LLMProvider(
            name="test_p",
            provider_type=ProviderType.OPENAI,
            base_url="https://test.com/v1",
            api_key="k",
            model="m",
            rpm=30,
            api_keys=["k1", "k2"],
            priority=50,
        ))
        health = router.health_check()
        assert "test_p" in health
        info = health["test_p"]
        assert info["available"] is True
        assert info["model"] == "m"
        assert info["priority"] == 50
        assert info["rpm"] == 30
        assert info["has_multi_keys"] is True
        assert info["provider_type"] == "openai"
        assert info["circuit_open"] is False

    def test_chat_with_retries_on_failure(self):
        """chat 应按指数退避重试"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider = LLMProvider(
            name="retry_provider",
            provider_type=ProviderType.OPENAI,
            base_url="https://retry.test.com/v1",
            api_key="k",
            model="m",
            max_retries=2,
            retry_delay=0.01,  # 缩短延迟便于测试
        )
        router.add_provider(provider)

        call_count = {"n": 0}

        def mock_post(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise requests.exceptions.ConnectionError("fail")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {},
            }
            return resp

        with patch("requests.post", side_effect=mock_post):
            result = router.chat([{"role": "user", "content": "test"}])
            assert result.content == "ok"
            assert call_count["n"] == 3  # 前两次失败,第三次成功

    def test_chat_circuit_opens_after_3_failures(self):
        """连续3次失败应触发熔断"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider = LLMProvider(
            name="will_break",
            provider_type=ProviderType.OPENAI,
            base_url="https://break.test.com/v1",
            api_key="k",
            model="m",
            max_retries=0,  # 不重试,直接失败
            retry_delay=0.01,
        )
        router.add_provider(provider)

        with patch("requests.post", side_effect=requests.exceptions.ConnectionError("fail")):
            # 第一次调用:1次失败即触发(max_retries=0, max_attempts=1)
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "1"}])
            assert provider._fail_count == 1
            assert not provider._circuit_open

            # 第二次
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "2"}])
            assert provider._fail_count == 2
            assert not provider._circuit_open

            # 第三次:应触发熔断
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "3"}])
            assert provider._fail_count == 3
            assert provider._circuit_open
            assert not provider.is_available  # 熔断后不可用

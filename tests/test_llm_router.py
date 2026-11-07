"""LLM路由器测试"""
import threading
from collections import Counter

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

    def test_chat_error_masks_api_key(self):
        """异常信息回显 API Key → 抛出的 RuntimeError 文案必须脱敏。"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        key = "sk-super-secret-1234567890"
        provider = LLMProvider(
            name="leaky",
            provider_type=ProviderType.OPENAI,
            base_url="https://mock.test.com/v1",
            api_key=key,
            model="gpt-4o",
            max_retries=0,
            retry_delay=0.0,
        )
        router.add_provider(provider)

        with patch.object(router, "_call_provider", side_effect=RuntimeError(f"401 key={key} rejected")):
            with pytest.raises(RuntimeError) as excinfo:
                router.chat([{"role": "user", "content": "test"}])
        assert key not in str(excinfo.value)
        assert "***" in str(excinfo.value)

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

    def test_current_api_key_thread_safe(self):
        """并发轮换不丢更新:总调用次数守恒且各 Key 命中次数均等"""
        provider = LLMProvider(
            name="test_concurrent",
            provider_type=ProviderType.OPENAI,
            base_url="https://test.com/v1",
            api_key="single-key",
            api_keys=["key1", "key2", "key3", "key4"],
        )
        n_threads, per_thread = 8, 200
        results = []
        results_lock = threading.Lock()
        start = threading.Barrier(n_threads)

        def worker():
            start.wait()
            local = [provider.current_api_key for _ in range(per_thread)]
            with results_lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = n_threads * per_thread
        assert len(results) == total
        # 无锁时 _key_index += 1 会丢失更新,索引总量小于调用次数
        assert provider._key_index == total
        counts = Counter(results)
        assert set(counts) == {"key1", "key2", "key3", "key4"}
        assert all(c == total // 4 for c in counts.values())

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
            "ZHIPU_API_KEY", "ZHIPU_API_KEYS", "ZHIPU_MODEL", "ZHIPU_MODEL_FALLBACK", "ZHIPU_BASE_URL",
            "SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS", "SILICONFLOW_MODEL", "SILICONFLOW_BASE_URL",
            "DASHSCOPE_API_KEY", "DASHSCOPE_API_KEYS", "DASHSCOPE_MODEL", "DASHSCOPE_BASE_URL",
            "GROQ_API_KEY", "GROQ_API_KEYS", "GROQ_MODEL", "GROQ_BASE_URL",
            "OPENROUTER_API_KEY", "OPENROUTER_API_KEYS", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL",
            "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
            "CF_ACCOUNT_ID", "CF_API_TOKEN", "CF_MODEL",
            "LY77625_API_KEY", "LY77625_BASE_URL", "LY77625_MODEL", "LY77625_TIMEOUT",
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
            assert zhipu[0].model == "glm-4.7-flash"
            assert zhipu[0].rpm == 30
            assert zhipu[0].priority == -1  # 用户指定:智谱免费模型优先
            assert "zhipu_glm" in router._token_buckets  # 已创建令牌桶

    def test_siliconflow_loading(self):
        """硅基流动加载测试"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"SILICONFLOW_API_KEY": "sf-test-key"}, clear=False):
            router = LLMRouter()
            sf = [p for p in router._providers if p.name == "siliconflow"]
            assert len(sf) == 1
            assert sf[0].model == "Qwen/Qwen2-7B-Instruct"
            assert sf[0].rpm == 1000
            assert sf[0].priority == 95

    def test_ly77625_loading_defaults(self):
        """ly.77625.xyz 加载测试:默认 base_url/model/超时/优先级"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"LY77625_API_KEY": "ly-test-key"}, clear=False):
            router = LLMRouter()
            ly = [p for p in router._providers if p.name == "ly77625"]
            assert len(ly) == 1
            assert ly[0].base_url == "https://ly.77625.xyz/v1"
            assert ly[0].model == "gemini-3.8-flash"
            assert ly[0].priority == 95  # 最后一档兜底
            assert ly[0].free_tier is True
            assert ly[0].timeout == 30
            assert ly[0].provider_type == ProviderType.OPENAI

    def test_ly77625_not_loaded_without_key(self):
        """未配置 Key 时不加载,其它 Provider 不受影响"""
        self._clear_llm_env()
        router = LLMRouter()
        assert not [p for p in router._providers if p.name == "ly77625"]

    def test_ly77625_env_overrides(self):
        """base_url/model/超时可通过环境变量覆盖"""
        self._clear_llm_env()
        env = {
            "LY77625_API_KEY": "ly-test-key",
            "LY77625_BASE_URL": "https://ly.example.test/v1",
            "LY77625_MODEL": "gemini-3.8-flash",
            "LY77625_TIMEOUT": "12",
        }
        with patch.dict("os.environ", env, clear=False):
            router = LLMRouter()
            ly = [p for p in router._providers if p.name == "ly77625"][0]
            assert ly.base_url == "https://ly.example.test/v1"
            assert ly.timeout == 12

    def test_ly77625_health_check_reports_available(self):
        """/api/health/llm 依赖的 health_check 能反映新 Provider"""
        self._clear_llm_env()
        with patch.dict("os.environ", {"LY77625_API_KEY": "ly-test-key"}, clear=False):
            router = LLMRouter()
            info = router.health_check().get("ly77625")
            assert info is not None
            assert info["available"] is True
            assert info["model"] == "gemini-3.8-flash"
            assert info["free_tier"] is True
            assert info["priority"] == 95

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
            # priority: zhipu=-1(用户指定优先) < zhipu_45=1(同平台免费兜底)
            #          < cloudflare=65 < deepseek=70 < openrouter=75 < groq=80
            #          < aliyun=85 < siliconflow=95
            assert router._providers[0].name == "zhipu_glm"
            assert router._providers[0].priority == -1
            assert router._providers[1].name == "zhipu_glm_45"
            assert router._providers[1].priority == 1
            assert router._providers[2].name == "cloudflare_ai"
            assert router._providers[2].priority == 65
            assert router._providers[-1].name == "siliconflow"
            assert router._providers[-1].priority == 95


class TestLy77625Provider:
    """ly.77625.xyz Provider 的调用行为(全部 mock 网络,不依赖外网)"""

    def test_chat_routes_to_openai_compatible_endpoint(self):
        with patch.dict("os.environ",
                        {"LY77625_API_KEY": "ly-test-key",
                         "LY77625_BASE_URL": "https://ly.77625.xyz/v1"},
                        clear=True):
            router = LLMRouter()
        assert [p.name for p in router._providers] == ["ly77625"]

        captured = {}

        def mock_post(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["json"] = kwargs.get("json") or {}
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok-from-ly"}}],
                "usage": {"total_tokens": 12},
            }
            return resp

        with patch("requests.post", side_effect=mock_post):
            result = router.chat([{"role": "user", "content": "ping"}])

        assert result.content == "ok-from-ly"
        assert captured["url"] == "https://ly.77625.xyz/v1/chat/completions"
        assert captured["headers"].get("Authorization", "").startswith("Bearer ")
        assert captured["json"]["model"] == "gemini-3.8-flash"

    def test_failure_is_reported_not_faked(self):
        """上游失败时 chat 抛错(由上层转 503),不返回伪造成功"""
        with patch.dict("os.environ",
                        {"LY77625_API_KEY": "ly-test-key"}, clear=True):
            router = LLMRouter()

        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.side_effect = requests.exceptions.HTTPError("502")
            return resp

        with patch("requests.post", side_effect=mock_post):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "ping"}])


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

    def test_chat_429_skips_retry_and_switches_provider(self):
        """429 限流应立即切换下一 Provider,而不是原地重试 3 次。

        线上实测(2026-10-27):智谱 GLM-4.7-Flash 免费档高峰期返回 429(code 1305
        平台过载),1s/2s 原地重试全部失败,用户请求被拖到 37.5s 才降级。
        """
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()

        provider_a = LLMProvider(
            name="throttled",
            provider_type=ProviderType.OPENAI,
            base_url="https://a.test.com/v1",
            api_key="k-a",
            model="m-a",
            max_retries=2,
            retry_delay=0.01,
            priority=0,
        )
        provider_b = LLMProvider(
            name="healthy",
            provider_type=ProviderType.OPENAI,
            base_url="https://b.test.com/v1",
            api_key="k-b",
            model="m-b",
            priority=1,
        )
        router.add_provider(provider_a)
        router.add_provider(provider_b)

        calls = {"a": 0, "b": 0}

        def mock_post(url, **kwargs):
            if "a.test.com" in url:
                calls["a"] += 1
                resp = MagicMock()
                resp.status_code = 429
                raise requests.exceptions.HTTPError(
                    "429 Client Error: Too Many Requests", response=resp
                )
            calls["b"] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "from-b"}}],
                "usage": {},
            }
            return resp

        with patch("requests.post", side_effect=mock_post):
            result = router.chat([{"role": "user", "content": "429-fast-fail"}])

        assert result.provider == "healthy"
        assert calls["a"] == 1, "429 不应原地重试"
        assert provider_a._fail_count == 1
        assert provider_b._fail_count == 0

    def test_chat_timeout_skips_retry_and_switches_provider(self):
        """读取超时应直接切下一 Provider,而不是把超时预算再花一遍。

        线上实测(2026-10-27):智谱 4.7-Flash 高峰期首个请求读超时 30s,
        随后原地重试才拿到 429,用户共等 31s 才降级。
        """
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider_a = LLMProvider(
            name="slow",
            provider_type=ProviderType.OPENAI,
            base_url="https://slow.test.com/v1",
            api_key="k-a",
            model="m-a",
            max_retries=2,
            retry_delay=0.01,
            priority=0,
        )
        provider_b = LLMProvider(
            name="healthy",
            provider_type=ProviderType.OPENAI,
            base_url="https://b.test.com/v1",
            api_key="k-b",
            model="m-b",
            priority=1,
        )
        router.add_provider(provider_a)
        router.add_provider(provider_b)

        calls = {"a": 0, "b": 0}

        def mock_post(url, **kwargs):
            if "slow.test.com" in url:
                calls["a"] += 1
                raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")
            calls["b"] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "from-b"}}],
                "usage": {},
            }
            return resp

        with patch("requests.post", side_effect=mock_post):
            result = router.chat([{"role": "user", "content": "timeout-fast-switch"}])

        assert result.provider == "healthy"
        assert calls["a"] == 1, "超时不应原地重试"
        assert provider_a._fail_count == 1
        assert provider_b._fail_count == 0

    def test_chat_non_429_still_retries(self):
        """非 429 的瞬时错误(如 ConnectionError)仍按指数退避重试。"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider = LLMProvider(
            name="flaky",
            provider_type=ProviderType.OPENAI,
            base_url="https://flaky.test.com/v1",
            api_key="k",
            model="m",
            max_retries=2,
            retry_delay=0.01,
        )
        router.add_provider(provider)

        calls = {"n": 0}

        def mock_post(url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.ConnectionError("fail")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {},
            }
            return resp

        with patch("requests.post", side_effect=mock_post):
            result = router.chat([{"role": "user", "content": "non-429-retry"}])

        assert result.content == "ok"
        assert calls["n"] == 3

    def test_chat_429_opens_circuit_after_3_requests(self):
        """连续 3 次请求都被 429 时仍应熔断,避免继续打满上游。"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        provider = LLMProvider(
            name="always_429",
            provider_type=ProviderType.OPENAI,
            base_url="https://rl.test.com/v1",
            api_key="k",
            model="m",
            max_retries=2,
            retry_delay=0.01,
        )
        router.add_provider(provider)

        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 429
            raise requests.exceptions.HTTPError("429 Client Error", response=resp)

        with patch("requests.post", side_effect=mock_post):
            for i in range(3):
                with pytest.raises(RuntimeError):
                    router.chat([{"role": "user", "content": f"429-circuit-{i}"}])
                assert provider._fail_count == i + 1
        assert provider._circuit_open is True
        assert provider.is_available is False

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
        assert info["base_url"] == "https://test.com/v1"
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


# ===== T02：chat() 分层执行引擎（2026-11-07 增量，只追加，不改既有断言）=====

_ONLINE_TIER_ENV = {
    "ZHIPU_API_KEY": "zk",
    "AGNES_API_KEY": "ak",
    "LY77625_API_KEY": "lk",
}


def _tier_router() -> LLMRouter:
    """线上等价 4 Provider 环境：zhipu_glm(-1) / zhipu_glm_45(1) / agnes(90) / ly77625(95)。"""
    from unittest.mock import patch as _patch
    with _patch.dict("os.environ", _ONLINE_TIER_ENV, clear=True):
        return LLMRouter()


def _http_429() -> requests.exceptions.HTTPError:
    resp = MagicMock()
    resp.status_code = 429
    return requests.exceptions.HTTPError("429 Too Many Requests", response=resp)


def _ok(provider_name: str, model: str | None = None) -> LLMResponse:
    return LLMResponse(
        content=f"ok-{provider_name}", provider=provider_name, model=model or "m", latency_ms=1.0
    )


@pytest.fixture(autouse=True)
def _reset_routing_recorder():
    """每个用例前清空路由记录器，保证 last_routing 断言确定性。"""
    from src.core.llm_router import _recorder
    from src.core.llm_task_tier import reset_warn_cache
    _recorder.reset()
    reset_warn_cache()
    yield
    _recorder.reset()
    reset_warn_cache()


class TestChatTaskTier:
    """chat(task_tier=...) 的分层执行：降级链 / 模型覆盖 / 预算 / 缓存 / 可观测。"""

    def test_deep_429_falls_back_to_second_hop(self):
        """AC6/AC9：deep 首跳 429 → 直接切第 2 跳成功，fallback_count == 1。"""
        router = _tier_router()

        def fake_call(provider, messages, **kwargs):
            if provider.name == "zhipu_glm":
                raise _http_429()
            return _ok(provider.name, kwargs.get("model") or provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat([{"role": "user", "content": "deep-429"}], task_tier="deep")

        assert result.provider == "agnes"
        assert result.content == "ok-agnes"
        from src.core.llm_router import _recorder
        last = _recorder.last()
        assert last["tier"] == "deep"
        assert last["provider"] == "agnes"
        assert last["fallback_count"] == 1
        assert last["cached"] is False
        assert last["budget_seconds"] == 60.0

    def test_deep_chain_skips_zhipu_glm_45(self):
        """P0-6：deep 链不含 zhipu_glm_45（同源），即使它在 _providers 里。"""
        router = _tier_router()
        visited: list[str] = []

        def fake_call(provider, messages, **kwargs):
            visited.append(provider.name)
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "deep-excl"}], task_tier="deep")

        assert visited == ["zhipu_glm", "agnes", "ly77625"]

    def test_standard_chain_skips_disabled_zhipu_glm_45(self):
        """D10：glm_45 已 enabled=False → standard 的**有效跳**只有 3 个（同源位被跳过）。

        注意区分两层：
        - `_providers_for_tier("standard")` 仍返回 4 个（视图不减员，C-1/D13 要求
          `== list(self._providers)`）；
        - `chat()` 的**有效跳**靠 `is_available` 跳过 disabled → 3 个。
        """
        router = _tier_router()
        visited: list[str] = []

        def fake_call(provider, messages, **kwargs):
            visited.append(provider.name)
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "std-excl"}])

        assert visited == ["zhipu_glm", "agnes", "ly77625"]
        # 视图层仍是 4 个（含 disabled）
        assert [p.name for p in router._providers_for_tier("standard")] == [
            "zhipu_glm", "zhipu_glm_45", "agnes", "ly77625"
        ]

    def test_light_chain_has_two_hops(self):
        router = _tier_router()
        visited: list[str] = []

        def fake_call(provider, messages, **kwargs):
            visited.append(provider.name)
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "light-excl"}], task_tier="light")

        assert visited == ["zhipu_glm", "agnes"]

    def test_model_override_is_per_hop_not_broadcast(self):
        """矛盾 2：agnes 条目拿到 agnes-3.0-flash，前两跳拿不到该 model。"""
        router = _tier_router()
        captured: list[tuple[str, str | None]] = []

        def fake_call(provider, messages, **kwargs):
            captured.append((provider.name, kwargs.get("model")))
            if provider.name != "agnes":
                raise _http_429()
            return _ok(provider.name, kwargs.get("model") or provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat([{"role": "user", "content": "model-per-hop"}], task_tier="deep")

        # D9 顺序：zhipu_glm(第1跳) → agnes(第2跳, 带 model 覆盖) → ly77625(末位兜底, 不下发 model)
        assert captured == [
            ("zhipu_glm", None),
            ("agnes", "agnes-3.0-flash"),
        ]
        assert result.model == "agnes-3.0-flash"
        assert result.provider == "agnes"

    def test_explicit_model_overrides_every_hop(self):
        """显式 model= 入参仍保持"最强覆盖"语义。"""
        router = _tier_router()
        captured: list[str | None] = []

        def fake_call(provider, messages, **kwargs):
            captured.append(kwargs.get("model"))
            return _ok(provider.name, kwargs.get("model") or provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat(
                [{"role": "user", "content": "explicit-model"}],
                model="explicit-x",
                task_tier="deep",
            )

        assert captured == ["explicit-x"]
        assert result.model == "explicit-x"

    def test_route_model_none_uses_provider_model(self):
        """route.model=None → 沿用 Provider 自身 model（保留 ZHIPU_MODEL 等 env 覆盖）。"""
        from unittest.mock import patch as _patch
        with _patch.dict(
            "os.environ",
            {**_ONLINE_TIER_ENV, "ZHIPU_MODEL": "glm-custom"},
            clear=True,
        ):
            router = LLMRouter()
        captured: list[tuple[str, str | None]] = []

        def fake_call(provider, messages, **kwargs):
            captured.append((provider.name, kwargs.get("model")))
            return _ok(provider.name)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            router.chat([{"role": "user", "content": "zm"}], task_tier="deep")

        # zhipu_glm 的 Provider.model 已是 env 覆盖值，chat 不再重复下发 model
        zhipu = next(p for p in router._providers if p.name == "zhipu_glm")
        assert zhipu.model == "glm-custom"
        assert captured[0] == ("zhipu_glm", None)

    def test_cross_tier_cache_isolation(self):
        """AC5：同 prompt 先 light 后 deep，第二次 cached == False。"""
        router = _tier_router()
        messages = [{"role": "user", "content": "same-prompt-cross-tier"}]

        def fake_call(provider, messages, **kwargs):
            return _ok(provider.name, kwargs.get("model") or provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            first = router.chat(messages, task_tier="light")
            second = router.chat(messages, task_tier="deep")

        assert first.cached is False
        assert second.cached is False  # 若 key 不含 tier，这里会变成 True（F3 污染）
        assert first.provider == "zhipu_glm"

    def test_same_tier_cache_hits(self):
        """同 prompt 同 tier → 第二次命中缓存（证明缓存本身仍生效）。"""
        router = _tier_router()
        messages = [{"role": "user", "content": "same-tier-hit"}]

        with patch.object(
            router, "_call_provider",
            side_effect=lambda p, m, **kw: _ok(p.name, kw.get("model") or p.model),
        ):
            router.chat(messages, task_tier="light")
            second = router.chat(messages, task_tier="light")

        assert second.cached is True

    def test_cache_hit_records_last_routing(self):
        """§6.4：缓存命中也必须写 last_routing（AC12 依赖）。"""
        router = _tier_router()
        messages = [{"role": "user", "content": "cache-hit-last-routing"}]

        with patch.object(
            router, "_call_provider",
            side_effect=lambda p, m, **kw: _ok(p.name, kw.get("model") or p.model),
        ):
            router.chat(messages, task_tier="deep")
            router.chat(messages, task_tier="deep")

        from src.core.llm_router import _recorder
        last = _recorder.last()
        assert last["tier"] == "deep"
        assert last["cached"] is True
        assert last["latency_ms"] == 0.0

    def test_budget_exhausted_records_error(self):
        """预算耗尽 → last_routing.budget_exhausted=True 且 error == "budget_exhausted"。"""
        router = _tier_router()
        with patch.object(router, "_call_provider") as mocked:
            with pytest.raises(RuntimeError, match="总预算 0s 已耗尽"):
                router.chat([{"role": "user", "content": "zero"}], task_tier="deep", total_timeout=0)
        mocked.assert_not_called()
        from src.core.llm_router import _recorder
        last = _recorder.last()
        assert last["tier"] == "deep"
        assert last["budget_exhausted"] is True
        assert last["error"] == "budget_exhausted"

    def test_all_failed_records_error(self):
        router = _tier_router()
        with patch.object(router, "_call_provider", side_effect=_http_429()):
            with pytest.raises(RuntimeError, match="所有LLM Provider均不可用"):
                router.chat([{"role": "user", "content": "allfail"}], task_tier="light")
        from src.core.llm_router import _recorder
        assert _recorder.last()["error"] == "all_failed"
        assert _recorder.last()["fallback_count"] == 2

    def test_unknown_tier_falls_back_to_standard_behavior(self):
        """AC8：未知 tier 不抛异常，链路 == 全链。"""
        router = _tier_router()
        visited: list[str] = []

        def fake_call(provider, messages, **kwargs):
            visited.append(provider.name)
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "bogus-tier"}], task_tier="bogus")

        # standard 有效跳 = 3 个（glm_45 disabled 被 is_available 跳过）
        assert visited == ["zhipu_glm", "agnes", "ly77625"]
        from src.core.llm_router import _recorder
        assert _recorder.last()["tier"] == "standard"

    def test_kill_switch_restores_full_chain_and_old_formula(self):
        """B4/C-8：LLM_TIER_ROUTING_ENABLED=0 → deep 也退化全链，且单跳超时回到旧公式。"""
        router = _tier_router()
        timeouts: list[int] = []

        def fake_call(provider, messages, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            raise _http_429()

        with patch.dict("os.environ", {"LLM_TIER_ROUTING_ENABLED": "0"}, clear=False):
            with patch.object(router, "_call_provider", side_effect=fake_call):
                with pytest.raises(RuntimeError):
                    router.chat([{"role": "user", "content": "killswitch"}], task_tier="deep")

        # 全链但 glm_45 disabled → 3 跳，且每跳 timeout == min(provider.timeout, 25) 的旧公式
        assert len(timeouts) == 3
        assert all(t <= 25 for t in timeouts)

    def test_failed_hop_counts_fallback_after_retries_exhausted(self):
        """非 429 异常重试耗尽 → fallback_count 仍只 +1（每跳计一次）。"""
        router = _tier_router()
        calls = {"n": 0}

        def fake_call(provider, messages, **kwargs):
            if provider.name == "zhipu_glm":
                calls["n"] += 1
                raise requests.exceptions.ConnectionError("boom")
            return _ok(provider.name, kwargs.get("model") or provider.model)

        zhipu = next(p for p in router._providers if p.name == "zhipu_glm")
        zhipu.max_retries = 1
        zhipu.retry_delay = 0.0
        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat([{"role": "user", "content": "retry-exhaust"}], task_tier="deep")

        assert calls["n"] == 2  # max_attempts = min(max_retries+1, 3) = 2
        assert result.provider == "agnes"  # D9：deep 第 2 跳 = agnes
        from src.core.llm_router import _recorder
        assert _recorder.last()["fallback_count"] == 1

    def test_circuit_open_provider_skipped_without_counting_fallback(self):
        """disabled/熔断的 Provider 静默跳过，不计入 fallback（与原实现一致）。"""
        router = _tier_router()
        zhipu = next(p for p in router._providers if p.name == "zhipu_glm")
        zhipu.enabled = False
        with patch.object(
            router, "_call_provider",
            side_effect=lambda p, m, **kw: _ok(p.name, kw.get("model") or p.model),
        ):
            result = router.chat([{"role": "user", "content": "skip-disabled"}], task_tier="deep")
        assert result.provider == "agnes"  # 首跳被跳过 → 直接落 deep 第 2 跳
        from src.core.llm_router import _recorder
        assert _recorder.last()["fallback_count"] == 0

    def test_probe_tier_uses_route_model(self):
        """probe(tier=deep) 逐跳使用 route.model 覆盖（agnes 第 2 跳打 agnes-3.0-flash）。"""
        router = _tier_router()
        captured: list[tuple[str, str | None]] = []

        def fake_call(provider, messages, **kwargs):
            captured.append((provider.name, kwargs.get("model")))
            return _ok(provider.name, kwargs.get("model") or provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.probe(tier="deep")

        assert result["status"] == "ok"
        assert result["tier"] == "deep"
        assert result["total"] == 3
        assert [c[0] for c in captured] == ["zhipu_glm", "agnes", "ly77625"]
        assert captured[1][1] == "agnes-3.0-flash"

    def test_probe_name_still_backward_compatible(self):
        """probe(name=...) 保持既有语义：只探该 Provider，不下发 model 覆盖。"""
        router = _tier_router()
        captured: list[tuple[str, str | None]] = []

        def fake_call(provider, messages, **kwargs):
            captured.append((provider.name, kwargs.get("model")))
            return _ok(provider.name, provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.probe(name="agnes")

        assert result["total"] == 1
        assert captured == [("agnes", None)]
        assert "tier" not in result

    def test_probe_unknown_provider_message_unchanged(self):
        router = _tier_router()
        result = router.probe(name="does-not-exist")
        assert result["status"] == "error"
        assert result["message"] == "未配置 Provider: does-not-exist"


class TestTierHopTimeoutCap:
    """R2/D12：单跳 timeout_cap —— 保证 deep 名义 3 跳时第 3 跳真的还有预算。"""

    def test_deep_per_hop_timeouts_follow_cap(self):
        """deep 三跳实际下发的 timeout == 20 / 25 / 20（不是 provider.timeout=30）。"""
        router = _tier_router()
        timeouts: list[int] = []

        def fake_call(provider, messages, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "deep-caps"}], task_tier="deep")

        assert timeouts == [20, 25, 20]

    def test_deep_third_hop_is_really_attempted(self):
        """F2/D12 回归：前两跳快速失败后，deep 第 3 跳（ly77625）仍被调用。"""
        router = _tier_router()
        visited: list[tuple[str, int | None]] = []

        def fake_call(provider, messages, **kwargs):
            visited.append((provider.name, kwargs.get("timeout")))
            if provider.name != "ly77625":
                raise _http_429()  # 前两跳快速失败，只消耗极少时间
            return _ok(provider.name, kwargs.get("model") or provider.model)

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat([{"role": "user", "content": "deep-3rd"}], task_tier="deep")

        assert [v[0] for v in visited] == ["zhipu_glm", "agnes", "ly77625"]
        assert result.provider == "ly77625"
        # 第 3 跳拿到的超时是它自己的 cap，而不是 0 / 1
        assert visited[2][1] == 20

    def test_deep_cap_sum_within_budget_tolerance(self):
        """三跳 cap 之和 ≤ 65（预算 60 + 5s 容差），否则末位跳必然拿不到时间。"""
        from src.core.llm_task_tier import TIER_DEEP, TIER_SPECS

        spec = TIER_SPECS[TIER_DEEP]
        caps = [r.timeout_cap or 30 for r in spec.routes or ()]
        assert caps == [20, 25, 20]
        assert sum(caps) <= 65
        assert sum(caps) > spec.budget_seconds  # 允许"最后一跳被预算夹逼"，但差距必须很小
        assert sum(caps) - spec.budget_seconds <= 5

    def test_light_per_hop_timeouts_follow_cap(self):
        """light 两跳各 10s（< 15s 总预算），保证第 2 跳一定还有时间。"""
        router = _tier_router()
        timeouts: list[int] = []

        def fake_call(provider, messages, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "light-caps"}], task_tier="light")

        assert timeouts == [10, 10]

    def test_standard_has_no_hop_cap(self):
        """B5：standard 不做任何 cap，单跳仍是 provider.timeout（今日行为）。"""
        router = _tier_router()
        timeouts: list[int] = []

        def fake_call(provider, messages, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            raise _http_429()

        with patch.dict("os.environ", {"LLM_TOTAL_TIMEOUT": "100"}, clear=False):
            with patch.object(router, "_call_provider", side_effect=fake_call):
                with pytest.raises(RuntimeError):
                    router.chat([{"role": "user", "content": "std-caps"}])

        # 3 个有效跳（glm_45 disabled 被跳过）的 timeout 都是 30 → 未被任何 cap 收紧到 10/20
        assert timeouts == [30, 30, 30]

    def test_caller_timeout_still_wins_when_smaller(self):
        """调用方 timeout < cap 时，仍以调用方为准（例如 light 调用传 timeout=5）。"""
        router = _tier_router()
        timeouts: list[int] = []

        def fake_call(provider, messages, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat(
                    [{"role": "user", "content": "caller-cap"}],
                    timeout=5,
                    task_tier="light",
                )
        assert timeouts == [5, 5]


class TestRetiredModelIdRemap:
    """R3/D11：线上 AGNES_MODEL 指向已下线模型时，加载期自动纠正 + WARN。"""

    def test_retired_agnes_model_is_remapped(self):
        with patch.dict(
            "os.environ", {"AGNES_API_KEY": "ak", "AGNES_MODEL": "agnes-2.0-flash"}, clear=True
        ):
            with patch("src.core.llm_router.logger") as mock_logger:
                router = LLMRouter()

        agnes = router._providers[0]
        assert agnes.name == "agnes"
        assert agnes.model == "agnes-3.0-flash"
        warnings = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("agnes-2.0-flash" in w and ("已不可用" in w or "已下线" in w) for w in warnings), warnings

    def test_default_agnes_model_is_current(self):
        """不配 AGNES_MODEL 时，代码默认值本身就是可用 ID（不需要纠正）。"""
        with patch.dict("os.environ", {"AGNES_API_KEY": "ak"}, clear=True):
            router = LLMRouter()
        assert router._providers[0].model == "agnes-3.0-flash"

    def test_valid_agnes_model_is_not_touched(self):
        with patch.dict(
            "os.environ", {"AGNES_API_KEY": "ak", "AGNES_MODEL": "agnes-3.0-flash"}, clear=True
        ):
            router = LLMRouter()
        assert router._providers[0].model == "agnes-3.0-flash"

    def test_remap_does_not_affect_other_providers(self):
        """纠正只作用于 agnes，ZHIPU_MODEL 等既有覆盖能力不受影响。"""
        with patch.dict(
            "os.environ",
            {
                "AGNES_API_KEY": "ak",
                "AGNES_MODEL": "agnes-2.0-flash",
                "ZHIPU_API_KEY": "zk",
                "ZHIPU_MODEL": "glm-custom",
            },
            clear=True,
        ):
            router = LLMRouter()

        by_name = {p.name: p for p in router._providers}
        assert by_name["agnes"].model == "agnes-3.0-flash"
        assert by_name["zhipu_glm"].model == "glm-custom"

    def test_standard_chain_uses_corrected_model(self):
        """standard 全链走到 agnes 时不再打已下线的 500 模型。"""
        with patch.dict(
            "os.environ", {"AGNES_API_KEY": "ak", "AGNES_MODEL": "agnes-2.0-flash"}, clear=True
        ):
            router = LLMRouter()
        std = router._providers_for_tier("standard")
        assert [p.model for p in std] == ["agnes-3.0-flash"]

    def test_retired_map_declared_in_module(self):
        from src.core.llm_router import RETIRED_MODEL_IDS

        assert RETIRED_MODEL_IDS["agnes-2.0-flash"] == "agnes-3.0-flash"


class TestDeepChainOrderD9:
    """R1/D9：deep 链顺序 = zhipu_glm → agnes-3.0-flash → ly77625。"""

    def test_deep_chain_order(self):
        router = _tier_router()
        assert [p.name for p in router._providers_for_tier("deep")] == [
            "zhipu_glm", "agnes", "ly77625"
        ]

    def test_agnes_precedes_gemini_relay(self):
        """agnes-3.0-flash（E1 已验 JSON 严格）必须排在第三方中转 gemini 之前。"""
        from src.core.llm_task_tier import TIER_DEEP, TIER_SPECS

        routes = TIER_SPECS[TIER_DEEP].routes or ()
        assert [r.provider for r in routes].index("agnes") < [
            r.provider for r in routes
        ].index("ly77625")

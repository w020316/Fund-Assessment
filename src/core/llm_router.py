"""多LLM Provider路由器 - 支持多模型自动故障切换

参考: ZhuLinsen/daily_stock_analysis 的 LiteLLM Router 设计
支持: OpenAI兼容(TTAPI/Agnes/通义千问)、Gemini、Anthropic、Ollama本地模型
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests
from loguru import logger


class ProviderType(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


@dataclass
class LLMProvider:
    """LLM Provider配置"""
    name: str
    provider_type: ProviderType
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout: int = 60
    max_retries: int = 2
    retry_delay: float = 1.0
    priority: int = 0  # 越小优先级越高
    enabled: bool = True
    rpm: int = 0  # 每分钟请求数限制(0=不限),用于令牌桶限流
    api_keys: list = field(default_factory=list)  # 多Key轮换(逗号分隔的多个key)
    _fail_count: int = field(default=0, repr=False)
    _last_fail_time: float = field(default=0.0, repr=False)
    _circuit_open: bool = field(default=False, repr=False)
    _key_index: int = field(default=0, repr=False)  # Key轮换索引
    _last_health_status: dict = field(default_factory=dict, repr=False)  # 最近健康检查结果

    @property
    def is_available(self) -> bool:
        """检查Provider是否可用（熔断器状态）"""
        if not self.enabled:
            return False
        if not self._circuit_open:
            return True
        # 熔断器冷却期：30秒后半开
        if time.monotonic() - self._last_fail_time > 30:
            self._circuit_open = False
            logger.info(f"LLM Provider [{self.name}] 熔断器半开，尝试恢复")
            return True
        return False

    @property
    def current_api_key(self) -> str:
        """获取当前轮换的API Key(支持多Key负载均衡)"""
        if self.api_keys:
            key = self.api_keys[self._key_index % len(self.api_keys)]
            self._key_index += 1
            return key
        return self.api_key

    def record_success(self):
        """记录成功调用"""
        self._fail_count = 0
        self._circuit_open = False

    def record_failure(self):
        """记录失败调用"""
        self._fail_count += 1
        self._last_fail_time = time.monotonic()
        if self._fail_count >= 3:
            self._circuit_open = True
            logger.warning(f"LLM Provider [{self.name}] 连续失败{self._fail_count}次，熔断器开启")


class TokenBucket:
    """令牌桶限流器 - 按Provider配置RPM限制请求速率

    原理:每分钟补充 rpm 个令牌,每次请求消耗1个令牌。
    令牌不足时等待,避免触发429。
    """

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._tokens = float(rpm) if rpm > 0 else float('inf')
        self._max_tokens = float(rpm) if rpm > 0 else float('inf')
        self._last_refill = time.monotonic()
        # P3-11 修复(2026-07-30):原 __import__('threading').Lock() 反模式,
        # 改为顶部 import threading,直接 threading.Lock()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> bool:
        """获取一个令牌,超时返回False"""
        if self.rpm <= 0:
            return True  # 不限流

        import time as _time
        deadline = _time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
                wait_time = (1 - self._tokens) / (self.rpm / 60.0)
            if _time.monotonic() + wait_time > deadline:
                return False
            _time.sleep(min(wait_time, 0.5))

    def _refill(self):
        """补充令牌(按时间比例)"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            refill = elapsed * (self.rpm / 60.0)
            self._tokens = min(self._max_tokens, self._tokens + refill)
            self._last_refill = now


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str
    provider: str
    model: str
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    cached: bool = False  # P0:是否命中缓存(借鉴 openai/openai-python 的缓存设计)


# ===== P0:LLM 结果缓存(借鉴 openai/openai-python 的 response_cache 设计)=====
# 设计目的:
# - 免费模型(agnes-2.0-flash / glm-4-flash)额度有限,重复相同 prompt 浪费额度
# - 同一基金的多智能体分析(7 个 agent 调 LLM)存在大量重复调用
# - 缓存命中时直接返回,不消耗 LLM 额度
# 实现:纯 Python LRU+TTL(maxsize=100, ttl=300s),不增加依赖
class _LLMResponseCache:
    """LLM 响应缓存(LRU + TTL)"""

    def __init__(self, maxsize: int = 100, ttl: float = 300.0):
        self._maxsize = max(maxsize, 8)
        self._ttl = ttl
        self._store: dict = {}  # key -> {response, ts}
        # P3-11 修复(2026-07-30):原 __import__('threading').Lock() 反模式,
        # 改为顶部 import threading,直接 threading.Lock()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _make_key(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        temperature: float,
        json_mode: bool,
    ) -> str:
        """构造缓存 key(基于 messages 内容 + 模型 + 温度)"""
        import hashlib
        import json as _json
        # 排除 role=system 的可变时间戳(如"当前时间:2026-07-29 15:30")
        # 但保留 system 消息内容本身(影响分析结果)
        payload = _json.dumps({
            "m": messages,
            "model": model or "",
            "temp": round(temperature, 2),
            "json_mode": json_mode,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def get(self, messages, model, temperature, json_mode) -> "LLMResponse | None":
        key = self._make_key(messages, model, temperature, json_mode)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            if time.monotonic() - entry["ts"] > self._ttl:
                self._store.pop(key, None)
                self.misses += 1
                return None
            # 命中:返回缓存的响应副本(标注 cached=True)
            self.hits += 1
            resp = entry["response"]
            return LLMResponse(
                content=resp.content,
                provider=resp.provider,
                model=resp.model,
                usage=resp.usage,
                latency_ms=0.0,  # 缓存命中,延迟为 0
                cached=True,
            )

    def set(self, messages, model, temperature, json_mode, response: "LLMResponse") -> None:
        key = self._make_key(messages, model, temperature, json_mode)
        with self._lock:
            if key in self._store:
                # 已存在,更新时间戳
                self._store[key] = {"response": response, "ts": time.monotonic()}
                return
            self._store[key] = {"response": response, "ts": time.monotonic()}
            # LRU 淘汰:超过上限时删除最早的条目
            while len(self._store) > self._maxsize:
                oldest_key = next(iter(self._store))
                self._store.pop(oldest_key, None)

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._store),
                "maxsize": self._maxsize,
                "ttl_seconds": self._ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total > 0 else 0.0,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0


# 全局 LLM 响应缓存(单例,LLMRouter 共享)
_llm_cache = _LLMResponseCache(maxsize=100, ttl=300.0)


class LLMRouter:
    """多LLM Provider路由器

    特性:
    - 多Provider自动故障切换
    - 熔断器保护（连续3次失败自动熔断，30秒后半开）
    - 按优先级排序选择Provider
    - 支持JSON Mode输出
    """

    def __init__(self):
        self._providers: list[LLMProvider] = []
        self._token_buckets: dict = {}  # {provider_name: TokenBucket}
        self._load_from_env()

    def _load_from_env(self):
        """从环境变量加载Provider配置"""
        # TTAPI (OpenAI兼容)
        ttapi_key = os.getenv("TTAPI_API_KEY", "")
        if ttapi_key:
            self.add_provider(LLMProvider(
                name="ttapi",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("TTAPI_BASE_URL", "https://ttapi.io/v1"),
                api_key=ttapi_key,
                model=os.getenv("TTAPI_MODEL", "gpt-4o"),
                priority=0,
            ))

        # Agnes (OpenAI兼容, 全模态永久免费)
        agnes_key = os.getenv("AGNES_API_KEY", "")
        if agnes_key:
            self.add_provider(LLMProvider(
                name="agnes",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1"),
                api_key=agnes_key,
                model=os.getenv("AGNES_MODEL", "agnes-2.0-flash"),
                priority=90,
                timeout=30,
            ))

        # Gemini
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            self.add_provider(LLMProvider(
                name="gemini",
                provider_type=ProviderType.GEMINI,
                base_url=os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
                api_key=gemini_key,
                model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
                priority=2,
            ))

        # Anthropic
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            self.add_provider(LLMProvider(
                name="anthropic",
                provider_type=ProviderType.ANTHROPIC,
                base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
                api_key=anthropic_key,
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                priority=3,
            ))

        # Ollama (本地模型)
        ollama_base = os.getenv("OLLAMA_BASE_URL", "")
        if ollama_base:
            self.add_provider(LLMProvider(
                name="ollama",
                provider_type=ProviderType.OLLAMA,
                base_url=ollama_base,
                model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
                priority=10,
                timeout=120,
            ))

        # ===== P1 新增:免费 LLM Provider(8个,全部OpenAI兼容) =====

        # 智谱 GLM (永久免费,无token上限,30并发)
        zhipu_keys = self._parse_api_keys("ZHIPU_API_KEY", "ZHIPU_API_KEYS")
        if zhipu_keys:
            self.add_provider(LLMProvider(
                name="zhipu_glm",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
                api_key=zhipu_keys[0],
                api_keys=zhipu_keys,
                model=os.getenv("ZHIPU_MODEL", "glm-4-flash"),
                priority=100,
                rpm=30,  # 智谱限制30并发
                timeout=30,  # 快速失败,避免 Render 免费实例超时
            ))

        # 硅基流动 SiliconFlow (9B以下永久免费,1000 RPM)
        siliconflow_keys = self._parse_api_keys("SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS")
        if siliconflow_keys:
            self.add_provider(LLMProvider(
                name="siliconflow",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
                api_key=siliconflow_keys[0],
                api_keys=siliconflow_keys,
                model=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
                priority=95,
                rpm=1000,
            ))

        # 阿里云百炼 DashScope (qwen-turbo永久免费)
        dashscope_keys = self._parse_api_keys("DASHSCOPE_API_KEY", "DASHSCOPE_API_KEYS")
        if dashscope_keys:
            self.add_provider(LLMProvider(
                name="aliyun_qwen",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                api_key=dashscope_keys[0],
                api_keys=dashscope_keys,
                model=os.getenv("DASHSCOPE_MODEL", "qwen-turbo"),
                priority=85,
            ))

        # Groq (国内可直连,极速,30 RPM)
        groq_keys = self._parse_api_keys("GROQ_API_KEY", "GROQ_API_KEYS")
        if groq_keys:
            self.add_provider(LLMProvider(
                name="groq",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                api_key=groq_keys[0],
                api_keys=groq_keys,
                model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                priority=80,
                rpm=30,
            ))

        # OpenRouter (聚合26+免费模型,20 RPM)
        openrouter_keys = self._parse_api_keys("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS")
        if openrouter_keys:
            self.add_provider(LLMProvider(
                name="openrouter",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                api_key=openrouter_keys[0],
                api_keys=openrouter_keys,
                model=os.getenv("OPENROUTER_MODEL", "qwen/qwen-3-235b:free"),
                priority=75,
                rpm=20,
            ))

        # DeepSeek (低价兜底)
        deepseek_keys = self._parse_api_keys("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS")
        if deepseek_keys:
            self.add_provider(LLMProvider(
                name="deepseek",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                api_key=deepseek_keys[0],
                api_keys=deepseek_keys,
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                priority=70,
            ))

        # Cloudflare Workers AI (边缘节点)
        cf_account = os.getenv("CF_ACCOUNT_ID", "")
        cf_token = os.getenv("CF_API_TOKEN", "")
        if cf_account and cf_token:
            self.add_provider(LLMProvider(
                name="cloudflare_ai",
                provider_type=ProviderType.OPENAI,
                base_url=f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1",
                api_key=cf_token,
                model=os.getenv("CF_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
                priority=65,
            ))

        # 按优先级排序
        self._providers.sort(key=lambda p: p.priority)
        if self._providers:
            logger.info(f"LLM Router 已加载 {len(self._providers)} 个Provider: {[p.name for p in self._providers]}")
        else:
            logger.warning("LLM Router 未检测到任何LLM Provider配置")

    def add_provider(self, provider: LLMProvider):
        """添加Provider"""
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)
        # 为配置了rpm的Provider创建令牌桶
        if provider.rpm > 0:
            self._token_buckets[provider.name] = TokenBucket(provider.rpm)

    def _parse_api_keys(self, single_key_env: str, multi_keys_env: str) -> list:
        """解析API Key,支持单Key和多Key(逗号分隔)

        优先读取多Key环境变量(XXX_API_KEYS=key1,key2),
        回退到单Key环境变量(XXX_API_KEY=key1)。
        """
        multi = os.getenv(multi_keys_env, "")
        if multi:
            keys = [k.strip() for k in multi.split(",") if k.strip()]
            if keys:
                return keys
        single = os.getenv(single_key_env, "")
        return [single] if single else []

    @property
    def available_providers(self) -> list[str]:
        """返回可用的Provider名称列表"""
        return [p.name for p in self._providers if p.is_available]

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        json_mode: bool = False,
        timeout: int | None = None,
    ) -> LLMResponse:
        """发送聊天请求，自动故障切换 + 令牌桶限流 + 指数退避重试

        Args:
            messages: 消息列表
            model: 指定模型（覆盖Provider默认模型）
            temperature: 温度参数
            json_mode: 是否启用JSON输出模式
            timeout: 超时时间（秒）

        Returns:
            LLMResponse

        Raises:
            RuntimeError: 所有Provider均不可用
        """
        # P0:LLM 结果缓存(命中直接返回,不消耗免费额度)
        cached = _llm_cache.get(messages, model, temperature, json_mode)
        if cached is not None:
            logger.debug(f"LLM 缓存命中(provider={cached.provider}, model={cached.model})")
            return cached

        last_error = None
        for provider in self._providers:
            if not provider.is_available:
                continue

            # 令牌桶限流:获取令牌,超时则跳过该Provider
            bucket = self._token_buckets.get(provider.name)
            if bucket and not bucket.acquire(timeout=10):
                logger.warning(f"LLM Provider [{provider.name}] 令牌桶限流超时,跳过")
                continue

            # 指数退避重试(1s → 2s → 4s)
            max_attempts = min(provider.max_retries + 1, 3)
            for attempt in range(max_attempts):
                try:
                    logger.debug(f"尝试 LLM Provider: {provider.name} (第{attempt+1}次)")
                    response = self._call_provider(
                        provider, messages,
                        model=model, temperature=temperature,
                        json_mode=json_mode, timeout=timeout,
                    )
                    provider.record_success()
                    # P0:写入 LLM 结果缓存(下次相同 prompt 直接命中)
                    _llm_cache.set(messages, model, temperature, json_mode, response)
                    return response
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        delay = provider.retry_delay * (2 ** attempt)
                        logger.warning(f"LLM Provider [{provider.name}] 第{attempt+1}次失败: {e},{delay:.1f}s后重试")
                        time.sleep(delay)
                    else:
                        provider.record_failure()
                        logger.warning(f"LLM Provider [{provider.name}] 全部{max_attempts}次重试失败: {e}")

        raise RuntimeError(f"所有LLM Provider均不可用，最后错误: {last_error}")

    def _call_provider(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        json_mode: bool = False,
        timeout: int | None = None,
    ) -> LLMResponse:
        """调用指定Provider"""
        use_model = model or provider.model
        use_timeout = timeout or provider.timeout

        if provider.provider_type == ProviderType.OPENAI:
            return self._call_openai_compatible(provider, messages, use_model, temperature, json_mode, use_timeout)
        elif provider.provider_type == ProviderType.GEMINI:
            return self._call_gemini(provider, messages, use_model, temperature, json_mode, use_timeout)
        elif provider.provider_type == ProviderType.ANTHROPIC:
            return self._call_anthropic(provider, messages, use_model, temperature, json_mode, use_timeout)
        elif provider.provider_type == ProviderType.OLLAMA:
            return self._call_ollama(provider, messages, use_model, temperature, use_timeout)
        else:
            raise ValueError(f"不支持的Provider类型: {provider.provider_type}")

    def _call_openai_compatible(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        json_mode: bool,
        timeout: int,
    ) -> LLMResponse:
        """调用OpenAI兼容API"""
        url = f"{provider.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.current_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        latency = (time.monotonic() - start) * 1000
        resp.raise_for_status()

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        if not content:
            raise ValueError(f"Provider [{provider.name}] 返回空内容")

        return LLMResponse(
            content=content,
            provider=provider.name,
            model=model,
            usage=usage,
            latency_ms=latency,
        )

    def _call_gemini(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        json_mode: bool,
        timeout: int,
    ) -> LLMResponse:
        """调用Gemini API"""
        # 修复:API key 从 URL query 改为 HTTP Header,避免 access log 泄露
        url = f"{provider.base_url}/models/{model}:generateContent"

        # 转换消息格式
        contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}],
            })

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        start = time.monotonic()
        resp = requests.post(
            url, json=payload, timeout=timeout,
            headers={"x-goog-api-key": provider.api_key},
        )
        latency = (time.monotonic() - start) * 1000
        resp.raise_for_status()

        data = resp.json()
        content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

        if not content:
            raise ValueError(f"Provider [{provider.name}] 返回空内容")

        return LLMResponse(
            content=content,
            provider=provider.name,
            model=model,
            latency_ms=latency,
        )

    def _call_anthropic(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        json_mode: bool,
        timeout: int,
    ) -> LLMResponse:
        """调用Anthropic API"""
        url = f"{provider.base_url}/messages"
        headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        # 分离system消息
        system_content = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_content += msg["content"] + "\n"
            else:
                chat_messages.append(msg)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "messages": chat_messages,
            "temperature": temperature,
        }
        if system_content:
            payload["system"] = system_content.strip()

        start = time.monotonic()
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        latency = (time.monotonic() - start) * 1000
        resp.raise_for_status()

        data = resp.json()
        content = data.get("content", [{}])[0].get("text", "")
        usage = {
            "input_tokens": data.get("usage", {}).get("input_tokens", 0),
            "output_tokens": data.get("usage", {}).get("output_tokens", 0),
        }

        if not content:
            raise ValueError(f"Provider [{provider.name}] 返回空内容")

        return LLMResponse(
            content=content,
            provider=provider.name,
            model=model,
            usage=usage,
            latency_ms=latency,
        )

    def _call_ollama(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        timeout: int,
        **kwargs,
    ) -> LLMResponse:
        """调用Ollama本地模型API"""
        url = f"{provider.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        start = time.monotonic()
        resp = requests.post(url, json=payload, timeout=timeout)
        latency = (time.monotonic() - start) * 1000
        resp.raise_for_status()

        data = resp.json()
        content = data.get("message", {}).get("content", "")

        if not content:
            raise ValueError(f"Provider [{provider.name}] 返回空内容")

        return LLMResponse(
            content=content,
            provider=provider.name,
            model=model,
            latency_ms=latency,
        )

    def health_check(self) -> dict:
        """检查所有Provider的健康状态

        Returns:
            {provider_name: {"available": bool, "model": str, "priority": int, ...}}
        """
        result = {}
        for provider in self._providers:
            result[provider.name] = {
                "available": provider.is_available,
                "model": provider.model,
                "priority": provider.priority,
                "fail_count": provider._fail_count,
                "circuit_open": provider._circuit_open,
                "rpm": provider.rpm,
                "has_multi_keys": len(provider.api_keys) > 1,
                "provider_type": provider.provider_type.value,
            }
        return result


# 全局单例
_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """获取LLM路由器单例"""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router

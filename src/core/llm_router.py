"""多LLM Provider路由器 - 支持多模型自动故障切换

参考: ZhuLinsen/daily_stock_analysis 的 LiteLLM Router 设计
支持: OpenAI兼容(TTAPI/DeepSeek/通义千问)、Gemini、Anthropic、Ollama本地模型
"""
from __future__ import annotations

import json
import os
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
    _fail_count: int = field(default=0, repr=False)
    _last_fail_time: float = field(default=0.0, repr=False)
    _circuit_open: bool = field(default=False, repr=False)

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


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str
    provider: str
    model: str
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0


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

        # DeepSeek (OpenAI兼容)
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            self.add_provider(LLMProvider(
                name="deepseek",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                api_key=deepseek_key,
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                priority=1,
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
        """发送聊天请求，自动故障切换

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
        last_error = None
        for provider in self._providers:
            if not provider.is_available:
                continue

            try:
                logger.debug(f"尝试 LLM Provider: {provider.name}")
                response = self._call_provider(
                    provider, messages,
                    model=model, temperature=temperature,
                    json_mode=json_mode, timeout=timeout,
                )
                provider.record_success()
                return response
            except Exception as e:
                provider.record_failure()
                last_error = e
                logger.warning(f"LLM Provider [{provider.name}] 调用失败: {e}")
                continue

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
            "Authorization": f"Bearer {provider.api_key}",
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
        url = f"{provider.base_url}/models/{model}:generateContent?key={provider.api_key}"

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
        resp = requests.post(url, json=payload, timeout=timeout)
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


# 全局单例
_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """获取LLM路由器单例"""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router

"""多LLM Provider路由器 - 支持多模型自动故障切换

参考: ZhuLinsen/daily_stock_analysis 的 LiteLLM Router 设计
支持: OpenAI兼容(TTAPI/Agnes/通义千问)、Gemini、Anthropic、Ollama本地模型

免费 Provider(默认模型均为核实过的免费档,来源与核实日期见
docs/free-llm-providers.md):
  国内 - 智谱 GLM-4.7-Flash、硅基流动、讯飞星火 Lite、百度千帆 ERNIE-Speed、阿里百炼
  海外 - Groq、Cerebras、OpenRouter(免费模型)、Agnes
未配置 API Key 的 Provider 不会加载,也不影响既有调用链。
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

# ===== 任务难度分层路由（增量 2026-11-07）=====
# llm_task_tier 是纯数据/纯函数模块，**不得**反向 import 本模块（否则循环依赖）。
from src.core.llm_task_tier import (
    DEFAULT_TIER,
    KNOWN_MODEL_REPLACEMENTS,
    SAME_UPSTREAM,
    TIER_NAMES,
    TIER_SPECS,
    RoutingRecorder,
    TierRoute,
    correct_model_id,
    hop_cap_seconds,
    hop_timeout_env_key,
    match_routes,
    normalize_tier,
    resolve_budget_seconds,
    resolve_hop_timeout,
    routing_enabled,
    tier_budget_env_key,
)

# 兼容别名（旧名，保留给外部脚本/测试；数据本体在 llm_task_tier.KNOWN_MODEL_REPLACEMENTS）。
# 键从 "provider:model" 拆成扁平 model → 当前 ID，仅供诊断展示，**不参与运行时判定**。
RETIRED_MODEL_IDS: dict[str, str] = {
    key.split(":", 1)[1]: target for key, target in KNOWN_MODEL_REPLACEMENTS.items()
}


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
    free_tier: bool = False  # 是否使用免费档(仅用于状态展示,不影响调度)
    api_keys: list = field(default_factory=list)  # 多Key轮换(逗号分隔的多个key)
    _fail_count: int = field(default=0, repr=False)
    _last_fail_time: float = field(default=0.0, repr=False)
    _circuit_open: bool = field(default=False, repr=False)
    _key_index: int = field(default=0, repr=False)  # Key轮换索引
    _last_health_status: dict = field(default_factory=dict, repr=False)  # 最近健康检查结果
    # 修复(2026-07-31): 新增 _key_lock 保护 _key_index 的原子自增,避免多线程并发调用
    # current_api_key 时 _key_index 丢失更新,导致 key 轮换不公平(多线程选到同一 key)
    _key_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        # 各 _call_* 用 f"{base_url}/path" 拼接，尾斜杠会拼出 "//"
        # (线上实测:智谱默认值 .../v4/ → .../v4//chat/completions)
        self.base_url = self.base_url.rstrip("/")

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
            with self._key_lock:
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
        tier: str = "",
    ) -> str:
        """构造缓存 key(基于 messages 内容 + 模型 + 温度 + 难度档位)

        2026-11-07（设计 §1.1 矛盾 3 / F3）：key 必须包含 tier。
        三个档位的**首跳是同一个** (zhipu_glm, glm-4.7-flash)，若 key 只靠 model 区分，
        则三档 key 完全相同 —— light 的低质答案会 100% 命中给 deep。因此 tier 进 key
        提供的隔离度远大于 model。
        """
        import hashlib
        import json as _json
        # 排除 role=system 的可变时间戳(如"当前时间:2026-07-29 15:30")
        # 但保留 system 消息内容本身(影响分析结果)
        payload = _json.dumps({
            "m": messages,
            "model": model or "",
            "temp": round(temperature, 2),
            "json_mode": json_mode,
            "tier": tier,  # 恒为规范化后的档名(由 chat() 保证)，不会是 None/""
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def get(self, messages, model, temperature, json_mode, tier: str = "") -> "LLMResponse | None":
        key = self._make_key(messages, model, temperature, json_mode, tier)
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

    def set(self, messages, model, temperature, json_mode, response: "LLMResponse", tier: str = "") -> None:
        """写入缓存。

        注意(设计 §6.4)：这里存的是**请求级** ``model`` 入参，不是 resolved model。
        ``get()`` 发生时还不知道会路由到哪个 Provider，若改存 resolved model，
        deep 将永远查不到自己写过的缓存。这是有意的不对称，不要"优化"。
        """
        key = self._make_key(messages, model, temperature, json_mode, tier)
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

# 全局路由决策记录器(单例)：只存非敏感字段,供 /api/health/llm 的 routing.last_routing
_recorder = RoutingRecorder()


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
        # 2026-11-07（D4）：ttapi.io 已失效（HTTP 404 返回 HTML），线上也未再配置 Key。
        # 保留配置（不删代码、不动 priority），只置 enabled=False → chat() 通过
        # is_available 跳过它，而 probe()/health_check() 仍能看到它（probe 不按
        # enabled 过滤），便于诊断"为什么它不参与调度"。
        ttapi_key = os.getenv("TTAPI_API_KEY", "")
        if ttapi_key:
            self.add_provider(LLMProvider(
                name="ttapi",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("TTAPI_BASE_URL", "https://ttapi.io/v1"),
                api_key=ttapi_key,
                model=os.getenv("TTAPI_MODEL", "gpt-4o"),
                priority=0,
                enabled=False,
            ))

        # Agnes (OpenAI兼容, 全模态永久免费)
        # D11（2026-11-07）：线上 AGNES_MODEL 仍写死 agnes-2.0-flash，而该模型 ID 已被平台
        # 下架（实测 HTTP 500 "Model agnes-2.0-flash is temporarily unavailable"，
        # /v1/models 也不再列出）→ standard 全链走到 agnes 时必然 500。
        # deep/light 靠 TierRoute.model 覆盖能绕开，但 standard 不能，所以这里做配置纠偏：
        # 命中 RETIRED_MODEL_IDS 即替换为当前 ID 并 WARN（环境变量可继续覆盖，只是值被纠正）。
        agnes_key = os.getenv("AGNES_API_KEY", "")
        if agnes_key:
            self.add_provider(LLMProvider(
                name="agnes",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1"),
                api_key=agnes_key,
                model=correct_model_id(
                    "agnes",
                    os.getenv("AGNES_MODEL", "agnes-3.0-flash"),
                    warn=lambda msg: logger.warning(f"[llm-routing] {msg}"),
                ),
                priority=90,
                timeout=30,
                free_tier=True,
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

        # ===== 免费 LLM Provider(11个,全部OpenAI兼容) =====

        # 智谱 GLM (GLM-4.7-Flash 免费,200K 上下文)
        zhipu_keys = self._parse_api_keys("ZHIPU_API_KEY", "ZHIPU_API_KEYS")
        if zhipu_keys:
            self.add_provider(LLMProvider(
                name="zhipu_glm",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
                api_key=zhipu_keys[0],
                api_keys=zhipu_keys,
                model=os.getenv("ZHIPU_MODEL", "glm-4.7-flash"),
                priority=-1,  # 用户指定优先使用智谱免费模型:负数保证排在所有 Provider 之前
                rpm=30,  # 智谱限制30并发
                timeout=30,  # 快速失败,避免 Render 免费实例超时
                free_tier=True,
            ))
            # GLM-4.5-Flash 同为免费(128K)。与 4.7-Flash 共用 Key 但模型独立,
            # 高峰期某个免费模型被平台限流(429/1305)时作为同平台兜底。
            # D10（主理人 2026-11-07 裁定 A）：**enabled=False**。
            # 理由：GLM-4.5-Flash 已于 2026-01-30 下线并自动路由到 4.7-Flash，与 zhipu_glm
            # 同源（同 Key / 同 30 rpm），禁用避免标准链上的同源伪重试（F1 伪冗余）。
            # 注意：`priority` 数值与 model 一个字都没动（AC3）；对象仍留在 _providers 里，
            # 因此 probe()/health_check() 仍能看到它（probe 不按 enabled 过滤）。
            self.add_provider(LLMProvider(
                name="zhipu_glm_45",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
                api_key=zhipu_keys[0],
                api_keys=zhipu_keys,
                model=os.getenv("ZHIPU_MODEL_FALLBACK", "glm-4.5-flash"),
                priority=1,
                rpm=30,
                timeout=30,
                free_tier=True,
                enabled=False,
            ))

        # 硅基流动 SiliconFlow (Qwen2-7B 等 7 个模型免费,1000 RPM)
        siliconflow_keys = self._parse_api_keys("SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS")
        if siliconflow_keys:
            self.add_provider(LLMProvider(
                name="siliconflow",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
                api_key=siliconflow_keys[0],
                api_keys=siliconflow_keys,
                model=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen2-7B-Instruct"),
                priority=95,
                rpm=1000,
                free_tier=True,
            ))

        # 讯飞星火 Lite (免费,OpenAI兼容,Bearer <APIPassword>)
        spark_keys = self._parse_api_keys("SPARK_API_KEY", "SPARK_API_KEYS")
        if spark_keys:
            self.add_provider(LLMProvider(
                name="spark_lite",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("SPARK_BASE_URL", "https://spark-api-open.xf-yun.com/v1"),
                api_key=spark_keys[0],
                api_keys=spark_keys,
                model=os.getenv("SPARK_MODEL", "lite"),
                priority=88,
                rpm=30,
                timeout=30,
                free_tier=True,
            ))

        # 阿里云百炼 DashScope (qwen-turbo 新人免费额度,90 天)
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
                free_tier=True,
            ))

        # 百度千帆 ERNIE-Speed (永久免费,OpenAI兼容 Bearer API Key)
        qianfan_keys = self._parse_api_keys("QIANFAN_API_KEY", "QIANFAN_API_KEYS")
        if qianfan_keys:
            self.add_provider(LLMProvider(
                name="qianfan_ernie",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("QIANFAN_BASE_URL", "https://qianfan.baidubce.com/v2"),
                api_key=qianfan_keys[0],
                api_keys=qianfan_keys,
                model=os.getenv("QIANFAN_MODEL", "ernie-speed-128k"),
                priority=82,
                rpm=60,  # 免费档 QPS 1,按 60 RPM 保守限流
                timeout=30,
                free_tier=True,
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
                free_tier=True,
            ))

        # Cerebras Inference (免费 5 RPM / 100万 tokens/日)
        cerebras_keys = self._parse_api_keys("CEREBRAS_API_KEY", "CEREBRAS_API_KEYS")
        if cerebras_keys:
            self.add_provider(LLMProvider(
                name="cerebras",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
                api_key=cerebras_keys[0],
                api_keys=cerebras_keys,
                model=os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
                priority=78,
                rpm=5,
                timeout=30,
                free_tier=True,
            ))

        # OpenRouter (聚合免费模型,20 RPM)
        openrouter_keys = self._parse_api_keys("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS")
        if openrouter_keys:
            self.add_provider(LLMProvider(
                name="openrouter",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                api_key=openrouter_keys[0],
                api_keys=openrouter_keys,
                model=os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free"),
                priority=75,
                rpm=20,
                free_tier=True,
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

        # ly.77625.xyz(第三方免费模型站,OpenAI 兼容;可用性不保证)
        # 实测:GET /v1/models 200、gemini-3.8-flash 对话可用(~15s);
        # x-deepseek-v4.1-flash 返回 502。仅作最后一档兜底,不改变既有优先级。
        ly_key = os.getenv("LY77625_API_KEY", "")
        if ly_key:
            self.add_provider(LLMProvider(
                name="ly77625",
                provider_type=ProviderType.OPENAI,
                base_url=os.getenv("LY77625_BASE_URL", "https://ly.77625.xyz/v1"),
                api_key=ly_key,
                model=os.getenv("LY77625_MODEL", "gemini-3.8-flash"),
                priority=95,
                timeout=int(os.getenv("LY77625_TIMEOUT", "30")),
                free_tier=True,
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

    # ===== 任务难度分层路由:只读派生视图（2026-11-07）=====
    # 全部方法**不修改** self._providers（顺序/长度/字段），返回新列表但元素是原对象。
    def _normalize_tier(self, task_tier: Any) -> str:
        """把任意入参归一化为合法档名（D8：未知值回落 standard 且只 WARN 一次）。"""
        return normalize_tier(task_tier, warn=lambda msg: logger.warning(f"[llm-routing] {msg}"))

    def _routes_for_tier(self, task_tier: Any) -> list[tuple[LLMProvider, TierRoute | None]]:
        """解析档位 → [(provider, route_or_None)] 有序链（契约 C-1 ~ C-10）。"""
        tier = self._normalize_tier(task_tier)

        # C-8 全局旁路：LLM_TIER_ROUTING_ENABLED=0 → 任何档位退化为全链
        if not routing_enabled():
            return [(p, None) for p in self._providers]

        spec = TIER_SPECS[tier]  # normalize_tier 保证 key 合法

        # C-1/C-2/C-3 standard：动态全链，新列表 + 原对象
        if spec.is_dynamic:
            return [(p, None) for p in self._providers]

        # C-6 逐条精确匹配（大小写敏感），未加载的跳过
        pairs = match_routes(spec, self._providers)

        # C-7 fail-open：一条都没命中（配置与部署环境不匹配）→ 回落全链 + WARN
        # 注意 self._providers 为空时不报警（此时"回落全链"只是空链，报警会误导且刷屏）
        if self._providers and all(route is None for _, route in pairs):
            logger.warning(
                f"[llm-routing] tier={tier} 的候选 Provider 均未加载 "
                f"(需要 {[r.provider for r in spec.routes or ()]}，"
                f"实际 {[p.name for p in self._providers]})，回落全链"
            )
            return pairs

        # C-6 部分缺失：逐条 DEBUG，不刷 WARN（每次 chat 都会走到这里）
        loaded = {p.name for p, r in pairs if r is not None}
        for route in spec.routes or ():
            if route.provider not in loaded:
                logger.debug(f"[llm-routing] tier={tier} 跳过未加载的 Provider: {route.provider}")
        return pairs

    def _providers_for_tier(self, task_tier: str | None = None) -> list[LLMProvider]:
        """返回某档位的有序 Provider 视图（独立列表，元素为 _providers 中的原对象）。"""
        return [provider for provider, _route in self._routes_for_tier(task_tier)]

    def _resolve_budget(self, tier: str, total_timeout: float | None) -> float:
        """预算优先级：显式 total_timeout > LLM_TIER_TIMEOUT_<TIER> > 档位常量 > LLM_TOTAL_TIMEOUT。

        全局旁路（``LLM_TIER_ROUTING_ENABLED=0``）时**同时**退回"预算 = LLM_TOTAL_TIMEOUT"，
        这样一条环境变量即可真正恢复今日行为（链 + 单跳公式 + 预算三处全部回退），
        而不是只回退链却留下 60s 长尾。
        """
        if not routing_enabled():
            if total_timeout is not None:
                return max(float(total_timeout), 0.0)
            return max(float(os.getenv("LLM_TOTAL_TIMEOUT", "25")), 0.0)
        return resolve_budget_seconds(tier, total_timeout)

    def _hop_timeout(
        self,
        tier: str,
        route: TierRoute | None,
        provider: LLMProvider,
        caller_timeout: int | None,
        remaining: float,
    ) -> int:
        """单跳超时（秒），委托给唯一实现 ``resolve_hop_timeout``（设计 §6.1 规则 5）。"""
        return resolve_hop_timeout(tier, route, provider.timeout, caller_timeout, remaining)

    @staticmethod
    def _is_rate_limited(exc: Exception) -> bool:
        """请求被上游限流(HTTP 429)时返回 True。"""
        response = getattr(exc, "response", None)
        return getattr(response, "status_code", None) == 429

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        """请求在超时预算内未拿到响应(连接/读取超时)时返回 True。"""
        return isinstance(exc, requests.exceptions.Timeout)

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        json_mode: bool = False,
        timeout: int | None = None,
        total_timeout: float | None = None,
        task_tier: str = "standard",
    ) -> LLMResponse:
        """发送聊天请求，自动故障切换 + 令牌桶限流 + 指数退避重试

        Args:
            messages: 消息列表
            model: 指定模型（覆盖 Provider 默认模型与档位路由的 model 覆盖，最高优先级）
            temperature: 温度参数
            json_mode: 是否启用JSON输出模式
            timeout: 单 Provider 超时时间（秒），会被总预算进一步收紧
            total_timeout: 本次调用的总预算（秒）；缺省按档位解析（standard 档继承
                ``LLM_TOTAL_TIMEOUT``，默认 25s）
            task_tier: 任务难度档位（deep / standard / light，大小写与空白归一化）。
                **默认 standard，其链恒等于 self._providers 全链原序 → 不传此参数时
                行为与今日完全一致（零回归）**。未知值回落 standard 并 WARN 一次。

        Returns:
            LLMResponse

        Raises:
            RuntimeError: 所有Provider均不可用，或总预算耗尽
        """
        # ① 归一化档位（D8：绝不抛异常）
        tier = self._normalize_tier(task_tier)

        # ② P0:LLM 结果缓存(命中直接返回,不消耗免费额度)。key 含 tier（F3）
        cached = _llm_cache.get(messages, model, temperature, json_mode, tier)
        if cached is not None:
            logger.debug(
                f"[llm-routing] tier={tier} 缓存命中 provider={cached.provider} model={cached.model}"
            )
            # 缓存命中也要写 last_routing，否则 AC12 会误判"没走 deep"
            _recorder.record(
                tier=tier, provider=cached.provider, model=cached.model,
                latency_ms=0.0, fallback_count=0, cached=True,
                budget_seconds=self._resolve_budget(tier, total_timeout),
            )
            return cached

        # ③ 预算：阶段二 R-09(2026-11-06)单次 chat 的端到端总预算；本增量把来源换成
        # 档位预算（standard 档仍继承 LLM_TOTAL_TIMEOUT，与今日逐字节一致）。
        # 原实现逐个 Provider 串行等待,3~4 个 30s Provider 会让前端空等 90s+;
        # 现在剩余预算会实时收紧每个 Provider 的超时,预算耗尽立即失败。
        budget = self._resolve_budget(tier, total_timeout)
        budget = max(budget, 0.0)
        deadline = time.monotonic() + budget

        # ④ 取链（已含归一化）。standard / 旁路开关 → route 全为 None
        routes = self._routes_for_tier(tier)

        last_error = None
        last_error_provider: LLMProvider | None = None
        budget_exhausted = False
        fallback_count = 0  # ⑤ 本次调用在链上放弃的跳数（仅用于可观测，不改变行为）
        for provider, route in routes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                budget_exhausted = True
                break
            # 与原实现一致：disabled/熔断静默跳过，不计入 fallback
            if not provider.is_available:
                continue

            # 令牌桶限流:获取令牌,超时则跳过该Provider
            bucket = self._token_buckets.get(provider.name)
            if bucket and not bucket.acquire(timeout=min(10.0, max(remaining, 0.1))):
                logger.warning(f"LLM Provider [{provider.name}] 令牌桶限流超时,跳过")
                fallback_count += 1
                continue

            # ⑥ 逐跳模型解析（矛盾 2）：显式 model > route.model > provider.model
            # 降级到下一跳时按下一跳自己的 route.model 重新计算，不存在"沿链继承"。
            hop_model = model if model is not None else (route.model if route is not None else None)

            # 指数退避重试(1s → 2s → 4s)
            max_attempts = min(provider.max_retries + 1, 3)
            for attempt in range(max_attempts):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    budget_exhausted = True
                    break
                # ⑦ 单跳超时（设计 §4.2/§6.1 规则 5）：一律经 resolve_hop_timeout，
                # 禁止在此手写 min(...)。B5 的 LLM_TIER_HOP_TIMEOUT_<TIER> 也由它消费。
                call_timeout = self._hop_timeout(tier, route, provider, timeout, remaining)
                try:
                    logger.debug(
                        f"尝试 LLM Provider: {provider.name} (第{attempt+1}次) tier={tier}"
                    )
                    response = self._call_provider(
                        provider, messages,
                        model=hop_model, temperature=temperature,
                        json_mode=json_mode, timeout=call_timeout,
                    )
                    provider.record_success()
                    # P0:写入 LLM 结果缓存(下次相同 prompt 直接命中);key 含 tier
                    _llm_cache.set(messages, model, temperature, json_mode, response, tier)
                    _recorder.record(
                        tier=tier, provider=provider.name, model=response.model,
                        latency_ms=response.latency_ms, fallback_count=fallback_count,
                        cached=False, budget_seconds=budget,
                    )
                    logger.info(
                        f"[llm-routing] tier={tier} provider={provider.name} "
                        f"model={response.model} fallback={fallback_count} "
                        f"latency_ms={response.latency_ms:.1f} budget={budget:.0f}s cached=False"
                    )
                    return response
                except Exception as e:
                    last_error = e
                    last_error_provider = provider
                    safe_error = self._sanitize_error(e, provider)
                    # 429 是上游限流而非 Provider 故障:官方建议平台过载时拉开重试间隔,
                    # 1s/2s 原地重试救不回来(线上实测白等 37.5s 才降级),直接切下一个。
                    # 超时同理:该 Provider 已耗满超时预算(线上实测首个 Provider 白等 30s),
                    # 再原地重试只会让用户等待翻倍。
                    if self._is_rate_limited(e):
                        switch_reason = "触发限流(429)"
                    elif self._is_timeout(e):
                        switch_reason = "请求超时"
                    else:
                        switch_reason = None
                    if switch_reason:
                        provider.record_failure()
                        fallback_count += 1
                        logger.warning(f"LLM Provider [{provider.name}] {switch_reason},跳过重试切换下一个: {safe_error}")
                        break
                    if attempt < max_attempts - 1:
                        delay = provider.retry_delay * (2 ** attempt)
                        # 重试等待同样不能突破总预算
                        delay = max(0.0, min(delay, deadline - time.monotonic()))
                        logger.warning(f"LLM Provider [{provider.name}] 第{attempt+1}次失败: {safe_error},{delay:.1f}s后重试")
                        if delay > 0:
                            time.sleep(delay)
                    else:
                        provider.record_failure()
                        fallback_count += 1
                        logger.warning(f"LLM Provider [{provider.name}] 全部{max_attempts}次重试失败: {safe_error}")
            if budget_exhausted:
                break

        # 修复(2026-07-31): 所有 Provider 不可用时返回明确错误。
        # 当所有 Provider 被熔断或未配置时,循环不执行,last_error 仍为 None,
        # 原"最后错误: None"信息含糊。区分"有 Provider 调用失败"和"无可用 Provider"两种情况。
        # 注意:三条文案模板保持不变（既有用例 match 子串依赖）。
        _recorder.record(
            tier=tier, provider=None, model=None, latency_ms=0.0,
            fallback_count=fallback_count, cached=False, budget_seconds=budget,
            budget_exhausted=budget_exhausted,
            error="budget_exhausted" if budget_exhausted else "all_failed",
        )
        if last_error is not None:
            safe_last = (
                self._sanitize_error(last_error, last_error_provider)
                if last_error_provider is not None else "unknown"
            )
            raise RuntimeError(f"所有LLM Provider均不可用，最后错误: {safe_last}")
        if budget_exhausted:
            raise RuntimeError(f"LLM 总预算 {budget:.0f}s 已耗尽，未拿到有效响应")
        raise RuntimeError("所有LLM Provider均不可用: 无可用Provider(全部被熔断或未配置)")

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

    @staticmethod
    def _sanitize_error(error: Exception, provider: LLMProvider) -> str:
        """错误信息脱敏:剔除可能被上游回显的 API Key,并截断长度"""
        text = str(error) or error.__class__.__name__
        for key in [provider.api_key, *provider.api_keys]:
            if key and len(key) >= 8:
                text = text.replace(key, "***")
        return text[:200]

    def probe(
        self,
        name: str | None = None,
        timeout: int = 20,
        tier: str | None = None,
    ) -> dict:
        """对指定(或全部/某档位)Provider 发一条最小请求,验证 Key/网络/模型是否可用

        与 chat() 的区别:不做故障切换、不读不写响应缓存、不重试,
        每个 Provider 只打一次并原样返回失败原因,用于定位"哪个 Key 没配好"。

        Args:
            name: 指定 Provider 名；与 ``tier`` 同时给出时 ``name`` 优先。
            timeout: 单次探活超时（秒）。
            tier: 按难度档位探活（deep/standard/light），按该档的有序链逐跳打一次。
                逐跳会使用该档的 ``route.model`` 覆盖（例如 deep 的 agnes 用
                ``agnes-3.0-flash``），从而真实验证"档位链是否可用"。
        """
        resolved_tier: str | None = None
        if name:
            pairs: list[tuple[LLMProvider, TierRoute | None]] = [
                (p, None) for p in self._providers if p.name == name
            ]
            if not pairs:
                return {"status": "error", "message": f"未配置 Provider: {name}", "results": []}
        elif tier is not None:
            resolved_tier = self._normalize_tier(tier)
            pairs = self._routes_for_tier(resolved_tier)
            if not pairs:
                return {
                    "status": "error",
                    "message": f"档位 {resolved_tier} 无可用 Provider",
                    "tier": resolved_tier,
                    "results": [],
                }
        else:
            pairs = [(p, None) for p in self._providers]
            if not pairs:
                return {"status": "error", "message": "未配置任何 Provider", "results": []}

        results = []
        for provider, route in pairs:
            bucket = self._token_buckets.get(provider.name)
            if bucket and not bucket.acquire(timeout=3):
                results.append({
                    "provider": provider.name, "model": provider.model, "ok": False,
                    "latency_ms": 0, "error": "令牌桶限流中,请稍后重试",
                })
                continue
            # 档位探活需要按 route.model 覆盖（显式 name 探活保持 provider.model）
            hop_model = route.model if route is not None else None
            start = time.monotonic()
            try:
                resp = self._call_provider(
                    provider,
                    [{"role": "user", "content": "ping"}],
                    model=hop_model,
                    temperature=0.0,
                    timeout=timeout,
                )
                provider.record_success()
                results.append({
                    "provider": provider.name,
                    "model": resp.model,
                    "ok": True,
                    "latency_ms": round(resp.latency_ms or (time.monotonic() - start) * 1000, 1),
                    "sample": (resp.content or "")[:40],
                })
            except Exception as e:
                provider.record_failure()
                results.append({
                    "provider": provider.name,
                    "model": hop_model or provider.model,
                    "ok": False,
                    "latency_ms": round((time.monotonic() - start) * 1000, 1),
                    "error": self._sanitize_error(e, provider),
                })

        payload = {
            "status": "ok",
            "total": len(results),
            "ok_count": sum(1 for r in results if r["ok"]),
            "results": results,
        }
        if resolved_tier is not None:
            payload["tier"] = resolved_tier
        return payload

    def health_check(self) -> dict:
        """检查所有Provider的健康状态

        Returns:
            {provider_name: {"available": bool, "model": str, "priority": int, ...}}

        2026-11-07 增量：新增 ``same_upstream_as``（F1/L2）。``zhipu_glm_45`` 与
        ``zhipu_glm`` 共享同一上游与同一 30 rpm 配额，二者相邻出现**不构成冗余**，
        显式标注避免运维误判。值为 None 表示该 Provider 无同源关系。
        """
        result = {}
        for provider in self._providers:
            result[provider.name] = {
                "available": provider.is_available,
                "model": provider.model,
                "base_url": provider.base_url,
                "priority": provider.priority,
                "fail_count": provider._fail_count,
                "circuit_open": provider._circuit_open,
                "rpm": provider.rpm,
                "has_multi_keys": len(provider.api_keys) > 1,
                "provider_type": provider.provider_type.value,
                "free_tier": provider.free_tier,
                "same_upstream_as": SAME_UPSTREAM.get(provider.name),
            }
        return result

    def routing_health(self) -> dict:
        """难度分层路由的可观测快照（**绝不含 api_key / headers / query**）。

        Returns:
            {
              "enabled": bool,                     # LLM_TIER_ROUTING_ENABLED 旁路开关
              "tiers": {tier: [provider_name, ...]},        # 各档有序链（AC9/AC11）
              "routes": {tier: [{provider, model, label, available, same_upstream_as}]},
              "budgets_seconds": {tier: float},   # 该档当前生效的总预算（含 env 覆盖）
              "same_upstream_groups": {provider: [同源 provider...]},
              "last_routing": dict,               # 最近一次决策
              "counters": {tier: {calls, success, budget_exhausted, fallback_total}},
            }
        """
        tiers: dict[str, list[str]] = {}
        routes_detail: dict[str, list[dict]] = {}
        budgets: dict[str, float] = {}
        for tier_name in TIER_NAMES:
            pairs = self._routes_for_tier(tier_name)
            tiers[tier_name] = [provider.name for provider, _route in pairs]
            detail: list[dict] = []
            for provider, route in pairs:
                detail.append({
                    "provider": provider.name,
                    "model": (route.model if route is not None else None) or provider.model,
                    "label": route.display if route is not None else provider.name,
                    "available": provider.is_available,
                    "same_upstream_as": SAME_UPSTREAM.get(provider.name),
                })
            routes_detail[tier_name] = detail
            budgets[tier_name] = round(self._resolve_budget(tier_name, None), 1)

        same_upstream_groups: dict[str, list[str]] = {}
        for provider_name, upstream in SAME_UPSTREAM.items():
            same_upstream_groups.setdefault(upstream, []).append(provider_name)

        return {
            "enabled": routing_enabled(),
            "tiers": tiers,
            "routes": routes_detail,
            "budgets_seconds": budgets,
            "budget_env_keys": {tier_name: tier_budget_env_key(tier_name) for tier_name in TIER_NAMES},
            # B5：单跳上限的可选 env 覆盖（默认未设置 → None，即走 TierRoute.timeout_cap）
            "hop_caps_seconds": {tier_name: hop_cap_seconds(tier_name) for tier_name in TIER_NAMES},
            "hop_cap_env_keys": {tier_name: hop_timeout_env_key(tier_name) for tier_name in TIER_NAMES},
            "same_upstream_groups": same_upstream_groups,
            "last_routing": _recorder.last(),
            "counters": _recorder.snapshot(),
            "cache": _llm_cache.stats(),
            "default_tier": DEFAULT_TIER,
        }


# 全局单例
_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """获取LLM路由器单例"""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router

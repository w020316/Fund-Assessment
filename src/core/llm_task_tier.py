"""任务难度分层路由的纯数据与纯函数定义（零第三方依赖、零 os.getenv 副作用）。

设计约束（见 docs/llm-task-routing-design-2026-11-07.md）：
- 只描述"哪个档位该用哪些 (provider, model)"，不持有一分钱运行时状态
- 不 import src.core.llm_router（避免循环依赖）；对 Provider 对象仅做鸭子类型（name/model）
- 不新增 Provider（D7）：所有 provider 名必须已存在于 _load_from_env

三档语义：
- ``deep``     ：7 智能体深度决策（多角色/多步骤推理 + 约束下裁决），预算 60s
- ``standard`` ：保底档，链在运行时动态等于 ``LLMRouter._providers`` 全链原序，预算继承
                 ``LLM_TOTAL_TIMEOUT``（默认 25s），保证"不传 tier 时行为与今日逐字节一致"
- ``light``    ：短文本生成/摘要，预算 15s
"""
from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

TIER_DEEP = "deep"
TIER_STANDARD = "standard"
TIER_LIGHT = "light"
DEFAULT_TIER = TIER_STANDARD

#: 全部合法档名（顺序即 health/routing 输出顺序）
TIER_NAMES: tuple[str, ...] = (TIER_DEEP, TIER_STANDARD, TIER_LIGHT)


@dataclass(frozen=True)
class TierRoute:
    """一条路由条目：把某个 provider 按指定模型使用。"""
    provider: str                     # 必须精确等于 LLMProvider.name（大小写敏感）
    model: str | None = None          # None = 沿用 provider.model（保留 ZHIPU_MODEL 等 env 覆盖）
    label: str = ""                   # 展示名（health/日志/AC 命名），空则用 provider
    timeout_cap: int | None = None    # 该跳硬上限（秒），None = 用 provider.timeout

    @property
    def display(self) -> str:
        return self.label or self.provider


@dataclass(frozen=True)
class TierSpec:
    """一个难度档位的规格。"""
    name: str
    budget_seconds: float | None = None          # None = 继承 LLM_TOTAL_TIMEOUT（默认 25s）
    routes: tuple[TierRoute, ...] | None = None  # None = 动态继承 self._providers 全链（仅 standard）

    @property
    def is_dynamic(self) -> bool:
        return self.routes is None


# ===== 三档映射（唯一事实源；改这里必须过 code review，不进 yaml）=====
TIER_SPECS: dict[str, TierSpec] = {
    # D3 合规优先：首位国内 zhipu_glm（金融持仓数据不出境）。
    # D9 第二顺位 = agnes-3.0-flash（E1 实测：JSON 严格、582 tokens 完整 7 分析师 schema）；
    # ly77625/gemini-3.8-flash 降为**最后兜底**——它是第三方免费中转，额度与数据链路 E4 未验证，
    # 不该以"JSON mode 官方未明确支持"的 GLM 之外的身份抢占第二顺位。
    # D2 总预算 60s：Render 免费层请求上限 ~100s，需留部署开销；deep 实测 9.7~16.5s。
    # D12 单跳上限 20/25/20，合计 65 ≥ 预算 60 —— 保证「首选失败 → 次选 → 末位」在前两跳
    # 快速失败时第 3 跳仍能拿到时间（若三跳都用 provider.timeout=30，30+30 就把 60s 打满，
    # 名义 3 跳实际只有 2 跳，这是 D12 要修的缺陷）。
    # L1：注意首位与 standard/light 首位相同（首跳同源），分层只分层降级链与预算。
    # L2：zhipu_glm_45 与 zhipu_glm 同源，故不得出现在 deep 链。
    TIER_DEEP: TierSpec(
        name=TIER_DEEP,
        budget_seconds=60.0,
        routes=(
            TierRoute("zhipu_glm", None, "zhipu_glm", timeout_cap=20),
            TierRoute("agnes", "agnes-3.0-flash", "agnes_30", timeout_cap=25),
            TierRoute("ly77625", None, "ly77625", timeout_cap=20),
        ),
    ),
    # standard = 保底档：不承担分层职责，链恒等于 self._providers 全链原序（矛盾 1 裁决）。
    # budget_seconds=None → 继承 LLM_TOTAL_TIMEOUT，保证"线上改过该 env 也不影响旧行为"。
    # B5 裁决：**不设任何单跳 cap**，保持今日 min(caller_timeout, provider.timeout) 公式；
    # 否则 standard 的单跳超时会从 provider.timeout(30) 掉到 15，与"零回归"冲突。
    TIER_STANDARD: TierSpec(name=TIER_STANDARD, budget_seconds=None, routes=None),
    # light：最高频、失败代价最低；D5 排除 agnes-2.5-flash；PRD light 顺位3 的 agnes-2.0-flash
    # 因该模型 ID 已下线（D11：_load_from_env 会自动纠正为 agnes-3.0-flash）而不入链，实际 2 跳。
    # D12：两跳各 10s（< 15s 总预算），保证第 2 跳一定还有钱。
    TIER_LIGHT: TierSpec(
        name=TIER_LIGHT,
        budget_seconds=15.0,
        routes=(
            TierRoute("zhipu_glm", None, "zhipu_glm", timeout_cap=10),
            TierRoute("agnes", "agnes-3.0-flash", "agnes_30", timeout_cap=10),
        ),
    ),
}

# 全档排除对象（仅用于单测与文档，运行时不会出现在任何 tier 链上）
# D1：agnes-2.5-pro-* 从未在 _load_from_env 注册（无对应 Provider），此处只做声明式登记。
# D5：agnes-2.5-flash 慢 + 推理 token 浪费 + 趋势判断错误，全档排除。
GLOBAL_EXCLUDED_MODELS: frozenset[str] = frozenset({
    "agnes-2.5-flash",
    "agnes-2.5-pro-alpha",
    "agnes-2.5-pro-beta",
})
# 全档排除 provider（D4：ttapi 平台已 404；此处仅声明，链上本就不含）
GLOBAL_EXCLUDED_PROVIDERS: frozenset[str] = frozenset({"ttapi"})
# 同源关系（F1/L2）：值表示"与谁共享同一上游与配额"，不构成冗余
SAME_UPSTREAM: dict[str, str] = {"zhipu_glm_45": "zhipu_glm"}

# ===== D11：平台级"已下线模型 ID → 当前可用 ID"纠正表 =====
# 键格式 "provider:model"（精确匹配，大小写敏感）。
# 存在理由：线上 AGNES_MODEL=agnes-2.0-flash 是【环境变量】，
# 只改 os.getenv("AGNES_MODEL", "agnes-3.0-flash") 的默认值对它【完全无效】。
# 收录标准（主理人 2026-11-07 裁定②）：**只收"平台已下线"的 ID**。
#   - agnes-2.0-flash  ：已下架（HTTP 500，/v1/models 不再列出）→ 收
#   - agnes-2.5-flash  ：**未下线**（只是实测慢、判题错，已从所有 tier 链排除 P2 backlog）；
#                        用户若显式把 AGNES_MODEL 设为它是用户意图，不得静默改写 → 不收
#   - agnes-2.5-pro-*  ：403 是「额度 $0」而非 ID 下线，且属 P2（不付费）→ 不收
# 新增条目必须附带"已下线"的证据（实测状态码 + 官方下架说明）。
KNOWN_MODEL_REPLACEMENTS: dict[str, str] = {
    "agnes:agnes-2.0-flash": "agnes-3.0-flash",
}

_WARNED_TIERS: set[str] = set()


def correct_model_id(provider_name: str, model: str, warn=None) -> str:
    """把"已知不可用"的模型 ID 纠正为当前可用 ID（D11），并 WARN 提示同步 env。

    这是 D11 的**第一道保险**；第二道是 ``TierRoute.model``（路由条目显式覆盖）。
    两道独立生效：即使将来有人把纠正表删了，deep/light 仍会用 agnes-3.0-flash。

    Args:
        provider_name: Provider 名（映射键前缀）。
        model: 从环境变量解析出的模型 ID。
        warn: 可选 WARN 回调（由 router 注入 logger.warning），默认走 logging。

    Returns:
        可用的模型 ID；未命中映射时原样返回。
    """
    key = f"{provider_name}:{(model or '').strip()}"
    target = KNOWN_MODEL_REPLACEMENTS.get(key)
    if not target:
        return model
    (warn or _default_warn)(
        f"Provider [{provider_name}] 配置的模型 {model} 已不可用，自动纠正为 {target}"
        f"（请同步更新环境变量，否则下次仍会走纠正表）"
    )
    return target


def routing_enabled() -> bool:
    """全局旁路开关：LLM_TIER_ROUTING_ENABLED=0 时所有档位退化为全链（线上回滚用）。"""
    return os.getenv("LLM_TIER_ROUTING_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def normalize_tier(task_tier: Any, warn=None) -> str:
    """把任意入参归一化为合法档名；未知值回落 DEFAULT_TIER 并只 WARN 一次。

    D8：绝不抛异常。warn 为可选回调（由 router 传入 logger.warning），便于纯函数单测。
    """
    if task_tier is None:
        return DEFAULT_TIER
    if not isinstance(task_tier, str):
        _warn_once(
            f"<non-str:{type(task_tier).__name__}>",
            f"未知 task_tier={task_tier!r}，回落 {DEFAULT_TIER}",
            warn,
        )
        return DEFAULT_TIER
    key = task_tier.strip().lower()
    if key in TIER_SPECS:
        return key
    _warn_once(key or "<empty>", f"未知 task_tier={task_tier!r}，回落 {DEFAULT_TIER}", warn)
    return DEFAULT_TIER


def _warn_once(key: str, message: str, warn=None) -> None:
    """同一非法值只 WARN 一次（防日志刷屏）。"""
    if key in _WARNED_TIERS:
        return
    _WARNED_TIERS.add(key)
    (warn or _default_warn)(message)


def _default_warn(message: str) -> None:  # pragma: no cover - 正常由 router 注入 logger
    import logging
    logging.getLogger(__name__).warning(message)


def reset_warn_cache() -> None:
    """测试用：清空 WARN 去重缓存。"""
    _WARNED_TIERS.clear()


def resolve_budget_seconds(tier: str, total_timeout: float | None = None) -> float:
    """预算优先级：显式入参 > LLM_TIER_TIMEOUT_<TIER> env > 档位常量 > LLM_TOTAL_TIMEOUT env(默认25)。"""
    if total_timeout is not None:
        return max(float(total_timeout), 0.0)
    spec = TIER_SPECS.get(tier) or TIER_SPECS[DEFAULT_TIER]
    env_key = f"LLM_TIER_TIMEOUT_{tier.upper()}"
    raw = os.getenv(env_key)
    if raw:
        try:
            return max(float(raw), 0.0)
        except ValueError:
            pass  # 非法值忽略，落回常量/全局默认
    if spec.budget_seconds is not None:
        return max(float(spec.budget_seconds), 0.0)
    return max(float(os.getenv("LLM_TOTAL_TIMEOUT", "25")), 0.0)


def match_routes(
    spec: TierSpec,
    providers: list[Any],
) -> list[tuple[Any, TierRoute | None]]:
    """把档位规格解析为 [(provider_obj, route_or_None)]。

    - spec.is_dynamic（standard）→ 全链，(p, None)，元素为原对象（不 copy）
    - 否则按 spec.routes 顺序精确匹配 provider.name；未加载的跳过
    - 若一条都没匹配上 → 回落全链（fail-open），由调用方负责 WARN

    返回的是**新列表**（可安全 pop），元素是 providers 中的**原对象**
    （熔断状态/令牌桶/失败计数必须唯一实例共享）。
    """
    if spec.is_dynamic:
        return [(p, None) for p in providers]
    by_name = {p.name: p for p in providers}
    out: list[tuple[Any, TierRoute | None]] = []
    for route in spec.routes or ():
        provider = by_name.get(route.provider)
        if provider is None:
            continue
        out.append((provider, route))
    if not out:
        return [(p, None) for p in providers]
    return out


def tier_budget_env_key(tier: str) -> str:
    """返回某档位对应的预算覆盖环境变量名（供 health/文档展示）。"""
    return f"LLM_TIER_TIMEOUT_{tier.upper()}"


def resolve_hop_timeout(
    tier: str,
    route: TierRoute | None,
    provider_timeout: int,
    caller_timeout: int | None,
    remaining: float,
) -> int:
    """单跳超时（秒）。``route is None`` ⇒ 逐字节复用今日公式（零回归路径）。

    优先级（数值上取 min，越小越严）：
      1. caller_timeout / provider_timeout（今日语义，保持）
      2. route.timeout_cap（D12 的逐跳封顶：deep 20/25/20，light 10/10）
      3. remaining（链总预算剩余的硬夹逼，防超出前端 90s）

    下限 1s（与今日 ``max(1, ...)`` 一致）。

    **这是单跳超时的唯一实现**：``chat()`` 内禁止再手写 ``min(...)``（设计 §6.1 规则 5）。
    ``tier`` 仅用于签名对齐设计 §4.2 的 ``resolve_hop_timeout(tier, route, ...)``；
    本期不读 ``LLM_TIER_HOP_TIMEOUT_<TIER>``（主理人 2026-11-07 裁定②：不引入该配置面，
    B5 的结论就是 "standard 不设单跳 cap"；若将来要"standard 各 15s"再在此加一层 env）。
    """
    if route is None:
        # standard / 全局旁路 / kill switch —— 今日公式，逐字节不变
        base = caller_timeout if caller_timeout is not None else provider_timeout
        return max(1, int(min(base, remaining)))
    caps = [caller_timeout if caller_timeout is not None else provider_timeout]
    if route.timeout_cap:
        caps.append(route.timeout_cap)
    caps.append(remaining)
    return max(1, int(min(caps)))


class RoutingRecorder:
    """记录最近一次路由决策 + 分档计数。

    线程安全；仅存非敏感字段（绝不存 api_key / headers / base_url query）。
    ``last()`` / ``snapshot()`` 一律返回深拷贝副本，避免 FastAPI 多线程并发读时
    迭代期被改写。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict = {}
        self._counters: dict[str, dict] = {}

    def record(
        self,
        *,
        tier: str,
        provider: str | None,
        model: str | None,
        latency_ms: float,
        fallback_count: int,
        cached: bool,
        budget_seconds: float,
        budget_exhausted: bool = False,
        error: str | None = None,
    ) -> None:
        """记录一次路由决策（成功、缓存命中、或全链失败）。

        Args:
            tier: 归一化后的档名（deep/standard/light）。
            provider: 实际命中的 Provider 名；全链失败时为 None。
            model: 实际发给上游的模型名；未知时可为 None。
            latency_ms: 本次调用耗时（毫秒），缓存命中传 0.0。
            fallback_count: 本次调用在链上放弃的跳数。
            cached: 是否缓存命中。
            budget_seconds: 本次调用生效的总预算（秒）。
            budget_exhausted: 是否因预算耗尽而终止。
            error: 失败原因标签（"all_failed" / "budget_exhausted"），成功为 None。
        """
        entry = {
            "tier": tier,
            "provider": provider,
            "model": model,
            "latency_ms": round(float(latency_ms or 0.0), 1),
            "fallback_count": int(fallback_count or 0),
            "cached": bool(cached),
            "budget_seconds": round(float(budget_seconds or 0.0), 1),
            "budget_exhausted": bool(budget_exhausted),
            "success": error is None,
            "error": error,
            # 仅用于展示（不参与任何时间差计算，差值一律用 time.monotonic()）
            "recorded_at": round(time.time(), 3),
        }
        with self._lock:
            self._last = entry
            counter = self._counters.setdefault(
                tier,
                {"calls": 0, "success": 0, "budget_exhausted": 0, "fallback_total": 0},
            )
            counter["calls"] += 1
            if error is None:
                counter["success"] += 1
            if entry["budget_exhausted"]:
                counter["budget_exhausted"] += 1
            counter["fallback_total"] += entry["fallback_count"]

    def last(self) -> dict:
        """最近一次路由决策的副本（无记录时返回空 dict）。"""
        with self._lock:
            return copy.deepcopy(self._last)

    def snapshot(self) -> dict:
        """按档位聚合的计数副本：``{tier: {calls, success, budget_exhausted, fallback_total}}``。"""
        with self._lock:
            return copy.deepcopy(self._counters)

    def reset(self) -> None:
        """清空全部记录（测试用）。"""
        with self._lock:
            self._last = {}
            self._counters = {}

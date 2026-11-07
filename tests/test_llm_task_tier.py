"""T01 契约层单测：任务难度分层路由（`src/core/llm_task_tier.py` + `LLMRouter` 派生视图）。

覆盖：
- AC2  `_providers_for_tier("standard") == list(router._providers)`（逐元素、同序、同一对象）
- AC3  派生视图零副作用（不改 `_providers` 顺序/长度/字段）
- AC4' `deep` 链 = zhipu_glm → ly77625 → agnes(label=agnes_30)，且不含 zhipu_glm_45
- AC8  未知 tier → 回落 standard + 恰好 1 条 WARN，绝不抛异常
- D1/D5 三档链均不含 agnes-2.5-flash / agnes-2.5-pro-*
- C-6/C-7 部分缺失跳过、全缺失 fail-open 回落全链
- C-8  `LLM_TIER_ROUTING_ENABLED=0` → 任何档位都返回全链
- 预算解析优先级（§4.1）

全部离线：只构造 Router 与常量，不发任何网络请求。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core import llm_task_tier as tier_mod
from src.core.llm_router import LLMProvider, LLMRouter, ProviderType
from src.core.llm_task_tier import (
    DEFAULT_TIER,
    GLOBAL_EXCLUDED_MODELS,
    GLOBAL_EXCLUDED_PROVIDERS,
    SAME_UPSTREAM,
    TIER_DEEP,
    TIER_LIGHT,
    TIER_SPECS,
    TIER_STANDARD,
    TierRoute,
    TierSpec,
    hop_cap_seconds,
    normalize_tier,
    resolve_budget_seconds,
    routing_enabled,
)

# 线上等价的 4 Provider 环境（PRD §1：zhipu_glm / zhipu_glm_45 / agnes / ly77625）
_ONLINE_ENV = {
    "ZHIPU_API_KEY": "zk",
    "AGNES_API_KEY": "ak",
    "LY77625_API_KEY": "lk",
}


@pytest.fixture(autouse=True)
def _reset_tier_state():
    """每个用例前后清空 WARN 去重缓存与路由记录器，保证断言确定性。"""
    from src.core.llm_router import _recorder

    tier_mod.reset_warn_cache()
    _recorder.reset()
    yield
    tier_mod.reset_warn_cache()
    _recorder.reset()


def _online_router() -> LLMRouter:
    with patch.dict("os.environ", _ONLINE_ENV, clear=True):
        return LLMRouter()


# ===== 纯函数：normalize_tier / resolve_budget_seconds =====


class TestNormalizeTier:
    def test_none_maps_to_standard(self):
        assert normalize_tier(None) == TIER_STANDARD
        assert DEFAULT_TIER == TIER_STANDARD

    @pytest.mark.parametrize("raw", ["deep", "DEEP", " Deep ", "\tdeep\n"])
    def test_deep_variants_normalized(self, raw):
        assert normalize_tier(raw) == TIER_DEEP

    @pytest.mark.parametrize("raw", ["light", "Light", " LIGHT "])
    def test_light_variants_normalized(self, raw):
        assert normalize_tier(raw) == TIER_LIGHT

    @pytest.mark.parametrize("raw", ["standard", "STANDARD", " Standard"])
    def test_standard_variants_normalized(self, raw):
        assert normalize_tier(raw) == TIER_STANDARD

    def test_unknown_string_falls_back_with_exactly_one_warn(self):
        """AC8：未知 tier 回落 standard，且同一非法值只 WARN 一次。"""
        warnings: list[str] = []
        assert normalize_tier("bogus", warn=warnings.append) == TIER_STANDARD
        assert normalize_tier("bogus", warn=warnings.append) == TIER_STANDARD
        assert normalize_tier("bogus", warn=warnings.append) == TIER_STANDARD
        assert len(warnings) == 1
        assert "bogus" in warnings[0]

    def test_empty_string_falls_back_with_warn(self):
        warnings: list[str] = []
        assert normalize_tier("", warn=warnings.append) == TIER_STANDARD
        assert normalize_tier("   ", warn=warnings.append) == TIER_STANDARD
        # 空串与全空白归一化后同为 ""，只 WARN 一次
        assert len(warnings) == 1

    @pytest.mark.parametrize("raw", [123, [], {}, 3.5, object()])
    def test_non_string_never_raises(self, raw):
        """D8：绝不对非字符串入参抛异常。"""
        assert normalize_tier(raw, warn=lambda _m: None) == TIER_STANDARD

    def test_warn_cache_reset_allows_new_warn(self):
        warnings: list[str] = []
        normalize_tier("zzz", warn=warnings.append)
        tier_mod.reset_warn_cache()
        normalize_tier("zzz", warn=warnings.append)
        assert len(warnings) == 2


class TestResolveBudgetSeconds:
    def test_deep_default_is_60(self):
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_budget_seconds(TIER_DEEP) == 60.0

    def test_light_default_is_15(self):
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_budget_seconds(TIER_LIGHT) == 15.0

    def test_standard_inherits_total_timeout(self):
        """§4.1：standard 的 budget_seconds=None → 继承 LLM_TOTAL_TIMEOUT（默认 25）。"""
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_budget_seconds(TIER_STANDARD) == 25.0
        with patch.dict("os.environ", {"LLM_TOTAL_TIMEOUT": "40"}, clear=True):
            # 线上若把该 env 改成 40，旧调用行为必须保持 40（零回归）
            assert resolve_budget_seconds(TIER_STANDARD) == 40.0

    def test_explicit_total_timeout_wins(self):
        with patch.dict("os.environ", {"LLM_TOTAL_TIMEOUT": "40"}, clear=True):
            assert resolve_budget_seconds(TIER_DEEP, 5) == 5.0

    def test_env_override_beats_constant(self):
        with patch.dict("os.environ", {"LLM_TIER_TIMEOUT_DEEP": "90"}, clear=True):
            assert resolve_budget_seconds(TIER_DEEP) == 90.0

    def test_illegal_env_value_ignored(self):
        with patch.dict("os.environ", {"LLM_TIER_TIMEOUT_DEEP": "abc"}, clear=True):
            assert resolve_budget_seconds(TIER_DEEP) == 60.0

    def test_negative_values_clamped_to_zero(self):
        assert resolve_budget_seconds(TIER_DEEP, -3) == 0.0
        with patch.dict("os.environ", {"LLM_TIER_TIMEOUT_LIGHT": "-1"}, clear=True):
            assert resolve_budget_seconds(TIER_LIGHT) == 0.0


# ===== 映射表常量断言（AC4' / D1 / D5 / D6）=====


class TestTierSpecsConstants:
    def test_deep_chain_order_is_compliance_first(self):
        """AC4'（覆盖 PRD AC4 + D9）：zhipu_glm 首位，agnes-3.0-flash 第二，ly77625 最后兜底。"""
        routes = TIER_SPECS[TIER_DEEP].routes
        assert routes is not None
        assert [r.provider for r in routes] == ["zhipu_glm", "agnes", "ly77625"]

    def test_deep_agnes_uses_model_override_and_label(self):
        """D6/D7：agnes 用 model 覆盖代替新增 Provider，label 固定 agnes_30。"""
        routes = TIER_SPECS[TIER_DEEP].routes
        assert routes is not None
        agnes = routes[1]
        gemini = routes[2]
        assert agnes.model == "agnes-3.0-flash"
        assert agnes.display == "agnes_30"
        # 第 1、3 跳 route.model=None → 沿用 Provider 自身 model（保留 ZHIPU_MODEL 等 env 覆盖）
        assert routes[0].model is None
        assert gemini.provider == "ly77625" and gemini.model is None

    def test_deep_excludes_zhipu_glm_45(self):
        """P0-6：zhipu_glm_45 与 zhipu_glm 同源，禁止进 deep 链。"""
        routes = TIER_SPECS[TIER_DEEP].routes
        assert routes is not None
        assert "zhipu_glm_45" not in [r.provider for r in routes]

    def test_no_paid_model_in_any_tier(self):
        """D1：agnes-2.5-pro-alpha/beta 不入任何链。"""
        for spec in TIER_SPECS.values():
            for route in spec.routes or ():
                assert route.model not in {"agnes-2.5-pro-alpha", "agnes-2.5-pro-beta"}
        assert "agnes-2.5-pro-alpha" in GLOBAL_EXCLUDED_MODELS
        assert "agnes-2.5-pro-beta" in GLOBAL_EXCLUDED_MODELS
        # D4：ttapi 平台已 404，全档排除（保留配置但 enabled=False）
        assert "ttapi" in GLOBAL_EXCLUDED_PROVIDERS

    def test_no_agnes_25_flash_in_any_tier(self):
        """D5：agnes-2.5-flash 从所有档位排除。"""
        for spec in TIER_SPECS.values():
            for route in spec.routes or ():
                assert route.model != "agnes-2.5-flash"

    def test_standard_is_dynamic(self):
        spec = TIER_SPECS[TIER_STANDARD]
        assert spec.is_dynamic is True
        assert spec.routes is None
        assert spec.budget_seconds is None

    def test_light_chain_and_budget(self):
        spec = TIER_SPECS[TIER_LIGHT]
        assert spec.routes is not None
        assert [r.provider for r in spec.routes] == ["zhipu_glm", "agnes"]
        assert spec.routes[1].model == "agnes-3.0-flash"
        assert spec.budget_seconds == 15.0

    def test_deep_budget_is_60_not_90(self):
        """D2：deep 总预算 60s。"""
        assert TIER_SPECS[TIER_DEEP].budget_seconds == 60.0

    def test_same_upstream_table(self):
        """F1/L2：zhipu_glm_45 与 zhipu_glm 同源。"""
        assert SAME_UPSTREAM == {"zhipu_glm_45": "zhipu_glm"}

    def test_tier_route_display_falls_back_to_provider(self):
        assert TierRoute("ly77625").display == "ly77625"
        assert TierRoute("agnes", "m", "").display == "agnes"

    def test_tier_spec_is_dynamic_property(self):
        assert TierSpec(name="x").is_dynamic is True
        assert TierSpec(name="x", routes=()).is_dynamic is False


# ===== Router 派生视图：C-1 ~ C-10（AC2/AC3）=====


class TestProvidersForTier:
    def test_standard_equals_providers_exactly(self):
        """AC2/C-1/C-3：逐元素、同序、**同一对象**，且是新列表。"""
        router = _online_router()
        view = router._providers_for_tier("standard")
        assert view == list(router._providers)
        assert all(a is b for a, b in zip(view, router._providers))
        assert view is not router._providers
        # 返回值可安全 pop，不影响 _providers（C-3）
        view.pop()
        assert len(router._providers) == 4

    def test_none_is_alias_of_standard(self):
        """C-2：None 与 "standard" 返回完全相同的列表。"""
        router = _online_router()
        assert router._providers_for_tier(None) == router._providers_for_tier("standard")

    def test_deep_chain_actual_providers(self):
        """AC4'：线上等价环境下 deep 链 = zhipu_glm → ly77625 → agnes。"""
        router = _online_router()
        assert [p.name for p in router._providers_for_tier("deep")] == ["zhipu_glm", "agnes", "ly77625"]

    def test_light_chain_actual_providers(self):
        router = _online_router()
        assert [p.name for p in router._providers_for_tier("light")] == ["zhipu_glm", "agnes"]

    def test_deep_chain_excludes_ttapi_and_disabled(self):
        """D4：ttapi enabled=False，且不进任何 tier 链。"""
        with patch.dict("os.environ", {**_ONLINE_ENV, "TTAPI_API_KEY": "tk"}, clear=True):
            router = LLMRouter()
        names = [p.name for p in router._providers]
        assert "ttapi" in names  # 仍加载（保留配置）
        ttapi = next(p for p in router._providers if p.name == "ttapi")
        assert ttapi.enabled is False  # 但 chat 会跳过
        assert "ttapi" not in router._providers_for_tier("deep")
        assert "ttapi" not in router._providers_for_tier("light")

    def test_routes_for_tier_carries_model_override(self):
        """矛盾 2：model 覆盖内聚在路由条目上，逐跳解析。"""
        router = _online_router()
        pairs = dict((p.name, r) for p, r in router._routes_for_tier("deep"))
        assert pairs["zhipu_glm"] is not None and pairs["zhipu_glm"].model is None
        assert pairs["agnes"] is not None and pairs["agnes"].model == "agnes-3.0-flash"
        # standard 档全部 route=None
        assert all(r is None for _p, r in router._routes_for_tier("standard"))

    def test_view_has_no_side_effects(self):
        """AC3/C-4：派生视图不修改 _providers 的顺序、长度、字段。"""
        router = _online_router()
        before_names = [p.name for p in router._providers]
        before_ids = [id(p) for p in router._providers]
        for tier in ("deep", "standard", "light", None, "bogus"):
            router._providers_for_tier(tier)
        assert [p.name for p in router._providers] == before_names
        assert [id(p) for p in router._providers] == before_ids

    def test_unknown_tier_returns_standard_chain(self):
        """C-5/AC8：未知 tier 回落 standard 链，不抛异常。"""
        router = _online_router()
        assert router._providers_for_tier("bogus") == list(router._providers)
        assert router._providers_for_tier("") == list(router._providers)
        assert router._providers_for_tier(123) == list(router._providers)  # type: ignore[arg-type]

    def test_unknown_tier_warns_exactly_once(self):
        router = _online_router()
        with patch("src.core.llm_router.logger") as mock_logger:
            router._providers_for_tier("nope")
            router._providers_for_tier("nope")
            router._providers_for_tier("nope")
        warnings = [c for c in mock_logger.warning.call_args_list if "nope" in str(c)]
        assert len(warnings) == 1

    def test_partial_missing_provider_skipped(self):
        """C-6：缺失的 Provider 跳过，链变短，不报错。"""
        with patch.dict("os.environ", {"ZHIPU_API_KEY": "zk"}, clear=True):
            router = LLMRouter()
        assert [p.name for p in router._providers] == ["zhipu_glm", "zhipu_glm_45"]
        assert [p.name for p in router._providers_for_tier("deep")] == ["zhipu_glm"]
        assert [p.name for p in router._providers_for_tier("light")] == ["zhipu_glm"]

    def test_all_mapped_missing_falls_back_to_full_chain(self):
        """C-7：映射里的 Provider 一个都没加载 → fail-open 回落全链 + WARN。"""
        with patch.dict("os.environ", {"GROQ_API_KEY": "gk"}, clear=True):
            router = LLMRouter()
        assert [p.name for p in router._providers] == ["groq"]
        assert [p.name for p in router._providers_for_tier("deep")] == ["groq"]

    def test_empty_router_returns_empty_chain_without_crash(self):
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        assert router._providers_for_tier("deep") == []
        assert router._providers_for_tier("standard") == []

    def test_disabled_provider_still_listed(self):
        """C-10：enabled=False / 熔断中的 Provider 仍出现在返回值里（由 chat 跳过）。"""
        router = _online_router()
        zhipu = next(p for p in router._providers if p.name == "zhipu_glm")
        zhipu.enabled = False
        assert [p.name for p in router._providers_for_tier("standard")] == [
            "zhipu_glm", "zhipu_glm_45", "agnes", "ly77625"
        ]
        assert "zhipu_glm" in [p.name for p in router._providers_for_tier("deep")]

    def test_global_bypass_disables_all_tiers(self):
        """C-8/B4：LLM_TIER_ROUTING_ENABLED=0 → 任何档位退化为全链（线上回滚开关）。"""
        router = _online_router()
        full = [p.name for p in router._providers]
        with patch.dict("os.environ", {"LLM_TIER_ROUTING_ENABLED": "0"}, clear=False):
            assert routing_enabled() is False
            assert [p.name for p in router._providers_for_tier("deep")] == full
            assert [p.name for p in router._providers_for_tier("light")] == full
            assert [p.name for p in router._providers_for_tier("standard")] == full
            assert all(r is None for _p, r in router._routes_for_tier("deep"))

    @pytest.mark.parametrize("raw", ["0", "false", "False", "off", " no "])
    def test_bypass_env_variants(self, raw):
        with patch.dict("os.environ", {"LLM_TIER_ROUTING_ENABLED": raw}, clear=False):
            assert routing_enabled() is False

    def test_bypass_enabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert routing_enabled() is True

    def test_added_provider_is_visible_in_standard_chain(self):
        """动态 add_provider 必须出现在 standard 链里（8+ 条既有用例依赖此行为）。"""
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        router.add_provider(LLMProvider(
            name="flaky",
            provider_type=ProviderType.OPENAI,
            base_url="https://flaky.test/v1",
            api_key="k",
            model="m",
        ))
        assert [p.name for p in router._providers_for_tier("standard")] == ["flaky"]
        assert [p.name for p in router._providers_for_tier(None)] == ["flaky"]


# ===== health_check / routing_health 契约 =====


class TestRoutingHealth:
    def test_routing_health_contains_three_tiers(self):
        """AC9/AC11：routing.tiers 含 deep/standard/light 三键，值为有序 provider 名数组。"""
        router = _online_router()
        health = router.routing_health()
        assert set(health["tiers"]) == {"deep", "standard", "light"}
        assert health["tiers"]["deep"] == ["zhipu_glm", "agnes", "ly77625"]
        assert health["tiers"]["light"] == ["zhipu_glm", "agnes"]
        assert health["tiers"]["standard"] == [p.name for p in router._providers]

    def test_routing_health_budgets(self):
        with patch.dict("os.environ", {}, clear=True):
            router = _online_router()
            health = router.routing_health()
        assert health["budgets_seconds"]["deep"] == 60.0
        assert health["budgets_seconds"]["light"] == 15.0
        assert health["budgets_seconds"]["standard"] == 25.0

    def test_routing_health_route_details(self):
        router = _online_router()
        health = router.routing_health()
        deep = health["routes"]["deep"]
        assert [r["provider"] for r in deep] == ["zhipu_glm", "agnes", "ly77625"]
        assert deep[1]["model"] == "agnes-3.0-flash"
        assert deep[1]["label"] == "agnes_30"
        assert deep[2]["provider"] == "ly77625"
        assert all("available" in r for r in deep)
        assert all("same_upstream_as" in r for r in deep)

    def test_routing_health_never_leaks_secrets(self):
        """AC10：routing 段绝不含 api_key / Bearer 片段。"""
        import json

        router = _online_router()
        blob = json.dumps(router.routing_health(), ensure_ascii=False, default=str)
        for secret in ("zk", "ak", "lk"):
            assert f'"{secret}"' not in blob
        assert "api_key" not in blob.lower()
        assert "bearer" not in blob.lower()

    def test_health_check_marks_same_upstream(self):
        """F1/L2：zhipu_glm_45 标注 same_upstream_as=zhipu_glm，其余为 None。"""
        router = _online_router()
        health = router.health_check()
        assert health["zhipu_glm_45"]["same_upstream_as"] == "zhipu_glm"
        assert health["zhipu_glm"]["same_upstream_as"] is None
        assert health["agnes"]["same_upstream_as"] is None

    def test_health_check_keeps_existing_fields(self):
        router = _online_router()
        info = router.health_check()["zhipu_glm"]
        for key in (
            "available", "model", "base_url", "priority", "fail_count",
            "circuit_open", "rpm", "has_multi_keys", "provider_type", "free_tier",
        ):
            assert key in info

    def test_routing_health_same_upstream_groups(self):
        router = _online_router()
        groups = router.routing_health()["same_upstream_groups"]
        assert groups == {"zhipu_glm": ["zhipu_glm_45"]}

    def test_routing_health_empty_router(self):
        with patch.dict("os.environ", {}, clear=True):
            router = LLMRouter()
        health = router.routing_health()
        assert health["tiers"] == {"deep": [], "standard": [], "light": []}
        assert health["last_routing"] == {}


class TestHopTimeoutCaps:
    """R2/D12：单跳 timeout_cap 契约（deep 20/25/20、light 10/10、standard 无 cap）。"""

    def test_deep_caps(self):
        routes = TIER_SPECS[TIER_DEEP].routes or ()
        assert [r.timeout_cap for r in routes] == [20, 25, 20]

    def test_deep_cap_sum_leaves_room_for_third_hop(self):
        routes = TIER_SPECS[TIER_DEEP].routes or ()
        total = sum(r.timeout_cap or 0 for r in routes)
        assert total <= 65
        assert total - TIER_SPECS[TIER_DEEP].budget_seconds <= 5

    def test_light_caps(self):
        routes = TIER_SPECS[TIER_LIGHT].routes or ()
        assert [r.timeout_cap for r in routes] == [10, 10]
        budget = TIER_SPECS[TIER_LIGHT].budget_seconds
        # 单跳 cap 必须小于总预算，否则一次慢请求就能吃掉整个 light 预算
        assert all((r.timeout_cap or 0) < budget for r in routes)

    def test_standard_has_no_cap(self):
        """B5：standard 无 routes → 无 cap，单跳仍是 provider.timeout（零回归）。"""
        assert TIER_SPECS[TIER_STANDARD].routes is None

    def test_deep_order_matches_d9(self):
        """R1/D9：zhipu_glm → agnes(3.0-flash) → ly77625(gemini 兜底)。"""
        routes = TIER_SPECS[TIER_DEEP].routes or ()
        assert [(r.provider, r.model) for r in routes] == [
            ("zhipu_glm", None),
            ("agnes", "agnes-3.0-flash"),
            ("ly77625", None),
        ]


class TestResolveHopTimeout:
    """B5/§4.2：单跳超时的唯一实现 resolve_hop_timeout（chat() 内禁止手写 min）。"""

    def test_deep_first_hop_exact_design_case(self):
        """T01 验收⑩：deep 首跳 (caller=120, provider=30, remaining=60) == 20。"""
        from src.core.llm_task_tier import resolve_hop_timeout

        hop1 = TIER_SPECS[TIER_DEEP].routes[0]
        assert resolve_hop_timeout(TIER_DEEP, hop1, 30, 120, 60.0) == 20

    def test_deep_all_hops(self):
        from src.core.llm_task_tier import resolve_hop_timeout

        hops = TIER_SPECS[TIER_DEEP].routes
        got = [resolve_hop_timeout(TIER_DEEP, r, 30, 120, 60.0) for r in hops]
        assert got == [20, 25, 20]

    def test_light_all_hops(self):
        from src.core.llm_task_tier import resolve_hop_timeout

        hops = TIER_SPECS[TIER_LIGHT].routes
        got = [resolve_hop_timeout(TIER_LIGHT, r, 30, None, 15.0) for r in hops]
        assert got == [10, 10]

    def test_standard_route_none_is_todays_formula(self):
        """route=None ⇒ min(caller/provider, remaining)，逐字节等于今日公式。"""
        from src.core.llm_task_tier import resolve_hop_timeout

        with patch.dict("os.environ", {}, clear=True):
            assert resolve_hop_timeout(TIER_STANDARD, None, 30, None, 25.0) == 25
            assert resolve_hop_timeout(TIER_STANDARD, None, 30, None, 100.0) == 30
            assert resolve_hop_timeout(TIER_STANDARD, None, 30, 5, 100.0) == 5
            assert resolve_hop_timeout(TIER_STANDARD, None, 30, None, 0.4) == 1  # 下限 1s

    def test_env_cap_tightens_standard(self):
        """B5：LLM_TIER_HOP_TIMEOUT_STANDARD=15 → standard 单跳被压到 15s（可选旋钮）。"""
        from src.core.llm_task_tier import resolve_hop_timeout

        with patch.dict("os.environ", {"LLM_TIER_HOP_TIMEOUT_STANDARD": "15"}, clear=False):
            assert resolve_hop_timeout(TIER_STANDARD, None, 30, None, 100.0) == 15
            assert resolve_hop_timeout(TIER_STANDARD, None, 30, 5, 100.0) == 5  # 更小者胜

    def test_env_cap_also_applies_to_tier_hops(self):
        """env 上限对带 route 的跳同样生效（取 min，只会更严不会更宽）。"""
        from src.core.llm_task_tier import resolve_hop_timeout

        hop1 = TIER_SPECS[TIER_DEEP].routes[0]  # cap=20
        with patch.dict("os.environ", {"LLM_TIER_HOP_TIMEOUT_DEEP": "8"}, clear=False):
            assert resolve_hop_timeout(TIER_DEEP, hop1, 30, 120, 60.0) == 8
        # 反向（把 env 设成比 cap 大）不应放宽：20 仍生效
        with patch.dict("os.environ", {"LLM_TIER_HOP_TIMEOUT_DEEP": "99"}, clear=False):
            assert resolve_hop_timeout(TIER_DEEP, hop1, 30, 120, 60.0) == 20

    def test_illegal_env_cap_ignored(self):
        from src.core.llm_task_tier import resolve_hop_timeout

        with patch.dict("os.environ", {"LLM_TIER_HOP_TIMEOUT_LIGHT": "abc"}, clear=False):
            assert hop_cap_seconds(TIER_LIGHT) is None
            assert resolve_hop_timeout(TIER_LIGHT, TIER_SPECS[TIER_LIGHT].routes[0], 30, None, 15.0) == 10

    def test_hop_cap_seconds_public_view(self):
        """默认未设置 → None（= 今日行为）；设了才可见。"""
        with patch.dict("os.environ", {}, clear=True):
            assert hop_cap_seconds(TIER_DEEP) is None
        with patch.dict("os.environ", {"LLM_TIER_HOP_TIMEOUT_DEEP": "12"}, clear=False):
            assert hop_cap_seconds(TIER_DEEP) == 12.0

    def test_router_delegates_to_resolve_hop_timeout(self):
        """chat() 不再手写 min：router._hop_timeout 与纯函数结果一致。"""
        from src.core.llm_task_tier import resolve_hop_timeout

        router = _online_router()
        hop1 = TIER_SPECS[TIER_DEEP].routes[0]
        provider = next(p for p in router._providers if p.name == "zhipu_glm")
        assert router._hop_timeout(TIER_DEEP, hop1, provider, 120, 60.0) == resolve_hop_timeout(
            TIER_DEEP, hop1, provider.timeout, 120, 60.0
        )

    def test_no_manual_min_in_chat_source(self):
        """§6.1 规则5：chat() 内不得再出现手写 min(...) 计算单跳超时。"""
        import inspect

        import src.core.llm_router as mod

        src = inspect.getsource(mod.LLMRouter.chat)
        assert "_hop_timeout" in src
        assert "route.timeout_cap" not in src  # cap 只在 resolve_hop_timeout 里消费


class TestCorrectModelId:
    """D11：KNOWN_MODEL_REPLACEMENTS / correct_model_id 契约（provider:model 键）。"""

    def test_table_only_contains_retired_ids(self):
        """裁定②：纠正表只收"平台已下线"的 ID。

        agnes-2.5-flash（未下线，只是慢/判题错，已从所有 tier 链排除）与
        agnes-2.5-pro-*（403 是额度问题）**不得**入表 —— 用户显式设置视为用户意图。
        """
        from src.core.llm_task_tier import KNOWN_MODEL_REPLACEMENTS

        assert KNOWN_MODEL_REPLACEMENTS == {"agnes:agnes-2.0-flash": "agnes-3.0-flash"}

    def test_non_retired_ids_are_not_rewritten(self):
        from src.core.llm_task_tier import correct_model_id

        warnings: list[str] = []
        for model in ("agnes-2.5-flash", "agnes-2.5-pro-alpha", "agnes-2.5-pro-beta"):
            assert correct_model_id("agnes", model, warn=warnings.append) == model
        assert warnings == []

    def test_correct_model_id_warns_and_replaces(self):
        from src.core.llm_task_tier import correct_model_id

        warnings: list[str] = []
        assert correct_model_id("agnes", "agnes-2.0-flash", warn=warnings.append) == "agnes-3.0-flash"
        assert len(warnings) == 1
        assert "agnes-2.0-flash" in warnings[0]

    def test_correct_model_id_passthrough(self):
        from src.core.llm_task_tier import correct_model_id

        warnings: list[str] = []
        assert correct_model_id("agnes", "agnes-3.0-flash", warn=warnings.append) == "agnes-3.0-flash"
        assert correct_model_id("zhipu_glm", "agnes-2.0-flash", warn=warnings.append) == "agnes-2.0-flash"
        assert warnings == []  # provider 前缀不匹配时不得纠正

    def test_second_safety_net_is_tier_route_model(self):
        """D11 第二道保险：即使纠正表被清空，deep 第 2 跳仍是 agnes-3.0-flash。"""
        from src.core import llm_task_tier as mod

        with patch.dict(mod.KNOWN_MODEL_REPLACEMENTS, {}, clear=True):
            hop = mod.TIER_SPECS[TIER_DEEP].routes[1]
            assert hop.model == "agnes-3.0-flash"

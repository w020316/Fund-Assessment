"""T05 端到端 tier 契约回归（QA 独立视角，不复用工程师用例）。

设计文档 `docs/llm-task-routing-design-2026-11-07.md` §2.1 规划的 QA 侧新增文件。

覆盖目标（独立构造，只依赖公开行为）：
- AC5  跨档缓存隔离：同 prompt 先 light 后 deep，第二次 cached == False 且**真的再次调用**
      了 provider（不只断言返回对象，避免"只 assert 返回了对象没查 cached/调用次数"的假过）。
- AC6  deep 首跳 429 → 落第 2 跳 agnes（**不是** ly77625），fallback_count==1、熔断计数正确。
- AC7  multi_agent_fund 的 timeout=120 在 deep 档被收紧：首跳 20s / 次跳 25s / 末跳 ≤20s，
      链预算 60s；且 `total_timeout=5` 显式入参仍具最高优先级。
      另验证边界：deep 三跳 cap 和 65 > 预算 60，前两跳快速失败时第 3 跳仍被真正调用。
- 模型不串档：deep 链中只有 agnes 条目拿到 `agnes-3.0-flash`，zhipu_glm / ly77625 拿不到。
- 路由矩阵：三把 key 环境下三档链正确；未知档 / None / "" / 123 / [] 均回落 standard 且不抛异常。
- 过期模型纠正：AGNES_MODEL=agnes-2.0-flash → provider.model == agnes-3.0-flash 且有 WARN。
- 旁路开关：LLM_TIER_ROUTING_ENABLED=0 → 三档全部退化为全链。
- 安全：routing/health 快照不含 api_key / sk- / 真实密钥片段。

全部离线，mock `_call_provider`，不发网络请求。
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.core import llm_task_tier as tier_mod
from src.core.llm_router import LLMProvider, LLMResponse, LLMRouter, ProviderType

# 线上等价 4 Provider 环境（PRD §1）
_ONLINE_ENV = {
    "ZHIPU_API_KEY": "zk-qa-secret",
    "AGNES_API_KEY": "ak-qa-secret",
    "LY77625_API_KEY": "lk-qa-secret",
}


@pytest.fixture(autouse=True)
def _isolate_router_state():
    """每个用例前后复位全局单例状态（缓存由 conftest 统一清理，这里补 record/warn）。"""
    from src.core.llm_router import _llm_cache, _recorder

    _recorder.reset()
    tier_mod.reset_warn_cache()
    _llm_cache.clear()
    yield
    _recorder.reset()
    tier_mod.reset_warn_cache()
    _llm_cache.clear()


def _online_router() -> LLMRouter:
    with patch.dict(os.environ, _ONLINE_ENV, clear=True):
        return LLMRouter()


def _http_429() -> requests.exceptions.HTTPError:
    resp = MagicMock()
    resp.status_code = 429
    return requests.exceptions.HTTPError("429 Too Many Requests", response=resp)


def _ok(provider, model: str | None = None) -> LLMResponse:
    m = model or getattr(provider, "model", "m")
    return LLMResponse(content=f"ok-{provider.name}", provider=provider.name, model=m, latency_ms=1.0)


# ============================ AC5 缓存隔离 ============================


class TestAC5CacheIsolation:
    def test_light_then_deep_does_not_share_cache(self):
        """同 prompt：light 写入后，deep 不得命中（cached==False 且真的又调了 provider）。"""
        router = _online_router()
        msgs = [{"role": "user", "content": "QA-AC5-同prompt跨档-1"}]
        calls: list[tuple[str, str | None]] = []

        def fake_call(provider, messages, **kwargs):
            calls.append((provider.name, kwargs.get("model")))
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            light = router.chat(msgs, task_tier="light")
            n_after_light = len(calls)
            deep = router.chat(msgs, task_tier="deep")

        assert light.cached is False
        assert deep.cached is False, "deep 命中了 light 的缓存 → F3 跨档污染"
        assert n_after_light == 1, "light 首调应只调用一次 provider"
        assert len(calls) == 2, "deep 必须重新调用 provider（未走 light 缓存）"

    def test_deep_then_light_does_not_share_cache(self):
        """反向：deep 写入后，light 不得命中。"""
        router = _online_router()
        msgs = [{"role": "user", "content": "QA-AC5-同prompt跨档-2"}]
        calls: list[str] = []

        def fake_call(provider, messages, **kwargs):
            calls.append(provider.name)
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            deep = router.chat(msgs, task_tier="deep")
            light = router.chat(msgs, task_tier="light")

        assert deep.cached is False
        assert light.cached is False, "light 命中了 deep 的缓存 → 跨档污染"
        assert len(calls) == 2

    def test_same_tier_cache_still_hits(self):
        """同档重复调用仍应命中（证明缓存机制本身有效，隔离不是"永久不命中"）。"""
        router = _online_router()
        msgs = [{"role": "user", "content": "QA-AC5-同档可命中"}]
        calls: list[str] = []

        def fake_call(provider, messages, **kwargs):
            calls.append(provider.name)
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            first = router.chat(msgs, task_tier="deep")
            second = router.chat(msgs, task_tier="deep")

        assert first.cached is False
        assert second.cached is True
        assert len(calls) == 1, "同档第二次应命中缓存，不再调用 provider"


# ============================ AC6 429 降级 ============================


class TestAC6429108Fallback:
    def test_deep_first_hop_429_falls_to_agnes(self):
        """deep 首跳 429 → 切第 2 跳 agnes（不是 ly77625），fallback==1、熔断计数 +1。"""
        router = _online_router()
        visited: list[str] = []

        def fake_call(provider, messages, **kwargs):
            visited.append(provider.name)
            if provider.name == "zhipu_glm":
                raise _http_429()
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat([{"role": "user", "content": "QA-AC6-deep-429"}], task_tier="deep")

        assert result.provider == "agnes"
        assert visited == ["zhipu_glm", "agnes"], "429 后应直接落 agnes，不应先试 ly77625"

        from src.core.llm_router import _recorder
        last = _recorder.last()
        assert last["tier"] == "deep"
        assert last["fallback_count"] == 1
        assert last["cached"] is False

        zhipu = next(p for p in router._providers if p.name == "zhipu_glm")
        assert zhipu._fail_count == 1, "429 应触发 record_failure（熔断计数 +1）"
        agnes = next(p for p in router._providers if p.name == "agnes")
        assert agnes._fail_count == 0


# ============================ AC7 预算 / 逐跳 cap ============================


class TestAC7BudgetAndHopCaps:
    def test_deep_timeout_120_clamped_to_60_with_per_hop_caps(self):
        """multi_agent_fund 传 timeout=120：三跳实收 20/25/20，链预算 60（不是 25、不是 120）。"""
        router = _online_router()
        seen: list[tuple[str, int | None]] = []

        def fake_call(provider, messages, **kwargs):
            seen.append((provider.name, kwargs.get("timeout")))
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat(
                    [{"role": "user", "content": "QA-AC7-timeout120"}],
                    timeout=120,
                    task_tier="deep",
                )

        assert [t for _, t in seen] == [20, 25, 20], "逐跳 cap 必须是 20/25/20"
        from src.core.llm_router import _recorder
        assert _recorder.last()["budget_seconds"] == 60.0, "deep 链预算必须是 60s（而非 LLM_TOTAL_TIMEOUT=25）"

    def test_explicit_total_timeout_has_highest_priority(self):
        """total_timeout=5 显式入参仍压过档位常量：每跳 ≤5s。"""
        router = _online_router()
        seen: list[int | None] = []

        def fake_call(provider, messages, **kwargs):
            seen.append(kwargs.get("timeout"))
            raise _http_429()

        with patch.object(router, "_call_provider", side_effect=fake_call):
            with pytest.raises(RuntimeError):
                router.chat(
                    [{"role": "user", "content": "QA-AC7-total5"}],
                    total_timeout=5,
                    task_tier="deep",
                )

        assert seen and all(t is not None and 1 <= t <= 5 for t in seen)

    def test_third_hop_reachable_when_first_two_fail_fast(self):
        """边界回归：cap 和 65 > 预算 60，前两跳快速失败时第 3 跳仍被真正调用并拿到 20s。"""
        router = _online_router()
        visited: list[tuple[str, int | None]] = []

        def fake_call(provider, messages, **kwargs):
            visited.append((provider.name, kwargs.get("timeout")))
            if provider.name != "ly77625":
                raise _http_429()  # 快速失败，几乎不耗时
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat([{"role": "user", "content": "QA-AC7-3rdhop"}], task_tier="deep")

        assert [v[0] for v in visited] == ["zhipu_glm", "agnes", "ly77625"]
        assert result.provider == "ly77625"
        # 第 3 跳拿到自己的 cap（20），而不是被预算夹成 0/1
        assert visited[2][1] == 20


# ============================ 模型不串档 ============================


class TestModelNotCrossHop:
    def test_deep_chain_model_params_per_hop(self):
        """agnes 条目拿 agnes-3.0-flash，zhipu_glm / ly77625 拿不到该 model。"""
        router = _online_router()
        captured: list[tuple[str, str | None]] = []

        def fake_call(provider, messages, **kwargs):
            captured.append((provider.name, kwargs.get("model")))
            if provider.name != "ly77625":
                raise _http_429()
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            router.chat([{"role": "user", "content": "QA-model-per-hop"}], task_tier="deep")

        by_name = dict(captured)
        assert by_name["agnes"] == "agnes-3.0-flash"
        assert by_name["zhipu_glm"] is None, "zhipu_glm 不得收到 agnes 的 model"
        assert by_name["ly77625"] is None, "ly77625 不得收到 agnes 的 model"

    def test_explicit_model_overrides_all_hops(self):
        routed: list[str | None] = []

        router = _online_router()

        def fake_call(provider, messages, **kwargs):
            routed.append(kwargs.get("model"))
            return _ok(provider, kwargs.get("model"))

        with patch.object(router, "_call_provider", side_effect=fake_call):
            result = router.chat(
                [{"role": "user", "content": "QA-explicit-model"}],
                model="explicit-qa-model",
                task_tier="deep",
            )
        assert routed == ["explicit-qa-model"]
        assert result.model == "explicit-qa-model"


# ============================ 路由矩阵 ============================


class TestRoutingMatrix:
    def test_three_tiers_in_online_env(self):
        router = _online_router()
        assert [p.name for p in router._providers_for_tier("deep")] == ["zhipu_glm", "agnes", "ly77625"]
        assert [p.name for p in router._providers_for_tier("light")] == ["zhipu_glm", "agnes"]
        assert [p.name for p in router._providers_for_tier("standard")] == list(
            [p.name for p in router._providers]
        )

    @pytest.mark.parametrize("bad", [None, "", "   ", "bogus", 123, [], {}, 3.5])
    def test_unknown_tier_never_raises_and_falls_back(self, bad):
        router = _online_router()
        full = [p.name for p in router._providers]
        # 不抛异常即可
        assert [p.name for p in router._providers_for_tier(bad)] == full

    def test_standard_view_is_new_list_same_objects(self):
        router = _online_router()
        view = router._providers_for_tier("standard")
        assert view is not router._providers
        assert view == list(router._providers)
        assert all(a is b for a, b in zip(view, router._providers))


# ============================ 过期模型纠正 ============================


class TestRetiredModelCorrection:
    def test_old_agnes_model_corrected_with_warn(self):
        with patch("src.core.llm_router.logger") as mock_logger:
            with patch.dict(os.environ, {**_ONLINE_ENV, "AGNES_MODEL": "agnes-2.0-flash"}, clear=True):
                router = LLMRouter()
        agnes = next(p for p in router._providers if p.name == "agnes")
        assert agnes.model == "agnes-3.0-flash"
        warns = [c for c in mock_logger.warning.call_args_list if "agnes-2.0-flash" in str(c)]
        assert len(warns) == 1, "旧模型 ID 应恰好产生 1 条 WARN"

    def test_tier_chain_second_hop_uses_current_model_regardless(self):
        """第二道保险：即使 provider.model 被改成旧值，deep 第 2 跳仍解析为 agnes-3.0-flash。"""
        router = _online_router()
        pairs = dict((p.name, r) for p, r in router._routes_for_tier("deep"))
        assert pairs["agnes"] is not None and pairs["agnes"].model == "agnes-3.0-flash"


# ============================ 旁路开关 ============================


class TestGlobalBypass:
    def test_bypass_degrades_all_tiers_to_full_chain(self):
        router = _online_router()
        full = [p.name for p in router._providers]
        with patch.dict(os.environ, {"LLM_TIER_ROUTING_ENABLED": "0"}, clear=False):
            tier_mod.reset_warn_cache()
            for t in ("deep", "standard", "light"):
                assert [p.name for p in router._providers_for_tier(t)] == full, f"{t} 未退化为全链"
            assert all(r is None for _p, r in router._routes_for_tier("deep"))


# ============================ 安全（无密钥泄露） ============================


class TestNoSecretLeak:
    def test_routing_health_has_no_secret(self):
        router = _online_router()
        blob = json.dumps(router.routing_health(), ensure_ascii=False, default=str)
        assert "api_key" not in blob.lower()
        assert "sk-" not in blob
        assert "bearer" not in blob.lower()
        for secret in _ONLINE_ENV.values():
            assert secret not in blob

    def test_health_check_snapshot_has_no_secret(self):
        router = _online_router()
        blob = json.dumps(router.health_check(), ensure_ascii=False, default=str)
        assert "api_key" not in blob.lower()
        for secret in _ONLINE_ENV.values():
            assert secret not in blob


# ============================ 已知偏差锁定（不影响判定，仅登记） ============================


class TestKnownDeviationSnapshot:
    def test_zhipu_glm_45_enabled_state_snapshot(self):
        """【已收口】主理人 2026-11-07 裁定 A：`zhipu_glm_45` 置 `enabled=False`。

        原用例锁定的是"偏差现状（enabled=True）"，裁定 A 落地后偏差消除，
        本用例改为锁定**裁定后的正确状态**，防止静默回退：
        - `priority == 1` 未被改动（AC3 满足）
        - `enabled is False`（D10）
        - 对象仍留在 `_providers` 里，因此 **视图层** 仍包含它（C-1/D13：
          `_providers_for_tier("standard") == list(_providers)`）；
          但 `chat()` 的有效跳会因 `is_available=False` 跳过它。
        """
        router = _online_router()
        glm45 = next(p for p in router._providers if p.name == "zhipu_glm_45")
        assert glm45.priority == 1
        assert glm45.enabled is False  # D10
        assert glm45.is_available is False
        # 视图层不减员（D13），但它的可用性为 False
        assert "zhipu_glm_45" in [p.name for p in router._providers_for_tier("standard")]
        # health 里可见且标注同源
        health = router.health_check()["zhipu_glm_45"]
        assert health["available"] is False
        assert health["same_upstream_as"] == "zhipu_glm"
        assert health["priority"] == 1

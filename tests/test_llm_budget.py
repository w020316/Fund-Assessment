"""R-09 回归:LLM 单次 chat 必须有端到端总预算。"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from src.core.llm_router import LLMProvider, LLMResponse, LLMRouter, ProviderType


def _router(timeout: int = 60) -> LLMRouter:
    with patch.dict("os.environ", {}, clear=True):
        router = LLMRouter()
    router.add_provider(
        LLMProvider(
            name="slow",
            provider_type=ProviderType.OPENAI,
            base_url="https://mock.test/v1",
            api_key="k",
            model="m",
            timeout=timeout,
            max_retries=0,
        )
    )
    return router


def test_provider_timeout_is_clamped_to_total_budget():
    router = _router(timeout=60)
    with patch.object(router, "_call_provider", side_effect=requests.exceptions.Timeout("t")) as mocked:
        with pytest.raises(RuntimeError, match="所有LLM Provider均不可用"):
            router.chat([{"role": "user", "content": "hi"}], total_timeout=5)
    assert mocked.call_args.kwargs["timeout"] <= 5
    assert mocked.call_args.kwargs["timeout"] >= 1


def test_zero_budget_fails_fast_without_calling_provider():
    router = _router()
    with patch.object(router, "_call_provider") as mocked:
        with pytest.raises(RuntimeError, match="总预算 0s 已耗尽"):
            router.chat([{"role": "user", "content": "hi"}], total_timeout=0)
    mocked.assert_not_called()


def test_success_within_budget():
    router = _router()
    with patch.object(
        router,
        "_call_provider",
        return_value=LLMResponse(content="ok", provider="slow", model="m"),
    ):
        result = router.chat([{"role": "user", "content": "hi"}], total_timeout=10)
    assert result.content == "ok"


# ===== T02/AC7：分层档位预算（2026-11-07 增量，只追加，不改既有断言）=====


def _tier_router(timeout: int = 30, name: str = "zhipu_glm") -> LLMRouter:
    """构造一个"已加载 zhipu_glm"的空 Router（deep/light 链可命中它）。"""
    with patch.dict("os.environ", {}, clear=True):
        router = LLMRouter()
    router.add_provider(
        LLMProvider(
            name=name,
            provider_type=ProviderType.OPENAI,
            base_url="https://mock.test/v1",
            api_key="k",
            model="m",
            timeout=timeout,
            max_retries=0,
        )
    )
    return router


def test_deep_budget_is_not_clamped_by_total_timeout_env():
    """AC7/F2：即使线上 LLM_TOTAL_TIMEOUT=1，deep 档预算仍是 60s，单跳不被掐死。"""
    with patch.dict("os.environ", {"LLM_TOTAL_TIMEOUT": "1"}, clear=False):
        router = _tier_router(timeout=30)
        with patch.object(
            router, "_call_provider", side_effect=requests.exceptions.Timeout("t")
        ) as mocked:
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "deep"}], task_tier="deep")
    assert mocked.call_args.kwargs["timeout"] > 1


def test_standard_budget_still_follows_total_timeout():
    """零回归：standard 档（默认档）预算仍等于 LLM_TOTAL_TIMEOUT。"""
    with patch.dict("os.environ", {"LLM_TOTAL_TIMEOUT": "1"}, clear=False):
        router = _tier_router(timeout=30)
        with patch.object(
            router, "_call_provider", side_effect=requests.exceptions.Timeout("t")
        ) as mocked:
            with pytest.raises(RuntimeError):
                router.chat([{"role": "user", "content": "std"}])
    assert mocked.call_args.kwargs["timeout"] <= 1


def test_explicit_total_timeout_still_has_highest_priority():
    """§4.1 优先级 1：chat(total_timeout=...) 仍然覆盖一切档位预算。"""
    router = _tier_router(timeout=60)
    with patch.object(
        router, "_call_provider", side_effect=requests.exceptions.Timeout("t")
    ) as mocked:
        with pytest.raises(RuntimeError):
            router.chat([{"role": "user", "content": "explicit"}], total_timeout=3, task_tier="deep")
    assert mocked.call_args.kwargs["timeout"] <= 3
    assert mocked.call_args.kwargs["timeout"] >= 1


def test_deep_second_hop_attempted_after_first_hop_timeout():
    """AC7/F2 核心：deep 首跳超时后，第 2 跳仍被实际尝试（3 候选不退化为 1）。"""
    with patch.dict("os.environ", {}, clear=True):
        router = LLMRouter()
    for name, timeout in (("zhipu_glm", 30), ("ly77625", 30)):
        router.add_provider(
            LLMProvider(
                name=name,
                provider_type=ProviderType.OPENAI,
                base_url="https://mock.test/v1",
                api_key="k",
                model="m",
                timeout=timeout,
                max_retries=0,
            )
        )

    attempts: list[str] = []

    def fake_call(provider, messages, **kwargs):
        attempts.append(provider.name)
        if provider.name == "zhipu_glm":
            raise requests.exceptions.Timeout("first hop too slow")
        return LLMResponse(content="ok", provider=provider.name, model="m")

    with patch.object(router, "_call_provider", side_effect=fake_call):
        result = router.chat([{"role": "user", "content": "deep-fallback"}], task_tier="deep")

    assert attempts == ["zhipu_glm", "ly77625"]
    assert result.provider == "ly77625"


def test_deep_and_standard_budgets_differ():
    """deep=60 / standard=继承 LLM_TOTAL_TIMEOUT（默认 25）/ light=15。"""
    router = _tier_router()
    with patch.dict("os.environ", {}, clear=True):
        assert router._resolve_budget("deep", None) == 60.0
        assert router._resolve_budget("standard", None) == 25.0
        assert router._resolve_budget("light", None) == 15.0


def test_tier_timeout_env_override():
    """运维旋钮 LLM_TIER_TIMEOUT_<TIER> 只改"多久"，不改"发给谁"。"""
    router = _tier_router()
    with patch.dict("os.environ", {"LLM_TIER_TIMEOUT_DEEP": "90"}, clear=False):
        assert router._resolve_budget("deep", None) == 90.0
    with patch.dict("os.environ", {"LLM_TIER_TIMEOUT_LIGHT": "8"}, clear=False):
        assert router._resolve_budget("light", None) == 8.0


def test_kill_switch_also_reverts_budget():
    """全局旁路 → 预算也退回 LLM_TOTAL_TIMEOUT（一条 env 即完整回滚）。"""
    router = _tier_router()
    with patch.dict(
        "os.environ",
        {"LLM_TIER_ROUTING_ENABLED": "0", "LLM_TOTAL_TIMEOUT": "25"},
        clear=False,
    ):
        assert router._resolve_budget("deep", None) == 25.0
        assert router._resolve_budget("light", None) == 25.0

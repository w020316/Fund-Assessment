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

"""R-01 回归:AI 分析不得用 0 值冒充缺失数据。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.core.ai_service import (
    _attach_data_quality,
    _build_analysis_prompt,
    _compute_data_completeness,
    _prompt_value,
)
from web.routes.agent import _raise_if_degraded


def test_prompt_value_marks_missing():
    assert _prompt_value({}, "price") == "缺失"
    assert _prompt_value({"price": None}, "price") == "缺失"
    assert _prompt_value({"price": 1688.0}, "price", digits=2) == "1688.00"


def test_completeness_empty_is_zero():
    score, missing = _compute_data_completeness({})
    assert score == 0
    assert "实时行情/现价" in missing
    assert "K线" in missing


def test_completeness_full_data():
    data = {
        "quote": {"price": 1688.0},
        "kline_daily": [{"close": 1.0}] * 5,
        "financial": {"pe_ttm": 28.5},
        "capital_flow": {"main_net_inflow": 1e8},
        "company": {"name": "贵州茅台"},
        "news": [{"title": "t"}],
        "margin": {"margin_balance": 1e9},
        "shareholder": {"holder_num": 150000},
    }
    score, missing = _compute_data_completeness(data)
    assert score == 100
    assert missing == []


def test_attach_quality_degrades_on_quote_only():
    result = _attach_data_quality({"action": "BUY", "confidence": 0.8}, {"quote": {"code": "600519"}})
    assert result["data_completeness"] == 0
    assert result["degraded"] is True
    assert result["error_code"] == "DATA_INCOMPLETE"
    assert "核心数据不足" in result["degraded_reason"]


def test_attach_quality_keeps_good_data():
    data = {
        "quote": {"price": 1688.0},
        "kline_daily": [{"close": 1.0}] * 5,
        "financial": {"pe_ttm": 28.5},
        "capital_flow": {"main_net_inflow": 1e8},
        "company": {"name": "贵州茅台"},
        "news": [{"title": "t"}],
        "margin": {"margin_balance": 1e9},
        "shareholder": {"holder_num": 150000},
    }
    result = _attach_data_quality({"action": "BUY", "confidence": 0.8}, data)
    assert result["data_completeness"] == 100
    assert "degraded" not in result


def test_prompt_empty_data_says_missing_not_zero():
    prompt = _build_analysis_prompt("000000", {}, [], "quick")
    assert "当前价格: 缺失" in prompt
    assert "当前价格: 0" not in prompt
    assert "PE(TTM): 缺失" in prompt
    assert "禁止把“缺失”当作 0" in prompt


def test_route_returns_503_on_incomplete_data():
    with pytest.raises(HTTPException) as exc:
        _raise_if_degraded(
            {
                "degraded": True,
                "error_code": "DATA_INCOMPLETE",
                "data_completeness": 20,
                "degraded_reason": "核心数据不足",
            },
            "analyze",
        )
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "DATA_INCOMPLETE"
    assert "核心数据不足" in exc.value.detail["message"]

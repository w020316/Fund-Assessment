from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core.ai_service import analyze_stock, quick_analysis as ai_quick_analysis, multi_analyze as ai_multi_analyze, analyze_portfolio as ai_analyze_portfolio, get_market_outlook as ai_get_market_outlook

router = APIRouter()

_decision_history: list[dict[str, Any]] = []


class AnalyzeRequest(BaseModel):
    stock_code: str


class QuickAnalysisRequest(BaseModel):
    stock_code: str


class MultiAnalyzeRequest(BaseModel):
    stock_code: str
    mode: str = "deep"
    agents: list[str] = []


class PortfolioRequest(BaseModel):
    positions: list[dict]


@router.post("/analyze")
async def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    result = await asyncio.to_thread(analyze_stock, req.stock_code, "deep")
    _decision_history.append(result)
    if len(_decision_history) > 100:
        _decision_history.pop(0)
    return result


@router.get("/opinions")
async def get_opinions(code: str = Query(..., description="股票代码")) -> dict[str, Any]:
    result = await asyncio.to_thread(ai_quick_analysis, code)
    return {
        "stock_code": code,
        "opinions": result.get("agent_opinions", []),
    }


@router.get("/debate")
async def get_debate(code: str = Query(..., description="股票代码")) -> dict[str, Any]:
    result = await asyncio.to_thread(analyze_stock, code, "quick")
    return result.get("debate_result", {
        "topic": f"{code}多空辩论",
        "bull_arguments": [],
        "bear_arguments": [],
        "bull_score": 50,
        "bear_score": 50,
        "consensus": "NEUTRAL",
        "confidence": 0.1,
    })


@router.get("/history")
async def get_history() -> dict[str, Any]:
    return {
        "count": len(_decision_history),
        "history": _decision_history[-20:],
    }


@router.post("/quick_analysis")
async def quick_analysis(req: QuickAnalysisRequest) -> dict[str, Any]:
    result = await asyncio.to_thread(ai_quick_analysis, req.stock_code)
    return result


@router.post("/multi_analyze")
async def multi_analyze(req: MultiAnalyzeRequest) -> dict[str, Any]:
    result = await asyncio.to_thread(ai_multi_analyze, req.stock_code, req.mode)
    _decision_history.append(result)
    if len(_decision_history) > 100:
        _decision_history.pop(0)
    result["selected_agents"] = req.agents
    return result


@router.post("/portfolio_advice")
async def portfolio_advice(req: PortfolioRequest) -> dict[str, Any]:
    result = await asyncio.to_thread(ai_analyze_portfolio, req.positions)
    return result


@router.get("/market_outlook")
async def market_outlook() -> dict[str, Any]:
    result = await asyncio.to_thread(ai_get_market_outlook)
    return result

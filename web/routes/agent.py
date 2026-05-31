from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.agents.base import AgentOpinion, DebateResult, TradingDecision
from src.agents.trading_manager import TradingManager

router = APIRouter()

_manager: TradingManager | None = None


def _get_manager() -> TradingManager:
    global _manager
    if _manager is None:
        _manager = TradingManager()
    return _manager


class AnalyzeRequest(BaseModel):
    stock_code: str


class QuickAnalysisRequest(BaseModel):
    stock_code: str


def _opinion_to_dict(op: AgentOpinion) -> dict[str, Any]:
    return {
        "role": op.role.value,
        "stock_code": op.stock_code,
        "signal": op.signal,
        "confidence": op.confidence,
        "reasoning": op.reasoning,
        "key_points": op.key_points,
        "score": op.score,
        "timestamp": op.timestamp,
    }


def _debate_to_dict(dr: DebateResult) -> dict[str, Any]:
    return {
        "topic": dr.topic,
        "bull_arguments": dr.bull_arguments,
        "bear_arguments": dr.bear_arguments,
        "bull_score": dr.bull_score,
        "bear_score": dr.bear_score,
        "consensus": dr.consensus,
        "confidence": dr.confidence,
    }


def _decision_to_dict(d: TradingDecision) -> dict[str, Any]:
    return {
        "stock_code": d.stock_code,
        "action": d.action,
        "position_size": d.position_size,
        "confidence": d.confidence,
        "reasoning": d.reasoning,
        "agent_opinions": [_opinion_to_dict(op) for op in d.agent_opinions],
        "debate_result": _debate_to_dict(d.debate_result) if d.debate_result else None,
        "risk_assessment": d.risk_assessment,
        "timestamp": d.timestamp,
    }


@router.post("/analyze")
async def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    manager = _get_manager()
    decision = manager.run_analysis(req.stock_code)
    return _decision_to_dict(decision)


@router.get("/opinions")
async def get_opinions(code: str = Query(..., description="股票代码")) -> dict[str, Any]:
    manager = _get_manager()
    opinions = manager.quick_analysis(code)
    return {
        "stock_code": code,
        "opinions": [_opinion_to_dict(op) for op in opinions],
    }


@router.get("/debate")
async def get_debate(code: str = Query(..., description="股票代码")) -> dict[str, Any]:
    manager = _get_manager()
    opinions = manager.quick_analysis(code)
    debate_result = manager.research_team.debate(opinions, rounds=2)
    return _debate_to_dict(debate_result)


@router.get("/history")
async def get_history() -> dict[str, Any]:
    manager = _get_manager()
    history = manager.get_decision_history()
    return {
        "count": len(history),
        "history": [_decision_to_dict(d) for d in history],
    }


@router.post("/quick_analysis")
async def quick_analysis(req: QuickAnalysisRequest) -> dict[str, Any]:
    manager = _get_manager()
    opinions = manager.quick_analysis(req.stock_code)
    return {
        "stock_code": req.stock_code,
        "opinions": [_opinion_to_dict(op) for op in opinions],
    }

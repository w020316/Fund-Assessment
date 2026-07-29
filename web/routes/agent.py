from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
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


# ===== P1-5: 基金多智能体分析 =====

class FundMultiAnalyzeRequest(BaseModel):
    fund_code: str
    fund_name: str = ""
    cost_nav: float = 0.0
    shares: float = 0.0
    mode: str = "deep"


@router.post("/fund_analyze")
async def fund_multi_analyze(request: Request, req: FundMultiAnalyzeRequest) -> dict[str, Any]:
    """基金多智能体分析(7角色:消息面/基金/板块/技术/基本面/风险/宏观)

    集成 P1-1消息面 + P1-2重仓股板块 + P1-3大盘研判 + P1-4五信号 + LLM多空辩论
    限流:3次/分钟/客户端(借鉴 la-deps/slowapi,保护 LLM 高成本调用)
    """
    from src.analysis.multi_agent_fund import analyze_fund_with_agents
    result = await analyze_fund_with_agents(
        fund_code=req.fund_code,
        fund_name=req.fund_name,
        cost_nav=req.cost_nav,
        shares=req.shares,
        mode=req.mode,
    )
    return result


# ===== 多智能体分析进度状态(借鉴 TradingAgents-CN Streamlit 实时进度展示) =====
# 7 步骤定义,与前端进度条联动(每 ~12s 推进一步,90s 内走完)
_FUND_ANALYZE_STEPS = [
    {"step": 1, "key": "data", "name": "抓取基金数据", "desc": "净值/估值/重仓股/板块轮动"},
    {"step": 2, "key": "news", "name": "消息面分析", "desc": "情绪指数/热点事件/公告研报"},
    {"step": 3, "key": "sector", "name": "板块趋势分析", "desc": "重仓股板块暴露/集中度"},
    {"step": 4, "key": "technical", "name": "技术面分析", "desc": "K线形态/均线/量价"},
    {"step": 5, "key": "fundamental", "name": "基本面分析", "desc": "重仓股PE/PB/ROE"},
    {"step": 6, "key": "risk", "name": "风险评估", "desc": "解禁/减持/质押/融资融券"},
    {"step": 7, "key": "debate", "name": "多空辩论与决策", "desc": "7分析师辩论+组合经理决议"},
]


@router.get("/fund_analyze_steps")
async def fund_analyze_steps() -> dict[str, Any]:
    """获取基金多智能体分析的7个步骤定义(供前端渲染进度条)。

    借鉴 hsliuping/TradingAgents-CN 项目 Streamlit 实时进度展示思路:
    前端在用户点击"深度"按钮后,先调用本端点获取步骤列表,
    然后在等待 LLM 响应期间按时间间隔逐步推进进度条,
    让用户知道"正在分析什么"而非空白等待。
    """
    return {
        "total_steps": len(_FUND_ANALYZE_STEPS),
        "steps": _FUND_ANALYZE_STEPS,
        "estimated_seconds": 90,
        "source": "TradingAgents-CN-inspired",
    }


# ===== 限流配置(借鉴 la-deps/slowapi)=====
# 对 fund_analyze 这种 LLM 高成本端点单独限流 3次/分钟
# 注意:由于 Limiter 实例在 web/api.py 创建,这里通过 request 引用全局 limiter
try:
    from web.api import limiter as _limiter
    # 重新装饰 fund_multi_analyze 添加限流(3次/分钟/客户端)
    fund_multi_analyze = _limiter.limit("3/minute")(fund_multi_analyze)
except ImportError:
    # slowapi 不可用时降级为无限流(开发环境)
    pass

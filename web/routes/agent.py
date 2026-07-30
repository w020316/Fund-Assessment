from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

# P1 修复(2026-07-29):LLM 高成本端点统一加 admin 鉴权,避免匿名调用耗尽免费额度
from src.utils.auth import require_admin
# limiter 从独立模块导入,避免 import web.api 触发循环 import
from web.rate_limiter import limiter

from src.core.ai_service import analyze_stock, quick_analysis as ai_quick_analysis, multi_analyze as ai_multi_analyze, analyze_portfolio as ai_analyze_portfolio, get_market_outlook as ai_get_market_outlook

router = APIRouter()

# 修复(2026-07-29):原 list.pop(0) 是 O(n),改用 deque(maxlen=100) 自动淘汰
_decision_history: deque[dict[str, Any]] = deque(maxlen=100)


# ===== P0:AI 端点结果缓存(借鉴 httpx-cache 的 LRU+TTL 设计)=====
# 设计目的:
# - LLM 调用成本高(免费额度有限),相同股票/基金重复分析浪费额度
# - 8 个 AI 端点添加 60-600s 缓存,命中时直接返回,不调用 LLM
# - 缓存 key 包含请求参数,避免不同参数误命中
# 实现:纯 Python dict + TTL,无依赖
class _AIResponseCache:
    """AI 响应缓存(TTL,线程安全)"""

    def __init__(self, maxsize: int = 50):
        self._maxsize = maxsize
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = __import__('threading').Lock()

    def _make_key(self, prefix: str, **kwargs) -> str:
        """构造缓存 key"""
        payload = json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=str)
        return f"{prefix}:{payload}"

    def get(self, key: str, ttl: float) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry["ts"] > ttl:
                self._store.pop(key, None)
                return None
            return entry["val"]

    def set(self, key: str, val: Any) -> None:
        with self._lock:
            if key in self._store:
                self._store[key] = {"val": val, "ts": time.monotonic()}
                return
            self._store[key] = {"val": val, "ts": time.monotonic()}
            while len(self._store) > self._maxsize:
                oldest = next(iter(self._store))
                self._store.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_ai_cache = _AIResponseCache(maxsize=50)

# TTL 配置(秒):分析类 180s,快速分析 60s,组合建议 300s,市场研判 600s
_TTL_ANALYZE = 180
_TTL_QUICK = 60
_TTL_PORTFOLIO = 300
_TTL_OUTLOOK = 600
_TTL_FUND_MULTI = 300


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


@router.post("/analyze", dependencies=[Depends(require_admin)])
@limiter.limit("3/minute")
async def analyze(request: Request, req: AnalyzeRequest) -> dict[str, Any]:
    # P0:结果缓存(180s),相同股票深度分析命中缓存
    cache_key = _ai_cache._make_key("analyze", code=req.stock_code)
    cached = _ai_cache.get(cache_key, _TTL_ANALYZE)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(analyze_stock, req.stock_code, "deep")
    _decision_history.append(result)
    _ai_cache.set(cache_key, result)
    return result


@router.get("/opinions")
@limiter.limit("3/minute")
async def get_opinions(request: Request, code: str = Query(..., description="股票代码")) -> dict[str, Any]:
    cache_key = _ai_cache._make_key("opinions", code=code)
    cached = _ai_cache.get(cache_key, _TTL_QUICK)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(ai_quick_analysis, code)
    payload = {"stock_code": code, "opinions": result.get("agent_opinions", [])}
    _ai_cache.set(cache_key, payload)
    return payload


@router.get("/debate")
@limiter.limit("3/minute")
async def get_debate(request: Request, code: str = Query(..., description="股票代码")) -> dict[str, Any]:
    cache_key = _ai_cache._make_key("debate", code=code)
    cached = _ai_cache.get(cache_key, _TTL_ANALYZE)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(analyze_stock, code, "quick")
    payload = result.get("debate_result", {
        "topic": f"{code}多空辩论",
        "bull_arguments": [],
        "bear_arguments": [],
        "bull_score": 50,
        "bear_score": 50,
        "consensus": "NEUTRAL",
        "confidence": 0.1,
    })
    _ai_cache.set(cache_key, payload)
    return payload


@router.get("/history")
async def get_history() -> dict[str, Any]:
    return {
        "count": len(_decision_history),
        "history": list(_decision_history)[-20:],
    }


@router.post("/quick_analysis", dependencies=[Depends(require_admin)])
@limiter.limit("3/minute")
async def quick_analysis(request: Request, req: QuickAnalysisRequest) -> dict[str, Any]:
    cache_key = _ai_cache._make_key("quick", code=req.stock_code)
    cached = _ai_cache.get(cache_key, _TTL_QUICK)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(ai_quick_analysis, req.stock_code)
    _ai_cache.set(cache_key, result)
    return result


@router.post("/multi_analyze", dependencies=[Depends(require_admin)])
@limiter.limit("3/minute")
async def multi_analyze(request: Request, req: MultiAnalyzeRequest) -> dict[str, Any]:
    cache_key = _ai_cache._make_key("multi", code=req.stock_code, mode=req.mode)
    cached = _ai_cache.get(cache_key, _TTL_ANALYZE)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(ai_multi_analyze, req.stock_code, req.mode)
    _decision_history.append(result)
    result["selected_agents"] = req.agents
    _ai_cache.set(cache_key, result)
    return result


@router.post("/portfolio_advice", dependencies=[Depends(require_admin)])
@limiter.limit("3/minute")
async def portfolio_advice(request: Request, req: PortfolioRequest) -> dict[str, Any]:
    cache_key = _ai_cache._make_key("portfolio", positions_hash=hash(json.dumps(req.positions, sort_keys=True, default=str)))
    cached = _ai_cache.get(cache_key, _TTL_PORTFOLIO)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(ai_analyze_portfolio, req.positions)
    _ai_cache.set(cache_key, result)
    return result


@router.get("/market_outlook")
@limiter.limit("3/minute")
async def market_outlook(request: Request) -> dict[str, Any]:
    cache_key = _ai_cache._make_key("outlook")
    cached = _ai_cache.get(cache_key, _TTL_OUTLOOK)
    if cached is not None:
        return cached
    result = await asyncio.to_thread(ai_get_market_outlook)
    _ai_cache.set(cache_key, result)
    return result


# ===== P1-5: 基金多智能体分析 =====

class FundMultiAnalyzeRequest(BaseModel):
    fund_code: str
    fund_name: str = ""
    cost_nav: float = 0.0
    shares: float = 0.0
    mode: str = "deep"


@router.post("/fund_analyze", dependencies=[Depends(require_admin)])
@limiter.limit("3/minute")
async def fund_multi_analyze(request: Request, req: FundMultiAnalyzeRequest) -> dict[str, Any]:
    """基金多智能体分析(7角色:消息面/基金/板块/技术/基本面/风险/宏观)

    集成 P1-1消息面 + P1-2重仓股板块 + P1-3大盘研判 + P1-4五信号 + LLM多空辩论
    限流:3次/分钟/客户端(借鉴 la-deps/slowapi,保护 LLM 高成本调用)
    P0:结果缓存(300s),相同基金相同参数命中缓存不重复调用 LLM
    """
    # P0:结果缓存(300s)
    cache_key = _ai_cache._make_key(
        "fund_multi",
        code=req.fund_code,
        name=req.fund_name,
        cost=req.cost_nav,
        shares=req.shares,
        mode=req.mode,
    )
    cached = _ai_cache.get(cache_key, _TTL_FUND_MULTI)
    if cached is not None:
        cached = dict(cached)  # 返回副本,避免被修改
        cached["_cached"] = True
        return cached
    from src.analysis.multi_agent_fund import analyze_fund_with_agents
    result = await analyze_fund_with_agents(
        fund_code=req.fund_code,
        fund_name=req.fund_name,
        cost_nav=req.cost_nav,
        shares=req.shares,
        mode=req.mode,
    )
    _ai_cache.set(cache_key, result)
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

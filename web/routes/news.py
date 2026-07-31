"""消息面API路由

端点:
- GET  /api/news/feed          消息面流(支持sector/fund_code过滤)
- GET  /api/news/hot           热点事件 Top N
- GET  /api/news/sentiment     情绪指数
- POST /api/news/search        AI检索(Tavily+LLM总结)
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

# P1 修复(2026-07-29):AI 检索端点加 admin 鉴权,触发 LLM 调用属高成本写操作
from src.utils.auth import require_admin

from src.analysis.news_aggregator import (
    get_news_feed,
    get_hot_events,
    get_sentiment_index,
    ai_search,
)
from src.core.cache import DataCache

router = APIRouter()
cache = DataCache(default_ttl=60)


def _build_meta(data_source: str, cached: bool = False) -> dict:
    return {
        "data_source": data_source,
        "quality_score": 100.0 if cached else 80.0,
        "cached": cached,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/feed")
async def news_feed(
    sector: str = Query("", description="板块名称过滤(可选)"),
    fund_code: str = Query("", description="基金代码(可选,查询重仓股新闻)"),
):
    """消息面流(支持板块/基金过滤)"""
    cache_key = f"news:feed:{sector}:{fund_code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("aggregator", cached=True)}
    try:
        data = await get_news_feed(sector=sector, fund_code=fund_code)
        cache.set(cache_key, data, ttl=60)
        return {"data": data, "_meta": _build_meta("aggregator", cached=False)}
    except Exception as e:
        from loguru import logger
        logger.warning(f"news_feed failed: {e}")
        return {"data": [], "_meta": _build_meta("aggregator", cached=False, quality_score=0.0)}


@router.get("/hot")
async def news_hot(
    limit: int = Query(10, description="返回条数", ge=1, le=50),
):
    """热点事件 Top N"""
    cache_key = f"news:hot:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("aggregator", cached=True)}
    try:
        data = await get_hot_events(limit=limit)
        cache.set(cache_key, data, ttl=60)
        return {"data": data, "_meta": _build_meta("aggregator", cached=False)}
    except Exception as e:
        from loguru import logger
        logger.warning(f"news_hot failed: {e}")
        return {"data": [], "_meta": _build_meta("aggregator", cached=False, quality_score=0.0)}


@router.get("/sentiment")
async def news_sentiment():
    """情绪指数 0-100"""
    cache_key = "news:sentiment"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("aggregator", cached=True)}
    try:
        data = await get_sentiment_index()
        cache.set(cache_key, data, ttl=60)
        return {"data": data, "_meta": _build_meta("aggregator", cached=False)}
    except Exception as e:
        from loguru import logger
        logger.warning(f"news_sentiment failed: {e}")
        return {"data": {"score": 50, "level": "neutral"}, "_meta": _build_meta("aggregator", cached=False, quality_score=0.0)}


class SearchRequest(BaseModel):
    # P1 修复(2026-07-30):原 query 无长度上限,可被提交超长文本(数 MB)
    # 直接打入 Tavily+LLM 产生高额费用;空字符串触发无意义 LLM 调用。
    # 限制 1-500 字符,兼顾正常检索需求与成本保护。
    query: str = Field(..., min_length=1, max_length=500, description="检索关键词,限500字符")


@router.post("/search", dependencies=[Depends(require_admin)])
async def news_search(req: SearchRequest):
    """AI检索(Tavily+LLM总结)"""
    # AI检索不缓存(实时性要求高)
    try:
        data = await ai_search(req.query)
        return {"data": data, "_meta": _build_meta("tavily+llm", cached=False)}
    except Exception as e:
        from loguru import logger
        logger.warning(f"news_search failed: {e}")
        return {"data": {"summary": "检索失败,请稍后重试", "results": []}, "_meta": _build_meta("tavily+llm", cached=False, quality_score=0.0)}

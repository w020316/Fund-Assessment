"""基金重仓股板块分析路由

端点:
- GET  /api/holdings/{fund_code}    基金重仓股分析(持仓/板块/集中度/净值影响)
- GET  /api/holdings/sector-rotation  板块轮动总览
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from src.analysis.fund_holdings import (
    analyze_fund_holdings,
    get_sector_rotation,
)
from src.core.cache import DataCache

router = APIRouter()
cache = DataCache(default_ttl=300)


def _build_meta(data_source: str, cached: bool = False) -> dict:
    return {
        "data_source": data_source,
        "quality_score": 100.0 if cached else 80.0,
        "cached": cached,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/{fund_code}")
async def fund_holdings_analysis(
    fund_code: str,
    refresh: bool = Query(False, description="强制刷新(忽略缓存)"),
):
    """基金重仓股板块分析

    返回:
    - holdings: 前十大重仓股
    - sector_exposure: 板块暴露度
    - concentration: 集中度指标(Top5/Top10/HHI)
    - nav_impact: 净值影响预估(基于重仓股当日涨跌)
    - sector_rotation: 板块轮动信号
    """
    if refresh:
        cache.delete(f"holdings:{fund_code}")
    else:
        cache_key = f"holdings:{fund_code}"
        cached = cache.get(cache_key)
        if cached is not None:
            return {"data": cached, "_meta": _build_meta("em_fund+akshare", cached=True)}

    data = await analyze_fund_holdings(fund_code)
    # 缓存5分钟(持仓数据变化慢,但净值影响需刷新)
    cache.set(f"holdings:{fund_code}", data, ttl=300)
    return {"data": data, "_meta": _build_meta("em_fund+akshare", cached=False)}


@router.get("/sector-rotation/overview")
async def sector_rotation_overview():
    """板块轮动总览(全局,非基金特定)"""
    cache_key = "holdings:sector_rotation"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("em_sector_ranking", cached=True)}
    data = await get_sector_rotation()
    cache.set(cache_key, data, ttl=60)
    return {"data": data, "_meta": _build_meta("em_sector_ranking", cached=False)}

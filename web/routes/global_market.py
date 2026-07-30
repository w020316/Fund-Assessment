"""国际市场路由

提供国际指数(道琼斯/纳斯达克/标普500/恒生/国企)、美股热门个股、港股热门个股行情。
数据源:腾讯财经接口(qt.gtimg.cn)。
"""
from __future__ import annotations

import asyncio

from loguru import logger
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core import data_source_v2 as ds2
from src.core.cache import DataCache
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str

router = APIRouter()

cache = DataCache(default_ttl=30)


class ResponseMeta(BaseModel):
    """API 响应元数据"""
    data_source: str = ""
    quality_score: float = 0.0
    cached: bool = False
    timestamp: str = ""


def _build_meta(data_source: str, cached: bool = False, quality_score: float | None = None) -> dict:
    return ResponseMeta(
        data_source=data_source,
        quality_score=quality_score if quality_score is not None else (100.0 if cached else 80.0),
        cached=cached,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ).model_dump()


class GlobalIndexItem(BaseModel):
    code: str
    name: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    high: float
    low: float
    market: str = ""
    currency: str = ""


class UsStockItem(BaseModel):
    code: str
    name: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    high: float
    low: float
    volume: float = 0.0
    currency: str = "USD"


class HkStockItem(BaseModel):
    code: str
    name: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    high: float
    low: float
    volume: float = 0.0
    currency: str = "HKD"


@router.get("/indices")
async def global_indices():
    """国际指数实时行情(道琼斯/纳斯达克/标普500/恒生/国企)"""
    cache_key = "global:indices"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    # P2 修复(2026-07-30):原代码 raw 未防御 None,且无 try/except,上游异常直接 500
    try:
        raw = await asyncio.to_thread(ds2.get_global_indices)
    except Exception as e:
        logger.warning(f"get_global_indices failed: {e}")
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    if not raw:
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    result: list[GlobalIndexItem] = []
    for item in raw:
        result.append(GlobalIndexItem(
            code=_safe_str(item.get("code")),
            name=_safe_str(item.get("name")),
            price=_safe_float(item.get("price")),
            prev_close=_safe_float(item.get("prev_close")),
            change=_safe_float(item.get("change")),
            change_pct=_safe_float(item.get("change_pct")),
            high=_safe_float(item.get("high")),
            low=_safe_float(item.get("low")),
            market=_safe_str(item.get("market")),
            currency=_safe_str(item.get("currency")),
        ))
    cache.set(cache_key, result, ttl=15)
    quality = 90.0 if result else 0.0
    return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality)}


@router.get("/us_hot")
async def us_hot_stocks():
    """美股热门个股(预设 10 只科技龙头)"""
    cache_key = "global:us_hot"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    # P2 修复(2026-07-30):None 防御 + try/except
    try:
        raw = await asyncio.to_thread(ds2.get_us_hot_stocks)
    except Exception as e:
        logger.warning(f"get_us_hot_stocks failed: {e}")
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    if not raw:
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    result: list[UsStockItem] = []
    for item in raw:
        result.append(UsStockItem(
            code=_safe_str(item.get("code")),
            name=_safe_str(item.get("name")),
            price=_safe_float(item.get("price")),
            prev_close=_safe_float(item.get("prev_close")),
            change=_safe_float(item.get("change")),
            change_pct=_safe_float(item.get("change_pct")),
            high=_safe_float(item.get("high")),
            low=_safe_float(item.get("low")),
            volume=_safe_float(item.get("volume")),
            currency=_safe_str(item.get("currency")) or "USD",
        ))
    cache.set(cache_key, result, ttl=15)
    quality = 90.0 if result else 0.0
    return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality)}


@router.get("/hk_hot")
async def hk_hot_stocks():
    """港股热门个股(预设 10 只蓝筹)"""
    cache_key = "global:hk_hot"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    # P2 修复(2026-07-30):None 防御 + try/except
    try:
        raw = await asyncio.to_thread(ds2.get_hk_hot_stocks)
    except Exception as e:
        logger.warning(f"get_hk_hot_stocks failed: {e}")
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    if not raw:
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    result: list[HkStockItem] = []
    for item in raw:
        result.append(HkStockItem(
            code=_safe_str(item.get("code")),
            name=_safe_str(item.get("name")),
            price=_safe_float(item.get("price")),
            prev_close=_safe_float(item.get("prev_close")),
            change=_safe_float(item.get("change")),
            change_pct=_safe_float(item.get("change_pct")),
            high=_safe_float(item.get("high")),
            low=_safe_float(item.get("low")),
            volume=_safe_float(item.get("volume")),
            currency=_safe_str(item.get("currency")) or "HKD",
        ))
    cache.set(cache_key, result, ttl=15)
    quality = 90.0 if result else 0.0
    return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality)}


@router.get("/us_realtime")
async def us_realtime(codes: str = Query(..., description="美股代码,逗号分隔,如 AAPL,TSLA")):
    """美股实时行情(按代码查询)"""
    cache_key = f"global:us_realtime:{codes}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    symbol_list = [c.strip().upper() for c in codes.split(",") if c.strip()]
    # P2 修复(2026-07-30):None 防御 + try/except
    try:
        raw = await asyncio.to_thread(ds2.get_us_stock_realtime, symbol_list)
    except Exception as e:
        logger.warning(f"get_us_stock_realtime failed: {e}")
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    if not raw:
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    result: list[UsStockItem] = []
    for item in raw:
        result.append(UsStockItem(
            code=_safe_str(item.get("code")),
            name=_safe_str(item.get("name")),
            price=_safe_float(item.get("price")),
            prev_close=_safe_float(item.get("prev_close")),
            change=_safe_float(item.get("change")),
            change_pct=_safe_float(item.get("change_pct")),
            high=_safe_float(item.get("high")),
            low=_safe_float(item.get("low")),
            volume=_safe_float(item.get("volume")),
            currency=_safe_str(item.get("currency")) or "USD",
        ))
    cache.set(cache_key, result, ttl=15)
    quality = 90.0 if result else 0.0
    return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality)}


@router.get("/hk_realtime")
async def hk_realtime(codes: str = Query(..., description="港股代码,逗号分隔,如 00700,09988")):
    """港股实时行情(按代码查询)"""
    cache_key = f"global:hk_realtime:{codes}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    # P2 修复(2026-07-30):None 防御 + try/except
    try:
        raw = await asyncio.to_thread(ds2.get_hk_stock_realtime, code_list)
    except Exception as e:
        logger.warning(f"get_hk_stock_realtime failed: {e}")
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    if not raw:
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    result: list[HkStockItem] = []
    for item in raw:
        result.append(HkStockItem(
            code=_safe_str(item.get("code")),
            name=_safe_str(item.get("name")),
            price=_safe_float(item.get("price")),
            prev_close=_safe_float(item.get("prev_close")),
            change=_safe_float(item.get("change")),
            change_pct=_safe_float(item.get("change_pct")),
            high=_safe_float(item.get("high")),
            low=_safe_float(item.get("low")),
            volume=_safe_float(item.get("volume")),
            currency=_safe_str(item.get("currency")) or "HKD",
        ))
    cache.set(cache_key, result, ttl=15)
    quality = 90.0 if result else 0.0
    return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality)}


@router.get("/overview")
async def global_overview():
    """国际市场总览(并行拉取指数 + 美股热门 + 港股热门)"""
    cache_key = "global:overview"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    # P2 修复(2026-07-30):None 防御 + try/except,原代码 raw.get() 在 raw=None 时会 AttributeError
    try:
        raw = await asyncio.to_thread(ds2.get_global_market_overview)
    except Exception as e:
        logger.warning(f"get_global_market_overview failed: {e}")
        return {"data": {"indices": [], "us_hot": [], "hk_hot": []}, "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    if not raw:
        return {"data": {"indices": [], "us_hot": [], "hk_hot": []}, "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}
    # 转换为响应模型
    indices = [GlobalIndexItem(
        code=_safe_str(i.get("code")), name=_safe_str(i.get("name")),
        price=_safe_float(i.get("price")), prev_close=_safe_float(i.get("prev_close")),
        change=_safe_float(i.get("change")), change_pct=_safe_float(i.get("change_pct")),
        high=_safe_float(i.get("high")), low=_safe_float(i.get("low")),
        market=_safe_str(i.get("market")), currency=_safe_str(i.get("currency")),
    ).model_dump() for i in raw.get("indices", [])]
    us_hot = [UsStockItem(
        code=_safe_str(i.get("code")), name=_safe_str(i.get("name")),
        price=_safe_float(i.get("price")), prev_close=_safe_float(i.get("prev_close")),
        change=_safe_float(i.get("change")), change_pct=_safe_float(i.get("change_pct")),
        high=_safe_float(i.get("high")), low=_safe_float(i.get("low")),
        volume=_safe_float(i.get("volume")), currency=_safe_str(i.get("currency")) or "USD",
    ).model_dump() for i in raw.get("us_hot", [])]
    hk_hot = [HkStockItem(
        code=_safe_str(i.get("code")), name=_safe_str(i.get("name")),
        price=_safe_float(i.get("price")), prev_close=_safe_float(i.get("prev_close")),
        change=_safe_float(i.get("change")), change_pct=_safe_float(i.get("change_pct")),
        high=_safe_float(i.get("high")), low=_safe_float(i.get("low")),
        volume=_safe_float(i.get("volume")), currency=_safe_str(i.get("currency")) or "HKD",
    ).model_dump() for i in raw.get("hk_hot", [])]
    result = {"indices": indices, "us_hot": us_hot, "hk_hot": hk_hot}
    cache.set(cache_key, result, ttl=15)
    quality = 90.0 if (indices or us_hot or hk_hot) else 0.0
    return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality)}

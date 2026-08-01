from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from src.core import data_source_v2 as ds2
from src.core.cache import DataCache
from src.core.data_validator import get_data_validator
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str

router = APIRouter()

cache = DataCache(default_ttl=60)


class ResponseMeta(BaseModel):
    """API 响应元数据 - 数据质量与来源信息"""
    data_source: str = ""
    quality_score: float = 0.0
    cached: bool = False
    timestamp: str = ""


def _build_meta(
    data_source: str,
    cached: bool = False,
    quality_score: float | None = None,
) -> dict:
    """构建响应元数据"""
    return ResponseMeta(
        data_source=data_source,
        quality_score=quality_score if quality_score is not None else (100.0 if cached else 80.0),
        cached=cached,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ).model_dump()


class StockRealtimeItem(BaseModel):
    code: str
    name: str
    price: float
    change: float
    change_pct: float
    volume: float
    amount: float
    high: float
    low: float
    open: float
    prev_close: float


class KlineItem(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


class FundRealtimeItem(BaseModel):
    code: str
    name: str
    nav: float
    estimated_nav: float
    change: float
    change_pct: float
    update_time: str


class FundHistoryItem(BaseModel):
    date: str
    nav: float
    acc_nav: float
    change_pct: float


class IndexRealtimeItem(BaseModel):
    code: str
    name: str
    price: float
    change: float
    change_pct: float
    volume: float
    amount: float


class HotStocksItem(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float


class HotStocksResponse(BaseModel):
    top_gainers: list[HotStocksItem]
    top_losers: list[HotStocksItem]
    top_volume: list[HotStocksItem]


class SectorFlowItem(BaseModel):
    sector: str
    change_pct: float
    main_net_inflow: float
    large_order_ratio: float


@router.get("/stock_realtime")
async def stock_realtime(codes: str = Query(..., max_length=200, description="股票代码，逗号分隔(最多20个)")):
    # P2-2 修复(2026-07-30):限制 codes 长度+数量,防止超长字符串撑大缓存键和外部请求 URL
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if len(code_list) > 20:
        raise HTTPException(400, "单次最多查询 20 个股票代码")
    cache_key = f"market:stock_realtime:{codes}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("tencent", cached=True)}
    # P1 修复(2026-08-01):整个函数体包裹,确保数据源/模型构造/cache.set 异常也返回空数据而非 500
    try:
        data = await asyncio.to_thread(ds2.get_realtime_quote_tencent, code_list)
        result: list[StockRealtimeItem] = []
        for item in data:
            price = _safe_float(item.get("price"))
            prev_close = _safe_float(item.get("prev_close"))
            change = _safe_float(item.get("change"))
            if change == 0.0 and price != 0.0 and prev_close != 0.0:
                change = round(price - prev_close, 2)
            result.append(StockRealtimeItem(
                code=_safe_str(item.get("code")),
                name=_safe_str(item.get("name")),
                price=price,
                change=change,
                change_pct=_safe_float(item.get("change_pct")),
                volume=_safe_float(item.get("volume")),
                amount=_safe_float(item.get("amount")),
                high=_safe_float(item.get("high")),
                low=_safe_float(item.get("low")),
                open=_safe_float(item.get("open")),
                prev_close=prev_close,
            ))
        cache.set(cache_key, result)
        quality_score = 80.0
        if result:
            validator = get_data_validator()
            scores = []
            for r in result:
                vr = validator.validate_quote(r.model_dump())
                scores.append(vr.quality_score)
            quality_score = sum(scores) / len(scores) if scores else 80.0
        return {"data": result, "_meta": _build_meta("tencent", cached=False, quality_score=quality_score)}
    except Exception as e:
        logger.warning(f"stock_realtime failed: {e}")
        return {"data": [], "_meta": _build_meta("tencent", cached=False, quality_score=0.0)}


@router.get("/stock_kline")
async def stock_kline(
    code: str = Query(..., description="股票代码"),
    period: str = Query("daily", description="周期: daily/weekly/monthly"),
    count: int = Query(120, ge=1, le=500, description="返回条数(最大500)"),
):
    cache_key = f"market:stock_kline:{code}:{period}:{count}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("mootdx", cached=True)}
    # P1 修复(2026-08-01):整个函数体包裹,确保数据源/模型构造/cache.set 异常也返回空数据而非 500
    try:
        data = await asyncio.to_thread(ds2.get_kline_mootdx, code, period=period, count=count)
        result: list[KlineItem] = []
        for item in data:
            result.append(KlineItem(
                date=_safe_str(item.get("date")),
                open=_safe_float(item.get("open")),
                high=_safe_float(item.get("high")),
                low=_safe_float(item.get("low")),
                close=_safe_float(item.get("close")),
                volume=_safe_float(item.get("volume")),
                amount=_safe_float(item.get("amount")),
            ))
        cache.set(cache_key, result, ttl=300)
        quality_score = 80.0
        if result:
            validator = get_data_validator()
            vr = validator.validate_kline([r.model_dump() for r in result], expected_count=count)
            quality_score = vr.quality_score
        return {"data": result, "_meta": _build_meta("mootdx", cached=False, quality_score=quality_score)}
    except Exception as e:
        logger.warning(f"stock_kline failed: {e}")
        return {"data": [], "_meta": _build_meta("mootdx", cached=False, quality_score=0.0)}


@router.get("/fund_realtime")
async def fund_realtime(codes: str = Query(..., description="基金代码，逗号分隔")):
    cache_key = f"market:fund_realtime:{codes}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("akshare", cached=True)}
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    data_source = "akshare"
    if _HAS_AKSHARE:
        result: list[FundRealtimeItem] = []
        try:
            for fund_code in code_list:
                try:
                    df = await asyncio.to_thread(ak.fund_em_open_fund_info, fund_code, indicator="单位净值走势")
                    if df is None or df.empty:
                        continue
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) >= 2 else latest
                    nav = _safe_float(latest.get("单位净值"))
                    prev_nav = _safe_float(prev.get("单位净值"))
                    change = nav - prev_nav if prev_nav != 0 else 0.0
                    change_pct = (change / prev_nav * 100) if prev_nav != 0 else 0.0
                    result.append(FundRealtimeItem(
                        code=fund_code, name="", nav=nav, estimated_nav=nav,
                        change=round(change, 4), change_pct=round(change_pct, 2),
                        update_time=_safe_str(latest.get("净值日期")),
                    ))
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"fetch fund_realtime via akshare failed: {e}")
        if result:
            cache.set(cache_key, result)
            return {"data": result, "_meta": _build_meta("akshare", cached=False)}
    data = await asyncio.to_thread(ds2.get_fund_realtime_tencent, code_list)
    result = [FundRealtimeItem(**item) for item in data]
    data_source = "tencent"
    cache.set(cache_key, result)
    return {"data": result, "_meta": _build_meta(data_source, cached=False)}


@router.get("/fund_history")
async def fund_history(
    code: str = Query(..., description="基金代码"),
    period: str = Query("1y", description="周期: 1m/3m/6m/1y/3y/all"),
):
    cache_key = f"market:fund_history:{code}:{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("akshare", cached=True)}
    data_source = "akshare"
    if _HAS_AKSHARE:
        try:
            df = await asyncio.to_thread(ak.fund_em_open_fund_info, code, indicator="单位净值走势")
            if df is None or df.empty:
                result = await asyncio.to_thread(_fund_history_tencent_fallback, code, period)
                data_source = "tencent"
                cache.set(cache_key, result, ttl=300)
                return {"data": result, "_meta": _build_meta(data_source, cached=False)}
            period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "all": 99999}
            days = period_days.get(period, 365)
            cutoff = datetime.now() - timedelta(days=days)
            df["净值日期"] = pd.to_datetime(df["净值日期"], errors="coerce")
            df = df[df["净值日期"] >= cutoff]
            result: list[FundHistoryItem] = []
            prev_nav: Optional[float] = None
            for _, row in df.iterrows():
                nav = _safe_float(row.get("单位净值"))
                acc_nav = _safe_float(row.get("累计净值"), nav)
                if prev_nav is not None and prev_nav != 0:
                    change_pct = round((nav - prev_nav) / prev_nav * 100, 2)
                else:
                    change_pct = 0.0
                result.append(FundHistoryItem(
                    date=_safe_str(row.get("净值日期"))[:10],
                    nav=nav, acc_nav=acc_nav, change_pct=change_pct,
                ))
                prev_nav = nav
            cache.set(cache_key, result, ttl=300)
            return {"data": result, "_meta": _build_meta("akshare", cached=False)}
        except Exception as e:
            logger.warning(f"fetch fund_history via akshare failed: {e}")
    result = await asyncio.to_thread(_fund_history_tencent_fallback, code, period)
    data_source = "tencent"
    cache.set(cache_key, result, ttl=300)
    return {"data": result, "_meta": _build_meta(data_source, cached=False)}


def _fund_history_tencent_fallback(code: str, period: str) -> list[FundHistoryItem]:
    data = ds2.get_fund_history_tencent(code, period=period)
    return [FundHistoryItem(**item) for item in data]


@router.get("/index_realtime")
async def index_realtime():
    cache_key = "market:index_realtime"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    # P1 修复(2026-07-31):整个函数体包裹,确保模型构造/cache.set 异常也返回空数据而非 500
    try:
        data = await asyncio.to_thread(ds2.get_index_realtime)
        result: list[IndexRealtimeItem] = []
        for item in data:
            result.append(IndexRealtimeItem(
                code=_safe_str(item.get("code")),
                name=_safe_str(item.get("name")),
                price=_safe_float(item.get("price")),
                change=_safe_float(item.get("change")),
                change_pct=_safe_float(item.get("change_pct")),
                volume=_safe_float(item.get("volume")),
                amount=_safe_float(item.get("amount")),
            ))
        cache.set(cache_key, result)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"index_realtime failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/hot_stocks")
async def hot_stocks():
    cache_key = "market:hot_stocks"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    # P1 修复(2026-07-31):整个函数体包裹,确保模型构造/cache.set 异常也返回空数据而非 500
    try:
        # 三个排行并行获取(用 asyncio.to_thread 避免阻塞事件循环)
        top_gainers_data, top_losers_data, top_volume_data = await asyncio.gather(
            asyncio.to_thread(ds2.get_stock_ranking_em, "f3", 0, 10),
            asyncio.to_thread(ds2.get_stock_ranking_em, "f3", 1, 10),
            asyncio.to_thread(ds2.get_stock_ranking_em, "f6", 0, 10),
        )

        def _to_item(item: dict) -> HotStocksItem:
            return HotStocksItem(
                code=_safe_str(item.get("code")),
                name=_safe_str(item.get("name")),
                price=_safe_float(item.get("price")),
                change_pct=_safe_float(item.get("change_pct")),
                volume=_safe_float(item.get("volume")),
                amount=_safe_float(item.get("amount")),
            )

        result = HotStocksResponse(
            top_gainers=[_to_item(i) for i in top_gainers_data],
            top_losers=[_to_item(i) for i in top_losers_data],
            top_volume=[_to_item(i) for i in top_volume_data],
        )
        cache.set(cache_key, result.model_dump())
        return {"data": result.model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"hot_stocks failed: {e}")
        return {"data": HotStocksResponse(top_gainers=[], top_losers=[], top_volume=[]).model_dump(), "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/sector_flow")
async def sector_flow():
    cache_key = "market:sector_flow"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    # P1 修复(2026-07-31):整个函数体包裹,确保模型构造/cache.set 异常也返回空数据而非 500
    try:
        data = await asyncio.to_thread(ds2.get_sector_ranking)
        result: list[SectorFlowItem] = []
        for item in data:
            result.append(SectorFlowItem(
                sector=_safe_str(item.get("name")),
                change_pct=_safe_float(item.get("change_pct")),
                main_net_inflow=_safe_float(item.get("main_net_inflow")),
                large_order_ratio=_safe_float(item.get("main_inflow_pct")),
            ))
        cache.set(cache_key, result)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"sector_flow failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


class ResearchReportItem(BaseModel):
    title: str
    rating: str
    eps_predict: float
    org_name: str
    publish_date: str


class DragonTigerItem(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float
    reason: str
    buy_amount: float
    sell_amount: float
    net_amount: float
    trade_date: str


class MarginTradingItem(BaseModel):
    code: str
    trade_date: str
    margin_buy: float
    margin_balance: float
    short_sell: float
    short_balance: float
    total_balance: float


class BlockTradeItem(BaseModel):
    code: str
    name: str
    trade_date: str
    price: float
    volume: float
    amount: float
    premium_pct: float
    buyer: str
    seller: str


class ShareholderItem(BaseModel):
    code: str
    end_date: str
    holder_num: int
    change_pct: float


class NewsItem(BaseModel):
    title: str
    content: str
    url: str
    source: str
    publish_time: str


class HotStockSignalItem(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    reason: str
    limit_up_time: str
    open_times: int


class SectorRankingItem(BaseModel):
    code: str
    name: str
    change_pct: float
    price: float
    main_net_inflow: float
    main_inflow_pct: float
    super_large_net: float
    super_large_pct: float
    large_net: float
    large_pct: float
    medium_net: float
    medium_pct: float
    small_net: float
    small_pct: float


@router.get("/research_reports")
async def research_reports(
    code: str = Query("", description="股票代码（可选，为空时返回最新研报）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=50, description="每页条数"),
):
    cache_key = f"market:research_reports:{code}:{page}:{page_size}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_research_reports, code, page=page, page_size=page_size)
        result = [ResearchReportItem(**item) for item in data]
        cache.set(cache_key, result, ttl=300)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"research_reports failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/dragon_tiger")
async def dragon_tiger():
    cache_key = "market:dragon_tiger"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_dragon_tiger)
        result = [DragonTigerItem(**item) for item in data]
        cache.set(cache_key, result, ttl=300)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"dragon_tiger failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/margin")
async def margin(code: str = Query("", description="股票代码")):
    if not code:
        return {"data": MarginTradingItem(code="", trade_date="", margin_buy=0, margin_balance=0, short_sell=0, short_balance=0, total_balance=0).model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
    cache_key = f"market:margin:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_margin_trading, code)
        if not data:
            return {"data": MarginTradingItem(code=code, trade_date="", margin_buy=0, margin_balance=0, short_sell=0, short_balance=0, total_balance=0).model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
        result = MarginTradingItem(**data)
        cache.set(cache_key, result.model_dump(), ttl=300)
        return {"data": result.model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"margin failed: {e}")
        return {"data": MarginTradingItem(code=code, trade_date="", margin_buy=0, margin_balance=0, short_sell=0, short_balance=0, total_balance=0).model_dump(), "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/block_trades")
async def block_trades(code: str = Query("", description="股票代码")):
    if not code:
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False)}
    cache_key = f"market:block_trades:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_block_trades, code)
        result = [BlockTradeItem(**item) for item in data]
        cache.set(cache_key, result, ttl=300)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"block_trades failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/shareholder")
async def shareholder(code: str = Query("", description="股票代码")):
    if not code:
        return {"data": ShareholderItem(code="", end_date="", holder_num=0, change_pct=0.0).model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
    cache_key = f"market:shareholder:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_shareholder_count, code)
        if not data:
            return {"data": ShareholderItem(code=code, end_date="", holder_num=0, change_pct=0.0).model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
        result = ShareholderItem(**data)
        cache.set(cache_key, result.model_dump(), ttl=300)
        return {"data": result.model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"shareholder failed: {e}")
        return {"data": ShareholderItem(code=code, end_date="", holder_num=0, change_pct=0.0).model_dump(), "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/news")
async def news(
    code: str = Query("", description="股票代码（可选，为空时返回全局新闻）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=50, description="每页条数"),
):
    cache_key = f"market:news:{code}:{page}:{page_size}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        if not code:
            data = await asyncio.to_thread(ds2.get_global_news)
            result = [NewsItem(**item) for item in data]
        else:
            data = await asyncio.to_thread(ds2.get_stock_news, code, page=page, page_size=page_size)
            result = [NewsItem(**item) for item in data]
        cache.set(cache_key, result, ttl=120)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"news failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/global_news")
async def global_news():
    cache_key = "market:global_news"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_global_news)
        result = [NewsItem(**item) for item in data]
        cache.set(cache_key, result, ttl=120)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"global_news failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/hot_stocks_signal")
async def hot_stocks_signal():
    cache_key = "market:hot_stocks_signal"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("ths", cached=True)}
    try:
        data_source = "ths"
        data = await asyncio.to_thread(ds2.get_hot_stocks_ths)
        if data:
            result = [HotStockSignalItem(**item) for item in data]
        else:
            data = await asyncio.to_thread(ds2.get_hot_stocks_signal_fallback)
            data_source = "eastmoney"
            result = [HotStockSignalItem(**item) for item in data]
        cache.set(cache_key, result)
        return {"data": result, "_meta": _build_meta(data_source, cached=False)}
    except Exception as e:
        logger.warning(f"hot_stocks_signal failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/sector_ranking")
async def sector_ranking():
    cache_key = "market:sector_ranking"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_sector_ranking)
        result = [SectorRankingItem(**item) for item in data]
        cache.set(cache_key, result)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"sector_ranking failed: {e}")
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


class HeatmapItem(BaseModel):
    name: str
    change_pct: float
    color: str


class SearchResultItem(BaseModel):
    code: str
    name: str
    price: float
    change_pct: float


class MarketStatusResponse(BaseModel):
    is_open: bool
    session: str
    next_open_time: str
    current_time: str


class StockDetailResponse(BaseModel):
    code: str
    name: str
    quote: dict
    financial: dict
    capital_flow: dict
    kline_summary: dict


@router.get("/heatmap")
async def market_heatmap():
    cache_key = "market:heatmap"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_sector_ranking)
        result: list[HeatmapItem] = []
        for item in data:
            change_pct = _safe_float(item.get("change_pct"))
            if change_pct > 0:
                intensity = min(int(change_pct * 25.5), 255)
                color = f"rgb({intensity},0,0)"
            elif change_pct < 0:
                intensity = min(int(abs(change_pct) * 25.5), 255)
                color = f"rgb(0,{intensity},0)"
            else:
                color = "rgb(128,128,128)"
            result.append(HeatmapItem(
                name=_safe_str(item.get("name")),
                change_pct=change_pct,
                color=color,
            ))
        cache.set(cache_key, result)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception:
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/search")
async def stock_search(q: str = Query(..., min_length=1, max_length=50, description="搜索关键词（股票代码或名称）")):
    cache_key = f"market:search:{q}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.search_stock, q)
        result: list[SearchResultItem] = []
        for item in data:
            result.append(SearchResultItem(
                code=_safe_str(item.get("code")),
                name=_safe_str(item.get("name")),
                price=_safe_float(item.get("price")),
                change_pct=_safe_float(item.get("change_pct")),
            ))
        cache.set(cache_key, result, ttl=600)
        return {"data": result, "_meta": _build_meta("eastmoney", cached=False)}
    except Exception:
        return {"data": [], "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/status")
async def market_status():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    current_time = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now.weekday()
    time_val = now.hour * 100 + now.minute

    is_open = False
    session = "closed"

    if weekday < 5:
        if 930 <= time_val <= 1130:
            is_open = True
            session = "morning"
        elif 1300 <= time_val <= 1500:
            is_open = True
            session = "afternoon"
        elif 1130 < time_val < 1300:
            session = "lunch_break"
        else:
            session = "closed"
    else:
        session = "weekend"

    next_open_time = ""
    if is_open:
        next_open_time = ""
    elif session == "lunch_break":
        next_open_time = now.replace(hour=13, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    elif session == "closed" and weekday < 5:
        if time_val < 930:
            next_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        else:
            from datetime import timedelta as _td
            days_ahead = 1
            next_day = now + _td(days=days_ahead)
            while next_day.weekday() >= 5:
                next_day += _td(days=1)
            next_open_time = next_day.replace(hour=9, minute=30, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    else:
        from datetime import timedelta as _td
        days_ahead = (7 - weekday) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_day = now + _td(days=days_ahead)
        next_open_time = next_day.replace(hour=9, minute=30, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    result = MarketStatusResponse(
        is_open=is_open,
        session=session,
        next_open_time=next_open_time,
        current_time=current_time,
    )
    return {"data": result.model_dump(), "_meta": _build_meta("local", cached=False, quality_score=100.0)}


@router.get("/stock_detail")
async def stock_detail(code: str = Query(..., description="股票代码")):
    cache_key = f"market:stock_detail:{code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("mixed", cached=True)}
    try:
        data = await asyncio.to_thread(ds2.get_stock_detail, code)
        result = StockDetailResponse(
            code=_safe_str(data.get("code")),
            name=_safe_str(data.get("name")),
            quote=data.get("quote", {}),
            financial=data.get("financial", {}),
            capital_flow=data.get("capital_flow", {}),
            kline_summary=data.get("kline_summary", {}),
        )
        cache.set(cache_key, result.model_dump(), ttl=120)
        # 计算数据质量评分
        validator = get_data_validator()
        vr = validator.validate_analysis_data(data)
        return {"data": result.model_dump(), "_meta": _build_meta("mixed", cached=False, quality_score=vr.quality_score)}
    except Exception:
        return {"data": StockDetailResponse(code=code, name="", quote={}, financial={}, capital_flow={}, kline_summary={}).model_dump(), "_meta": _build_meta("mixed", cached=False, quality_score=0.0)}


class MarketWideStatsResponse(BaseModel):
    margin_balance: float
    block_trades_count: int
    avg_shareholder_change_pct: float


class NorthboundFlowItem(BaseModel):
    date: str = ""
    total_net_inflow: float = 0.0
    sh_net_inflow: float = 0.0
    sz_net_inflow: float = 0.0


@router.get("/northbound")
async def northbound():
    cache_key = "market:northbound"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("eastmoney", cached=True)}
    # P1 修复(2026-08-01):整个函数体包裹,防止数据源异常导致 500
    try:
        data = await asyncio.to_thread(ds2.get_northbound_flow_realtime)
        if not data:
            return {"data": NorthboundFlowItem().model_dump(), "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}
        result = NorthboundFlowItem(
            date=_safe_str(data.get("date", "")),
            total_net_inflow=_safe_float(data.get("total_net_inflow", 0)),
            sh_net_inflow=_safe_float(data.get("sh_net_inflow", 0)),
            sz_net_inflow=_safe_float(data.get("sz_net_inflow", 0)),
        )
        cache.set(cache_key, result.model_dump())
        return {"data": result.model_dump(), "_meta": _build_meta("eastmoney", cached=False)}
    except Exception as e:
        logger.warning(f"northbound failed: {e}")
        return {"data": NorthboundFlowItem().model_dump(), "_meta": _build_meta("eastmoney", cached=False, quality_score=0.0)}


@router.get("/market_sentiment")
async def market_sentiment():
    cache_key = "market:market_sentiment"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("mixed", cached=True)}
    data = await asyncio.to_thread(ds2.get_market_sentiment)
    cache.set(cache_key, data)
    return {"data": data, "_meta": _build_meta("mixed", cached=False)}


@router.get("/market_wide_stats")
async def market_wide_stats():
    cache_key = "market:market_wide_stats"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("mixed", cached=True)}
    data = await asyncio.to_thread(ds2.get_market_wide_stats)
    result = MarketWideStatsResponse(
        margin_balance=_safe_float(data.get("margin_balance", 0)),
        block_trades_count=int(_safe_float(data.get("block_trades_count", 0))),
        avg_shareholder_change_pct=_safe_float(data.get("avg_shareholder_change_pct", 0)),
    )
    cache.set(cache_key, result.model_dump(), ttl=300)
    return {"data": result.model_dump(), "_meta": _build_meta("mixed", cached=False)}


# ===== P1-3: 大盘研判增强 =====

@router.get("/thermometer")
async def market_thermometer():
    """大盘温度计(0-100) - 综合指数趋势/板块轮动/资金流向/市场情绪"""
    cache_key = "market:thermometer"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("aggregator", cached=True)}
    from src.analysis.market_assessment import get_market_thermometer
    data = await get_market_thermometer()
    cache.set(cache_key, data, ttl=60)
    return {"data": data, "_meta": _build_meta("aggregator", cached=False)}


@router.get("/capital-flow")
async def capital_flow_overview():
    """资金流向总览(北向资金 + 板块主力净流入Top5)"""
    cache_key = "market:capital_flow"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("aggregator", cached=True)}
    from src.analysis.market_assessment import get_capital_flow_overview
    data = await get_capital_flow_overview()
    cache.set(cache_key, data, ttl=60)
    return {"data": data, "_meta": _build_meta("aggregator", cached=False)}


@router.get("/assessment")
async def market_assessment():
    """大盘综合研判(温度计 + 资金流向 + 板块轮动信号)"""
    cache_key = "market:assessment"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("aggregator", cached=True)}
    from src.analysis.market_assessment import get_market_assessment
    data = await get_market_assessment()
    cache.set(cache_key, data, ttl=60)
    return {"data": data, "_meta": _build_meta("aggregator", cached=False)}


@router.get("/data-quality/{stock_code}")
async def check_data_quality(stock_code: str):
    """检查指定股票的数据质量

    P0 修复(2026-07-29):原代码 4 个数据源调用串行同步执行,阻塞事件循环
    - 改为 asyncio.gather + asyncio.to_thread 并行执行,总耗时从 4× 降为 1×
    - 异常隔离:任一数据源失败不影响其他维度
    """
    from src.core.data_source_v2 import get_realtime_quote_tencent, get_kline_mootdx, get_capital_flow_detail, get_financial_snapshot

    async def _safe_fetch(name: str, fn, *args, **kwargs):
        try:
            return name, await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:
            logger.warning(f"fetch {name} for data_quality failed: {e}")
            return name, None

    # P0:并行抓取 4 个数据源(原串行 ~8s,并行后 ~2s)
    results = await asyncio.gather(
        _safe_fetch("quote", get_realtime_quote_tencent, [stock_code]),
        _safe_fetch("kline", get_kline_mootdx, stock_code, period="daily", count=30),
        _safe_fetch("capital_flow", get_capital_flow_detail, stock_code),
        _safe_fetch("financial", get_financial_snapshot, stock_code),
    )

    data: dict = {}
    for name, value in results:
        if value is None:
            continue
        if name == "quote" and value:
            data["quote"] = value[0]
        elif name == "kline" and value:
            data["kline_daily"] = value
        elif name == "capital_flow" and value:
            data["capital_flow"] = value
        elif name == "financial" and value:
            data["financial"] = value

    validator = get_data_validator()
    result = validator.validate_analysis_data(data)

    return {
        "stock_code": stock_code,
        "quality_score": result.quality_score,
        "is_valid": result.is_valid,
        "warnings": result.warnings,
        "criticals": result.criticals,
        "issues_count": len(result.issues),
        "data_dimensions": {
            "quote": bool(data.get("quote")),
            "kline": bool(data.get("kline_daily")),
            "capital_flow": bool(data.get("capital_flow")),
            "financial": bool(data.get("financial")),
        },
    }

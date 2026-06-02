from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False
import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core import data_source_v2 as ds2

router = APIRouter()


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


def _safe_float(val: object, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    try:
        result = float(val)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def _safe_str(val: object, default: str = "") -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    return str(val)


@router.get("/stock_realtime", response_model=list[StockRealtimeItem])
async def stock_realtime(codes: str = Query(..., description="股票代码，逗号分隔")):
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    data = ds2.get_realtime_quote_tencent(code_list)
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
    return result


@router.get("/stock_kline", response_model=list[KlineItem])
async def stock_kline(
    code: str = Query(..., description="股票代码"),
    period: str = Query("daily", description="周期: daily/weekly/monthly"),
    count: int = Query(120, description="返回条数"),
):
    data = ds2.get_kline_mootdx(code, period=period, count=count)
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
    return result


@router.get("/fund_realtime", response_model=list[FundRealtimeItem])
async def fund_realtime(codes: str = Query(..., description="基金代码，逗号分隔")):
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if _HAS_AKSHARE:
        result: list[FundRealtimeItem] = []
        try:
            for fund_code in code_list:
                try:
                    df = ak.fund_em_open_fund_info(fund_code, indicator="单位净值走势")
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
        except Exception:
            pass
        if result:
            return result
    data = ds2.get_fund_realtime_tencent(code_list)
    return [FundRealtimeItem(**item) for item in data]


@router.get("/fund_history", response_model=list[FundHistoryItem])
async def fund_history(
    code: str = Query(..., description="基金代码"),
    period: str = Query("1y", description="周期: 1m/3m/6m/1y/3y/all"),
):
    if not _HAS_AKSHARE:
        return []
    try:
        df = ak.fund_em_open_fund_info(code, indicator="单位净值走势")
        if df is None or df.empty:
            return []
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
        return result
    except Exception:
        return []


@router.get("/index_realtime", response_model=list[IndexRealtimeItem])
async def index_realtime():
    data = ds2.get_index_realtime()
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
    return result


@router.get("/hot_stocks", response_model=HotStocksResponse)
async def hot_stocks():
    top_gainers_data = ds2.get_stock_ranking_em(sort_field="f3", sort_order=0, count=10)
    top_losers_data = ds2.get_stock_ranking_em(sort_field="f3", sort_order=1, count=10)
    top_volume_data = ds2.get_stock_ranking_em(sort_field="f6", sort_order=0, count=10)

    def _to_item(item: dict) -> HotStocksItem:
        return HotStocksItem(
            code=_safe_str(item.get("code")),
            name=_safe_str(item.get("name")),
            price=_safe_float(item.get("price")),
            change_pct=_safe_float(item.get("change_pct")),
            volume=_safe_float(item.get("volume")),
            amount=_safe_float(item.get("amount")),
        )

    return HotStocksResponse(
        top_gainers=[_to_item(i) for i in top_gainers_data],
        top_losers=[_to_item(i) for i in top_losers_data],
        top_volume=[_to_item(i) for i in top_volume_data],
    )


@router.get("/sector_flow", response_model=list[SectorFlowItem])
async def sector_flow():
    data = ds2.get_sector_ranking()
    result: list[SectorFlowItem] = []
    for item in data:
        result.append(SectorFlowItem(
            sector=_safe_str(item.get("name")),
            change_pct=_safe_float(item.get("change_pct")),
            main_net_inflow=_safe_float(item.get("main_net_inflow")),
            large_order_ratio=_safe_float(item.get("main_inflow_pct")),
        ))
    return result


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


@router.get("/research_reports", response_model=list[ResearchReportItem])
async def research_reports(
    code: str = Query("", description="股票代码（可选，为空时返回最新研报）"),
    page: int = Query(1, description="页码"),
    page_size: int = Query(10, description="每页条数"),
):
    data = ds2.get_research_reports(code, page=page, page_size=page_size)
    return [ResearchReportItem(**item) for item in data]


@router.get("/dragon_tiger", response_model=list[DragonTigerItem])
async def dragon_tiger():
    data = ds2.get_dragon_tiger()
    return [DragonTigerItem(**item) for item in data]


@router.get("/margin", response_model=MarginTradingItem)
async def margin(code: str = Query("", description="股票代码")):
    if not code:
        return MarginTradingItem(code="", trade_date="", margin_buy=0, margin_balance=0, short_sell=0, short_balance=0, total_balance=0)
    data = ds2.get_margin_trading(code)
    if not data:
        return MarginTradingItem(code=code, trade_date="", margin_buy=0, margin_balance=0, short_sell=0, short_balance=0, total_balance=0)
    return MarginTradingItem(**data)


@router.get("/block_trades", response_model=list[BlockTradeItem])
async def block_trades(code: str = Query("", description="股票代码")):
    if not code:
        return []
    data = ds2.get_block_trades(code)
    return [BlockTradeItem(**item) for item in data]


@router.get("/shareholder", response_model=ShareholderItem)
async def shareholder(code: str = Query("", description="股票代码")):
    if not code:
        return ShareholderItem(code="", end_date="", holder_num=0, change_pct=0.0)
    data = ds2.get_shareholder_count(code)
    if not data:
        return ShareholderItem(code=code, end_date="", holder_num=0, change_pct=0.0)
    return ShareholderItem(**data)


@router.get("/news", response_model=list[NewsItem])
async def news(
    code: str = Query(..., description="股票代码"),
    page: int = Query(1, description="页码"),
    page_size: int = Query(10, description="每页条数"),
):
    data = ds2.get_stock_news(code, page=page, page_size=page_size)
    return [NewsItem(**item) for item in data]


@router.get("/global_news", response_model=list[NewsItem])
async def global_news():
    data = ds2.get_global_news()
    return [NewsItem(**item) for item in data]


@router.get("/hot_stocks_signal", response_model=list[HotStockSignalItem])
async def hot_stocks_signal():
    data = ds2.get_hot_stocks_ths()
    if data:
        return [HotStockSignalItem(**item) for item in data]
    data = ds2.get_hot_stocks_signal_fallback()
    return [HotStockSignalItem(**item) for item in data]


@router.get("/sector_ranking", response_model=list[SectorRankingItem])
async def sector_ranking():
    data = ds2.get_sector_ranking()
    return [SectorRankingItem(**item) for item in data]


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


@router.get("/heatmap", response_model=list[HeatmapItem])
async def market_heatmap():
    try:
        data = ds2.get_sector_ranking()
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
        return result
    except Exception:
        return []


@router.get("/search", response_model=list[SearchResultItem])
async def stock_search(q: str = Query(..., description="搜索关键词（股票代码或名称）")):
    try:
        data = ds2.search_stock(q)
        result: list[SearchResultItem] = []
        for item in data:
            result.append(SearchResultItem(
                code=_safe_str(item.get("code")),
                name=_safe_str(item.get("name")),
                price=_safe_float(item.get("price")),
                change_pct=_safe_float(item.get("change_pct")),
            ))
        return result
    except Exception:
        return []


@router.get("/status", response_model=MarketStatusResponse)
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

    return MarketStatusResponse(
        is_open=is_open,
        session=session,
        next_open_time=next_open_time,
        current_time=current_time,
    )


@router.get("/stock_detail", response_model=StockDetailResponse)
async def stock_detail(code: str = Query(..., description="股票代码")):
    try:
        data = ds2.get_stock_detail(code)
        return StockDetailResponse(
            code=_safe_str(data.get("code")),
            name=_safe_str(data.get("name")),
            quote=data.get("quote", {}),
            financial=data.get("financial", {}),
            capital_flow=data.get("capital_flow", {}),
            kline_summary=data.get("kline_summary", {}),
        )
    except Exception:
        return StockDetailResponse(
            code=code, name="", quote={}, financial={}, capital_flow={}, kline_summary={},
        )

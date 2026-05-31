from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core import data_source_v2 as ds2

router = APIRouter()

try:
    import akshare as ak
    import pandas as pd
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False


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


def _mock_stock_realtime(codes: str) -> list[StockRealtimeItem]:
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    names = {"000001": "平安银行", "600519": "贵州茅台", "000858": "五粮液",
             "601318": "中国平安", "000333": "美的集团", "600036": "招商银行",
             "002714": "牧原股份", "601012": "隆基绿能", "300750": "宁德时代",
             "002594": "比亚迪"}
    result: list[StockRealtimeItem] = []
    for code in code_list:
        name = names.get(code, f"股票{code}")
        base_price = random.uniform(10, 200)
        change_pct = random.uniform(-5, 5)
        price = round(base_price * (1 + change_pct / 100), 2)
        change = round(base_price * change_pct / 100, 2)
        result.append(StockRealtimeItem(
            code=code, name=name, price=price, change=change,
            change_pct=round(change_pct, 2),
            volume=random.uniform(100000, 50000000),
            amount=random.uniform(10000000, 500000000),
            high=round(price * 1.03, 2), low=round(price * 0.97, 2),
            open=round(price * random.uniform(0.98, 1.02), 2),
            prev_close=round(base_price, 2),
        ))
    return result


def _mock_kline(code: str, count: int) -> list[KlineItem]:
    result: list[KlineItem] = []
    base = random.uniform(10, 100)
    for i in range(count):
        d = (datetime.now() - timedelta(days=count - i))
        if d.weekday() >= 5:
            continue
        o = round(base * random.uniform(0.97, 1.03), 2)
        c = round(base * random.uniform(0.97, 1.03), 2)
        h = round(max(o, c) * random.uniform(1.0, 1.03), 2)
        l = round(min(o, c) * random.uniform(0.97, 1.0), 2)
        result.append(KlineItem(
            date=d.strftime("%Y-%m-%d"), open=o, high=h, low=l, close=c,
            volume=random.uniform(50000, 5000000),
            amount=random.uniform(5000000, 500000000),
        ))
        base = c
    return result


def _mock_index_realtime() -> list[IndexRealtimeItem]:
    indices = [
        ("000001", "上证指数", 3350.0), ("399001", "深证成指", 10800.0),
        ("399006", "创业板指", 2150.0),
    ]
    result: list[IndexRealtimeItem] = []
    for code, name, base in indices:
        change_pct = random.uniform(-2, 2)
        price = round(base * (1 + change_pct / 100), 2)
        change = round(base * change_pct / 100, 2)
        result.append(IndexRealtimeItem(
            code=code, name=name, price=price, change=change,
            change_pct=round(change_pct, 2),
            volume=random.uniform(1e8, 5e8),
            amount=random.uniform(3e11, 6e11),
        ))
    return result


def _mock_hot_stocks() -> HotStocksResponse:
    stocks = [
        ("000001", "平安银行"), ("600519", "贵州茅台"), ("000858", "五粮液"),
        ("601318", "中国平安"), ("000333", "美的集团"), ("600036", "招商银行"),
        ("002714", "牧原股份"), ("601012", "隆基绿能"), ("300750", "宁德时代"),
        ("002594", "比亚迪"), ("600900", "长江电力"), ("601899", "紫金矿业"),
    ]
    gainers, losers, volume_list = [], [], []
    for code, name in stocks[:10]:
        pct = random.uniform(3, 10)
        price = round(random.uniform(10, 200), 2)
        gainers.append(HotStocksItem(code=code, name=name, price=price,
                                     change_pct=round(pct, 2),
                                     volume=random.uniform(1e6, 1e7),
                                     amount=random.uniform(1e8, 1e9)))
    for code, name in stocks[2:12]:
        pct = random.uniform(-10, -3)
        price = round(random.uniform(10, 200), 2)
        losers.append(HotStocksItem(code=code, name=name, price=price,
                                    change_pct=round(pct, 2),
                                    volume=random.uniform(1e6, 1e7),
                                    amount=random.uniform(1e8, 1e9)))
    for code, name in stocks[:10]:
        price = round(random.uniform(10, 200), 2)
        volume_list.append(HotStocksItem(code=code, name=name, price=price,
                                         change_pct=round(random.uniform(-3, 3), 2),
                                         volume=random.uniform(5e7, 2e8),
                                         amount=random.uniform(5e9, 2e10)))
    return HotStocksResponse(top_gainers=gainers, top_losers=losers, top_volume=volume_list)


def _mock_sector_flow() -> list[SectorFlowItem]:
    sectors = ["半导体", "新能源", "医药生物", "消费电子", "汽车", "银行", "房地产",
               "计算机", "通信", "传媒", "食品饮料", "电力设备", "军工", "化工"]
    result: list[SectorFlowItem] = []
    for s in sectors:
        result.append(SectorFlowItem(
            sector=s,
            change_pct=round(random.uniform(-3, 3), 2),
            main_net_inflow=round(random.uniform(-5e9, 5e9), 0),
            large_order_ratio=round(random.uniform(-5, 5), 2),
        ))
    return result


@router.get("/stock_realtime", response_model=list[StockRealtimeItem])
async def stock_realtime(codes: str = Query(..., description="股票代码，逗号分隔")):
    if not _HAS_AKSHARE:
        return _mock_stock_realtime(codes)
    try:
        df = ak.stock_zh_a_spot_em()
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        df_filtered = df[df["代码"].isin(code_list)]
        result: list[StockRealtimeItem] = []
        for _, row in df_filtered.iterrows():
            price = _safe_float(row.get("最新价"))
            prev_close = _safe_float(row.get("昨收"))
            change = _safe_float(row.get("涨跌额"))
            change_pct = _safe_float(row.get("涨跌幅"))
            result.append(StockRealtimeItem(
                code=_safe_str(row.get("代码")),
                name=_safe_str(row.get("名称")),
                price=price, change=change, change_pct=change_pct,
                volume=_safe_float(row.get("成交量")),
                amount=_safe_float(row.get("成交额")),
                high=_safe_float(row.get("最高")),
                low=_safe_float(row.get("最低")),
                open=_safe_float(row.get("今开")),
                prev_close=prev_close,
            ))
        return result
    except Exception:
        return _mock_stock_realtime(codes)


@router.get("/stock_kline", response_model=list[KlineItem])
async def stock_kline(
    code: str = Query(..., description="股票代码"),
    period: str = Query("daily", description="周期: daily/weekly/monthly"),
    count: int = Query(120, description="返回条数"),
):
    if not _HAS_AKSHARE:
        return _mock_kline(code, count)
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
        period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
        ak_period = period_map.get(period, "daily")
        df = ak.stock_zh_a_hist(
            symbol=code, period=ak_period,
            start_date=start_date, end_date=end_date, adjust="qfq"
        )
        df = df.tail(count)
        result: list[KlineItem] = []
        for _, row in df.iterrows():
            result.append(KlineItem(
                date=_safe_str(row.get("日期")),
                open=_safe_float(row.get("开盘")),
                high=_safe_float(row.get("最高")),
                low=_safe_float(row.get("最低")),
                close=_safe_float(row.get("收盘")),
                volume=_safe_float(row.get("成交量")),
                amount=_safe_float(row.get("成交额")),
            ))
        return result
    except Exception:
        return _mock_kline(code, count)


@router.get("/fund_realtime", response_model=list[FundRealtimeItem])
async def fund_realtime(codes: str = Query(..., description="基金代码，逗号分隔")):
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not _HAS_AKSHARE:
        result: list[FundRealtimeItem] = []
        for fc in code_list:
            nav = round(random.uniform(0.5, 5.0), 4)
            change_pct = round(random.uniform(-3, 3), 2)
            result.append(FundRealtimeItem(
                code=fc, name=f"基金{fc}", nav=nav, estimated_nav=nav,
                change=round(nav * change_pct / 100, 4),
                change_pct=change_pct,
                update_time=datetime.now().strftime("%Y-%m-%d"),
            ))
        return result
    result: list[FundRealtimeItem] = []
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
    return result


@router.get("/fund_history", response_model=list[FundHistoryItem])
async def fund_history(
    code: str = Query(..., description="基金代码"),
    period: str = Query("1y", description="周期: 1m/3m/6m/1y/3y/all"),
):
    if not _HAS_AKSHARE:
        period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "all": 99999}
        days = period_days.get(period, 365)
        result: list[FundHistoryItem] = []
        nav = random.uniform(0.8, 3.0)
        for i in range(min(days, 365)):
            d = (datetime.now() - timedelta(days=min(days, 365) - i))
            if d.weekday() >= 5:
                continue
            pct = random.uniform(-2, 2)
            nav = round(nav * (1 + pct / 100), 4)
            acc_nav = round(nav * 1.3, 4)
            result.append(FundHistoryItem(
                date=d.strftime("%Y-%m-%d"), nav=nav, acc_nav=acc_nav,
                change_pct=round(pct, 2),
            ))
        return result
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
    if not _HAS_AKSHARE:
        return _mock_index_realtime()
    try:
        df = ak.stock_zh_index_spot_em(symbol="上证系列指数")
        df_sz = ak.stock_zh_index_spot_em(symbol="深证系列指数")
        df_cy = ak.stock_zh_index_spot_em(symbol="创业板指数")
        target_codes = {"000001", "399001", "399006"}
        combined = pd.concat([df, df_sz, df_cy], ignore_index=True)
        combined = combined[combined["代码"].isin(target_codes)]
        result: list[IndexRealtimeItem] = []
        for _, row in combined.iterrows():
            result.append(IndexRealtimeItem(
                code=_safe_str(row.get("代码")),
                name=_safe_str(row.get("名称")),
                price=_safe_float(row.get("最新价")),
                change=_safe_float(row.get("涨跌额")),
                change_pct=_safe_float(row.get("涨跌幅")),
                volume=_safe_float(row.get("成交量")),
                amount=_safe_float(row.get("成交额")),
            ))
        return result
    except Exception:
        return _mock_index_realtime()


@router.get("/hot_stocks", response_model=HotStocksResponse)
async def hot_stocks():
    if not _HAS_AKSHARE:
        return _mock_hot_stocks()
    try:
        df = ak.stock_zh_a_spot_em()
        df = df.dropna(subset=["涨跌幅", "成交额"])
        top_gainers = df.nlargest(10, "涨跌幅")
        top_losers = df.nsmallest(10, "涨跌幅")
        top_volume = df.nlargest(10, "成交额")

        def _to_item(row) -> HotStocksItem:
            return HotStocksItem(
                code=_safe_str(row.get("代码")),
                name=_safe_str(row.get("名称")),
                price=_safe_float(row.get("最新价")),
                change_pct=_safe_float(row.get("涨跌幅")),
                volume=_safe_float(row.get("成交量")),
                amount=_safe_float(row.get("成交额")),
            )

        return HotStocksResponse(
            top_gainers=[_to_item(r) for _, r in top_gainers.iterrows()],
            top_losers=[_to_item(r) for _, r in top_losers.iterrows()],
            top_volume=[_to_item(r) for _, r in top_volume.iterrows()],
        )
    except Exception:
        return _mock_hot_stocks()


@router.get("/sector_flow", response_model=list[SectorFlowItem])
async def sector_flow():
    if not _HAS_AKSHARE:
        return _mock_sector_flow()
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日")
        result: list[SectorFlowItem] = []
        for _, row in df.iterrows():
            result.append(SectorFlowItem(
                sector=_safe_str(row.get("名称")),
                change_pct=_safe_float(row.get("涨跌幅")),
                main_net_inflow=_safe_float(row.get("主力净流入-净额")),
                large_order_ratio=_safe_float(row.get("主力净流入-净占比")),
            ))
        return result
    except Exception:
        return _mock_sector_flow()


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
    code: str = Query(..., description="股票代码"),
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
async def margin(code: str = Query(..., description="股票代码")):
    data = ds2.get_margin_trading(code)
    if not data:
        return MarginTradingItem(code=code, trade_date="", margin_buy=0, margin_balance=0, short_sell=0, short_balance=0, total_balance=0)
    return MarginTradingItem(**data)


@router.get("/block_trades", response_model=list[BlockTradeItem])
async def block_trades(code: str = Query(..., description="股票代码")):
    data = ds2.get_block_trades(code)
    return [BlockTradeItem(**item) for item in data]


@router.get("/shareholder", response_model=ShareholderItem)
async def shareholder(code: str = Query(..., description="股票代码")):
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
    return [HotStockSignalItem(**item) for item in data]


@router.get("/sector_ranking", response_model=list[SectorRankingItem])
async def sector_ranking():
    data = ds2.get_sector_ranking()
    return [SectorRankingItem(**item) for item in data]

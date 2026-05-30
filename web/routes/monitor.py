from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_HAS_MONITOR = False
try:
    from src.analysis.capital_flow import analyze_capital_flow
    from src.strategies.stock_monitor import AlertType, StockMonitor
    _HAS_MONITOR = True
except ImportError:
    pass


class AlertItem(BaseModel):
    stock_code: str
    alert_type: str
    severity: str
    message: str
    detail: dict[str, Any]


class WatchlistItem(BaseModel):
    stock_code: str
    rules: list[str]


class AddWatchlistRequest(BaseModel):
    stock_code: str
    rules: list[str] | None = None


class MessageResponse(BaseModel):
    success: bool
    message: str


class CapitalFlowResponse(BaseModel):
    main_net_inflow: float
    large_order_ratio: float
    medium_order_ratio: float
    small_order_ratio: float
    northbound_change: float


class NorthboundResponse(BaseModel):
    total_net_inflow: float
    sh_net_inflow: float
    sz_net_inflow: float
    top_stocks: list[dict[str, Any]]


_mock_watchlist: dict[str, list[str]] = {
    "000001": ["price_surge", "rsi"],
    "600519": ["northbound_flow", "volume_price"],
}


@router.get("/alerts", response_model=list[AlertItem])
async def alerts(stock_code: str = ""):
    if not stock_code:
        return []
    if not _HAS_MONITOR:
        return [AlertItem(
            stock_code=stock_code, alert_type="price_surge",
            severity=random.choice(["info", "warning", "critical"]),
            message=f"{stock_code} 涨幅异常（模拟数据）",
            detail={"change_pct": round(random.uniform(5, 15), 2)},
        )]
    try:
        monitor = StockMonitor()
        results = monitor.check_alerts(stock_code)
        return [AlertItem(
            stock_code=a["stock_code"], alert_type=a["alert_type"],
            severity=a["severity"], message=a["message"],
            detail=a.get("detail", {}),
        ) for a in results]
    except Exception:
        return []


@router.get("/watchlist", response_model=list[WatchlistItem])
async def watchlist():
    if not _HAS_MONITOR:
        return [WatchlistItem(stock_code=code, rules=rules)
                for code, rules in _mock_watchlist.items()]
    try:
        monitor = StockMonitor()
        items: list[WatchlistItem] = []
        for code, rules in monitor._watchlist.items():
            items.append(WatchlistItem(stock_code=code, rules=[r.value for r in rules]))
        return items
    except Exception:
        return [WatchlistItem(stock_code=code, rules=rules)
                for code, rules in _mock_watchlist.items()]


@router.post("/watchlist", response_model=MessageResponse)
async def add_watchlist(req: AddWatchlistRequest):
    if not _HAS_MONITOR:
        _mock_watchlist[req.stock_code] = req.rules or ["price_surge"]
        return MessageResponse(success=True, message=f"已添加 {req.stock_code} 到自选（模拟）")
    try:
        monitor = StockMonitor()
        rules = None
        if req.rules:
            rule_map = {r.value: r for r in AlertType}
            rules = [rule_map[r] for r in req.rules if r in rule_map]
        monitor.add_watch(req.stock_code, rules)
        return MessageResponse(success=True, message=f"已添加 {req.stock_code} 到自选")
    except Exception:
        return MessageResponse(success=False, message="添加失败")


@router.delete("/watchlist/{stock_code}", response_model=MessageResponse)
async def remove_watchlist(stock_code: str):
    if not _HAS_MONITOR:
        _mock_watchlist.pop(stock_code, None)
        return MessageResponse(success=True, message=f"已移除 {stock_code}（模拟）")
    try:
        monitor = StockMonitor()
        monitor.remove_watch(stock_code)
        return MessageResponse(success=True, message=f"已移除 {stock_code}")
    except Exception:
        return MessageResponse(success=False, message="移除失败")


@router.get("/capital_flow", response_model=CapitalFlowResponse)
async def capital_flow(stock_code: str):
    if not _HAS_MONITOR:
        return CapitalFlowResponse(
            main_net_inflow=round(random.uniform(-5e8, 5e8), 0),
            large_order_ratio=round(random.uniform(-10, 10), 2),
            medium_order_ratio=round(random.uniform(-5, 5), 2),
            small_order_ratio=round(random.uniform(-3, 3), 2),
            northbound_change=round(random.uniform(-2, 2), 2),
        )
    try:
        result = analyze_capital_flow(stock_code)
        return CapitalFlowResponse(
            main_net_inflow=result.get("main_net_inflow", 0.0),
            large_order_ratio=result.get("large_order_ratio", 0.0),
            medium_order_ratio=result.get("medium_order_ratio", 0.0),
            small_order_ratio=result.get("small_order_ratio", 0.0),
            northbound_change=result.get("northbound_change", 0.0),
        )
    except Exception:
        return CapitalFlowResponse(
            main_net_inflow=0, large_order_ratio=0, medium_order_ratio=0,
            small_order_ratio=0, northbound_change=0,
        )


@router.get("/northbound", response_model=NorthboundResponse)
async def northbound():
    if not _HAS_MONITOR:
        return NorthboundResponse(
            total_net_inflow=round(random.uniform(-3e9, 3e9), 0),
            sh_net_inflow=round(random.uniform(-2e9, 2e9), 0),
            sz_net_inflow=round(random.uniform(-1e9, 1e9), 0),
            top_stocks=[{"code": "600519", "name": "贵州茅台",
                         "net_inflow": round(random.uniform(-5e7, 5e7), 0)}],
        )
    try:
        from src.strategies.trading_quant import TradingQuant
        quant = TradingQuant()
        result = quant.northbound_flow()
        return NorthboundResponse(
            total_net_inflow=result.get("total_net_inflow", 0.0),
            sh_net_inflow=result.get("sh_net_inflow", 0.0),
            sz_net_inflow=result.get("sz_net_inflow", 0.0),
            top_stocks=result.get("top_stocks", []),
        )
    except Exception:
        return NorthboundResponse(
            total_net_inflow=0, sh_net_inflow=0, sz_net_inflow=0, top_stocks=[],
        )

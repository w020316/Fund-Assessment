from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from src.analysis.capital_flow import analyze_capital_flow
from src.strategies.stock_monitor import AlertType, StockMonitor

router = APIRouter()

_monitor = StockMonitor()


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


@router.get("/alerts", response_model=list[AlertItem])
async def alerts(stock_code: str = ""):
    if not stock_code:
        return []
    results = _monitor.check_alerts(stock_code)
    return [
        AlertItem(
            stock_code=a["stock_code"],
            alert_type=a["alert_type"],
            severity=a["severity"],
            message=a["message"],
            detail=a.get("detail", {}),
        )
        for a in results
    ]


@router.get("/watchlist", response_model=list[WatchlistItem])
async def watchlist():
    items: list[WatchlistItem] = []
    for code, rules in _monitor._watchlist.items():
        items.append(WatchlistItem(
            stock_code=code,
            rules=[r.value for r in rules],
        ))
    return items


@router.post("/watchlist", response_model=MessageResponse)
async def add_watchlist(req: AddWatchlistRequest):
    rules: list[AlertType] | None = None
    if req.rules:
        rule_map = {r.value: r for r in AlertType}
        rules = [rule_map[r] for r in req.rules if r in rule_map]
    _monitor.add_watch(req.stock_code, rules)
    return MessageResponse(success=True, message=f"已添加 {req.stock_code} 到自选")


@router.delete("/watchlist/{stock_code}", response_model=MessageResponse)
async def remove_watchlist(stock_code: str):
    _monitor.remove_watch(stock_code)
    return MessageResponse(success=True, message=f"已移除 {stock_code}")


@router.get("/capital_flow", response_model=CapitalFlowResponse)
async def capital_flow(stock_code: str):
    result = analyze_capital_flow(stock_code)
    return CapitalFlowResponse(
        main_net_inflow=result.get("main_net_inflow", 0.0),
        large_order_ratio=result.get("large_order_ratio", 0.0),
        medium_order_ratio=result.get("medium_order_ratio", 0.0),
        small_order_ratio=result.get("small_order_ratio", 0.0),
        northbound_change=result.get("northbound_change", 0.0),
    )


@router.get("/northbound", response_model=NorthboundResponse)
async def northbound():
    from src.strategies.trading_quant import TradingQuant
    quant = TradingQuant()
    result = quant.northbound_flow()
    return NorthboundResponse(
        total_net_inflow=result.get("total_net_inflow", 0.0),
        sh_net_inflow=result.get("sh_net_inflow", 0.0),
        sz_net_inflow=result.get("sz_net_inflow", 0.0),
        top_stocks=result.get("top_stocks", []),
    )

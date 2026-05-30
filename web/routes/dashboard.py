from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()

_HAS_CORE = False
try:
    from src.core.data_source import DataSourceManager
    from src.core.executor import SimulatedBroker, TradeExecutor
    from src.core.risk_manager import RiskManager
    _HAS_CORE = True
except ImportError:
    pass


class OverviewResponse(BaseModel):
    available_cash: float
    total_assets: float
    market_value: float
    daily_pnl: float
    daily_pnl_pct: float
    position_count: int
    risk_level: str
    risk_message: str


class PositionItem(BaseModel):
    symbol: str
    name: str
    quantity: float
    available_quantity: float
    cost_price: float
    current_price: float
    market_value: float
    profit: float
    profit_pct: float


class TradeItem(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    amount: float
    commission: float
    stamp_tax: float
    net_amount: float
    strategy: str
    reason: str
    created_at: str


class RiskResponse(BaseModel):
    level: str
    total_assets: float
    peak_assets: float
    drawdown_pct: float
    daily_pnl: float
    daily_pnl_pct: float
    consecutive_stop_losses: int
    is_paused: bool
    pause_until: str | None
    is_emergency_stopped: bool
    no_new_positions: bool
    position_reduction: float
    message: str


def _get_state(request: Request) -> dict[str, Any]:
    if hasattr(request.app.state, "app_state"):
        return request.app.state.app_state
    from web.api import app_state
    return app_state


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request):
    if not _HAS_CORE:
        return OverviewResponse(
            available_cash=800000.0, total_assets=1000000.0,
            market_value=200000.0, daily_pnl=15000.0,
            daily_pnl_pct=1.5, position_count=3,
            risk_level="NORMAL", risk_message="模拟数据 - 系统正常运行",
        )
    state = _get_state(request)
    broker = state["broker"]
    risk_manager = state["risk_manager"]
    balance = broker.get_balance()
    positions = broker.get_positions()
    risk_status = risk_manager.get_risk_status()
    return OverviewResponse(
        available_cash=balance.available_cash,
        total_assets=balance.total_assets,
        market_value=balance.market_value,
        daily_pnl=risk_status.daily_pnl,
        daily_pnl_pct=risk_status.daily_pnl_pct,
        position_count=len(positions),
        risk_level=risk_status.level.value,
        risk_message=risk_status.message,
    )


@router.get("/positions", response_model=list[PositionItem])
async def positions(request: Request):
    if not _HAS_CORE:
        return [
            PositionItem(symbol="000001", name="平安银行", quantity=1000,
                         available_quantity=1000, cost_price=12.50,
                         current_price=13.20, market_value=13200.0,
                         profit=700.0, profit_pct=5.6),
            PositionItem(symbol="600519", name="贵州茅台", quantity=10,
                         available_quantity=10, cost_price=1680.00,
                         current_price=1720.00, market_value=17200.0,
                         profit=400.0, profit_pct=2.38),
            PositionItem(symbol="300750", name="宁德时代", quantity=200,
                         available_quantity=200, cost_price=195.00,
                         current_price=188.50, market_value=37700.0,
                         profit=-1300.0, profit_pct=-3.33),
        ]
    state = _get_state(request)
    broker = state["broker"]
    pos_list = broker.get_positions()
    return [
        PositionItem(
            symbol=p.symbol, name=p.name, quantity=p.quantity,
            available_quantity=p.available_quantity, cost_price=p.cost_price,
            current_price=p.current_price, market_value=p.market_value,
            profit=p.profit, profit_pct=p.profit_pct,
        )
        for p in pos_list
    ]


@router.get("/trades", response_model=list[TradeItem])
async def trades(request: Request, limit: int = 20):
    if not _HAS_CORE:
        return [
            TradeItem(trade_id="T001", order_id="O001", symbol="000001",
                      side="buy", price=12.50, quantity=1000, amount=12500.0,
                      commission=3.75, stamp_tax=0, net_amount=12503.75,
                      strategy="new_high", reason="模拟交易",
                      created_at="2026-05-30 09:35:00"),
            TradeItem(trade_id="T002", order_id="O002", symbol="600519",
                      side="buy", price=1680.00, quantity=10, amount=16800.0,
                      commission=5.04, stamp_tax=0, net_amount=16805.04,
                      strategy="long_value", reason="模拟交易",
                      created_at="2026-05-30 10:15:00"),
        ]
    state = _get_state(request)
    executor = state["executor"]
    history = executor.get_trade_history(limit=limit)
    return [
        TradeItem(
            trade_id=str(row.get("trade_id", "")),
            order_id=str(row.get("order_id", "")),
            symbol=str(row.get("symbol", "")),
            side=str(row.get("side", "")),
            price=float(row.get("price", 0)),
            quantity=float(row.get("quantity", 0)),
            amount=float(row.get("amount", 0)),
            commission=float(row.get("commission", 0)),
            stamp_tax=float(row.get("stamp_tax", 0)),
            net_amount=float(row.get("net_amount", 0)),
            strategy=str(row.get("strategy", "")),
            reason=str(row.get("reason", "")),
            created_at=str(row.get("created_at", "")),
        )
        for row in history
    ]


@router.get("/risk", response_model=RiskResponse)
async def risk(request: Request):
    if not _HAS_CORE:
        return RiskResponse(
            level="NORMAL", total_assets=1000000.0, peak_assets=1020000.0,
            drawdown_pct=1.96, daily_pnl=15000.0, daily_pnl_pct=1.5,
            consecutive_stop_losses=0, is_paused=False, pause_until=None,
            is_emergency_stopped=False, no_new_positions=False,
            position_reduction=1.0, message="模拟数据 - 风控正常",
        )
    state = _get_state(request)
    risk_manager = state["risk_manager"]
    status = risk_manager.get_risk_status()
    return RiskResponse(
        level=status.level.value,
        total_assets=status.total_assets,
        peak_assets=status.peak_assets,
        drawdown_pct=status.drawdown_pct,
        daily_pnl=status.daily_pnl,
        daily_pnl_pct=status.daily_pnl_pct,
        consecutive_stop_losses=status.consecutive_stop_losses,
        is_paused=status.is_paused,
        pause_until=status.pause_until.isoformat() if status.pause_until else None,
        is_emergency_stopped=status.is_emergency_stopped,
        no_new_positions=status.no_new_positions,
        position_reduction=status.position_reduction,
        message=status.message,
    )

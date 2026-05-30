from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()

_HAS_EXECUTOR = False
try:
    from src.core.executor import OrderSide, OrderType, Signal
    _HAS_EXECUTOR = True
except ImportError:
    pass


class BuyRequest(BaseModel):
    stock_code: str
    amount: float
    price: float = 0.0
    strategy: str = ""


class SellRequest(BaseModel):
    stock_code: str
    amount: float
    price: float = 0.0
    strategy: str = ""


class CancelRequest(BaseModel):
    order_id: str


class OrderResponse(BaseModel):
    order_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    order_type: str
    status: str
    filled_price: float
    filled_quantity: float


class TradeHistoryItem(BaseModel):
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


class MessageResponse(BaseModel):
    success: bool
    message: str


def _get_state(request: Request) -> dict[str, Any]:
    if hasattr(request.app.state, "app_state"):
        return request.app.state.app_state
    from web.api import app_state
    return app_state


@router.post("/buy", response_model=OrderResponse)
async def buy(req: BuyRequest, request: Request):
    if not _HAS_EXECUTOR:
        return OrderResponse(
            order_id=f"MOCK-{random.randint(1000,9999)}", symbol=req.stock_code,
            side="buy", price=req.price, quantity=req.amount,
            order_type="market", status="filled",
            filled_price=req.price, filled_quantity=req.amount,
        )
    state = _get_state(request)
    executor = state["executor"]
    signal = Signal(
        symbol=req.stock_code, side=OrderSide.BUY, price=req.price,
        quantity=req.amount,
        order_type=OrderType.MARKET if req.price == 0.0 else OrderType.LIMIT,
        strategy=req.strategy,
    )
    order = executor.execute_signal(signal)
    if order is None:
        return OrderResponse(
            order_id="", symbol=req.stock_code, side="buy",
            price=req.price, quantity=req.amount,
            order_type="market", status="rejected",
            filled_price=0.0, filled_quantity=0.0,
        )
    return OrderResponse(
        order_id=order.order_id, symbol=order.symbol, side=order.side.value,
        price=order.price, quantity=order.quantity,
        order_type=order.order_type.value, status=order.status.value,
        filled_price=order.filled_price, filled_quantity=order.filled_quantity,
    )


@router.post("/sell", response_model=OrderResponse)
async def sell(req: SellRequest, request: Request):
    if not _HAS_EXECUTOR:
        return OrderResponse(
            order_id=f"MOCK-{random.randint(1000,9999)}", symbol=req.stock_code,
            side="sell", price=req.price, quantity=req.amount,
            order_type="market", status="filled",
            filled_price=req.price, filled_quantity=req.amount,
        )
    state = _get_state(request)
    executor = state["executor"]
    signal = Signal(
        symbol=req.stock_code, side=OrderSide.SELL, price=req.price,
        quantity=req.amount,
        order_type=OrderType.MARKET if req.price == 0.0 else OrderType.LIMIT,
        strategy=req.strategy,
    )
    order = executor.execute_signal(signal)
    if order is None:
        return OrderResponse(
            order_id="", symbol=req.stock_code, side="sell",
            price=req.price, quantity=req.amount,
            order_type="market", status="rejected",
            filled_price=0.0, filled_quantity=0.0,
        )
    return OrderResponse(
        order_id=order.order_id, symbol=order.symbol, side=order.side.value,
        price=order.price, quantity=order.quantity,
        order_type=order.order_type.value, status=order.status.value,
        filled_price=order.filled_price, filled_quantity=order.filled_quantity,
    )


@router.post("/cancel", response_model=MessageResponse)
async def cancel(req: CancelRequest, request: Request):
    if not _HAS_EXECUTOR:
        return MessageResponse(success=True, message="模拟撤单成功")
    state = _get_state(request)
    broker = state["broker"]
    success = broker.cancel_order(req.order_id)
    return MessageResponse(
        success=success,
        message="撤单成功" if success else "撤单失败，订单不存在或已成交",
    )


@router.get("/orders", response_model=list[OrderResponse])
async def orders(request: Request):
    if not _HAS_EXECUTOR:
        return []
    state = _get_state(request)
    broker = state["broker"]
    order_list = broker.get_orders()
    return [
        OrderResponse(
            order_id=o.order_id, symbol=o.symbol, side=o.side.value,
            price=o.price, quantity=o.quantity,
            order_type=o.order_type.value, status=o.status.value,
            filled_price=o.filled_price, filled_quantity=o.filled_quantity,
        )
        for o in order_list
    ]


@router.get("/history", response_model=list[TradeHistoryItem])
async def history(request: Request, symbol: str = "", limit: int = 50):
    if not _HAS_EXECUTOR:
        return []
    state = _get_state(request)
    executor = state["executor"]
    stock_code = symbol if symbol else None
    records = executor.get_trade_history(symbol=stock_code, limit=limit)
    return [
        TradeHistoryItem(
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
        for row in records
    ]

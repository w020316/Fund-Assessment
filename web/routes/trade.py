from __future__ import annotations

from typing import Any

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.utils.auth import require_admin

router = APIRouter()

_HAS_EXECUTOR = False
try:
    from src.core.executor import OrderSide, OrderType, Signal
    _HAS_EXECUTOR = True
except ImportError as e:
    logger.warning(f"import src.core.executor failed: {e}")


class BuyRequest(BaseModel):
    # P0 修复(2026-07-30):amount/price 增加范围校验,防止前端绕过校验提交负数
    stock_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    amount: float = Field(..., gt=0, description="交易数量,必须为正数")
    price: float = Field(0.0, ge=0, description="限价价格,0表示市价")
    strategy: str = Field("", max_length=200, description="策略备注,限200字符")


class SellRequest(BaseModel):
    # P0 修复(2026-07-30):同 BuyRequest,增加范围校验
    stock_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    amount: float = Field(..., gt=0, description="交易数量,必须为正数")
    price: float = Field(0.0, ge=0, description="限价价格,0表示市价")
    strategy: str = Field("", max_length=200, description="策略备注,限200字符")


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


@router.post("/buy", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def buy(req: BuyRequest, request: Request):
    # P1 修复(2026-07-30):_execute_order 内含 broker IO,用 asyncio.to_thread 包裹避免阻塞事件循环
    return await asyncio.to_thread(_execute_order, req, OrderSide.BUY, request)


@router.post("/sell", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def sell(req: SellRequest, request: Request):
    # P1 修复(2026-07-30):同 buy
    return await asyncio.to_thread(_execute_order, req, OrderSide.SELL, request)


@router.post("/cancel", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def cancel(req: CancelRequest, request: Request):
    if not _HAS_EXECUTOR:
        return MessageResponse(success=False, message="交易功能未启用, 无法撤单")
    state = _get_state(request)
    broker = state["broker"]
    # P1 修复(2026-07-30):broker.cancel_order 同步方法,包裹到线程池
    success = await asyncio.to_thread(broker.cancel_order, req.order_id)
    return MessageResponse(
        success=success,
        message="撤单成功" if success else "撤单失败，订单不存在或已成交",
    )


def _execute_order(req: BuyRequest | SellRequest, side: OrderSide, request: Request) -> OrderResponse | JSONResponse:
    """买入/卖出公共执行逻辑(消除 buy/sell 重复代码)。"""
    if not _HAS_EXECUTOR:
        return JSONResponse(status_code=503, content={"error": "交易功能未启用", "detail": "请先配置交易模块"})
    state = _get_state(request)
    executor = state["executor"]
    signal = Signal(
        symbol=req.stock_code, side=side, price=req.price,
        quantity=req.amount,
        order_type=OrderType.MARKET if req.price == 0.0 else OrderType.LIMIT,
        strategy=req.strategy,
    )
    order = executor.execute_signal(signal)
    if order is None:
        return OrderResponse(
            order_id="", symbol=req.stock_code, side=side.value,
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


@router.get("/orders", response_model=list[OrderResponse], dependencies=[Depends(require_admin)])
async def orders(request: Request):
    if not _HAS_EXECUTOR:
        return []
    state = _get_state(request)
    broker = state.get("broker")
    if broker is None:
        return []
    # P1 修复(2026-07-30):broker.get_orders 可能含 IO,包裹到线程池
    order_list = await asyncio.to_thread(broker.get_orders)
    return [
        OrderResponse(
            order_id=o.order_id, symbol=o.symbol, side=o.side.value,
            price=o.price, quantity=o.quantity,
            order_type=o.order_type.value, status=o.status.value,
            filled_price=o.filled_price, filled_quantity=o.filled_quantity,
        )
        for o in order_list
    ]


@router.get("/history", response_model=list[TradeHistoryItem], dependencies=[Depends(require_admin)])
async def history(request: Request, symbol: str = "", limit: int = 50):
    if not _HAS_EXECUTOR:
        return []
    state = _get_state(request)
    executor = state.get("executor")
    if executor is None:
        return []
    stock_code = symbol if symbol else None
    # P1 修复(2026-07-30):executor.get_trade_history 可能含 IO,包裹到线程池
    records = await asyncio.to_thread(executor.get_trade_history, symbol=stock_code, limit=limit)
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

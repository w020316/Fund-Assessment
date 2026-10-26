from __future__ import annotations

from typing import Any

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.utils.auth import require_admin

router = APIRouter()

# 委托价格上界(2026-10-26):用于挡住 inf/NaN 与明显异常的报价。
# 取值 1e6 远高于 A 股任何真实股价(贵州茅台历史最高约 2600 元),不影响正常下单。
_MAX_ORDER_PRICE = 1_000_000.0

_HAS_EXECUTOR = False
try:
    from src.core.executor import OrderSide, OrderType, Signal
    _HAS_EXECUTOR = True
except ImportError as e:
    logger.warning(f"import src.core.executor failed: {e}")


class BuyRequest(BaseModel):
    # P0 修复(2026-07-30):amount/price 增加范围校验,防止前端绕过校验提交负数
    # P0 修复(2026-10-26):price 增加上界,阻挡 inf/NaN 与异常报价
    stock_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    amount: float = Field(..., gt=0, description="交易数量,必须为正数")
    price: float = Field(0.0, ge=0, le=_MAX_ORDER_PRICE, description="限价价格,0表示市价")
    strategy: str = Field("", max_length=200, description="策略备注,限200字符")


class SellRequest(BaseModel):
    # P0 修复(2026-07-30):同 BuyRequest,增加范围校验
    # P0 修复(2026-10-26):price 增加上界,阻挡 inf/NaN 与异常报价
    stock_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    amount: float = Field(..., gt=0, description="交易数量,必须为正数")
    price: float = Field(0.0, ge=0, le=_MAX_ORDER_PRICE, description="限价价格,0表示市价")
    strategy: str = Field("", max_length=200, description="策略备注,限200字符")


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


def _get_state(request: Request) -> dict[str, Any]:
    if hasattr(request.app.state, "app_state"):
        return request.app.state.app_state
    from web.api import app_state
    return app_state


def _resolve_market_price(state: dict[str, Any], symbol: str) -> float:
    """解析市价单(price=0)的最新成交价。

    P0 修复(2026-10-26):
    前端「委托价格」留空时提交 price=0 表示市价单。此前该 0 被原样传给
    SimulatedBroker,而 broker 直接以 price 作为成交价 —— 结果是:
      amount = 0 × 数量 = 0,total_cost 只剩 5 元最低佣金,
      持仓 cost_price 被记为 0,行情回填后凭空产生巨额浮盈。

    现在改为:市价单先解析最新价再下单;解析失败返回 0.0,
    由风控(price<=0 拒绝)与券商层(非正价拒绝)兜底,绝不以 0 价成交。
    """
    ds = state.get("data_source") if isinstance(state, dict) else None
    if ds is None:
        logger.warning(f"市价单 {symbol} 无法解析最新价: 数据源未就绪,委托将被拒绝")
        return 0.0
    try:
        quote = ds.get_realtime_quote(symbol)
        price = float(getattr(quote, "price", 0.0) or 0.0)
        if 0 < price < _MAX_ORDER_PRICE:
            return price
        logger.warning(f"市价单 {symbol} 解析到非法价格 {price},委托将被拒绝")
    except Exception as e:
        logger.warning(f"市价单 {symbol} 解析最新价失败: {type(e).__name__}: {e}")
    return 0.0


@router.post("/buy", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def buy(req: BuyRequest, request: Request):
    # P1 修复(2026-07-30):_execute_order 内含 broker IO,用 asyncio.to_thread 包裹避免阻塞事件循环
    return await asyncio.to_thread(_execute_order, req, OrderSide.BUY, request)


@router.post("/sell", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def sell(req: SellRequest, request: Request):
    # P1 修复(2026-07-30):同 buy
    return await asyncio.to_thread(_execute_order, req, OrderSide.SELL, request)


# 撤单端点已下线(2026-10-26):
# SimulatedBroker 的 buy/sell 在同一次加锁内完成"建单→判定→置终态"，
# 订单从不进入 PENDING，因此 cancel_order 恒返回 False、撤单按钮点了必然失败。
# 与其保留一个永远失败的按钮（比没有按钮更伤），选择下线该端点与 UI 入口。
# 若将来需要真实挂单/撤单，应连带实现撮合循环（订单保持 PENDING 并有成交驱动），
# 届时以新端点恢复，而不是让现有前端继续对着 dead path 发请求。


def _execute_order(req: BuyRequest | SellRequest, side: OrderSide, request: Request) -> OrderResponse | JSONResponse:
    """买入/卖出公共执行逻辑(消除 buy/sell 重复代码)。"""
    if not _HAS_EXECUTOR:
        return JSONResponse(status_code=503, content={"error": "交易功能未启用", "detail": "请先配置交易模块"})
    state = _get_state(request)
    executor = state["executor"]
    # P0 修复(2026-10-26):市价单(price=0)先解析最新价,避免以 0 元成交建仓
    order_price = req.price
    if order_price == 0.0:
        order_price = _resolve_market_price(state, req.stock_code)
    signal = Signal(
        symbol=req.stock_code, side=side, price=order_price,
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

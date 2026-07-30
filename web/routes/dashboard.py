from __future__ import annotations

import asyncio
from pathlib import Path

from typing import Any

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel

from src.core import data_source_v2 as ds2
from src.utils.auth import require_admin
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str
from src.utils.file_io import safe_read_json

router = APIRouter()

_HAS_CORE = False
try:
    from src.core.data_source import DataSourceManager
    from src.core.executor import SimulatedBroker, TradeExecutor
    from src.core.risk_manager import RiskManager
    _HAS_CORE = True
except ImportError as e:
    logger.warning(f"import src.core failed: {e}")


_USER_POSITIONS_FILE = Path(__file__).resolve().parent.parent / "user_positions.json"


def _load_user_positions() -> list[dict]:
    """读取用户持仓,文件缺失或损坏时返回空列表(不返回 mock 数据)

    P0 修复(2026-07-30):改用 safe_read_json,主文件损坏自动回退 .bak 备份
    """
    data = safe_read_json(_USER_POSITIONS_FILE, default={"positions": []})
    if data is None or not isinstance(data, dict):
        return []
    return data.get("positions", [])


def _load_user_cash() -> float:
    """读取用户现金,文件缺失时返回 0(不硬编码 80 万)"""
    data = safe_read_json(_USER_POSITIONS_FILE, default={"available_cash": 0.0})
    if data is None or not isinstance(data, dict):
        return 0.0
    try:
        return float(data.get("available_cash", 0.0))
    except (TypeError, ValueError):
        return 0.0


async def _enrich_positions_with_realtime(positions: list[dict]) -> list[dict]:
    symbols = [p["symbol"] for p in positions]
    if not symbols:
        return positions
    # P1 修复(2026-07-30):原代码 except Exception 后 quotes=[] 静默吞异常,
    # 用户会看到错误的市值/盈亏(用成本价计算)。改为日志记录,并在 positions 上标记 data_quality。
    try:
        quotes = await asyncio.to_thread(ds2.get_realtime_quote_tencent, symbols)
    except Exception as e:
        logger.warning(f"_enrich_positions_with_realtime: 行情接口失败,降级用成本价: {e}")
        quotes = []
        for p in positions:
            p["data_quality"] = "degraded"  # 标记行情数据降级,前端可显示提示
    quote_map = {q.get("code", ""): q for q in quotes}
    for p in positions:
        q = quote_map.get(p["symbol"], {})
        current_price = _safe_float(q.get("price"), p.get("cost_price", 0))
        p["current_price"] = current_price
        p["market_value"] = round(current_price * p["quantity"], 2)
        cost = p["cost_price"] * p["quantity"]
        p["profit"] = round(p["market_value"] - cost, 2)
        p["profit_pct"] = round((current_price / p["cost_price"] - 1) * 100, 2) if p["cost_price"] else 0.0
        p["_change_pct"] = _safe_float(q.get("change_pct"), 0)
        p["_prev_close"] = _safe_float(q.get("prev_close"), 0)
        if q.get("name"):
            p["name"] = _safe_str(q.get("name"))
    return positions


class OverviewResponse(BaseModel):
    available_cash: float
    total_assets: float
    market_value: float
    daily_pnl: float
    daily_pnl_pct: float
    position_count: int
    risk_level: str
    risk_message: str
    # P2-6 修复(2026-07-30):新增 data_quality 字段,
    # 标记 daily_pnl 是否因实时行情缺失/计算异常而降级为 0。
    # 前端据此显示"盈亏数据降级"提示,避免用户误以为当日无盈亏。
    data_quality: str = "normal"  # normal | degraded


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


@router.get("/overview", response_model=OverviewResponse, dependencies=[Depends(require_admin)])
async def overview(request: Request):
    if not _HAS_CORE:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        daily_pnl = 0.0
        # P2-6 修复(2026-07-30):标记 daily_pnl 计算是否降级。
        # 触发降级的情形:1) 实时行情获取失败(_change_pct/_prev_close 缺失)
        #                 2) 计算过程异常。前端据此显示"盈亏降级"提示。
        daily_pnl_degraded = False
        try:
            for p in enriched:
                change_pct = _safe_float(p.get("_change_pct", 0))
                prev_close = _safe_float(p.get("_prev_close", 0))
                if prev_close > 0:
                    daily_pnl += p["quantity"] * prev_close * change_pct / 100.0
                else:
                    # 有持仓但缺 prev_close → 行情数据不完整 → 降级
                    if p.get("quantity", 0) > 0:
                        daily_pnl_degraded = True
        except Exception as e:
            logger.warning(f"calc daily_pnl for overview failed: {e}")
            daily_pnl_degraded = True
        available_cash = _load_user_cash()
        total_assets = available_cash + market_value
        daily_pnl_pct = round(daily_pnl / (total_assets - daily_pnl) * 100, 2) if (total_assets - daily_pnl) > 0 else 0.0
        return OverviewResponse(
            available_cash=available_cash,
            total_assets=round(total_assets, 2),
            market_value=round(market_value, 2),
            daily_pnl=round(daily_pnl, 2),
            daily_pnl_pct=daily_pnl_pct,
            position_count=len(enriched),
            risk_level="NORMAL",
            risk_message="系统正常运行",
            data_quality="degraded" if daily_pnl_degraded else "normal",
        )
    state = _get_state(request)
    broker = state.get("broker")
    risk_manager = state.get("risk_manager")
    if not broker or not risk_manager:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        return OverviewResponse(
            available_cash=1_000_000.0,
            total_assets=round(1_000_000.0 + market_value, 2),
            market_value=round(market_value, 2),
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            position_count=len(enriched),
            risk_level="NORMAL",
            risk_message="模拟模式",
        )
    balance, positions, risk_status = await asyncio.gather(
        asyncio.to_thread(broker.get_balance),
        asyncio.to_thread(broker.get_positions),
        asyncio.to_thread(risk_manager.get_risk_status),
    )
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


@router.get("/positions", response_model=list[PositionItem], dependencies=[Depends(require_admin)])
async def positions(request: Request):
    if not _HAS_CORE:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        return [
            PositionItem(
                symbol=p["symbol"], name=p["name"], quantity=p["quantity"],
                available_quantity=p["available_quantity"], cost_price=p["cost_price"],
                current_price=p["current_price"], market_value=p["market_value"],
                profit=p["profit"], profit_pct=p["profit_pct"],
            )
            for p in enriched
        ]
    state = _get_state(request)
    broker = state["broker"]
    pos_list = await asyncio.to_thread(broker.get_positions)
    return [
        PositionItem(
            symbol=p.symbol, name=p.name, quantity=p.quantity,
            available_quantity=p.available_quantity, cost_price=p.cost_price,
            current_price=p.current_price, market_value=p.market_value,
            profit=p.profit, profit_pct=p.profit_pct,
        )
        for p in pos_list
    ]


@router.get("/trades", response_model=list[TradeItem], dependencies=[Depends(require_admin)])
async def trades(request: Request, limit: int = 20):
    if not _HAS_CORE:
        return []
    state = _get_state(request)
    executor = state["executor"]
    history = await asyncio.to_thread(executor.get_trade_history, limit=limit)
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


@router.get("/risk", response_model=RiskResponse, dependencies=[Depends(require_admin)])
async def risk(request: Request):
    if not _HAS_CORE:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        daily_pnl = 0.0
        try:
            for p in enriched:
                change_pct = _safe_float(p.get("_change_pct", 0))
                prev_close = _safe_float(p.get("_prev_close", 0))
                if prev_close > 0:
                    daily_pnl += p["quantity"] * prev_close * change_pct / 100.0
        except Exception as e:
            logger.warning(f"calc daily_pnl for risk failed: {e}")
        available_cash = _load_user_cash()
        total_assets = available_cash + market_value
        total_profit = sum(p.get("profit", 0) for p in enriched)
        total_cost = sum(p.get("cost_price", 0) * p.get("quantity", 0) for p in enriched)
        drawdown_pct = 0.0
        if total_cost > 0 and total_profit < 0:
            drawdown_pct = round(abs(total_profit) / total_cost * 100, 2)
        daily_pnl_pct = round(daily_pnl / (total_assets - daily_pnl) * 100, 2) if (total_assets - daily_pnl) > 0 else 0.0
        level = "NORMAL"
        if drawdown_pct >= 15:
            level = "CRITICAL"
        elif drawdown_pct >= 10:
            level = "DANGER"
        elif drawdown_pct >= 5:
            level = "WARNING"
        return RiskResponse(
            level=level, total_assets=round(total_assets, 2),
            peak_assets=round(total_assets, 2),
            drawdown_pct=drawdown_pct, daily_pnl=round(daily_pnl, 2),
            daily_pnl_pct=daily_pnl_pct, consecutive_stop_losses=0,
            is_paused=False, pause_until=None, is_emergency_stopped=False,
            no_new_positions=False, position_reduction=1.0,
            message="风控正常" if level == "NORMAL" else f"回撤 {drawdown_pct}%，需关注",
        )
    state = _get_state(request)
    risk_manager = state["risk_manager"]
    status = await asyncio.to_thread(risk_manager.get_risk_status)
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

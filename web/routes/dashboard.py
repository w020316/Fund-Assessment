from __future__ import annotations

import json
import os

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.core import data_source_v2 as ds2

router = APIRouter()

_HAS_CORE = False
try:
    from src.core.data_source import DataSourceManager
    from src.core.executor import SimulatedBroker, TradeExecutor
    from src.core.risk_manager import RiskManager
    _HAS_CORE = True
except ImportError:
    pass


def _safe_float(val: object, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        result = float(val)
        return result
    except (ValueError, TypeError):
        return default


def _safe_str(val: object, default: str = "") -> str:
    if val is None or (isinstance(val, float) and val != val):
        return default
    return str(val)


_MOCK_POSITIONS = [
    {"symbol": "000001", "name": "平安银行", "quantity": 1000, "available_quantity": 1000, "cost_price": 12.50},
    {"symbol": "600519", "name": "贵州茅台", "quantity": 10, "available_quantity": 10, "cost_price": 1680.00},
    {"symbol": "300750", "name": "宁德时代", "quantity": 200, "available_quantity": 200, "cost_price": 195.00},
]


def _load_user_positions() -> list[dict]:
    pos_file = os.path.join(os.path.dirname(__file__), "..", "user_positions.json")
    if os.path.exists(pos_file):
        try:
            with open(pos_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("positions", _MOCK_POSITIONS)
        except Exception:
            pass
    return _MOCK_POSITIONS


def _load_user_cash() -> float:
    pos_file = os.path.join(os.path.dirname(__file__), "..", "user_positions.json")
    if os.path.exists(pos_file):
        try:
            with open(pos_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return float(data.get("available_cash", 800000.0))
        except Exception:
            pass
    return 800000.0


def _enrich_positions_with_realtime(positions: list[dict]) -> list[dict]:
    symbols = [p["symbol"] for p in positions]
    if not symbols:
        return positions
    try:
        quotes = ds2.get_realtime_quote_tencent(symbols)
    except Exception:
        quotes = []
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
        enriched = _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        daily_pnl = 0.0
        try:
            for p in enriched:
                change_pct = _safe_float(p.get("_change_pct", 0))
                prev_close = _safe_float(p.get("_prev_close", 0))
                if prev_close > 0:
                    daily_pnl += p["quantity"] * prev_close * change_pct / 100.0
        except Exception:
            pass
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
        enriched = _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
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
        return []
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
        enriched = _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        daily_pnl = 0.0
        try:
            for p in enriched:
                change_pct = _safe_float(p.get("_change_pct", 0))
                prev_close = _safe_float(p.get("_prev_close", 0))
                if prev_close > 0:
                    daily_pnl += p["quantity"] * prev_close * change_pct / 100.0
        except Exception:
            pass
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

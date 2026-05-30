from __future__ import annotations

import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .risk_manager import RiskManager, TradeRecord

_DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_COMMISSION_RATE = 0.0003
_STAMP_TAX_RATE = 0.001
_MIN_COMMISSION = 5.0


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    filled_quantity: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Trade:
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    amount: float
    commission: float
    stamp_tax: float
    net_amount: float
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    symbol: str
    name: str
    quantity: float
    available_quantity: float
    cost_price: float
    current_price: float
    market_value: float
    profit: float
    profit_pct: float


@dataclass
class Balance:
    total_assets: float
    available_cash: float
    market_value: float
    profit: float
    profit_pct: float


@dataclass
class Signal:
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    order_type: OrderType = OrderType.MARKET
    strategy: str = ""
    reason: str = ""


class Notifier(ABC):
    @abstractmethod
    def notify(self, title: str, message: str) -> None: ...


class LogNotifier(Notifier):
    def notify(self, title: str, message: str) -> None:
        logger.info(f"[通知] {title}: {message}")


class BrokerAPI(ABC):
    @abstractmethod
    def buy(self, symbol: str, price: float, quantity: float, order_type: OrderType = OrderType.MARKET) -> Order: ...

    @abstractmethod
    def sell(self, symbol: str, price: float, quantity: float, order_type: OrderType = OrderType.MARKET) -> Order: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_balance(self) -> Balance: ...

    @abstractmethod
    def get_orders(self) -> list[Order]: ...


class SimulatedBroker(BrokerAPI):
    def __init__(self, initial_cash: float = 1_000_000.0):
        self._cash: float = initial_cash
        self._initial_cash: float = initial_cash
        self._positions: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []

    def _gen_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def _calc_commission(self, amount: float, side: OrderSide) -> tuple[float, float]:
        commission = max(amount * _COMMISSION_RATE, _MIN_COMMISSION)
        stamp_tax = amount * _STAMP_TAX_RATE if side == OrderSide.SELL else 0.0
        return commission, stamp_tax

    def buy(self, symbol: str, price: float, quantity: float, order_type: OrderType = OrderType.MARKET) -> Order:
        order_id = self._gen_id()
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            price=price,
            quantity=quantity,
            order_type=order_type,
        )
        self._orders[order_id] = order

        filled_price = price
        amount = filled_price * quantity
        commission, stamp_tax = self._calc_commission(amount, OrderSide.BUY)
        total_cost = amount + commission + stamp_tax

        if total_cost > self._cash:
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.now()
            logger.warning(f"SimulatedBroker: 买入被拒绝，资金不足 (需要 {total_cost:.2f}, 可用 {self._cash:.2f})")
            return order

        self._cash -= total_cost
        order.status = OrderStatus.FILLED
        order.filled_price = filled_price
        order.filled_quantity = quantity
        order.updated_at = datetime.now()

        if symbol in self._positions:
            pos = self._positions[symbol]
            total_qty = pos["quantity"] + quantity
            pos["cost_price"] = (pos["cost_price"] * pos["quantity"] + filled_price * quantity) / total_qty
            pos["quantity"] = total_qty
            pos["available_quantity"] = total_qty
        else:
            self._positions[symbol] = {
                "symbol": symbol,
                "name": symbol,
                "quantity": quantity,
                "available_quantity": quantity,
                "cost_price": filled_price,
                "current_price": filled_price,
            }

        trade = Trade(
            trade_id=self._gen_id(),
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            price=filled_price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            net_amount=total_cost,
        )
        self._trades.append(trade)
        return order

    def sell(self, symbol: str, price: float, quantity: float, order_type: OrderType = OrderType.MARKET) -> Order:
        order_id = self._gen_id()
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.SELL,
            price=price,
            quantity=quantity,
            order_type=order_type,
        )
        self._orders[order_id] = order

        pos = self._positions.get(symbol)
        if pos is None or pos["available_quantity"] < quantity:
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.now()
            logger.warning(f"SimulatedBroker: 卖出被拒绝，持仓不足")
            return order

        filled_price = price
        amount = filled_price * quantity
        commission, stamp_tax = self._calc_commission(amount, OrderSide.SELL)
        net_proceeds = amount - commission - stamp_tax

        self._cash += net_proceeds
        order.status = OrderStatus.FILLED
        order.filled_price = filled_price
        order.filled_quantity = quantity
        order.updated_at = datetime.now()

        pos["quantity"] -= quantity
        pos["available_quantity"] -= quantity
        if pos["quantity"] <= 0:
            del self._positions[symbol]

        trade = Trade(
            trade_id=self._gen_id(),
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.SELL,
            price=filled_price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            net_amount=net_proceeds,
        )
        self._trades.append(trade)
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None:
            return False
        if order.status != OrderStatus.PENDING:
            return False
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now()
        return True

    def get_positions(self) -> list[Position]:
        positions: list[Position] = []
        for pos in self._positions.values():
            current_price = pos["current_price"]
            cost_price = pos["cost_price"]
            qty = pos["quantity"]
            market_value = current_price * qty
            profit = (current_price - cost_price) * qty
            profit_pct = (current_price - cost_price) / cost_price if cost_price > 0 else 0.0
            positions.append(
                Position(
                    symbol=pos["symbol"],
                    name=pos["name"],
                    quantity=qty,
                    available_quantity=pos["available_quantity"],
                    cost_price=cost_price,
                    current_price=current_price,
                    market_value=market_value,
                    profit=profit,
                    profit_pct=profit_pct,
                )
            )
        return positions

    def get_balance(self) -> Balance:
        market_value = sum(p["current_price"] * p["quantity"] for p in self._positions.values())
        total_assets = self._cash + market_value
        profit = total_assets - self._initial_cash
        profit_pct = profit / self._initial_cash if self._initial_cash > 0 else 0.0
        return Balance(
            total_assets=total_assets,
            available_cash=self._cash,
            market_value=market_value,
            profit=profit,
            profit_pct=profit_pct,
        )

    def get_orders(self) -> list[Order]:
        return list(self._orders.values())

    def update_price(self, symbol: str, price: float) -> None:
        if symbol in self._positions:
            self._positions[symbol]["current_price"] = price


class LiveBroker(BrokerAPI):
    def __init__(self, broker_config: Optional[dict[str, Any]] = None):
        self._config = broker_config or {}

    def buy(self, symbol: str, price: float, quantity: float, order_type: OrderType = OrderType.MARKET) -> Order:
        raise NotImplementedError("LiveBroker.buy 未实现，请对接券商API")

    def sell(self, symbol: str, price: float, quantity: float, order_type: OrderType = OrderType.MARKET) -> Order:
        raise NotImplementedError("LiveBroker.sell 未实现，请对接券商API")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("LiveBroker.cancel_order 未实现，请对接券商API")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("LiveBroker.get_positions 未实现，请对接券商API")

    def get_balance(self) -> Balance:
        raise NotImplementedError("LiveBroker.get_balance 未实现，请对接券商API")

    def get_orders(self) -> list[Order]:
        raise NotImplementedError("LiveBroker.get_orders 未实现，请对接券商API")


class TradeExecutor:
    def __init__(
        self,
        broker: BrokerAPI,
        risk_manager: RiskManager,
        notifier: Optional[Notifier] = None,
        db_path: Optional[Path] = None,
    ):
        self._broker = broker
        self._risk_manager = risk_manager
        self._notifier = notifier or LogNotifier()
        self._db_path = db_path or _DEFAULT_DB_DIR / "trade.db"
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL UNIQUE,
                    order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    amount REAL NOT NULL,
                    commission REAL NOT NULL DEFAULT 0,
                    stamp_tax REAL NOT NULL DEFAULT 0,
                    net_amount REAL NOT NULL DEFAULT 0,
                    strategy TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at)"
            )

    def _record_trade(self, trade: Trade, strategy: str = "", reason: str = "") -> None:
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO trades (trade_id, order_id, symbol, side, price, quantity,
                                        amount, commission, stamp_tax, net_amount, strategy, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.trade_id,
                        trade.order_id,
                        trade.symbol,
                        trade.side.value,
                        trade.price,
                        trade.quantity,
                        trade.amount,
                        trade.commission,
                        trade.stamp_tax,
                        trade.net_amount,
                        strategy,
                        reason,
                    ),
                )
        except Exception as e:
            logger.warning(f"TradeExecutor._record_trade failed: {e}")

    def execute_signal(self, signal: Signal) -> Optional[Order]:
        order_dict: dict[str, Any] = {
            "symbol": signal.symbol,
            "side": signal.side.value,
            "price": signal.price,
            "quantity": signal.quantity,
            "order_type": signal.order_type.value,
        }

        passed, reason = self._risk_manager.check_order(order_dict)
        if not passed:
            logger.warning(f"订单被风控拒绝: {reason}")
            self._notifier.notify("订单被风控拒绝", f"{signal.symbol} {signal.side.value}: {reason}")
            return None

        if "仓位缩减" in reason:
            signal.quantity = int(signal.quantity * self._risk_manager.get_risk_status().position_reduction)
            if signal.quantity <= 0:
                logger.warning("仓位缩减后数量为0，取消订单")
                return None

        order: Optional[Order] = None
        if signal.side == OrderSide.BUY:
            order = self._broker.buy(
                signal.symbol, signal.price, signal.quantity, signal.order_type
            )
        elif signal.side == OrderSide.SELL:
            order = self._broker.sell(
                signal.symbol, signal.price, signal.quantity, signal.order_type
            )

        if order is None:
            logger.warning(f"订单执行失败: {signal.symbol}")
            return None

        if order.status == OrderStatus.REJECTED:
            logger.warning(f"订单被拒绝: {order.order_id}")
            self._notifier.notify("订单被拒绝", f"{signal.symbol}: 订单 {order.order_id}")
            return order

        if order.status == OrderStatus.FILLED:
            balance = self._broker.get_balance()
            self._risk_manager.update_position({"total_assets": balance.total_assets})

            is_stop_loss = signal.reason.lower().find("止损") >= 0 if signal.reason else False
            profit = 0.0
            if signal.side == OrderSide.SELL:
                positions = self._broker.get_positions()
                for pos in positions:
                    if pos.symbol == signal.symbol:
                        profit = (order.filled_price - pos.cost_price) * order.filled_quantity
                        break

            trade_record = TradeRecord(
                symbol=signal.symbol,
                side=signal.side.value,
                price=order.filled_price,
                quantity=order.filled_quantity,
                amount=order.filled_price * order.filled_quantity,
                profit=profit,
                is_stop_loss=is_stop_loss,
            )
            self._risk_manager.record_trade(trade_record)

            trade = Trade(
                trade_id=uuid.uuid4().hex[:16],
                order_id=order.order_id,
                symbol=signal.symbol,
                side=signal.side,
                price=order.filled_price,
                quantity=order.filled_quantity,
                amount=order.filled_price * order.filled_quantity,
                commission=0.0,
                stamp_tax=0.0,
                net_amount=order.filled_price * order.filled_quantity,
            )
            self._record_trade(trade, strategy=signal.strategy, reason=signal.reason)

            self._notifier.notify(
                "订单成交",
                f"{signal.symbol} {signal.side.value} {order.filled_quantity}@{order.filled_price:.2f} [{signal.strategy}]",
            )

        return order

    def get_trade_history(self, symbol: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        try:
            with self._get_conn() as conn:
                if symbol:
                    cursor = conn.execute(
                        "SELECT * FROM trades WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                        (symbol, limit),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    )
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"TradeExecutor.get_trade_history failed: {e}")
            return []

"""交易执行器单元测试

验证 src/core/executor.py 的核心逻辑:
- Order/Trade/Position/Balance/Signal 数据类
- SimulatedBroker 模拟券商(买入/卖出/取消/持仓/余额)
- TradeExecutor 交易执行器(信号→风控→下单→记录)
- 佣金/印花税计算
- 拒单/资金不足/持仓不足场景
- LiveBroker 未实现接口
- trade_history 持久化
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.executor import (
    Balance,
    BrokerAPI,
    LiveBroker,
    LogNotifier,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    SimulatedBroker,
    Trade,
    TradeExecutor,
)
from src.core.risk_manager import RiskManager


@pytest.fixture
def broker() -> SimulatedBroker:
    """模拟券商(初始资金 100 万)"""
    return SimulatedBroker(initial_cash=1_000_000.0)


@pytest.fixture
def risk_manager(tmp_path: Path) -> RiskManager:
    return RiskManager(db_path=tmp_path / "risk.db", initial_assets=1_000_000.0)


@pytest.fixture
def executor(broker: SimulatedBroker, risk_manager: RiskManager, tmp_path: Path) -> TradeExecutor:
    """交易执行器(独立 SQLite)"""
    return TradeExecutor(
        broker=broker,
        risk_manager=risk_manager,
        db_path=tmp_path / "trade.db",
    )


class TestEnums:
    """枚举类"""

    def test_order_side_values(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_order_status_values(self):
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.REJECTED.value == "rejected"

    def test_order_type_values(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"


class TestDataclasses:
    """数据类"""

    def test_order_defaults(self):
        """Order 默认 status=PENDING, filled_price=0"""
        order = Order(
            order_id="o1", symbol="600519", side=OrderSide.BUY,
            price=100.0, quantity=100, order_type=OrderType.MARKET,
        )
        assert order.status == OrderStatus.PENDING
        assert order.filled_price == 0.0
        assert isinstance(order.created_at, datetime)

    def test_trade_creation(self):
        trade = Trade(
            trade_id="t1", order_id="o1", symbol="600519",
            side=OrderSide.BUY, price=100.0, quantity=100, amount=10_000.0,
            commission=5.0, stamp_tax=0.0, net_amount=10_005.0,
        )
        assert trade.trade_id == "t1"
        assert trade.commission == 5.0

    def test_position_creation(self):
        pos = Position(
            symbol="600519", name="贵州茅台", quantity=100, available_quantity=100,
            cost_price=100.0, current_price=110.0, market_value=11_000.0,
            profit=1_000.0, profit_pct=0.1,
        )
        assert pos.symbol == "600519"

    def test_balance_creation(self):
        bal = Balance(
            total_assets=1_000_000.0, available_cash=500_000.0,
            market_value=500_000.0, profit=0.0, profit_pct=0.0,
        )
        assert bal.total_assets == 1_000_000.0

    def test_signal_defaults(self):
        """Signal 默认 order_type=MARKET"""
        sig = Signal(symbol="600519", side=OrderSide.BUY, price=100.0, quantity=100)
        assert sig.order_type == OrderType.MARKET
        assert sig.strategy == ""
        assert sig.reason == ""


class TestSimulatedBrokerBuy:
    """SimulatedBroker 买入"""

    def test_buy_success_creates_position(self, broker):
        """成功买入应创建持仓"""
        order = broker.buy("600519", 100.0, 100)
        assert order.status == OrderStatus.FILLED
        assert order.filled_price == 100.0
        assert order.filled_quantity == 100
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "600519"
        assert positions[0].quantity == 100

    def test_buy_insufficient_cash_rejected(self, broker):
        """资金不足 → 拒单"""
        # 100万资金买 200万市值的股票
        order = broker.buy("600519", 1000.0, 2000)
        assert order.status == OrderStatus.REJECTED
        assert len(broker.get_positions()) == 0

    def test_buy_accumulates_position(self, broker):
        """多次买入同一股票应累积持仓,成本均价"""
        broker.buy("600519", 100.0, 100)
        broker.buy("600519", 120.0, 100)
        positions = broker.get_positions()
        assert positions[0].quantity == 200
        # 加权成本 = (100*100 + 120*100) / 200 = 110
        assert positions[0].cost_price == pytest.approx(110.0)

    def test_buy_deducts_cash(self, broker):
        """买入应扣减现金(含佣金)"""
        initial = broker.get_balance().available_cash
        broker.buy("600519", 100.0, 100)
        after = broker.get_balance().available_cash
        assert after < initial
        # 金额 10000,佣金 max(10000*0.0003, 5) = 5,买入无印花税
        assert after == pytest.approx(initial - 10000 - 5)


class TestSimulatedBrokerSell:
    """SimulatedBroker 卖出"""

    def test_sell_success_reduces_position(self, broker):
        """卖出应减少持仓"""
        broker.buy("600519", 100.0, 100)
        order = broker.sell("600519", 110.0, 50)
        assert order.status == OrderStatus.FILLED
        positions = broker.get_positions()
        assert positions[0].quantity == 50

    def test_sell_insufficient_position_rejected(self, broker):
        """持仓不足 → 拒单"""
        broker.buy("600519", 100.0, 50)
        order = broker.sell("600519", 110.0, 100)
        assert order.status == OrderStatus.REJECTED

    def test_sell_no_position_rejected(self, broker):
        """无持仓卖出 → 拒单"""
        order = broker.sell("600519", 110.0, 100)
        assert order.status == OrderStatus.REJECTED

    def test_sell_full_position_removes(self, broker):
        """全部卖出后持仓应被删除"""
        broker.buy("600519", 100.0, 100)
        broker.sell("600519", 110.0, 100)
        assert len(broker.get_positions()) == 0

    def test_sell_adds_cash_with_commission_and_tax(self, broker):
        """卖出应增加现金(扣佣金+印花税)"""
        broker.buy("600519", 100.0, 100)
        cash_after_buy = broker.get_balance().available_cash
        broker.sell("600519", 110.0, 100)
        cash_after_sell = broker.get_balance().available_cash
        # 卖出金额 11000,佣金 max(11000*0.0003, 5) = 5,印花税 11000*0.001 = 11
        net = 11000 - 5 - 11
        assert cash_after_sell == pytest.approx(cash_after_buy + net)


class TestSimulatedBrokerOther:
    """SimulatedBroker 其他方法"""

    def test_cancel_pending_order(self, broker):
        """PENDING 订单可取消(模拟器直接 FILLED,需手动构造)"""
        # 模拟器 buy/sell 同步成交,这里直接测试 cancel 逻辑
        order = Order(
            order_id="x", symbol="t", side=OrderSide.BUY,
            price=100.0, quantity=100, order_type=OrderType.MARKET,
            status=OrderStatus.PENDING,
        )
        broker._orders["x"] = order
        assert broker.cancel_order("x") is True
        assert order.status == OrderStatus.CANCELLED

    def test_cancel_filled_order_returns_false(self, broker):
        """已成交订单不可取消"""
        broker.buy("600519", 100.0, 100)
        orders = broker.get_orders()
        assert broker.cancel_order(orders[0].order_id) is False

    def test_cancel_nonexistent_returns_false(self, broker):
        assert broker.cancel_order("nonexistent") is False

    def test_get_balance(self, broker):
        bal = broker.get_balance()
        assert bal.total_assets == 1_000_000.0
        assert bal.available_cash == 1_000_000.0
        assert bal.market_value == 0.0

    def test_update_price_changes_market_value(self, broker):
        """update_price 应更新持仓现价"""
        broker.buy("600519", 100.0, 100)
        broker.update_price("600519", 120.0)
        positions = broker.get_positions()
        assert positions[0].current_price == 120.0
        assert positions[0].profit == pytest.approx(2000.0)


class TestLiveBroker:
    """LiveBroker 未实现接口"""

    def test_buy_raises(self):
        broker = LiveBroker()
        with pytest.raises(NotImplementedError):
            broker.buy("600519", 100.0, 100)

    def test_sell_raises(self):
        broker = LiveBroker()
        with pytest.raises(NotImplementedError):
            broker.sell("600519", 100.0, 100)

    def test_get_positions_raises(self):
        broker = LiveBroker()
        with pytest.raises(NotImplementedError):
            broker.get_positions()


class TestLogNotifier:
    """LogNotifier 通知器"""

    def test_notify_does_not_raise(self):
        notifier = LogNotifier()
        notifier.notify("title", "message")  # 不抛异常即可


class TestTradeExecutor:
    """TradeExecutor 交易执行器"""

    def test_execute_buy_signal_success(self, executor, broker, risk_manager):
        """成功执行买入信号"""
        signal = Signal(
            symbol="600519", side=OrderSide.BUY, price=100.0,
            quantity=100, strategy="test_strategy", reason="买入信号",
        )
        order = executor.execute_signal(signal)
        assert order is not None
        assert order.status == OrderStatus.FILLED
        # 应记录到风控
        assert risk_manager._total_assets > 0

    def test_execute_sell_signal_success(self, executor, broker):
        """成功执行卖出信号(需先买入)"""
        broker.buy("600519", 100.0, 100)
        signal = Signal(
            symbol="600519", side=OrderSide.SELL, price=110.0,
            quantity=100, strategy="test", reason="卖出信号",
        )
        order = executor.execute_signal(signal)
        assert order is not None
        assert order.status == OrderStatus.FILLED

    def test_risk_rejected_signal_returns_none(self, executor, risk_manager):
        """风控拒绝 → 返回 None"""
        risk_manager.emergency_stop()
        signal = Signal(
            symbol="600519", side=OrderSide.BUY, price=100.0, quantity=100,
        )
        order = executor.execute_signal(signal)
        assert order is None

    def test_broker_rejected_returns_order(self, executor, broker):
        """券商拒单(资金不足) → 返回 REJECTED 订单"""
        signal = Signal(
            symbol="600519", side=OrderSide.BUY, price=1000.0, quantity=2000,
        )
        order = executor.execute_signal(signal)
        assert order is not None
        assert order.status == OrderStatus.REJECTED

    def test_stop_loss_signal_records_profit(self, executor, broker, risk_manager):
        """止损信号应记录 profit 和 is_stop_loss"""
        # 先买入建仓
        broker.buy("600519", 100.0, 100)
        # 触发止损卖出
        signal = Signal(
            symbol="600519", side=OrderSide.SELL, price=95.0,
            quantity=100, reason="触发止损",
        )
        executor.execute_signal(signal)
        # 风控应记录一次止损
        assert risk_manager._consecutive_stop_losses == 1

    def test_trade_history_persists(self, executor, broker):
        """交易历史应持久化到 SQLite"""
        signal = Signal(
            symbol="600519", side=OrderSide.BUY, price=100.0, quantity=100,
            strategy="t", reason="r",
        )
        executor.execute_signal(signal)
        history = executor.get_trade_history()
        assert len(history) == 1
        assert history[0]["symbol"] == "600519"
        assert history[0]["side"] == "buy"

    def test_trade_history_filter_by_symbol(self, executor, broker):
        """按 symbol 过滤交易历史"""
        executor.execute_signal(Signal(symbol="600519", side=OrderSide.BUY, price=100.0, quantity=100))
        executor.execute_signal(Signal(symbol="000001", side=OrderSide.BUY, price=10.0, quantity=1000))
        history_519 = executor.get_trade_history(symbol="600519")
        assert len(history_519) == 1
        assert history_519[0]["symbol"] == "600519"

    def test_position_reduction_applied(self, executor, broker, risk_manager):
        """连损 3 次 → 仓位缩减 50%"""
        risk_manager._consecutive_stop_losses = 3
        risk_manager._position_reduction = 0.5
        signal = Signal(
            symbol="600519", side=OrderSide.BUY, price=100.0, quantity=100,
        )
        order = executor.execute_signal(signal)
        assert order is not None
        # 100 * 0.5 = 50
        assert order.filled_quantity == 50

    def test_position_reduction_to_zero_cancels(self, executor, broker, risk_manager):
        """仓位缩减后数量为 0 → 取消订单"""
        risk_manager._consecutive_stop_losses = 3
        risk_manager._position_reduction = 0.5
        signal = Signal(
            symbol="600519", side=OrderSide.BUY, price=100.0, quantity=1,
        )
        # int(1 * 0.5) = 0
        order = executor.execute_signal(signal)
        assert order is None

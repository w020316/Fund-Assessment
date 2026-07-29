from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger

from .data_source import DataSourceManager
from .executor import OrderSide, OrderType, Signal

_COMMISSION_RATE = 0.0003
_STAMP_TAX_RATE = 0.001
_MIN_COMMISSION = 5.0
_RISK_FREE_RATE = 0.03
_TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestResult:
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_loss_ratio: float
    trade_count: int
    equity_curve: list[float]
    trades: list[dict]
    # 借鉴 mementum/backtrader Analyzer 设计,补充风险调整收益指标
    # 新字段带默认值,向后兼容旧代码(不传新字段也能构造)
    sortino_ratio: float = 0.0  # Sortino 比率:只惩罚下行波动(下行标准差)
    calmar_ratio: float = 0.0   # Calmar 比率:年化收益/最大回撤
    volatility: float = 0.0     # 年化波动率:daily_returns.std * sqrt(252)


def _calc_sortino_ratio(returns: list[float], risk_free: float = _RISK_FREE_RATE) -> float:
    """Sortino 比率 - 只惩罚下行波动。

    借鉴 mementum/backtrader TimeReturn Analyzer 设计思想:
    与 Sharpe 的区别是分子母只用"下行波动"(负收益的标准差),
    避免正收益也被算作风险,更符合投资者对"风险"的真实感受。

    Args:
        returns: 日收益率序列,如 [0.01, -0.02, 0.005]
        risk_free: 年化无风险利率,默认 3%

    Returns:
        Sortino 比率,无数据返回 0.0,无下行波动返回 inf 或 0.0
    """
    if not returns:
        return 0.0
    arr = np.array(returns, dtype=float)
    downside = arr[arr < 0]
    if len(downside) == 0:
        # 无下行波动:有正收益则无穷大,否则 0
        return float("inf") if float(arr.mean()) > 0 else 0.0
    if len(downside) < 2:
        # 单个下行点无法可靠估计 std,视为无下行波动
        daily_rf_single = risk_free / _TRADING_DAYS_PER_YEAR
        return float("inf") if float(arr.mean()) > daily_rf_single else 0.0
    downside_std = float(np.std(downside, ddof=1))
    if downside_std == 0:
        return 0.0
    daily_rf = risk_free / _TRADING_DAYS_PER_YEAR
    excess = float(arr.mean()) - daily_rf
    return excess / downside_std * float(np.sqrt(_TRADING_DAYS_PER_YEAR))


def _calc_calmar_ratio(annual_return: float, max_drawdown: float) -> float:
    """Calmar 比率 - 年化收益 / 最大回撤。

    借鉴 mementum/backtrader Calmar Analyzer 设计:
    衡量"每承担 1 单位回撤风险获得的年化收益",
    Calmar > 3 视为优秀策略,Calmar < 1 视为低质量策略。

    Args:
        annual_return: 年化收益率(小数,如 0.15 表示 15%)
        max_drawdown: 最大回撤(正小数,如 0.10 表示 10%)

    Returns:
        Calmar 比率,max_drawdown 为 0 时返回 0.0
    """
    if max_drawdown == 0:
        return 0.0
    return float(annual_return / abs(max_drawdown))


def _calc_volatility(returns: list[float]) -> float:
    """年化波动率 - daily_returns.std * sqrt(252)。

    借鉴 mementum/backtrader Volatility Analyzer 设计:
    衡量收益率的波动程度,波动率越低策略越稳健。
    一般股票年化波动率 20-30%,稳健策略应控制在 15% 以下。

    Args:
        returns: 日收益率序列

    Returns:
        年化波动率(小数,如 0.20 表示 20%),无数据返回 0.0
    """
    if not returns:
        return 0.0
    arr = np.array(returns, dtype=float)
    return float(np.std(arr) * np.sqrt(_TRADING_DAYS_PER_YEAR))


StrategyFunc = Callable[[date, dict, pd.DataFrame], list[Signal]]


def new_high_strategy(trade_date: date, portfolio: dict, data: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    if len(data) < 20:
        return signals

    symbol = portfolio.get("symbol", "")
    close = data["收盘"].astype(float)
    current_price = float(close.iloc[-1])
    high_20 = float(close.iloc[-20:].max())
    has_position = symbol in portfolio.get("positions", {})

    if not has_position and current_price >= high_20:
        invest_amount = portfolio["cash"] * 0.3
        quantity = int(invest_amount / current_price / 100) * 100
        if quantity >= 100:
            signals.append(Signal(
                symbol=symbol,
                side=OrderSide.BUY,
                price=current_price,
                quantity=float(quantity),
                strategy="new_high",
                reason="20日新高突破",
            ))

    if has_position:
        ma20 = float(close.iloc[-20:].mean())
        pos = portfolio["positions"][symbol]
        loss_pct = (current_price - pos["cost_price"]) / pos["cost_price"] if pos["cost_price"] > 0 else 0.0

        if current_price < ma20 or loss_pct < -0.05:
            sell_qty = pos["quantity"]
            if sell_qty > 0:
                reason = "跌破MA20" if current_price < ma20 else "止损-5%"
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    price=current_price,
                    quantity=float(sell_qty),
                    strategy="new_high",
                    reason=reason,
                ))

    return signals


def limit_up_strategy(trade_date: date, portfolio: dict, data: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    if len(data) < 2:
        return signals

    symbol = portfolio.get("symbol", "")
    close = data["收盘"].astype(float)
    open_prices = data["开盘"].astype(float)
    current_price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    current_open = float(open_prices.iloc[-1])
    change_pct = (current_price - prev_close) / prev_close if prev_close > 0 else 0.0
    has_position = symbol in portfolio.get("positions", {})

    if not has_position and change_pct >= 0.095:
        invest_amount = portfolio["cash"] * 0.2
        quantity = int(invest_amount / current_price / 100) * 100
        if quantity >= 100:
            signals.append(Signal(
                symbol=symbol,
                side=OrderSide.BUY,
                price=current_price,
                quantity=float(quantity),
                strategy="limit_up",
                reason=f"涨停板买入 涨幅{change_pct:.2%}",
            ))

    if has_position:
        pos = portfolio["positions"][symbol]
        loss_pct = (current_price - pos["cost_price"]) / pos["cost_price"] if pos["cost_price"] > 0 else 0.0
        intraday_return = (current_price - current_open) / current_open if current_open > 0 else 0.0

        if loss_pct < -0.03 or (loss_pct > 0.02 and intraday_return < -0.02):
            sell_qty = pos["quantity"]
            if sell_qty > 0:
                reason = "止损-3%" if loss_pct < -0.03 else "冲高回落止盈"
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    price=current_price,
                    quantity=float(sell_qty),
                    strategy="limit_up",
                    reason=reason,
                ))

    return signals


def cb_t0_strategy(trade_date: date, portfolio: dict, data: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    if len(data) < 5:
        return signals

    symbol = portfolio.get("symbol", "")
    close = data["收盘"].astype(float)
    open_prices = data["开盘"].astype(float)
    low_prices = data["最低"].astype(float)
    high_prices = data["最高"].astype(float)

    current_close = float(close.iloc[-1])
    current_open = float(open_prices.iloc[-1])
    current_low = float(low_prices.iloc[-1])
    current_high = float(high_prices.iloc[-1])
    prev_close = float(close.iloc[-2])
    ma5 = float(close.iloc[-5:].mean())

    has_position = symbol in portfolio.get("positions", {})

    if not has_position and current_open < prev_close and current_close > current_open:
        gap_down_pct = (current_open - prev_close) / prev_close if prev_close > 0 else 0.0
        if gap_down_pct > -0.03 and current_close > ma5:
            invest_amount = portfolio["cash"] * 0.1
            quantity = int(invest_amount / current_open / 100) * 100
            if quantity >= 100:
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    price=current_open,
                    quantity=float(quantity),
                    strategy="cb_t0",
                    reason="低开高走T+0买入",
                ))

    if has_position:
        pos = portfolio["positions"][symbol]
        intraday_pct = (current_close - pos.get("current_price", current_open)) / pos.get("current_price", current_open) if pos.get("current_price", 0) > 0 else 0.0
        loss_pct = (current_close - pos["cost_price"]) / pos["cost_price"] if pos["cost_price"] > 0 else 0.0

        if intraday_pct > 0.015 or loss_pct < -0.03:
            sell_qty = pos["quantity"]
            if sell_qty > 0:
                reason = "T+0止盈" if intraday_pct > 0.015 else "T+0止损"
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    price=current_close,
                    quantity=float(sell_qty),
                    strategy="cb_t0",
                    reason=reason,
                ))

    return signals


def long_value_strategy(trade_date: date, portfolio: dict, data: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    if len(data) < 60:
        return signals

    symbol = portfolio.get("symbol", "")
    close = data["收盘"].astype(float)
    current_price = float(close.iloc[-1])
    ma5 = float(close.iloc[-5:].mean())
    ma20 = float(close.iloc[-20:].mean())
    ma60 = float(close.iloc[-60:].mean())
    prev_ma5 = float(close.iloc[-6:-1].mean())
    prev_ma20 = float(close.iloc[-21:-1].mean())

    has_position = symbol in portfolio.get("positions", {})

    if not has_position:
        golden_cross = prev_ma5 <= prev_ma20 and ma5 > ma20
        bullish_align = ma5 > ma20 > ma60
        if golden_cross or bullish_align:
            invest_amount = portfolio["cash"] * 0.4
            quantity = int(invest_amount / current_price / 100) * 100
            if quantity >= 100:
                reason = "均线金叉买入" if golden_cross else "多头排列买入"
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    price=current_price,
                    quantity=float(quantity),
                    strategy="long_value",
                    reason=reason,
                ))

    if has_position:
        pos = portfolio["positions"][symbol]
        loss_pct = (current_price - pos["cost_price"]) / pos["cost_price"] if pos["cost_price"] > 0 else 0.0
        death_cross = prev_ma5 >= prev_ma20 and ma5 < ma20

        if death_cross or loss_pct < -0.08:
            sell_qty = pos["quantity"]
            if sell_qty > 0:
                reason = "均线死叉卖出" if death_cross else "长线止损-8%"
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    price=current_price,
                    quantity=float(sell_qty),
                    strategy="long_value",
                    reason=reason,
                ))

    return signals


class BacktestEngine:
    def __init__(self, data_manager: Optional[DataSourceManager] = None):
        self._data_manager = data_manager or DataSourceManager()

    def run(
        self,
        strategy_func: StrategyFunc,
        stock_code: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 1_000_000.0,
    ) -> BacktestResult:
        try:
            kline_result = self._data_manager.get_history_kline(
                stock_code, start_date, end_date
            )
            df = kline_result.data.copy()
        except Exception as e:
            logger.error(f"Failed to get kline data for {stock_code}: {e}")
            return BacktestResult(
                total_return=0.0, annual_return=0.0, max_drawdown=0.0,
                sharpe_ratio=0.0, win_rate=0.0, profit_loss_ratio=0.0,
                trade_count=0, equity_curve=[initial_capital], trades=[],
            )

        if df is None or df.empty:
            logger.warning(f"No data for {stock_code} from {start_date} to {end_date}")
            return BacktestResult(
                total_return=0.0, annual_return=0.0, max_drawdown=0.0,
                sharpe_ratio=0.0, win_rate=0.0, profit_loss_ratio=0.0,
                trade_count=0, equity_curve=[initial_capital], trades=[],
            )

        portfolio: dict = {
            "cash": initial_capital,
            "positions": {},
            "total_value": initial_capital,
            "symbol": stock_code,
        }

        equity_curve: list[float] = [initial_capital]
        trades: list[dict] = []
        peak_equity = initial_capital
        max_drawdown = 0.0

        for idx in range(len(df)):
            row = df.iloc[idx]
            try:
                current_date = pd.Timestamp(row["日期"]).date()
            except Exception:
                continue
            current_price = float(row["收盘"])

            for sym in portfolio["positions"]:
                portfolio["positions"][sym]["current_price"] = current_price

            position_value = sum(
                pos["quantity"] * pos.get("current_price", current_price)
                for pos in portfolio["positions"].values()
            )
            portfolio["total_value"] = portfolio["cash"] + position_value

            data_slice = df.iloc[:idx + 1].copy()

            try:
                signals = strategy_func(current_date, portfolio, data_slice)
            except Exception as e:
                logger.warning(f"Strategy error on {current_date}: {e}")
                signals = []

            for signal in signals:
                self._execute_signal(signal, portfolio, trades, current_date)

            position_value = sum(
                pos["quantity"] * pos.get("current_price", current_price)
                for pos in portfolio["positions"].values()
            )
            portfolio["total_value"] = portfolio["cash"] + position_value
            equity_curve.append(portfolio["total_value"])

            if portfolio["total_value"] > peak_equity:
                peak_equity = portfolio["total_value"]
            dd = (peak_equity - portfolio["total_value"]) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

        return self._calc_metrics(equity_curve, trades, initial_capital, start_date, end_date, max_drawdown)

    def _execute_signal(
        self,
        signal: Signal,
        portfolio: dict,
        trades: list[dict],
        trade_date: date,
    ) -> None:
        if signal.side == OrderSide.BUY:
            amount = signal.price * signal.quantity
            commission = max(amount * _COMMISSION_RATE, _MIN_COMMISSION)
            total_cost = amount + commission

            if total_cost > portfolio["cash"]:
                return

            portfolio["cash"] -= total_cost

            if signal.symbol in portfolio["positions"]:
                pos = portfolio["positions"][signal.symbol]
                total_qty = pos["quantity"] + signal.quantity
                pos["cost_price"] = (pos["cost_price"] * pos["quantity"] + signal.price * signal.quantity) / total_qty
                pos["quantity"] = total_qty
                pos["current_price"] = signal.price
            else:
                portfolio["positions"][signal.symbol] = {
                    "quantity": signal.quantity,
                    "cost_price": signal.price,
                    "current_price": signal.price,
                }

            trades.append({
                "date": trade_date.isoformat(),
                "symbol": signal.symbol,
                "side": "buy",
                "price": signal.price,
                "quantity": signal.quantity,
                "amount": amount,
                "commission": commission,
                "stamp_tax": 0.0,
                "strategy": signal.strategy,
                "reason": signal.reason,
            })

        elif signal.side == OrderSide.SELL:
            pos = portfolio["positions"].get(signal.symbol)
            if pos is None or pos["quantity"] < signal.quantity:
                return

            amount = signal.price * signal.quantity
            commission = max(amount * _COMMISSION_RATE, _MIN_COMMISSION)
            stamp_tax = amount * _STAMP_TAX_RATE
            net_proceeds = amount - commission - stamp_tax

            profit = (signal.price - pos["cost_price"]) * signal.quantity - commission - stamp_tax

            portfolio["cash"] += net_proceeds
            pos["quantity"] -= signal.quantity
            pos["current_price"] = signal.price

            if pos["quantity"] <= 0:
                del portfolio["positions"][signal.symbol]

            trades.append({
                "date": trade_date.isoformat(),
                "symbol": signal.symbol,
                "side": "sell",
                "price": signal.price,
                "quantity": signal.quantity,
                "amount": amount,
                "commission": commission,
                "stamp_tax": stamp_tax,
                "profit": profit,
                "strategy": signal.strategy,
                "reason": signal.reason,
            })

    def _calc_metrics(
        self,
        equity_curve: list[float],
        trades: list[dict],
        initial_capital: float,
        start_date: str,
        end_date: str,
        max_drawdown: float,
    ) -> BacktestResult:
        total_return = (equity_curve[-1] - initial_capital) / initial_capital if initial_capital > 0 else 0.0

        try:
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)
            years = max((end - start).days / 365.25, 1 / 252)
        except Exception:
            years = 1.0

        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

        sharpe = 0.0
        daily_returns_list: list[float] = []
        if len(equity_curve) > 1:
            daily_returns_list = (np.diff(equity_curve) / np.array(equity_curve[:-1])).tolist()
            std = np.std(daily_returns_list)
            if std > 0:
                sharpe = float((np.mean(daily_returns_list) * 252 - _RISK_FREE_RATE) / (std * np.sqrt(252)))

        # 借鉴 mementum/backtrader Analyzer,补充风险调整收益指标
        sortino = _calc_sortino_ratio(daily_returns_list, _RISK_FREE_RATE)
        calmar = _calc_calmar_ratio(annual_return, max_drawdown)
        volatility = _calc_volatility(daily_returns_list)

        sell_trades = [t for t in trades if t["side"] == "sell"]
        win_trades = [t for t in sell_trades if t.get("profit", 0) > 0]
        lose_trades = [t for t in sell_trades if t.get("profit", 0) < 0]

        win_rate = len(win_trades) / len(sell_trades) if sell_trades else 0.0

        avg_win = float(np.mean([t["profit"] for t in win_trades])) if win_trades else 0.0
        avg_loss = abs(float(np.mean([t["profit"] for t in lose_trades]))) if lose_trades else 1.0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        return BacktestResult(
            total_return=round(total_return, 4),
            annual_return=round(annual_return, 4),
            max_drawdown=round(max_drawdown, 4),
            sharpe_ratio=round(sharpe, 4),
            win_rate=round(win_rate, 4),
            profit_loss_ratio=round(float(profit_loss_ratio), 4),
            trade_count=len(trades),
            equity_curve=[round(v, 2) for v in equity_curve],
            trades=trades,
            sortino_ratio=round(sortino, 4) if sortino != float("inf") else float("inf"),
            calmar_ratio=round(calmar, 4),
            volatility=round(volatility, 4),
        )

    def run_all_strategies(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 1_000_000.0,
    ) -> dict[str, BacktestResult]:
        strategies: dict[str, StrategyFunc] = {
            "new_high": new_high_strategy,
            "limit_up": limit_up_strategy,
            "cb_t0": cb_t0_strategy,
            "long_value": long_value_strategy,
        }
        results: dict[str, BacktestResult] = {}
        for name, func in strategies.items():
            logger.info(f"Running backtest for strategy: {name}")
            try:
                result = self.run(func, stock_code, start_date, end_date, initial_capital)
                results[name] = result
            except Exception as e:
                logger.error(f"Backtest failed for {name}: {e}")
        return results

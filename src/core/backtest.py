from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger

from .data_source import DataSourceManager
from .executor import OrderSide, Signal

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
    information_ratio: float = 0.0  # 信息比率:相对买入持有基准的超额收益/跟踪误差
    # 立项 1(2026-11-12,借鉴 quantstats tear sheet):日收益分布形态 + 月度收益表
    skewness: float | None = None   # 日收益偏度,样本 <3 或退化时 None(JSON null → 前端 —)
    kurtosis: float | None = None   # 日收益超额峰度(pandas/Fisher 口径),样本 <4 时 None
    monthly_returns: list[dict] = field(default_factory=list)  # [{"month": "YYYY-MM", "return_pct": 5.2}]


def _calc_sortino_ratio(returns: list[float], risk_free: float = _RISK_FREE_RATE) -> float:
    """Sortino 比率 - 只惩罚下行波动。

    借鉴 mementum/backtrader TimeReturn Analyzer 设计思想:
    与 Sharpe 的区别是分子母只用"下行波动"(负收益的标准差),
    避免正收益也被算作风险,更符合投资者对"风险"的真实感受。

    Args:
        returns: 日收益率序列,如 [0.01, -0.02, 0.005]
        risk_free: 年化无风险利率,默认 3%

    Returns:
        Sortino 比率,无数据返回 0.0,无下行波动返回 inf(表示无下行风险)。
        注意: inf 不能直接进 JSON 响应,Web 边界必须先做 isfinite 消毒
        (见 web/routes/dashboard.py portfolio_risk)。
    """
    if not returns:
        return 0.0
    arr = np.array(returns, dtype=float)
    downside = arr[arr < 0]
    if len(downside) == 0:
        # 2026-08-01 决策(2dfe9e1): 无下行波动返回 inf 而非有限大数 99.0,inf 语义由
        # tests/test_backtest_analyzers.py 锁定。inf 会被 JSON 序列化为非法的裸
        # Infinity → Web 边界必须先做 isfinite 消毒(dashboard.py portfolio_risk)。
        return float('inf') if float(arr.mean()) > 0 else 0.0
    if len(downside) < 2:
        # 单个下行点无法可靠估计 std,视为无下行波动
        daily_rf_single = risk_free / _TRADING_DAYS_PER_YEAR
        return float('inf') if float(arr.mean()) > daily_rf_single else 0.0
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
    # P3-6 修复(2026-07-30):统一 ddof=1(样本标准差),与 Sharpe/Sortino 保持一致
    return float(np.std(arr, ddof=1) * np.sqrt(_TRADING_DAYS_PER_YEAR)) if len(arr) > 1 else 0.0


def _calc_information_ratio(
    strategy_returns: list[float],
    benchmark_returns: Optional[list[float]] = None,
) -> float:
    """信息比率 IR = E[Rp-Rb] / σ(Rp-Rb),年化。

    基准取同标的"买入持有"(不做任何调仓),衡量策略相对躺平是否值得:
    IR > 0 策略跑赢持有不动,IR < 0 策略反而不如躺平。

    Args:
        strategy_returns: 策略日收益率序列
        benchmark_returns: 基准日收益率序列(与策略序列按日对齐)

    Returns:
        年化信息比率;序列为空/长度不足/长度不一致/跟踪误差为 0 时返回 0.0
    """
    if not strategy_returns or not benchmark_returns:
        return 0.0
    if len(strategy_returns) != len(benchmark_returns) or len(strategy_returns) < 2:
        return 0.0
    strat = np.array(strategy_returns, dtype=float)
    bench = np.array(benchmark_returns, dtype=float)
    excess = strat - bench
    # ddof=1 与 Sharpe/Sortino 口径统一;跟踪误差为 0 时返回 0.0(JSON 安全,不返回 inf)
    tracking_error = float(np.std(excess, ddof=1))
    if tracking_error <= 0:
        return 0.0
    return float(np.mean(excess) * _TRADING_DAYS_PER_YEAR / (tracking_error * np.sqrt(_TRADING_DAYS_PER_YEAR)))


def _calc_skew_kurtosis(returns: list[float]) -> tuple[Optional[float], Optional[float]]:
    """日收益偏度与超额峰度(pandas 口径,kurt 为 Fisher 超额峰度,正态分布=0)。

    立项 1(2026-11-12,借鉴 quantstats tear sheet 的分布形态指标)。
    样本不足(偏度需 ≥3、峰度需 ≥4)或序列退化时返回 None —— 缺失显示缺失,
    不填 0 冒充"零偏度"。
    """
    if len(returns) < 3:
        return None, None
    s = pd.Series(returns, dtype=float)
    skew = s.skew()
    kurt = s.kurt() if len(returns) >= 4 else None
    skew_v = float(skew) if pd.notna(skew) else None
    kurt_v = float(kurt) if kurt is not None and pd.notna(kurt) else None
    return skew_v, kurt_v


def _calc_monthly_returns(equity_dates: list, equity_curve: list[float]) -> list[dict]:
    """按自然月聚合权益曲线的月度收益表(%),借鉴 quantstats Monthly Returns 表。

    equity_curve[0] 为期初本金(无日期),equity_dates 与 equity_curve[1:] 逐日对齐;
    长度不齐(如日期解析跳行)返回 [],由前端按缺失显示 —,不伪造月度序列。
    相邻月份存在日历缺口时,该行收益为两个观测月末间的区间收益(如实计算)。
    """
    if not equity_dates or len(equity_dates) != len(equity_curve) - 1:
        return []
    month_end: dict[tuple[int, int], float] = {}
    for d, equity in zip(equity_dates, equity_curve[1:]):
        month_end[(d.year, d.month)] = equity  # 逐日覆盖,循环结束即该月最后观测值
    monthly: list[dict] = []
    prev = equity_curve[0]
    for key in sorted(month_end):
        end_equity = month_end[key]
        monthly.append({
            "month": f"{key[0]:04d}-{key[1]:02d}",
            "return_pct": round((end_equity / prev - 1) * 100, 2) if prev > 0 else 0.0,
        })
        prev = end_equity
    return monthly


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

    current_close = float(close.iloc[-1])
    current_open = float(open_prices.iloc[-1])
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


def make_ma_cross_strategy(short_window: int = 5, long_window: int = 20, name: str = "ma_cross") -> StrategyFunc:
    """MA 金叉/死叉趋势策略工厂(视频"量化对轰"链路 STEP 09/12 的经典教学策略)。

    状态判定(非交叉瞬间):MA_short > MA_long → 满仓持有;MA_short < MA_long → 空仓观望。
    工厂形式支持参数网格优化(optimize_ma_cross 为每组 (short, long) 生成独立策略实例)。

    Args:
        short_window: 短期均线窗口,如 5
        long_window: 长期均线窗口,如 20
        name: 信号上的策略标识

    Returns:
        与其它策略同签名的策略函数
    """
    def ma_cross_strategy(trade_date: date, portfolio: dict, data: pd.DataFrame) -> list[Signal]:
        signals: list[Signal] = []
        if len(data) < long_window:
            return signals

        symbol = portfolio.get("symbol", "")
        close = data["收盘"].astype(float)
        current_price = float(close.iloc[-1])
        short_ma = float(close.iloc[-short_window:].mean())
        long_ma = float(close.iloc[-long_window:].mean())
        has_position = symbol in portfolio.get("positions", {})

        if not has_position and short_ma > long_ma:
            quantity = int(portfolio["cash"] / current_price / 100) * 100
            if quantity >= 100:
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    price=current_price,
                    quantity=float(quantity),
                    strategy=name,
                    reason=f"金叉 MA{short_window}>MA{long_window} 满仓买入",
                ))
        elif has_position and short_ma < long_ma:
            sell_qty = portfolio["positions"][symbol]["quantity"]
            if sell_qty > 0:
                signals.append(Signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    price=current_price,
                    quantity=float(sell_qty),
                    strategy=name,
                    reason=f"死叉 MA{short_window}<MA{long_window} 空仓观望",
                ))

        return signals
    return ma_cross_strategy


ma_cross_strategy = make_ma_cross_strategy()


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

        return self._simulate(strategy_func, df, start_date, end_date, initial_capital, stock_code=stock_code)

    def _simulate(
        self,
        strategy_func: StrategyFunc,
        df: pd.DataFrame,
        start_date: str,
        end_date: str,
        initial_capital: float,
        stock_code: str = "",
        copy_slices: bool = True,
    ) -> BacktestResult:
        """在给定K线数据上模拟策略(不重新取数),供 run 与 optimize_ma_cross 复用。

        copy_slices=False 时策略拿到的是切片视图而非副本:内置策略只读不写,
        网格优化跑几十组模拟时省掉 O(n²) 的每日复制;外部自定义策略若会修改
        切片,必须保持默认 True。
        """
        portfolio: dict = {
            "cash": initial_capital,
            "positions": {},
            "total_value": initial_capital,
            "symbol": stock_code,
        }

        equity_curve: list[float] = [initial_capital]
        equity_dates: list[date] = []  # 与 equity_curve[1:] 逐日对齐,供月度收益表聚合
        trades: list[dict] = []
        peak_equity = initial_capital
        max_drawdown = 0.0
        valid_rows = 0

        for idx in range(len(df)):
            row = df.iloc[idx]
            try:
                current_date = pd.Timestamp(row["日期"]).date()
            except Exception:
                continue
            valid_rows += 1
            current_price = float(row["收盘"])

            for sym in portfolio["positions"]:
                portfolio["positions"][sym]["current_price"] = current_price

            position_value = sum(
                pos["quantity"] * pos.get("current_price", current_price)
                for pos in portfolio["positions"].values()
            )
            portfolio["total_value"] = portfolio["cash"] + position_value

            data_slice = df.iloc[:idx + 1].copy() if copy_slices else df.iloc[:idx + 1]

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
            equity_dates.append(current_date)

            if portfolio["total_value"] > peak_equity:
                peak_equity = portfolio["total_value"]
            dd = (peak_equity - portfolio["total_value"]) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

        # 买入持有基准:只在每行日期都有效时收益序列才能与权益曲线逐日对齐
        benchmark_returns: Optional[list[float]] = None
        closes = df["收盘"].astype(float).tolist()
        if valid_rows == len(df) and len(closes) >= 2 and closes[0] > 0:
            padded = [closes[0]] + closes[:-1]
            benchmark_returns = ((np.array(closes) - np.array(padded)) / np.array(padded)).tolist()

        return self._calc_metrics(
            equity_curve, trades, initial_capital, start_date, end_date, max_drawdown,
            benchmark_returns=benchmark_returns, equity_dates=equity_dates,
        )

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
        benchmark_returns: Optional[list[float]] = None,
        equity_dates: Optional[list[date]] = None,
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
            # P3-6 修复(2026-07-30):统一为 ddof=1(样本标准差),与 Sortino 计算保持一致。
            # 原代码默认 ddof=0(总体标准差),与 Sortino 的 ddof=1 不统一,影响绝对值解读。
            std = np.std(daily_returns_list, ddof=1) if len(daily_returns_list) > 1 else np.std(daily_returns_list)
            if std > 0:
                sharpe = float((np.mean(daily_returns_list) * 252 - _RISK_FREE_RATE) / (std * np.sqrt(252)))

        # 借鉴 mementum/backtrader Analyzer,补充风险调整收益指标
        sortino = _calc_sortino_ratio(daily_returns_list, _RISK_FREE_RATE)
        calmar = _calc_calmar_ratio(annual_return, max_drawdown)
        volatility = _calc_volatility(daily_returns_list)
        information_ratio = _calc_information_ratio(daily_returns_list, benchmark_returns)
        skew, kurt = _calc_skew_kurtosis(daily_returns_list)
        monthly = _calc_monthly_returns(equity_dates or [], equity_curve)

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
            information_ratio=round(information_ratio, 4),
            skewness=round(skew, 4) if skew is not None else None,
            kurtosis=round(kurt, 4) if kurt is not None else None,
            monthly_returns=monthly,
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
            "ma_cross": ma_cross_strategy,
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

    def optimize_ma_cross(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 1_000_000.0,
        short_windows: Optional[range] = None,
        long_windows: Optional[range] = None,
    ) -> dict:
        """MA 金叉策略参数网格优化(视频"量化对轰"链路 STEP 15:参数优化与迭代)。

        K线只取一次,对每组 (short_window, long_window) 复用同一份数据模拟,
        按信息比率(IR)降序输出网格,便于前端画 MA_short × MA_long 热力图。
        仅评估 short < long 的组合。

        Args:
            short_windows: 短期均线窗口候选,默认 range(3, 12)
            long_windows: 长期均线窗口候选,默认 range(10, 41, 5)

        Returns:
            {"stock_code", "start_date", "end_date", "grid": [...按 IR 降序...],
             "best": 首项或 None(含 tear sheet 字段: monthly_returns/skewness/kurtosis,
             立项 1 借鉴 quantstats,仅 best 附带以免网格条目膨胀)}
        """
        if short_windows is None:
            short_windows = range(3, 12)
        if long_windows is None:
            long_windows = range(10, 41, 5)

        try:
            kline_result = self._data_manager.get_history_kline(stock_code, start_date, end_date)
            df = kline_result.data.copy()
        except Exception as e:
            logger.error(f"Failed to get kline data for {stock_code}: {e}")
            df = None

        grid: list[dict] = []
        results_by_combo: dict[tuple[int, int], BacktestResult] = {}
        if df is not None and not df.empty:
            for short in short_windows:
                for long in long_windows:
                    if short >= long:
                        continue
                    strategy = make_ma_cross_strategy(short, long)
                    try:
                        result = self._simulate(
                            strategy, df, start_date, end_date, initial_capital,
                            stock_code=stock_code, copy_slices=False,
                        )
                    except Exception as e:
                        logger.warning(f"optimize_ma_cross failed for ({short},{long}): {e}")
                        continue
                    results_by_combo[(short, long)] = result
                    grid.append({
                        "short_window": short,
                        "long_window": long,
                        "total_return": result.total_return,
                        "annual_return": result.annual_return,
                        "max_drawdown": result.max_drawdown,
                        "sharpe_ratio": result.sharpe_ratio,
                        "information_ratio": result.information_ratio,
                        "trade_count": result.trade_count,
                    })

        grid.sort(key=lambda item: item["information_ratio"], reverse=True)
        best = grid[0] if grid else None
        if best:
            best_result = results_by_combo[(best["short_window"], best["long_window"])]
            best["skewness"] = best_result.skewness
            best["kurtosis"] = best_result.kurtosis
            best["monthly_returns"] = best_result.monthly_returns
        return {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
            "grid": grid,
            "best": best,
        }

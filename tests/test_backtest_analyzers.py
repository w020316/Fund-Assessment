"""回测分析器单元测试

验证 src/core/backtest.py 新增的三大风险调整收益指标(借鉴 mementum/backtrader Analyzer 设计):
- _calc_sortino_ratio: Sortino 比率(只惩罚下行波动)
- _calc_calmar_ratio: Calmar 比率(年化收益/最大回撤)
- _calc_volatility: 年化波动率(daily_returns.std * sqrt(252))

测试覆盖:
- 正常输入(典型值范围)
- 边界输入(空列表/全零/全正/全负)
- 极端值(inf/NaN 兜底)
- BacktestResult dataclass 新字段默认值
- _calc_metrics 集成调用是否填充新字段
"""
from __future__ import annotations

import math
from datetime import date
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from src.core.backtest import (
    BacktestEngine,
    BacktestResult,
    _calc_calmar_ratio,
    _calc_sortino_ratio,
    _calc_volatility,
    new_high_strategy,
)


class TestCalcSortinoRatio:
    """_calc_sortino_ratio Sortino 比率(借鉴 backtrader TimeReturn Analyzer)"""

    def test_empty_returns_zero(self):
        """空列表 → 0.0"""
        assert _calc_sortino_ratio([]) == 0.0

    def test_all_positive_returns_inf(self):
        """全正收益(无下行波动)且均值>0 → inf"""
        result = _calc_sortino_ratio([0.01, 0.02, 0.005])
        assert result == float("inf")

    def test_all_zero_returns_zero(self):
        """全零收益(无下行波动且均值=0) → 0.0"""
        assert _calc_sortino_ratio([0.0, 0.0, 0.0]) == 0.0

    def test_mixed_returns_finite(self):
        """正负混合收益 → 有限值,且应大于0(超额收益>0)"""
        # 5 个点,3正2负,均值明显大于日无风险利率(0.03/252≈0.0001)
        returns = [0.02, -0.01, 0.015, -0.005, 0.025]
        result = _calc_sortino_ratio(returns)
        assert isinstance(result, float)
        assert not math.isnan(result)
        # 均值 = 0.009 > 0.0001,所以 Sortino > 0
        assert result > 0

    def test_negative_excess_returns_negative(self):
        """收益远低于无风险利率 → Sortino < 0"""
        # 日均收益 -5%,远低于无风险利率
        returns = [-0.05, -0.06, -0.04, -0.05, -0.07]
        result = _calc_sortino_ratio(returns, risk_free=0.03)
        assert result < 0

    def test_custom_risk_free_rate(self):
        """应支持自定义无风险利率"""
        # 用5个点确保有2+个下行点
        returns = [0.02, -0.01, 0.015, -0.005, 0.025]
        low_rf = _calc_sortino_ratio(returns, risk_free=0.01)
        high_rf = _calc_sortino_ratio(returns, risk_free=0.50)
        # 高无风险利率 → 超额收益更低 → Sortino 更低
        assert high_rf < low_rf

    def test_downside_only_punished(self):
        """验证只惩罚下行波动:同样 std 但只有负收益的下行 std 更大"""
        # 收益序列1:正负各半
        r1 = [0.02, -0.02, 0.02, -0.02]
        # 收益序列2:相同 std 但全正
        r2 = [0.02, 0.0, 0.02, 0.0]
        s1 = _calc_sortino_ratio(r1, risk_free=0.0)
        # r2 无下行波动应返回 inf
        assert s1 < float("inf")
        # r2 无下行波动,均值>0,应 inf
        assert _calc_sortino_ratio(r2, risk_free=0.0) == float("inf")


class TestCalcCalmarRatio:
    """_calc_calmar_ratio Calmar 比率(借鉴 backtrader Calmar Analyzer)"""

    def test_zero_drawdown_returns_zero(self):
        """max_drawdown=0 → 0.0(避免除零)"""
        assert _calc_calmar_ratio(0.15, 0.0) == 0.0

    def test_positive_returns_positive(self):
        """年化收益>0,回撤>0 → 正值"""
        result = _calc_calmar_ratio(0.15, 0.10)
        assert result == pytest.approx(1.5)

    def test_negative_return_negative_ratio(self):
        """负年化收益 → 负 Calmar"""
        result = _calc_calmar_ratio(-0.10, 0.05)
        assert result == pytest.approx(-2.0)

    def test_negative_drawdown_handled(self):
        """max_drawdown 传负数也能处理(取绝对值)"""
        # 实际调用约定 max_drawdown 为正数,但应容错负值
        result = _calc_calmar_ratio(0.20, -0.10)
        assert result == pytest.approx(2.0)

    def test_high_calmar_indicates_good_strategy(self):
        """Calmar > 3 视为优秀策略"""
        # 年化 30%,回撤 5% → Calmar=6
        result = _calc_calmar_ratio(0.30, 0.05)
        assert result > 3.0

    def test_low_calmar_indicates_poor_strategy(self):
        """Calmar < 1 视为低质量策略"""
        # 年化 5%,回撤 10% → Calmar=0.5
        result = _calc_calmar_ratio(0.05, 0.10)
        assert result < 1.0


class TestCalcVolatility:
    """_calc_volatility 年化波动率(借鉴 backtrader Volatility Analyzer)"""

    def test_empty_returns_zero(self):
        """空列表 → 0.0"""
        assert _calc_volatility([]) == 0.0

    def test_constant_returns_zero(self):
        """常量收益(无波动) → 0.0"""
        assert _calc_volatility([0.01, 0.01, 0.01, 0.01]) == 0.0

    def test_volatile_returns_high(self):
        """波动大的收益序列 → 高波动率"""
        low_vol = _calc_volatility([0.001, -0.001, 0.001, -0.001])
        high_vol = _calc_volatility([0.05, -0.05, 0.05, -0.05])
        assert high_vol > low_vol
        assert high_vol > 0.5  # 大于 50% 年化波动

    def test_annualization_factor(self):
        """应按 sqrt(252) 年化"""
        # 每日固定 ±1% 波动,std=0.01,年化=0.01*sqrt(252)≈0.1587
        returns = [0.01, -0.01] * 10  # 20 个点,std 应为 ~0.01
        result = _calc_volatility(returns)
        expected = 0.01 * math.sqrt(252)
        assert result == pytest.approx(expected, rel=0.1)

    def test_stock_typical_range(self):
        """典型股票年化波动率应在 15-40% 范围"""
        # 模拟日波动 ~1.2% 的股票
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0005, 0.012, 252).tolist()
        result = _calc_volatility(returns)
        assert 0.10 < result < 0.40


class TestBacktestResultDefaults:
    """BacktestResult dataclass 新字段默认值"""

    def test_new_fields_default_zero(self):
        """新字段应默认为 0.0,保持向后兼容"""
        result = BacktestResult(
            total_return=0.1,
            annual_return=0.05,
            max_drawdown=0.08,
            sharpe_ratio=1.2,
            win_rate=0.6,
            profit_loss_ratio=2.0,
            trade_count=10,
            equity_curve=[100000, 110000],
            trades=[],
        )
        assert result.sortino_ratio == 0.0
        assert result.calmar_ratio == 0.0
        assert result.volatility == 0.0

    def test_new_fields_can_be_set(self):
        """新字段可显式传入"""
        result = BacktestResult(
            total_return=0.1, annual_return=0.05, max_drawdown=0.08,
            sharpe_ratio=1.2, win_rate=0.6, profit_loss_ratio=2.0,
            trade_count=10, equity_curve=[100000, 110000], trades=[],
            sortino_ratio=1.5, calmar_ratio=0.625, volatility=0.18,
        )
        assert result.sortino_ratio == 1.5
        assert result.calmar_ratio == 0.625
        assert result.volatility == 0.18


class TestCalcMetricsIntegration:
    """_calc_metrics 集成测试 - 验证新指标被正确填充"""

    def test_calc_metrics_populates_new_fields(self):
        """_calc_metrics 应填充 sortino_ratio/calmar_ratio/volatility"""
        engine = BacktestEngine.__new__(BacktestEngine)
        # 构造合成数据:252 个交易日的权益曲线
        initial_capital = 1_000_000.0
        # 模拟一个年化 10%、有回撤的曲线
        rng = np.random.default_rng(7)
        daily_returns = rng.normal(0.0004, 0.012, 252)
        equity_curve = [initial_capital]
        for r in daily_returns:
            equity_curve.append(equity_curve[-1] * (1 + r))

        result = engine._calc_metrics(
            equity_curve=equity_curve,
            trades=[],
            initial_capital=initial_capital,
            start_date="2025-01-01",
            end_date="2025-12-31",
            max_drawdown=0.10,
        )
        assert result.sortino_ratio != 0.0  # 应被计算(有下行波动)
        assert result.calmar_ratio != 0.0  # max_drawdown=0.10 → 应计算
        assert result.volatility > 0.0  # 有波动

    def test_calc_metrics_empty_curve_safe(self):
        """空权益曲线(仅 initial)→ 新字段应为 0,不报错"""
        engine = BacktestEngine.__new__(BacktestEngine)
        result = engine._calc_metrics(
            equity_curve=[1_000_000.0],
            trades=[],
            initial_capital=1_000_000.0,
            start_date="2025-01-01",
            end_date="2025-01-01",
            max_drawdown=0.0,
        )
        assert result.sortino_ratio == 0.0
        assert result.calmar_ratio == 0.0
        assert result.volatility == 0.0

    def test_calc_metrics_constant_curve_zero_volatility(self):
        """权益曲线恒定 → 波动率=0,Sortino=0(无下行波动且均值=0)"""
        engine = BacktestEngine.__new__(BacktestEngine)
        result = engine._calc_metrics(
            equity_curve=[1_000_000.0] * 10,
            trades=[],
            initial_capital=1_000_000.0,
            start_date="2025-01-01",
            end_date="2025-01-15",
            max_drawdown=0.0,
        )
        assert result.volatility == 0.0
        assert result.sortino_ratio == 0.0


class TestBacktestEngineEndToEnd:
    """端到端验证 - 使用 mock 数据运行完整回测"""

    def test_run_returns_result_with_new_metrics(self):
        """BacktestEngine.run 应返回带新指标的 BacktestResult"""
        # 构造 mock K线数据
        dates = pd.date_range("2025-01-01", periods=60, freq="B")
        df = pd.DataFrame({
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": np.linspace(10, 12, 60),
            "收盘": np.linspace(10, 12, 60) + np.random.default_rng(1).normal(0, 0.1, 60),
            "最高": np.linspace(10.5, 12.5, 60),
            "最低": np.linspace(9.5, 11.5, 60),
            "成交量": [10000] * 60,
        })

        # Mock DataSourceManager
        mock_dm = MagicMock()
        mock_kline = MagicMock()
        mock_kline.data = df
        mock_dm.get_history_kline.return_value = mock_kline

        engine = BacktestEngine(data_manager=mock_dm)
        result = engine.run(
            strategy_func=new_high_strategy,
            stock_code="600519",
            start_date="2025-01-01",
            end_date="2025-03-31",
            initial_capital=1_000_000.0,
        )
        assert isinstance(result, BacktestResult)
        # 新字段应有值(即使为 0 也应是 float)
        assert isinstance(result.sortino_ratio, float)
        assert isinstance(result.calmar_ratio, float)
        assert isinstance(result.volatility, float)

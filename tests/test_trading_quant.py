"""交易量化策略单元测试

验证 src/strategies/trading_quant.py 的核心逻辑:
- Signal 枚举 / ScoreResult 数据类
- WEIGHTS 权重和为 1
- _signal_from_score 评分→信号映射(静态)
- _suggestion_from_signal 信号→建议映射(静态)
- stock_analysis 个股分析(mock akshare)
- capital_flow 资金流(mock akshare)
- market_anomaly 市场异动(mock akshare)
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategies import trading_quant as mod
from src.strategies.trading_quant import (
    Signal,
    ScoreResult,
    TradingQuant,
    WEIGHTS,
)


@pytest.fixture
def quant() -> TradingQuant:
    return TradingQuant()


def _make_kline_df(rows: int = 120) -> pd.DataFrame:
    closes = 100.0 + np.arange(rows) * 0.5
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=rows).strftime("%Y-%m-%d"),
        "开盘": closes - 0.3,
        "最高": closes + 0.8,
        "最低": closes - 0.8,
        "收盘": closes,
        "成交量": np.full(rows, 1_000_000.0),
        "成交额": closes * 1_000_000,
    })


class TestSignal:
    """Signal 枚举"""

    def test_all_signals_defined(self):
        expected = {"STRONG_BUY", "BUY", "WATCH", "HOLD", "SELL", "STRONG_SELL"}
        actual = {s.name for s in Signal}
        assert actual == expected

    def test_is_str_enum(self):
        assert isinstance(Signal.BUY, str)


class TestScoreResult:
    """ScoreResult 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(ScoreResult)

    def test_defaults(self):
        r = ScoreResult()
        assert r.composite == 0.0
        assert r.signal == Signal.HOLD
        assert r.suggestion == ""


class TestWeights:
    """WEIGHTS 权重"""

    def test_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_all_dimensions_present(self):
        assert set(WEIGHTS.keys()) == {"technical", "capital", "fundamental", "news", "sentiment"}


class TestSignalFromScore:
    """_signal_from_score 静态映射"""

    def test_strong_buy_threshold(self):
        assert TradingQuant._signal_from_score(80.0) == Signal.STRONG_BUY

    def test_buy_threshold(self):
        assert TradingQuant._signal_from_score(65.0) == Signal.BUY

    def test_watch_threshold(self):
        assert TradingQuant._signal_from_score(50.0) == Signal.WATCH

    def test_hold_threshold(self):
        assert TradingQuant._signal_from_score(35.0) == Signal.HOLD

    def test_sell_threshold(self):
        assert TradingQuant._signal_from_score(20.0) == Signal.SELL

    def test_strong_sell_threshold(self):
        assert TradingQuant._signal_from_score(10.0) == Signal.STRONG_SELL


class TestSuggestionFromSignal:
    """_suggestion_from_signal 静态映射"""

    def test_each_signal_has_suggestion(self):
        for signal in Signal:
            suggestion = TradingQuant._suggestion_from_signal(signal)
            assert isinstance(suggestion, str)
            assert len(suggestion) > 0


class TestStockAnalysis:
    """stock_analysis 个股分析"""

    def test_returns_full_structure(self, quant):
        """应返回完整结构(scores/composite/signal/suggestion)"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120)
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            result = quant.stock_analysis("600519")
        assert result["stock_code"] == "600519"
        assert "scores" in result
        assert "composite" in result
        assert "signal" in result
        assert "suggestion" in result
        assert set(result["scores"].keys()) == {"technical", "capital", "fundamental", "news", "sentiment"}
        assert 0.0 <= result["composite"] <= 100.0

    def test_insufficient_kline_data_technical_score_50(self, quant):
        """K线 < 30 行 → technical 默认 50"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(20)
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            result = quant.stock_analysis("600519")
        assert result["scores"]["technical"] == 50.0


class TestCapitalFlow:
    """capital_flow 资金流"""

    def test_empty_akshare_returns_defaults(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_individual_fund_flow_rank.return_value = pd.DataFrame()
            result = quant.capital_flow("600519")
        assert result["stock_code"] == "600519"
        assert result["main_net_inflow"] == 0.0
        assert result["main_net_pct"] == 0.0

    def test_exception_returns_defaults(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.side_effect = Exception("网络错误")
            mock_ak.stock_individual_fund_flow_rank.side_effect = Exception("网络错误")
            result = quant.capital_flow("600519")
        assert result["main_net_inflow"] == 0.0


class TestMarketAnomaly:
    """market_anomaly 市场异动"""

    def test_empty_spot_returns_empty(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            anomalies = quant.market_anomaly()
        assert anomalies == []

    def test_exception_returns_empty(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = Exception("网络错误")
            anomalies = quant.market_anomaly()
        assert anomalies == []

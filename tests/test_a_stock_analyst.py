"""A股分析师策略单元测试

验证 src/strategies/a_stock_analyst.py 的核心逻辑:
- FundamentalData / TechnicalData / IndustryData 数据类
- _get_kline 缓存机制(mock akshare)
- _analyze_fundamental 异常降级
- _analyze_technical 趋势判定(mock K线)
- comprehensive_analysis 综合评分
- compare_stocks 排序
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategies import a_stock_analyst as mod
from src.strategies.a_stock_analyst import (
    AStockAnalyst,
    FundamentalData,
    IndustryData,
    TechnicalData,
)


def _make_kline_df(rows: int = 120, trend: str = "bull") -> pd.DataFrame:
    """构造日K DataFrame(中文列名)"""
    dates = pd.date_range("2026-01-01", periods=rows)
    if trend == "bull":
        closes = 100.0 + np.arange(rows) * 0.8
    elif trend == "bear":
        closes = 200.0 - np.arange(rows) * 0.8
    else:
        closes = 100.0 + np.sin(np.arange(rows) * 0.1) * 5
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": closes - 0.3,
        "最高": closes + 0.8,
        "最低": closes - 0.8,
        "收盘": closes,
        "成交量": np.full(rows, 1_000_000.0),
        "成交额": closes * 1_000_000,
    })


@pytest.fixture
def analyst() -> AStockAnalyst:
    return AStockAnalyst()


class TestDataclasses:
    """数据类默认值"""

    def test_fundamental_data_defaults(self):
        d = FundamentalData()
        assert d.pe == 0.0
        assert d.roe == 0.0

    def test_technical_data_defaults(self):
        d = TechnicalData()
        assert d.trend == "neutral"
        assert d.rsi == 50.0
        assert d.macd_signal == "neutral"

    def test_industry_data_defaults(self):
        d = IndustryData()
        assert d.industry == ""
        assert d.policy_bias == "neutral"
        assert d.competition_level == "medium"

    def test_all_are_dataclasses(self):
        assert is_dataclass(FundamentalData)
        assert is_dataclass(TechnicalData)
        assert is_dataclass(IndustryData)


class TestGetKline:
    """_get_kline 缓存"""

    def test_cache_hit_avoids_akshare(self, analyst):
        """二次调用应使用缓存,不再次访问 akshare"""
        df = _make_kline_df(60)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            first = analyst._get_kline("600519")
            second = analyst._get_kline("600519")
        assert mock_ak.stock_zh_a_hist.call_count == 1
        assert len(first) == 60
        assert second is first  # 缓存同一对象

    def test_akshare_exception_returns_empty(self, analyst):
        """akshare 异常 → 返回空 DataFrame"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.side_effect = Exception("网络错误")
            df = analyst._get_kline("600519")
        assert df.empty


class TestAnalyzeTechnical:
    """_analyze_technical 趋势判定"""

    def test_bullish_trend(self, analyst):
        """多头排列 → trend=bullish"""
        df = _make_kline_df(120, trend="bull")
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            data = analyst._analyze_technical("600519")
        assert data.trend == "bullish"
        assert data.ma5 > 0

    def test_bearish_trend(self, analyst):
        """空头排列 → trend=bearish"""
        df = _make_kline_df(120, trend="bear")
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            data = analyst._analyze_technical("600519")
        assert data.trend == "bearish"

    def test_insufficient_data_returns_default(self, analyst):
        """数据 < 30 行 → 返回默认 TechnicalData"""
        df = _make_kline_df(20)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            data = analyst._analyze_technical("600519")
        assert data.trend == "neutral"
        assert data.ma5 == 0.0


class TestComprehensiveAnalysis:
    """comprehensive_analysis 综合分析"""

    def test_returns_full_structure(self, analyst):
        """应返回完整结构(含三维度评分 + composite_score)"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120, "bull")
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
            mock_ak.stock_board_industry_name_em.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            result = analyst.comprehensive_analysis("600519")
        assert result["stock_code"] == "600519"
        assert "fundamental" in result
        assert "technical" in result
        assert "industry" in result
        assert "composite_score" in result
        assert 0.0 <= result["composite_score"] <= 100.0


class TestCompareStocks:
    """compare_stocks 排序"""

    def test_single_stock_no_ranking(self, analyst):
        """单只股票 → ranking 为空列表"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120, "bull")
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
            mock_ak.stock_board_industry_name_em.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            result = analyst.compare_stocks(["600519"])
        assert "600519" in result["stocks"]
        assert result["ranking"] == []

    def test_two_stocks_sorted_by_score(self, analyst):
        """两只股票 → 按 composite_score 降序"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120, "bull")
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
            mock_ak.stock_board_industry_name_em.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            result = analyst.compare_stocks(["600519", "000001"])
        assert len(result["ranking"]) == 2
        assert result["ranking"][0]["composite_score"] >= result["ranking"][1]["composite_score"]

"""基本面分析模块单元测试

验证 src/analysis/fundamental.py 的核心逻辑:
- analyze_fundamental 数据抓取(mock akshare)
- _score_valuation 估值评分(PE/PB)
- _score_profitability 盈利能力评分(ROE)
- _score_growth 成长性评分(营收/利润增速)
- score_fundamental 综合评分
- 异常降级场景(akshare 失败/空数据)
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from src.analysis import fundamental
from src.analysis.fundamental import (
    _score_growth,
    _score_profitability,
    _score_valuation,
    analyze_fundamental,
    score_fundamental,
)


def _make_indicator_df(pe: float = 28.5, pb: float = 9.2) -> pd.DataFrame:
    """构造估值指标 DataFrame(模拟 stock_a_indicator_lg 返回)"""
    return pd.DataFrame([{
        "trade_date": "2026-07-26",
        "pe": pe,
        "pe_ttm": pe,
        "pb": pb,
        "ps": 12.5,
        "dv_ratio": 1.2,
    }])


def _make_financial_df(
    roe: float = 31.2,
    revenue_yoy: float = 15.8,
    profit_yoy: float = 18.2,
) -> pd.DataFrame:
    """构造财务指标 DataFrame(模拟 stock_financial_analysis_indicator 返回)"""
    return pd.DataFrame([{
        "日期": "2026-06-30",
        "净资产收益率": roe,
        "加权净资产收益率": roe * 0.95,
        "营业收入同比增长率": revenue_yoy,
        "净利润同比增长率": profit_yoy,
        "毛利率": 91.5,
        "净利率": 52.3,
    }])


class TestScoreValuation:
    """_score_valuation 估值评分"""

    def test_low_pe_low_pb_top_score(self):
        """低 PE(<15) + 低 PB(<1) → 40 分"""
        score = _score_valuation(pe=10.0, pb=0.8)
        assert score == 40.0

    def test_moderate_pe_pb_score(self):
        """PE 15-25 + PB 1-2 → 20+7+7=34"""
        score = _score_valuation(pe=20.0, pb=1.5)
        assert score == pytest.approx(34.0)

    def test_high_pe_high_pb_minimal_score(self):
        """高 PE(>=60) + 高 PB(>=5) → 20+0+0=20"""
        score = _score_valuation(pe=80.0, pb=8.0)
        assert score == 20.0

    def test_negative_pe_pb_bonus(self):
        """亏损 PE/PB < 0 → 各加 2 分"""
        score = _score_valuation(pe=-5.0, pb=-0.5)
        # 20 + 2(负PE) + 2(负PB) = 24
        assert score == pytest.approx(24.0)

    def test_zero_pe_ignored(self):
        """PE=0 不进入评分分支(既不 >0 也不 <0)"""
        score = _score_valuation(pe=0.0, pb=1.5)
        # 20 + 0(PE=0不加分) + 7(PB<2) = 27
        assert score == pytest.approx(27.0)

    def test_score_capped_at_40(self):
        """评分上限 40"""
        score = _score_valuation(pe=5.0, pb=0.5)
        assert score <= 40.0


class TestScoreProfitability:
    """_score_profitability 盈利能力评分"""

    def test_high_roe_top_score(self):
        """ROE > 20 → 30"""
        assert _score_profitability(25.0) == 30.0

    def test_moderate_roe(self):
        """ROE 15-20 → 24"""
        assert _score_profitability(18.0) == 24.0

    def test_medium_roe(self):
        """ROE 10-15 → 18"""
        assert _score_profitability(12.0) == 18.0

    def test_low_roe(self):
        """ROE 5-10 → 12"""
        assert _score_profitability(8.0) == 12.0

    def test_tiny_roe(self):
        """ROE 0-5 → 6"""
        assert _score_profitability(3.0) == 6.0

    def test_negative_roe_bottom_score(self):
        """ROE <= 0 → 2"""
        assert _score_profitability(-5.0) == 2.0


class TestScoreGrowth:
    """_score_growth 成长性评分"""

    def test_high_growth_top_score(self):
        """营收+利润 > 30 → 15+15=30"""
        score = _score_growth(40.0, 35.0)
        assert score == 30.0

    def test_negative_growth_bottom_score(self):
        """营收+利润负增长 → 2+2=4"""
        score = _score_growth(-10.0, -5.0)
        assert score == 4.0

    def test_mixed_growth(self):
        """营收增长(>15 → 12) + 利润负增长(2) = 14"""
        score = _score_growth(20.0, -3.0)
        assert score == pytest.approx(14.0)

    def test_zero_growth(self):
        """营收/利润 = 0 → 2+2=4"""
        score = _score_growth(0.0, 0.0)
        assert score == 4.0

    def test_score_capped_at_30(self):
        """评分上限 30"""
        score = _score_growth(100.0, 100.0)
        assert score <= 30.0


class TestAnalyzeFundamental:
    """analyze_fundamental 数据抓取"""

    def test_normal_fetch_returns_values(self):
        """正常抓取应返回 PE/PB/ROE/营收增长/利润增长"""
        indicator_df = _make_indicator_df(pe=28.5, pb=9.2)
        fin_df = _make_financial_df(roe=31.2, revenue_yoy=15.8, profit_yoy=18.2)
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=indicator_df):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=fin_df):
                result = analyze_fundamental("600519")
        assert result["PE"] == pytest.approx(28.5)
        assert result["PB"] == pytest.approx(9.2)
        assert result["ROE"] == pytest.approx(31.2)
        assert result["revenue_growth"] == pytest.approx(15.8)
        assert result["profit_growth"] == pytest.approx(18.2)

    def test_pe_ttm_preferred_over_pe(self):
        """列同时含 pe 和 pe_ttm 时,优先取 pe(实现:pe 在前)"""
        indicator_df = _make_indicator_df(pe=20.0, pb=3.0)
        indicator_df["pe_ttm"] = 25.0
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=indicator_df):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=pd.DataFrame()):
                result = analyze_fundamental("600519")
        # 实现按 ["pe", "pe_ttm", "市盈率"] 顺序,pe 命中即停
        assert result["PE"] == pytest.approx(20.0)

    def test_indicator_fetch_exception_returns_default(self):
        """估值抓取异常 → PE/PB 保持默认 0,不抛出"""
        with patch.object(fundamental.ak, "stock_a_indicator_lg", side_effect=Exception("net error")):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=pd.DataFrame()):
                result = analyze_fundamental("600519")
        assert result["PE"] == 0.0
        assert result["PB"] == 0.0

    def test_financial_fetch_exception_returns_default(self):
        """财务抓取异常 → ROE/营收增长/利润增长保持默认 0"""
        indicator_df = _make_indicator_df(pe=28.5, pb=9.2)
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=indicator_df):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", side_effect=Exception("net error")):
                result = analyze_fundamental("600519")
        assert result["PE"] == pytest.approx(28.5)
        assert result["ROE"] == 0.0
        assert result["revenue_growth"] == 0.0

    def test_empty_dataframes_return_default(self):
        """空 DataFrame → 全部字段默认 0"""
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=pd.DataFrame()):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=pd.DataFrame()):
                result = analyze_fundamental("600519")
        assert result == {
            "PE": 0.0, "PB": 0.0, "ROE": 0.0,
            "revenue_growth": 0.0, "profit_growth": 0.0,
        }

    def test_nan_value_skipped(self):
        """值为 NaN 时该字段保持默认 0"""
        indicator_df = _make_indicator_df(pe=float("nan"), pb=9.2)
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=indicator_df):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=pd.DataFrame()):
                result = analyze_fundamental("600519")
        assert result["PE"] == 0.0
        assert result["PB"] == pytest.approx(9.2)


class TestScoreFundamental:
    """score_fundamental 综合评分"""

    def test_score_returns_float_in_range(self):
        """综合评分应在 [0, 100] 范围内"""
        indicator_df = _make_indicator_df(pe=28.5, pb=9.2)
        fin_df = _make_financial_df(roe=31.2, revenue_yoy=15.8, profit_yoy=18.2)
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=indicator_df):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=fin_df):
                score = score_fundamental("600519")
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_score_all_zero_data(self):
        """全部数据为 0 → 估值(20+0+0=20) + 盈利(2) + 成长(2+2=4) = 26"""
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=pd.DataFrame()):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=pd.DataFrame()):
                score = score_fundamental("600519")
        assert score == pytest.approx(26.0)

    def test_score_high_quality_stock(self):
        """高质量股票(低估值+高ROE+高增长) → 高分"""
        indicator_df = _make_indicator_df(pe=10.0, pb=0.8)
        fin_df = _make_financial_df(roe=25.0, revenue_yoy=40.0, profit_yoy=35.0)
        with patch.object(fundamental.ak, "stock_a_indicator_lg", return_value=indicator_df):
            with patch.object(fundamental.ak, "stock_financial_analysis_indicator", return_value=fin_df):
                score = score_fundamental("600519")
        # 估值(40) + 盈利(30) + 成长(30) = 100
        assert score >= 90

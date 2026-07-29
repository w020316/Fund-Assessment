"""资金流向分析模块单元测试

验证 src/analysis/capital_flow.py 的核心逻辑:
- analyze_capital_flow 数据抓取(mock akshare)
- _score_main_inflow 主力净流入评分
- _score_northbound 北向资金评分
- _score_large_order 大单占比评分
- score_capital 综合评分
- 异常降级场景(akshare 失败/空数据)
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from src.analysis import capital_flow
from src.analysis.capital_flow import (
    _score_large_order,
    _score_main_inflow,
    _score_northbound,
    analyze_capital_flow,
    score_capital,
)


def _make_flow_df(
    main_net: float = 1e8,
    large_pct: float = 5.0,
    medium_pct: float = -2.0,
    small_pct: float = -3.0,
) -> pd.DataFrame:
    """构造单行资金流向 DataFrame(模拟 akshare 返回)"""
    return pd.DataFrame([{
        "日期": "2026-07-28",
        "收盘价": 1688.0,
        "涨跌幅": 1.11,
        "主力净流入-净额": main_net,
        "主力净流入-净占比": 5.26,
        "超大单净流入-净额": main_net * 0.6,
        "超大单净流入-净占比": large_pct,
        "大单净流入-净额": main_net * 0.4,
        "大单净流入-净占比": large_pct * 0.5,
        "中单净流入-净额": -1.5e7,
        "中单净流入-净占比": medium_pct,
        "小单净流入-净额": -3.5e7,
        "小单净流入-净占比": small_pct,
    }])


def _make_north_df(hold_change: float = 1e6) -> pd.DataFrame:
    """构造北向资金持股 DataFrame"""
    return pd.DataFrame([{
        "日期": "2026-07-28",
        "持股数量": hold_change,
        "持股市值": 1e9,
    }])


class TestScoreMainInflow:
    """_score_main_inflow 主力净流入评分"""

    def test_huge_inflow_top_score(self):
        """净流入 > 5亿 → 40 分"""
        assert _score_main_inflow(6e8) == 40.0

    def test_medium_inflow_32_score(self):
        """净流入 > 1亿 → 32 分"""
        assert _score_main_inflow(2e8) == 32.0

    def test_small_inflow_24_score(self):
        """净流入 > 5千万 → 24 分"""
        assert _score_main_inflow(6e7) == 24.0

    def test_tiny_inflow_16_score(self):
        """净流入 > 0 → 16 分"""
        assert _score_main_inflow(1e6) == 16.0

    def test_huge_outflow_bottom_score(self):
        """净流出 < -5亿 → 4 分"""
        assert _score_main_inflow(-6e8) == 4.0

    def test_medium_outflow_10_score(self):
        """净流出 < -1亿 → 10 分"""
        assert _score_main_inflow(-2e8) == 10.0

    def test_zero_inflow_18_score(self):
        """净流入 = 0 → 18 分(else 分支)"""
        assert _score_main_inflow(0.0) == 18.0


class TestScoreNorthbound:
    """_score_northbound 北向资金评分"""

    def test_huge_inflow_top_score(self):
        """持股增加 > 100万 → 30 分"""
        assert _score_northbound(2e6) == 30.0

    def test_medium_inflow_24_score(self):
        """持股增加 > 10万 → 24 分"""
        assert _score_northbound(5e5) == 24.0

    def test_small_inflow_18_score(self):
        """持股增加 > 0 → 18 分"""
        assert _score_northbound(1000) == 18.0

    def test_huge_outflow_bottom_score(self):
        """持股减少 < -100万 → 4 分"""
        assert _score_northbound(-2e6) == 4.0

    def test_zero_change_15_score(self):
        """无变化 → 15 分(中性)"""
        assert _score_northbound(0.0) == 15.0


class TestScoreLargeOrder:
    """_score_large_order 大单占比评分"""

    def test_high_positive_top_score(self):
        """占比 > 5 → 30 分"""
        assert _score_large_order(6.0) == 30.0

    def test_medium_positive_24_score(self):
        """占比 > 2 → 24 分"""
        assert _score_large_order(3.0) == 24.0

    def test_small_positive_18_score(self):
        """占比 > 0 → 18 分"""
        assert _score_large_order(1.0) == 18.0

    def test_small_negative_12_score(self):
        """占比 > -2 → 12 分"""
        assert _score_large_order(-1.0) == 12.0

    def test_huge_negative_bottom_score(self):
        """占比 < -5 → 4 分"""
        assert _score_large_order(-6.0) == 4.0


class TestAnalyzeCapitalFlow:
    """analyze_capital_flow 数据抓取"""

    def test_normal_fetch_returns_values(self):
        """正常抓取应返回主力/超大单/中单/小单/北向数据"""
        flow_df = _make_flow_df(main_net=1e8, large_pct=5.0)
        north_df = _make_north_df(hold_change=1e6)
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=north_df):
                result = analyze_capital_flow("600519")
        assert result["main_net_inflow"] == pytest.approx(1e8)
        assert result["large_order_ratio"] == pytest.approx(5.0)
        assert result["northbound_change"] == pytest.approx(1e6)
        assert "medium_order_ratio" in result
        assert "small_order_ratio" in result

    def test_sh_market_for_6_prefix(self):
        """6 开头股票应使用 sh 市场"""
        flow_df = _make_flow_df()
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df) as mock_flow:
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                analyze_capital_flow("600519")
        _, kwargs = mock_flow.call_args
        assert kwargs.get("market") == "sh"

    def test_sz_market_for_0_prefix(self):
        """0 开头股票应使用 sz 市场"""
        flow_df = _make_flow_df()
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df) as mock_flow:
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                analyze_capital_flow("000001")
        _, kwargs = mock_flow.call_args
        assert kwargs.get("market") == "sz"

    def test_flow_fetch_exception_returns_default(self):
        """资金流向抓取异常 → 返回默认 0 值,不抛出"""
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", side_effect=Exception("net error")):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                result = analyze_capital_flow("600519")
        assert result["main_net_inflow"] == 0.0
        assert result["large_order_ratio"] == 0.0
        assert result["northbound_change"] == 0.0

    def test_north_fetch_exception_returns_zero(self):
        """北向资金抓取异常 → northbound_change 保持 0"""
        flow_df = _make_flow_df()
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", side_effect=Exception("net error")):
                result = analyze_capital_flow("600519")
        assert result["main_net_inflow"] == pytest.approx(1e8)
        assert result["northbound_change"] == 0.0

    def test_empty_flow_df_returns_default(self):
        """空 DataFrame → 各字段保持默认 0"""
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=pd.DataFrame()):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                result = analyze_capital_flow("600519")
        assert result["main_net_inflow"] == 0.0
        assert result["northbound_change"] == 0.0

    def test_alternative_column_names_supported(self):
        """备选列名(主力净流入/超大单净占比/中单净占比/小单净占比)应被识别"""
        flow_df = pd.DataFrame([{
            "日期": "2026-07-28",
            "主力净流入": 8e7,  # 备选列名
            "超大单净占比": 4.0,
            "中单净占比": -1.5,
            "小单净占比": -2.5,
        }])
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                result = analyze_capital_flow("600519")
        assert result["main_net_inflow"] == pytest.approx(8e7)
        assert result["large_order_ratio"] == pytest.approx(4.0)
        assert result["medium_order_ratio"] == pytest.approx(-1.5)
        assert result["small_order_ratio"] == pytest.approx(-2.5)

    def test_nan_value_skipped(self):
        """值为 NaN 时该字段应保持默认 0"""
        flow_df = _make_flow_df()
        flow_df.loc[0, "主力净流入-净额"] = float("nan")
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                result = analyze_capital_flow("600519")
        # NaN 不被采用,保持默认 0
        assert result["main_net_inflow"] == 0.0


class TestScoreCapital:
    """score_capital 综合评分"""

    def test_score_returns_float_in_range(self):
        """综合评分应在 [0, 100] 范围内"""
        flow_df = _make_flow_df(main_net=1e8, large_pct=5.0)
        north_df = _make_north_df(hold_change=1e6)
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=north_df):
                score = score_capital("600519")
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_score_all_zero_data(self):
        """全部数据为 0 → 综合评分 = main(18) + north(15) + large(12) = 45"""
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=pd.DataFrame()):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=pd.DataFrame()):
                score = score_capital("600519")
        # main_net=0 → 18; northbound=0 → 15; large=0 → 12
        assert score == pytest.approx(45.0)

    def test_score_strong_bullish_high(self):
        """强多头数据 → 评分高(>=80)"""
        flow_df = _make_flow_df(main_net=6e8, large_pct=8.0)
        north_df = _make_north_df(hold_change=2e6)
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=north_df):
                score = score_capital("600519")
        # 40 + 30 + 30 = 100
        assert score >= 80

    def test_score_strong_bearish_low(self):
        """强空头数据 → 评分低(<30)"""
        flow_df = _make_flow_df(main_net=-6e8, large_pct=-6.0)
        north_df = _make_north_df(hold_change=-2e6)
        with patch.object(capital_flow.ak, "stock_individual_fund_flow", return_value=flow_df):
            with patch.object(capital_flow.ak, "stock_hsgt_individual_em", return_value=north_df):
                score = score_capital("600519")
        # 4 + 4 + 4 = 12
        assert score < 30

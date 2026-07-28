"""基金建议五信号融合引擎测试(P1-4)

验证 src/analysis/fund_advisor_v2.py 的核心逻辑:
- 技术面信号(_calc_technical_signal): MA5/MA20 趋势
- 基本面信号(_calc_fundamental_signal): PE/PB + 净值分位数
- 消息面信号(_calc_news_signal): 情绪指数
- 重仓股信号(_calc_holdings_signal): 净值影响+板块轮动
- 大盘信号(_calc_market_signal): 温度计
- 五信号融合(_merge_five_signals)
- 端到端 analyze_fund_five_signals(mock)
- 端到端 generate_fund_advice_v2(mock)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.analysis import fund_advisor_v2
from src.analysis.fund_advisor_v2 import (
    _calc_fundamental_signal,
    _calc_holdings_signal,
    _calc_market_signal,
    _calc_news_signal,
    _calc_technical_signal,
    _merge_five_signals,
    _signal_to_action,
    analyze_fund_five_signals,
    generate_fund_advice_v2,
)


class TestTechnicalSignal:
    """技术面信号"""

    def test_empty_data_neutral(self):
        result = _calc_technical_signal([])
        assert result["score"] == 50
        assert result["signal"] == "neutral"

    def test_insufficient_data_neutral(self):
        """净值数据不足5条"""
        nav_history = [{"nav": 1.0}, {"nav": 1.1}]
        result = _calc_technical_signal(nav_history)
        assert result["score"] == 50

    def test_bullish_trend(self):
        """多头排列(净值上涨)"""
        nav_history = [{"nav": 1.0 + i * 0.01} for i in range(25)]
        result = _calc_technical_signal(nav_history)
        assert result["score"] >= 70
        assert result["signal"] == "bullish"
        assert result["trend"] == "多头排列"
        assert result["ma5"] > result["ma20"]

    def test_bearish_trend(self):
        """空头排列(净值下跌)"""
        nav_history = [{"nav": 2.0 - i * 0.01} for i in range(25)]
        result = _calc_technical_signal(nav_history)
        assert result["score"] <= 30
        assert result["signal"] == "bearish"
        assert result["trend"] == "空头排列"
        assert result["ma5"] < result["ma20"]


class TestFundamentalSignal:
    """基本面信号"""

    def test_empty_data_neutral(self):
        result = _calc_fundamental_signal([], [], [])
        assert result["score"] == 50

    def test_low_pe_high_score(self):
        """低PE → 高分"""
        holdings = [{"code": "A", "weight": 10}]
        quotes = [{"code": "A", "pe_ttm": 12, "pb": 1.5}]
        nav_history = [{"nav": 1.0}] * 10
        result = _calc_fundamental_signal(holdings, quotes, nav_history)
        assert result["avg_pe"] == pytest.approx(12, abs=0.1)
        assert result["pe_label"] == "低估"

    def test_high_pe_low_score(self):
        """高PE → 低分"""
        holdings = [{"code": "A", "weight": 10}]
        quotes = [{"code": "A", "pe_ttm": 80, "pb": 8.0}]
        nav_history = [{"nav": 1.0}] * 10
        result = _calc_fundamental_signal(holdings, quotes, nav_history)
        assert result["pe_label"] == "高估"

    def test_nav_low_percentile(self):
        """净值低位 → 高分(逢低布局)"""
        holdings = [{"code": "A", "weight": 10}]
        quotes = [{"code": "A", "pe_ttm": 20}]
        # 当前净值在历史最低位
        nav_history = [{"nav": 2.0}, {"nav": 1.8}, {"nav": 1.5}, {"nav": 1.2}, {"nav": 1.0}] + \
                      [{"nav": 0.8}] * 5  # 当前0.8最低
        result = _calc_fundamental_signal(holdings, quotes, nav_history)
        assert result["nav_label"] == "低位"
        assert result["nav_percentile"] < 20


class TestNewsSignal:
    """消息面信号"""

    def test_empty_data_neutral(self):
        result = _calc_news_signal({})
        assert result["score"] == 50

    def test_no_news_neutral(self):
        result = _calc_news_signal({"total_count": 0})
        assert result["score"] == 50

    def test_bullish_news(self):
        result = _calc_news_signal({"sentiment_index": 75, "total_count": 20})
        assert result["score"] == 75
        assert result["signal"] == "bullish"
        assert result["label"] == "强烈利好"

    def test_bearish_news(self):
        result = _calc_news_signal({"sentiment_index": 25, "total_count": 20})
        assert result["score"] == 25
        assert result["signal"] == "bearish"
        assert result["label"] == "强烈利空"


class TestHoldingsSignal:
    """重仓股板块信号"""

    def test_empty_data_neutral(self):
        result = _calc_holdings_signal({})
        assert result["score"] == 50

    def test_no_holdings_neutral(self):
        result = _calc_holdings_signal({"holdings": []})
        assert result["score"] == 50

    def test_positive_nav_impact(self):
        """净值正影响"""
        holdings_data = {
            "holdings": [{"code": "A", "weight": 10}],
            "nav_impact": {"estimated_change_pct": 0.5},
            "sector_rotation": [{"sector": "白酒", "weight": 10, "signal": "强势"}],
        }
        result = _calc_holdings_signal(holdings_data)
        assert result["score"] > 50
        assert result["nav_impact_pct"] == 0.5

    def test_negative_nav_impact(self):
        """净值负影响"""
        holdings_data = {
            "holdings": [{"code": "A", "weight": 10}],
            "nav_impact": {"estimated_change_pct": -0.5},
            "sector_rotation": [{"sector": "房地产", "weight": 10, "signal": "弱势"}],
        }
        result = _calc_holdings_signal(holdings_data)
        assert result["score"] < 50


class TestMarketSignal:
    """大盘环境信号"""

    def test_empty_data_neutral(self):
        result = _calc_market_signal({})
        assert result["score"] == 50

    def test_extreme_hot_bearish_signal(self):
        """极热(>=80) → 反向信号(风险)"""
        thermometer = {"score": 85, "level": "极热", "action": "注意风险"}
        result = _calc_market_signal(thermometer)
        assert result["score"] == 85
        assert result["signal"] == "bearish"

    def test_extreme_cold_bullish_signal(self):
        """极冷(<20) → 底部机会(正面信号)"""
        thermometer = {"score": 15, "level": "极冷", "action": "可考虑定投"}
        result = _calc_market_signal(thermometer)
        assert result["score"] == 15
        assert result["signal"] == "bullish"

    def test_neutral_middle(self):
        """中性温度"""
        thermometer = {"score": 50, "level": "中性"}
        result = _calc_market_signal(thermometer)
        assert result["signal"] == "neutral"


class TestMergeFiveSignals:
    """五信号融合"""

    def test_all_bullish(self):
        """全部看多 → 高分"""
        signals = [
            {"score": 80, "signal": "bullish", "weight": 0.20, "reason": "技术看多"},
            {"score": 75, "signal": "bullish", "weight": 0.20, "reason": "基本面好"},
            {"score": 85, "signal": "bullish", "weight": 0.25, "reason": "消息利好"},
            {"score": 70, "signal": "bullish", "weight": 0.20, "reason": "重仓股强"},
            {"score": 65, "signal": "neutral_bullish", "weight": 0.15, "reason": "大盘偏热"},
        ]
        result = _merge_five_signals(*signals)
        assert result["final_score"] >= 70
        assert result["direction"] == "bullish"

    def test_all_bearish(self):
        """全部看空 → 低分"""
        signals = [
            {"score": 25, "signal": "bearish", "weight": 0.20, "reason": "技术看空"},
            {"score": 20, "signal": "bearish", "weight": 0.20, "reason": "基本面差"},
            {"score": 15, "signal": "bearish", "weight": 0.25, "reason": "消息利空"},
            {"score": 30, "signal": "bearish", "weight": 0.20, "reason": "重仓股弱"},
            {"score": 35, "signal": "neutral_bearish", "weight": 0.15, "reason": "大盘偏冷"},
        ]
        result = _merge_five_signals(*signals)
        assert result["final_score"] <= 30
        assert result["direction"] == "bearish"

    def test_mixed_signals_neutral(self):
        """多空混合 → 中性"""
        signals = [
            {"score": 70, "signal": "bullish", "weight": 0.20, "reason": "技术看多"},
            {"score": 30, "signal": "bearish", "weight": 0.20, "reason": "基本面差"},
            {"score": 50, "signal": "neutral", "weight": 0.25, "reason": "消息中性"},
            {"score": 50, "signal": "neutral", "weight": 0.20, "reason": "重仓股平"},
            {"score": 50, "signal": "neutral", "weight": 0.15, "reason": "大盘中性"},
        ]
        result = _merge_five_signals(*signals)
        assert 40 <= result["final_score"] <= 60
        assert result["direction"] == "neutral"


class TestSignalToAction:
    """信号→操作映射"""

    def test_bullish_high_score_add(self):
        sig, action = _signal_to_action("bullish", 75)
        assert sig == "add"
        assert action == "加仓"

    def test_bearish_high_score_take_profit(self):
        sig, action = _signal_to_action("bearish", 80)
        assert sig == "take_profit"
        assert action == "止盈"

    def test_bearish_low_score_dca(self):
        sig, action = _signal_to_action("bearish", 20)
        assert sig == "dca"
        assert action == "定投"

    def test_neutral_hold(self):
        sig, action = _signal_to_action("neutral", 50)
        assert sig == "hold"
        assert action == "持有"


class TestAnalyzeFundFiveSignals:
    """端到端测试(mock)"""

    @pytest.mark.asyncio
    async def test_analyze_with_mock_data(self):
        """完整五信号分析(mock数据)"""
        mock_nav_history = [{"nav": 1.0 + i * 0.01, "date": f"2026-07-{i+1:02d}"} for i in range(25)]
        mock_quotes = [{"code": "110022", "nav": 1.25, "name": "测试基金"}]
        mock_holdings = {
            "holdings": [{"code": "600519", "name": "贵州茅台", "weight": 9.85}],
            "nav_impact": {"estimated_change_pct": 0.3},
            "sector_rotation": [{"sector": "白酒", "weight": 9.85, "signal": "强势"}],
        }
        mock_news = {"sentiment_index": 70, "total_count": 15}
        mock_thermometer = {"score": 65, "level": "偏热", "action": "谨慎乐观"}
        mock_stock_quotes = [{"code": "600519", "pe_ttm": 25, "pb": 8.0}]

        with patch.object(fund_advisor_v2.ds2, "get_fund_history_tencent", return_value=mock_nav_history):
            with patch.object(fund_advisor_v2.ds2, "get_fund_realtime_tencent", return_value=mock_quotes):
                with patch.object(fund_advisor_v2.ds2, "get_realtime_quote_tencent", return_value=mock_stock_quotes):
                    with patch("src.analysis.fund_holdings.analyze_fund_holdings", return_value=mock_holdings):
                        with patch("src.analysis.news_aggregator.get_news_feed", return_value=mock_news):
                            with patch("src.analysis.market_assessment.get_market_thermometer", return_value=mock_thermometer):
                                result = await analyze_fund_five_signals(
                                    fund_code="110022",
                                    fund_name="测试基金",
                                    cost_nav=1.0,
                                    shares=1000,
                                )

        assert result["fund_code"] == "110022"
        assert result["current_nav"] == 1.25
        assert result["cost_nav"] == 1.0
        assert result["pnl_pct"] == 25.0  # 25% 浮盈
        assert "five_signals" in result
        assert "advice" in result
        # 浮盈25%触发止盈
        assert result["advice"]["signal"] == "take_profit"
        assert result["advice"]["action"] == "止盈"

    @pytest.mark.asyncio
    async def test_analyze_no_data_returns_neutral(self):
        """所有数据源都失败 → 返回中性"""
        with patch.object(fund_advisor_v2.ds2, "get_fund_history_tencent", return_value=[]):
            with patch.object(fund_advisor_v2.ds2, "get_fund_realtime_tencent", return_value=[]):
                with patch.object(fund_advisor_v2.ds2, "get_realtime_quote_tencent", return_value=[]):
                    with patch("src.analysis.fund_holdings.analyze_fund_holdings", return_value={}):
                        with patch("src.analysis.news_aggregator.get_news_feed", return_value={}):
                            with patch("src.analysis.market_assessment.get_market_thermometer", return_value={}):
                                result = await analyze_fund_five_signals(
                                    fund_code="999999",
                                    fund_name="未知基金",
                                )

        assert result["current_nav"] == 0
        assert result["five_signals"]["final_score"] == 50
        assert result["advice"]["signal"] in ("hold", "neutral")


class TestGenerateFundAdviceV2:
    """多持仓端到端"""

    @pytest.mark.asyncio
    async def test_empty_positions(self):
        result = await generate_fund_advice_v2([])
        assert result["positions_advice"] == []
        assert "暂无基金持仓" in result["summary"]

    @pytest.mark.asyncio
    async def test_multi_positions(self):
        """多持仓并行分析"""
        positions = [
            {"fund_code": "110022", "fund_name": "基金A", "cost_nav": 1.0, "shares": 1000},
            {"fund_code": "161725", "fund_name": "基金B", "cost_nav": 2.0, "shares": 500},
        ]
        mock_result = {
            "fund_code": "mock",
            "fund_name": "mock",
            "current_nav": 1.0,
            "cost_nav": 1.0,
            "shares": 1000,
            "pnl_pct": 0,
            "pnl_amount": 0,
            "five_signals": {"final_score": 50, "direction": "neutral", "signal": "hold", "action": "持有", "reason": "mock"},
            "advice": {"signal": "hold", "action": "持有", "score": 50, "reason": "mock"},
        }
        with patch.object(fund_advisor_v2, "analyze_fund_five_signals", return_value=mock_result):
            result = await generate_fund_advice_v2(positions)

        assert len(result["positions_advice"]) == 2
        assert result["method"] == "five_signals_v2"
        assert "weights" in result
        assert "持有" in result["summary"]

    @pytest.mark.asyncio
    async def test_position_failure_isolated(self):
        """单只基金失败不影响其他"""
        positions = [
            {"fund_code": "110022", "fund_name": "正常基金"},
            {"fund_code": "999999", "fund_name": "失败基金"},
        ]

        async def mock_analyze(fund_code, **kwargs):
            if fund_code == "999999":
                raise ValueError("模拟失败")
            return {
                "fund_code": fund_code,
                "fund_name": "正常基金",
                "current_nav": 1.0,
                "cost_nav": 1.0,
                "shares": 1000,
                "pnl_pct": 0,
                "pnl_amount": 0,
                "five_signals": {"final_score": 50, "direction": "neutral", "signal": "hold", "action": "持有", "reason": "ok"},
                "advice": {"signal": "hold", "action": "持有", "score": 50, "reason": "ok"},
            }

        with patch.object(fund_advisor_v2, "analyze_fund_five_signals", side_effect=mock_analyze):
            result = await generate_fund_advice_v2(positions)

        assert len(result["positions_advice"]) == 2
        # 失败的应该有error字段
        failed = next(p for p in result["positions_advice"] if p.get("fund_code") == "999999")
        assert "error" in failed
        # 正常的应该有advice字段
        normal = next(p for p in result["positions_advice"] if p.get("fund_code") == "110022")
        assert "advice" in normal

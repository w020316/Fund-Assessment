"""大盘研判增强单元测试

验证 src/analysis/market_assessment.py 的核心逻辑:
- 指数趋势评分(_calc_index_trend_score)
- 板块轮动评分(_calc_sector_rotation_score)
- 资金流向评分(_calc_capital_flow_score)
- 市场情绪评分(_calc_sentiment_score)
- 温度计综合计算(_calc_thermometer)
- 端到端 get_market_thermometer(mock 数据)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.analysis import market_assessment
from src.analysis.market_assessment import (
    _calc_capital_flow_score,
    _calc_index_trend_score,
    _calc_sector_rotation_score,
    _calc_sentiment_score,
    _calc_thermometer,
    get_capital_flow_overview,
    get_market_assessment,
    get_market_thermometer,
)


class TestIndexTrendScore:
    """指数趋势评分"""

    def test_empty_data_returns_neutral(self):
        result = _calc_index_trend_score([])
        assert result["score"] == 50
        assert result["note"] == "指数数据不可用"

    def test_positive_change_high_score(self):
        """指数涨1% → 60分"""
        index_data = [{"name": "上证指数", "change_pct": 1.0}]
        result = _calc_index_trend_score(index_data)
        assert result["score"] == pytest.approx(60, abs=0.1)
        assert result["change_pct"] == 1.0

    def test_negative_change_low_score(self):
        """指数跌1% → 40分"""
        index_data = [{"name": "上证指数", "change_pct": -1.0}]
        result = _calc_index_trend_score(index_data)
        assert result["score"] == pytest.approx(40, abs=0.1)

    def test_big_rise_capped_at_100(self):
        """指数涨10% → 100分(上限)"""
        index_data = [{"name": "上证指数", "change_pct": 10.0}]
        result = _calc_index_trend_score(index_data)
        assert result["score"] == 100

    def test_big_drop_floored_at_0(self):
        """指数跌10% → 0分(下限)"""
        index_data = [{"name": "上证指数", "change_pct": -10.0}]
        result = _calc_index_trend_score(index_data)
        assert result["score"] == 0

    def test_multi_index_average(self):
        """多指数取平均"""
        index_data = [
            {"name": "上证指数", "change_pct": 1.0},
            {"name": "深证成指", "change_pct": 2.0},
            {"name": "创业板指", "change_pct": 3.0},
        ]
        result = _calc_index_trend_score(index_data)
        # 平均2% → 70分
        assert result["score"] == pytest.approx(70, abs=0.1)
        assert result["change_pct"] == pytest.approx(2.0, abs=0.01)


class TestSectorRotationScore:
    """板块轮动评分"""

    def test_empty_data_returns_neutral(self):
        result = _calc_sector_rotation_score([])
        assert result["score"] == 50

    def test_all_gainers_high_score(self):
        """全部板块上涨 → 100分"""
        sectors = [{"change_pct": 1.0} for _ in range(10)]
        result = _calc_sector_rotation_score(sectors)
        assert result["score"] == 100
        assert result["gain_count"] == 10
        assert result["fall_count"] == 0

    def test_all_losers_low_score(self):
        """全部板块下跌 → 0分"""
        sectors = [{"change_pct": -1.0} for _ in range(10)]
        result = _calc_sector_rotation_score(sectors)
        assert result["score"] == 0
        assert result["gain_count"] == 0
        assert result["fall_count"] == 10

    def test_mixed_half_half(self):
        """涨跌对半 → 50分"""
        sectors = [{"change_pct": 1.0}] * 5 + [{"change_pct": -1.0}] * 5
        result = _calc_sector_rotation_score(sectors)
        assert result["score"] == 50


class TestCapitalFlowScore:
    """资金流向评分"""

    def test_empty_data_neutral(self):
        result = _calc_capital_flow_score({}, [])
        assert result["score"] == 50

    def test_northbound_big_inflow(self):
        """北向大幅净流入100亿 → 100分"""
        # 100亿 = 100 * 1e8 = 1e10
        northbound = {"total_net_inflow": 100 * 1_0000_0000}
        result = _calc_capital_flow_score(northbound, [])
        # nb_score = 50 + 100 * 0.5 = 100, sector_score = 50, avg = 75
        assert result["score"] == pytest.approx(75, abs=0.1)
        assert result["northbound_net"] == 100.0  # 亿元

    def test_northbound_big_outflow(self):
        """北向大幅净流出100亿 → 25分"""
        northbound = {"total_net_inflow": -100 * 1_0000_0000}
        result = _calc_capital_flow_score(northbound, [])
        # nb_score = 50 - 50 = 0, sector_score = 50, avg = 25
        assert result["score"] == pytest.approx(25, abs=0.1)
        assert result["northbound_net"] == -100.0

    def test_sector_main_inflow(self):
        """板块主力净流入"""
        sectors = [{"main_net_inflow": 20 * 1_0000_0000}]  # 20亿
        result = _calc_capital_flow_score({}, sectors)
        # sector_score = 50 + 20 * 0.5 = 60, nb_score = 50, avg = 55
        assert result["score"] == pytest.approx(55, abs=0.1)
        assert result["sector_main_net"] == 20.0


class TestSentimentScore:
    """市场情绪评分"""

    def test_empty_data_neutral(self):
        result = _calc_sentiment_score({})
        assert result["score"] == 50

    def test_dash_value_neutral(self):
        result = _calc_sentiment_score({"sentiment": "--"})
        assert result["score"] == 50

    def test_bullish_sentiment(self):
        """偏多情绪 → 高分"""
        sentiment = {"rise_count": 3500, "fall_count": 1500, "sentiment": "偏多"}
        result = _calc_sentiment_score(sentiment)
        # ratio = 3500/5000 = 0.7, score = 70
        assert result["score"] == pytest.approx(70, abs=0.1)
        assert result["sentiment"] == "偏多"

    def test_bearish_sentiment(self):
        """偏空情绪 → 低分"""
        sentiment = {"rise_count": 1500, "fall_count": 3500, "sentiment": "偏空"}
        result = _calc_sentiment_score(sentiment)
        assert result["score"] == pytest.approx(30, abs=0.1)


class TestThermometer:
    """温度计综合计算"""

    def test_extreme_hot(self):
        """极热(>=80)"""
        index_score = {"score": 95, "weight": 0.30}
        sector_score = {"score": 90, "weight": 0.25}
        capital_score = {"score": 85, "weight": 0.25}
        sentiment_score = {"score": 80, "weight": 0.20}
        result = _calc_thermometer(index_score, sector_score, capital_score, sentiment_score)
        assert result["score"] >= 80
        assert result["level"] == "极热"
        assert "风险" in result["action"]

    def test_extreme_cold(self):
        """极冷(<20)"""
        index_score = {"score": 5, "weight": 0.30}
        sector_score = {"score": 10, "weight": 0.25}
        capital_score = {"score": 15, "weight": 0.25}
        sentiment_score = {"score": 20, "weight": 0.20}
        result = _calc_thermometer(index_score, sector_score, capital_score, sentiment_score)
        assert result["score"] < 20
        assert result["level"] == "极冷"
        assert "底部" in result["action"] or "定投" in result["action"]

    def test_neutral_middle(self):
        """中性(40-60)"""
        index_score = {"score": 50, "weight": 0.30}
        sector_score = {"score": 50, "weight": 0.25}
        capital_score = {"score": 50, "weight": 0.25}
        sentiment_score = {"score": 50, "weight": 0.20}
        result = _calc_thermometer(index_score, sector_score, capital_score, sentiment_score)
        assert 40 <= result["score"] < 60
        assert result["level"] == "中性"


class TestMarketThermometerEndToEnd:
    """端到端测试(mock 数据)"""

    @pytest.mark.asyncio
    async def test_thermometer_with_mock_data(self):
        """温度计端到端"""
        mock_index = [
            {"name": "上证指数", "change_pct": 1.5},
            {"name": "深证成指", "change_pct": 2.0},
        ]
        mock_sectors = [{"change_pct": 1.0, "main_net_inflow": 10 * 1_0000_0000}] * 8 + \
                       [{"change_pct": -1.0, "main_net_inflow": -5 * 1_0000_0000}] * 2
        mock_northbound = {"total_net_inflow": 50 * 1_0000_0000, "sh_net_inflow": 30 * 1_0000_0000, "sz_net_inflow": 20 * 1_0000_0000}
        mock_sentiment = {"rise_count": 3000, "fall_count": 2000, "sentiment": "偏多"}

        with patch.object(market_assessment.ds2, "get_index_realtime", return_value=mock_index):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=mock_sectors):
                with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value=mock_northbound):
                    with patch.object(market_assessment.ds2, "get_market_sentiment", return_value=mock_sentiment):
                        result = await get_market_thermometer()

        assert "score" in result
        assert "level" in result
        assert "components" in result
        # 涨多跌少,应该偏热
        assert result["score"] > 50

    @pytest.mark.asyncio
    async def test_capital_flow_overview_with_mock(self):
        """资金流向总览端到端"""
        mock_northbound = {"total_net_inflow": 80 * 1_0000_0000, "sh_net_inflow": 50 * 1_0000_0000, "sz_net_inflow": 30 * 1_0000_0000, "date": "2026-07-28"}
        mock_sectors = [
            {"name": "白酒", "change_pct": 2.5, "main_net_inflow": 15 * 1_0000_0000},
            {"name": "半导体", "change_pct": 1.8, "main_net_inflow": 10 * 1_0000_0000},
            {"name": "房地产", "change_pct": -2.0, "main_net_inflow": -8 * 1_0000_0000},
        ]

        with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value=mock_northbound):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=mock_sectors):
                result = await get_capital_flow_overview()

        assert result["northbound"]["total_net_inflow"] == 80.0
        # 3个板块全部进入top_inflow(top5切片,实际只有3个)
        assert len(result["sector_main_flow"]["top_inflow"]) == 3
        # 按净流入降序: 白酒(15) > 半导体(10) > 房地产(-8)
        assert result["sector_main_flow"]["top_inflow"][0]["name"] == "白酒"
        assert result["sector_main_flow"]["top_inflow"][1]["name"] == "半导体"

    @pytest.mark.asyncio
    async def test_market_assessment_integration(self):
        """大盘综合研判端到端"""
        mock_index = [{"name": "上证指数", "change_pct": 2.5}]
        mock_sectors = [{"change_pct": 2.0, "main_net_inflow": 20 * 1_0000_0000, "name": "白酒"}] * 8 + \
                       [{"change_pct": -1.0, "main_net_inflow": -5 * 1_0000_0000, "name": "房地产"}] * 2
        mock_northbound = {"total_net_inflow": 100 * 1_0000_0000, "sh_net_inflow": 60 * 1_0000_0000, "sz_net_inflow": 40 * 1_0000_0000, "date": "2026-07-28"}
        mock_sentiment = {"rise_count": 4000, "fall_count": 1000, "sentiment": "偏多"}

        with patch.object(market_assessment.ds2, "get_index_realtime", return_value=mock_index):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=mock_sectors):
                with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value=mock_northbound):
                    with patch.object(market_assessment.ds2, "get_market_sentiment", return_value=mock_sentiment):
                        result = await get_market_assessment()

        assert "thermometer" in result
        assert "capital_overview" in result
        assert "assessment" in result
        assert "sector_signal" in result
        assert "capital_signal" in result
        # 大涨,温度计高分
        assert result["thermometer"]["score"] >= 70
        assert "大幅净流入" in result["capital_signal"]

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
        # 北向停更: net 必须为 None(缺失), 不能是 0(净流入恰为 0)
        assert result["northbound_net"] is None
        assert result["northbound_available"] is False

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
        """板块主力净流入

        A-08(2026-11-17)台账: 旧断言 `score == 55` 锁定的是"北向缺失时把 nb_score
        固定 50 再与板块各占一半"——即用占位值把资金维度系统性拉向中性。
        北向已永久停更, 缺失时资金分应直接等于板块分(权重 nb=0 / sector=1)。
        """
        sectors = [{"main_net_inflow": 20 * 1_0000_0000}]  # 20亿
        result = _calc_capital_flow_score({}, sectors)
        # sector_score = 50 + 20 * 0.5 = 60;北向缺失 → 资金分 = 板块分
        assert result["score"] == pytest.approx(60, abs=0.1)
        assert result["sector_main_net"] == 20.0
        # 北向仍然"不可用", 且资金口径不完整(缺 northbound)
        assert result["northbound_available"] is False
        assert result["available"] is True          # 这一维有板块数据
        assert result["complete"] is False          # 但口径不完整
        assert result["missing"] == ["northbound"]


class TestSentimentScore:
    """市场情绪评分"""

    def test_empty_data_neutral(self):
        result = _calc_sentiment_score({})
        assert result["score"] == 50

    def test_dash_value_neutral(self):
        result = _calc_sentiment_score({"sentiment": "--"})
        assert result["score"] == 50

    def test_none_counts_does_not_crash(self):
        """涨跌家数为 None 时不得抛异常（2026-11-08 回归）

        背景：上游在指数代理路径下把 rise_count/fall_count 置为 None 并标记
        counts_available=False（表示"没拿到"而非"0 只上涨"）。原实现
        int(sentiment.get("rise_count", 0)) 的默认值只在 key 缺失时生效，
        key 存在且为 None 时返回 None → int(None) 抛 TypeError →
        /api/market/assessment 直接 500，行情页「大盘研判」整块不可用。
        注意早退条件是 sentiment == "--"，而代理路径返回"偏空/偏多"，走不到早退。
        """
        sentiment = {
            "rise_count": None, "fall_count": None, "flat_count": None,
            "sentiment": "偏空", "ratio": 0.35,
            "basis": "index_proxy", "counts_available": False,
        }
        result = _calc_sentiment_score(sentiment)
        assert result["score"] == 50
        assert "不可用" in result["note"]

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
            # P1-2:温度计改用**全量**板块评分, patch 目标随之更新(断言未改)
            with patch.object(market_assessment.ds2, "get_sector_ranking_full", return_value=mock_sectors):
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
        assert result["northbound"]["available"] is True
        # 流入榜只收正值: 白酒(15) > 半导体(10); 房地产(-8) 不得混进流入榜
        assert len(result["sector_main_flow"]["top_inflow"]) == 2
        assert result["sector_main_flow"]["top_inflow"][0]["name"] == "白酒"
        assert result["sector_main_flow"]["top_inflow"][1]["name"] == "半导体"
        # 流出榜只收负值, 并按净流出从小到大(最负在前)
        assert len(result["sector_main_flow"]["top_outflow"]) == 1
        assert result["sector_main_flow"]["top_outflow"][0]["name"] == "房地产"
        assert result["sector_main_flow"]["total_net_inflow"] == 17.0

    @pytest.mark.asyncio
    async def test_capital_flow_top_outflow_excludes_zero_and_positive(self):
        """TOP5 净流出不得把 0 和正值算进去(旧实现会虚增/虚减合计)。"""
        mock_sectors = [
            {"name": "流入A", "change_pct": 1.0, "main_net_inflow": 5 * 1_0000_0000},
            {"name": "零值B", "change_pct": 0.0, "main_net_inflow": 0},
            {"name": "流出C", "change_pct": -1.0, "main_net_inflow": -3 * 1_0000_0000},
            {"name": "流出D", "change_pct": -2.0, "main_net_inflow": -1 * 1_0000_0000},
        ]
        with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value={}):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=mock_sectors):
                result = await get_capital_flow_overview()

        smf = result["sector_main_flow"]
        assert [x["name"] for x in smf["top_inflow"]] == ["流入A"]
        assert [x["name"] for x in smf["top_outflow"]] == ["流出C", "流出D"]
        assert all(x["main_net_inflow"] < 0 for x in smf["top_outflow"])
        assert smf["total_net_inflow"] == 1.0

    @pytest.mark.asyncio
    async def test_capital_flow_overview_northbound_unavailable_keeps_none(self):
        """北向停更时三项输出 None(前端显示「—」),不得落成 0 冒充"净流入 0"。"""
        with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value={}):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=[]):
                result = await get_capital_flow_overview()

        nb = result["northbound"]
        assert nb["available"] is False
        assert nb["total_net_inflow"] is None
        assert nb["sh_net_inflow"] is None
        assert nb["sz_net_inflow"] is None

    @pytest.mark.asyncio
    async def test_capital_flow_overview_total_only_keeps_split_none(self):
        """A-01(2026-11-17):上游只恢复"合计净额"、没有沪深拆分时,
        sh/sz 必须是 None 且把 sh_sz_split_available=False 透传给前端。

        旧实现只判 `bool(northbound)`,把数据源写死的 0.0 原样输出成
        "沪股通净流入 0 亿" —— 用户会把"没拿到拆分数据"读成"两市都没净流入"。
        """
        total_only = {
            "date": "2026-11-16",
            "total_net_inflow": 50 * 1_0000_0000,
            "sh_net_inflow": None,
            "sz_net_inflow": None,
            "sh_sz_split_available": False,
        }
        with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value=total_only):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=[]):
                result = await get_capital_flow_overview()

        nb = result["northbound"]
        assert nb["available"] is True
        assert nb["total_net_inflow"] == 50.0
        assert nb["sh_net_inflow"] is None
        assert nb["sz_net_inflow"] is None
        assert nb["sh_sz_split_available"] is False

    @pytest.mark.asyncio
    async def test_capital_flow_overview_split_available_keeps_numbers(self):
        """拆分可用时(flag 缺省/True)沪深数值照旧输出, 不得因为加 flag 而丢数据。"""
        with_split = {
            "date": "2026-11-16",
            "total_net_inflow": 80 * 1_0000_0000,
            "sh_net_inflow": 50 * 1_0000_0000,
            "sz_net_inflow": 30 * 1_0000_0000,
            "sh_sz_split_available": True,
        }
        with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value=with_split):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=[]):
                result = await get_capital_flow_overview()

        nb = result["northbound"]
        assert nb["sh_net_inflow"] == 50.0
        assert nb["sz_net_inflow"] == 30.0
        assert nb["sh_sz_split_available"] is True

    @pytest.mark.asyncio
    async def test_market_assessment_signal_when_northbound_missing(self):
        """北向缺失时资金信号必须说明"已停止披露",不能写成小幅净流出。"""
        mock_sectors = [{"change_pct": 2.0, "main_net_inflow": 20 * 1_0000_0000, "name": "白酒"}] * 8 + \
                       [{"change_pct": -1.0, "main_net_inflow": -5 * 1_0000_0000, "name": "房地产"}] * 2
        with patch.object(market_assessment.ds2, "get_index_realtime", return_value=[{"change_pct": 1.0}]):
            with patch.object(market_assessment.ds2, "get_sector_ranking", return_value=mock_sectors):
                with patch.object(market_assessment.ds2, "get_northbound_flow_realtime", return_value={}):
                    with patch.object(market_assessment.ds2, "get_market_sentiment", return_value={"rise_count": 3000, "fall_count": 2000}):
                        result = await get_market_assessment()

        assert result["capital_signal"] == "北向净流入已停止披露"

    @pytest.mark.asyncio
    async def test_market_assessment_integration(self):
        """大盘综合研判端到端"""
        mock_index = [{"name": "上证指数", "change_pct": 2.5}]
        mock_sectors = [{"change_pct": 2.0, "main_net_inflow": 20 * 1_0000_0000, "name": "白酒"}] * 8 + \
                       [{"change_pct": -1.0, "main_net_inflow": -5 * 1_0000_0000, "name": "房地产"}] * 2
        mock_northbound = {"total_net_inflow": 100 * 1_0000_0000, "sh_net_inflow": 60 * 1_0000_0000, "sz_net_inflow": 40 * 1_0000_0000, "date": "2026-07-28"}
        mock_sentiment = {"rise_count": 4000, "fall_count": 1000, "sentiment": "偏多"}

        with patch.object(market_assessment.ds2, "get_index_realtime", return_value=mock_index):
            # P1-2:同上 —— 评分路径改用全量板块
            with patch.object(market_assessment.ds2, "get_sector_ranking_full", return_value=mock_sectors):
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


# ===== P1-2(2026-11-17):板块轮动分不得用"涨幅榜前 50"当全市场 =====
# 线上实测: /api/market/sector_ranking 返回 50 条(change_pct 从 +5.82% 到 +0.30%),
# 上涨 50 / 下跌 0; 而 fs=m:90+t:2 真实有 496 个板块。同一时刻个股 1236/3925 上涨,
# "所有板块都在涨"是不可能的。根因: pz=50 + fid=f3 降序 = 只要了涨幅榜前 50。
# 真实全量(2026-11-17 21:00): 496 个板块 → 涨 72 / 跌 423 → ratio 0.145。

_REAL_SECTOR_TOTAL = 496
_REAL_SECTOR_GAIN = 72


def _real_sector_universe() -> list[dict]:
    """按真实分布构造 496 条: 72 涨 / 423 跌 / 1 平。"""
    rows = [{"change_pct": 1.0} for _ in range(_REAL_SECTOR_GAIN)]
    rows += [{"change_pct": -1.0} for _ in range(423)]
    rows += [{"change_pct": 0.0}]
    return rows


class TestSectorRotationFullUniverse:
    def test_full_universe_gives_real_score(self):
        """全量 496 条 → total_sectors=496、gain_count=72、score≈14.5, 不再是 100。"""
        result = _calc_sector_rotation_score(_real_sector_universe())

        assert result["total_sectors"] == _REAL_SECTOR_TOTAL
        assert result["gain_count"] == _REAL_SECTOR_GAIN
        assert result["ratio" if "ratio" in result else "score"] is not None
        assert result["score"] == pytest.approx(72 / 496 * 100, abs=0.1)
        assert result["score"] != 100
        assert result["available"] is True

    def test_truncated_top50_does_not_score_100_when_breadth_contradicts(self):
        """**反向证明**: 只拿到涨幅榜前 50(全上涨)+ 个股广度 23.9% → 不得给 100 分。

        这是修复的核心: 旧实现永远只看到这 50 条, score 恒 100。
        """
        top50 = [{"change_pct": 5.82 - i * 0.11} for i in range(50)]
        result = _calc_sector_rotation_score(top50, sentiment_ratio=0.239)

        assert result["score"] != 100
        assert result["available"] is False
        assert "背离" in result.get("note", "")
        assert result["score"] == 50  # 宁可中性也不给假的满分

    def test_guard_not_triggered_when_breadth_is_also_broad(self):
        """个股也普涨时不误伤: 全涨板块 + 个股广度 85% → 仍按 100 分算。"""
        all_up = [{"change_pct": 1.0} for _ in range(10)]
        result = _calc_sector_rotation_score(all_up, sentiment_ratio=0.85)

        assert result["available"] is True
        assert result["score"] == 100

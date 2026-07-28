"""基金重仓股板块分析单元测试

验证 src/analysis/fund_holdings.py 的核心逻辑:
- 板块关键词推断(_infer_sector)
- 板块映射(_map_holdings_to_sectors)
- 集中度计算(_calc_concentration: Top5/Top10/HHI)
- 净值影响预估(_estimate_nav_impact)
- 板块轮动信号(_build_sector_rotation_signal)
- 东方财富HTML解析(_fetch_fund_holdings_em, mock)
- 端到端 analyze_fund_holdings(mock 数据)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.analysis import fund_holdings
from src.analysis.fund_holdings import (
    _build_sector_rotation_signal,
    _calc_concentration,
    _estimate_nav_impact,
    _infer_sector,
    _map_holdings_to_sectors,
    analyze_fund_holdings,
    get_fund_holdings,
)


class TestInferSector:
    """股票名称 → 行业板块推断"""

    def test_bank_stock_matches_bank_sector(self):
        assert _infer_sector("工商银行") == "银行"
        assert _infer_sector("招商银行") == "银行"

    def test_baijiu_stock_matches_baijiu_sector(self):
        assert _infer_sector("贵州茅台") == "白酒"
        assert _infer_sector("五粮液") == "白酒"

    def test_tech_stock_matches_semi_sector(self):
        assert _infer_sector("中芯国际") == "半导体"
        assert _infer_sector("韦尔股份") == "半导体"  # 注:韦尔不直接匹配,但芯片关键字

    def test_pharma_stock_matches_pharma_sector(self):
        assert _infer_sector("恒瑞医药") == "医药"
        assert _infer_sector("药明康德") == "医药"

    def test_no_match_returns_empty(self):
        assert _infer_sector("未知股票ABC") == ""


class TestMapHoldingsToSectors:
    """重仓股 → 板块暴露度"""

    def test_empty_holdings_returns_empty(self):
        assert _map_holdings_to_sectors([]) == []

    def test_single_sector_aggregation(self):
        """同板块的多只重仓股权重应聚合"""
        holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85},
            {"code": "000858", "name": "五粮液", "weight": 7.23},
            {"code": "000568", "name": "泸州老窖", "weight": 5.12},
        ]
        result = _map_holdings_to_sectors(holdings)
        assert len(result) == 1
        assert result[0]["sector"] == "白酒"
        assert result[0]["total_weight"] == pytest.approx(22.20, abs=0.01)
        assert len(result[0]["stocks"]) == 3

    def test_multi_sector_sorted_by_weight(self):
        """多板块应按权重降序"""
        holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85},
            {"code": "600036", "name": "招商银行", "weight": 8.50},
            {"code": "000858", "name": "五粮液", "weight": 5.0},
        ]
        result = _map_holdings_to_sectors(holdings)
        assert len(result) == 2
        # 白酒(14.85) > 银行(8.5)
        assert result[0]["sector"] == "白酒"
        assert result[1]["sector"] == "银行"

    def test_unknown_stock_filtered(self):
        """未匹配板块的股票应被过滤"""
        holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85},
            {"code": "999999", "name": "未知股票XYZ", "weight": 5.0},
        ]
        result = _map_holdings_to_sectors(holdings)
        assert len(result) == 1
        assert result[0]["sector"] == "白酒"


class TestCalcConcentration:
    """集中度指标计算"""

    def test_empty_holdings_returns_zero(self):
        result = _calc_concentration([])
        assert result["top5_weight"] == 0
        assert result["top10_weight"] == 0
        assert result["hhi"] == 0
        assert result["level"] == "无数据"

    def test_top5_top10_sum(self):
        holdings = [{"weight": w} for w in [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]]
        result = _calc_concentration(holdings)
        assert result["top5_weight"] == pytest.approx(40, abs=0.01)
        assert result["top10_weight"] == pytest.approx(55, abs=0.01)

    def test_hhi_low_concentration(self):
        """HHI < 1000 低集中"""
        # 10只,每只5%,HHI = 10 * 25 = 250
        holdings = [{"weight": 5.0} for _ in range(10)]
        result = _calc_concentration(holdings)
        assert result["hhi"] == pytest.approx(250, abs=0.1)
        assert result["level"] == "低集中"

    def test_hhi_high_concentration(self):
        """HHI > 1800 高集中"""
        # 一只40%,其他小仓位: HHI = 1600 + ...
        holdings = [{"weight": 40.0}] + [{"weight": 2.0} for _ in range(10)]
        result = _calc_concentration(holdings)
        # HHI = 1600 + 10*4 = 1640 ... 不算高集中,调整数据
        # 一只50%, HHI = 2500 单独一只
        holdings = [{"weight": 50.0}] + [{"weight": 1.0} for _ in range(5)]
        result = _calc_concentration(holdings)
        assert result["hhi"] >= 1800
        assert result["level"] == "高集中"

    def test_hhi_medium_concentration(self):
        """HHI 在 1000-1800 中等集中"""
        # 一只30%: HHI = 900,加上其他
        holdings = [{"weight": 30.0}, {"weight": 10.0}, {"weight": 5.0}]
        result = _calc_concentration(holdings)
        # HHI = 900 + 100 + 25 = 1025
        assert 1000 <= result["hhi"] < 1800
        assert result["level"] == "中等集中"


class TestEstimateNavImpact:
    """净值影响预估"""

    def test_empty_data_returns_zero(self):
        result = _estimate_nav_impact([], [])
        assert result["estimated_change_pct"] == 0
        assert result["contributors"] == []
        assert result["draggers"] == []

    def test_positive_contributors(self):
        holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85},
        ]
        quotes = [
            {"code": "600519", "change_pct": 2.5},
        ]
        result = _estimate_nav_impact(holdings, quotes)
        # 贡献 = 9.85 * 2.5 / 100 = 0.24625
        assert result["estimated_change_pct"] == pytest.approx(0.2463, abs=0.001)
        assert len(result["contributors"]) == 1
        assert result["contributors"][0]["contribution"] > 0
        assert result["draggers"] == []

    def test_negative_draggers(self):
        holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85},
        ]
        quotes = [
            {"code": "600519", "change_pct": -3.0},
        ]
        result = _estimate_nav_impact(holdings, quotes)
        assert result["estimated_change_pct"] < 0
        assert len(result["draggers"]) == 1
        assert result["contributors"] == []

    def test_mixed_quotes_sorted(self):
        """混合涨跌,贡献和拖累分别排序"""
        holdings = [
            {"code": "A", "name": "A", "weight": 10},
            {"code": "B", "name": "B", "weight": 8},
            {"code": "C", "name": "C", "weight": 5},
        ]
        quotes = [
            {"code": "A", "change_pct": 3.0},
            {"code": "B", "change_pct": 1.0},
            {"code": "C", "change_pct": -2.0},
        ]
        result = _estimate_nav_impact(holdings, quotes)
        # 贡献排序: A(0.3) > B(0.08)
        assert result["contributors"][0]["code"] == "A"
        assert result["draggers"][0]["code"] == "C"

    def test_missing_quote_skipped(self):
        """行情中缺失的重仓股应被跳过"""
        holdings = [
            {"code": "A", "name": "A", "weight": 10},
            {"code": "B", "name": "B", "weight": 5},
        ]
        quotes = [{"code": "A", "change_pct": 2.0}]
        result = _estimate_nav_impact(holdings, quotes)
        # 只有A有贡献,B被跳过
        assert result["estimated_change_pct"] == pytest.approx(0.2, abs=0.001)


class TestSectorRotationSignal:
    """板块轮动信号"""

    def test_empty_data_returns_empty(self):
        assert _build_sector_rotation_signal([], []) == []

    def test_top_rank_strong_signal(self):
        """前20% 板块 → 强势/抗跌"""
        sector_exposure = [{"sector": "白酒", "total_weight": 30}]
        # 10个板块,白酒排第1
        sector_ranking = [
            {"name": "白酒", "change_pct": 3.5},
        ] + [{"name": f"板块{i}", "change_pct": 0.5} for i in range(9)]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        assert len(result) == 1
        assert result[0]["rank"] == 1
        assert result[0]["signal"] in ("强势", "抗跌")
        assert result[0]["change_pct"] == 3.5

    def test_bottom_rank_weak_signal(self):
        """后20% 板块 → 弱势/滞涨"""
        sector_exposure = [{"sector": "房地产", "total_weight": 20}]
        sector_ranking = [
            {"name": f"板块{i}", "change_pct": 1.0} for i in range(8)
        ] + [{"name": "房地产", "change_pct": -2.5}]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        assert result[0]["rank"] == 9  # 9/9 排最后
        assert result[0]["signal"] in ("弱势", "滞涨")

    def test_middle_rank_neutral_signal(self):
        """中间排名 → 中性"""
        sector_exposure = [{"sector": "银行", "total_weight": 25}]
        # 10个板块,银行排第5(中间),change_pct 设为明显的中等位置
        sector_ranking = [
            {"name": "板块A", "change_pct": 3.0},
            {"name": "板块B", "change_pct": 2.5},
            {"name": "板块C", "change_pct": 2.0},
            {"name": "板块D", "change_pct": 1.5},
            {"name": "银行", "change_pct": 1.0},  # 第5名(中间)
            {"name": "板块F", "change_pct": 0.5},
            {"name": "板块G", "change_pct": 0.0},
            {"name": "板块H", "change_pct": -0.5},
            {"name": "板块I", "change_pct": -1.0},
            {"name": "板块J", "change_pct": -1.5},
        ]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        # 10个板块,银行排第5,属于中间(前20%是1-2,后20%是9-10,中间是3-8)
        assert result[0]["rank"] == 5
        assert result[0]["signal"] == "中性"

    def test_no_matching_sector_returns_zero_rank(self):
        """板块排名中未找到对应板块"""
        sector_exposure = [{"sector": "白酒", "total_weight": 30}]
        sector_ranking = [{"name": "银行", "change_pct": 1.0}]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        assert result[0]["rank"] == 0
        assert result[0]["signal"] == "无数据"


class TestFundHoldingsFetch:
    """持仓数据抓取(mock)"""

    def test_get_fund_holdings_uses_cache(self):
        """已缓存时应直接返回,不调用抓取"""
        with patch.object(fund_holdings.ds2, "_cache_get", return_value=[{"code": "600519", "name": "贵州茅台", "weight": 9.85}]):
            with patch.object(fund_holdings, "_fetch_fund_holdings_em") as mock_em:
                result = get_fund_holdings("110022")
                assert len(result) == 1
                assert result[0]["code"] == "600519"
                mock_em.assert_not_called()

    def test_get_fund_holdings_fallback_to_akshare(self):
        """东方财富失败时降级到akshare"""
        with patch.object(fund_holdings.ds2, "_cache_get", return_value=None):
            with patch.object(fund_holdings, "_fetch_fund_holdings_em", return_value=[]):
                with patch.object(fund_holdings, "_fetch_fund_holdings_ak", return_value=[{"code": "000858", "name": "五粮液", "weight": 7.5}]) as mock_ak:
                    with patch.object(fund_holdings.ds2, "_cache_set"):
                        result = get_fund_holdings("110022")
                        assert len(result) == 1
                        assert result[0]["code"] == "000858"
                        mock_ak.assert_called_once_with("110022")


class TestAnalyzeFundHoldings:
    """端到端分析(mock 数据)"""

    @pytest.mark.asyncio
    async def test_empty_holdings_returns_default(self):
        """无重仓股数据时返回默认结构"""
        with patch.object(fund_holdings, "get_fund_holdings", return_value=[]):
            result = await analyze_fund_holdings("999999")
            assert result["holdings"] == []
            assert result["concentration"]["level"] == "无数据"
            assert result["nav_impact"]["note"] == "无重仓股数据"
            assert result["sector_rotation"] == []

    @pytest.mark.asyncio
    async def test_full_analysis_with_mock_data(self):
        """完整分析流程(mock 数据)"""
        mock_holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85, "hold_amount": 1000, "hold_value": 1000000, "quarter": "2026Q2", "change": "不变"},
            {"code": "000858", "name": "五粮液", "weight": 7.23, "hold_amount": 800, "hold_value": 800000, "quarter": "2026Q2", "change": "增持"},
            {"code": "600036", "name": "招商银行", "weight": 6.5, "hold_amount": 500, "hold_value": 500000, "quarter": "2026Q2", "change": "新进"},
        ]
        mock_quotes = [
            {"code": "600519", "change_pct": 2.5},
            {"code": "000858", "change_pct": 1.8},
            {"code": "600036", "change_pct": -0.5},
        ]
        mock_sectors = [
            {"name": "白酒", "change_pct": 2.0, "amount": 5000000},
            {"name": "银行", "change_pct": -0.3, "amount": 8000000},
        ]

        with patch.object(fund_holdings, "get_fund_holdings", return_value=mock_holdings):
            with patch.object(fund_holdings.ds2, "get_realtime_quote_tencent", return_value=mock_quotes):
                with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=mock_sectors):
                    result = await analyze_fund_holdings("110022")

        # 验证持仓
        assert len(result["holdings"]) == 3
        # 验证板块暴露度
        sectors = result["sector_exposure"]
        assert len(sectors) == 2
        # 白酒(17.08) > 银行(6.5)
        assert sectors[0]["sector"] == "白酒"
        assert sectors[0]["total_weight"] == pytest.approx(17.08, abs=0.01)
        # 验证集中度
        assert result["concentration"]["top5_weight"] == pytest.approx(23.58, abs=0.01)
        # 验证净值影响预估
        # 9.85*2.5/100 + 7.23*1.8/100 + 6.5*-0.5/100 = 0.2463 + 0.1301 - 0.0325 = 0.3439
        assert result["nav_impact"]["estimated_change_pct"] == pytest.approx(0.3439, abs=0.001)
        assert len(result["nav_impact"]["contributors"]) == 2  # 茅台+五粮液
        assert len(result["nav_impact"]["draggers"]) == 1  # 招行
        # 验证板块轮动
        assert len(result["sector_rotation"]) == 2

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

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.analysis import fund_holdings
from src.analysis.fund_holdings import (
    _build_sector_rotation_signal,
    _calc_concentration,
    _current_year_month,
    _estimate_nav_impact,
    _fetch_fund_holdings_ak,
    _fetch_fund_holdings_em,
    _infer_sector,
    _map_holdings_to_sectors,
    analyze_fund_holdings,
    get_fund_holdings,
    get_sector_rotation,
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


# ===== 东方财富 HTML 解析(_fetch_fund_holdings_em)=====


def _em_text(html: str) -> str:
    """构造东方财富 apidata 响应文本"""
    return f'var apidata={{ content:"{html}", arryear:[2026] }};'


def _em_row(idx, code, name, weight, hold_amount, hold_value, quarter, change):
    return (
        f"<tr><td>{idx}</td><td>{code}</td><td>{name}</td>"
        f"<td>{weight}%</td><td>{hold_amount}</td><td>{hold_value}</td>"
        f"<td>{quarter}</td><td>{change}</td></tr>"
    )


def _mock_em_get(text: str):
    """返回一个 mock 的 _EM_FUND_SESSION.get,其 .text 为给定文本"""
    resp = MagicMock()
    resp.text = text
    return MagicMock(return_value=resp)


class TestCurrentYearMonth:
    """_current_year_month"""

    def test_returns_year_and_zero_padded_month(self):
        year, month = _current_year_month()
        assert year.isdigit()
        assert len(year) == 4
        assert len(month) == 2
        assert 1 <= int(month) <= 12

    def test_month_zero_padded(self):
        """月份应补零(01-12)"""
        _, month = _current_year_month()
        assert month[0] in ("0", "1")  # 01-09 以 0 开头,10-12 以 1 开头
        assert int(month) >= 1


class TestFetchFundHoldingsEm:
    """_fetch_fund_holdings_em 东方财富抓取"""

    def test_parses_two_rows_with_changes(self):
        """正常解析两行,识别 增持/新进 变化标记"""
        html = (
            "<table><tbody>"
            + _em_row(1, "600519", "贵州茅台", "9.85", "1,234", "1,234,567", "2026Q2", "增持")
            + _em_row(2, "000858", "五粮液", "7.23", "2,000", "2,000,000", "2026Q2", "新进")
            + "</tbody></table>"
        )
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert len(result) == 2
        r0 = result[0]
        assert r0["code"] == "600519"
        assert r0["name"] == "贵州茅台"
        assert r0["weight"] == 9.85
        assert r0["hold_amount"] == 1234.0
        assert r0["hold_value"] == 1234567.0
        assert r0["quarter"] == "2026Q2"
        assert r0["change"] == "增持"
        assert result[1]["change"] == "新进"

    def test_change_decrease_and_unchanged(self):
        """减持/其他 → 减持/不变"""
        html = (
            "<table><tbody>"
            + _em_row(1, "600519", "贵州茅台", "9.85", "100", "100000", "2026Q2", "减持")
            + _em_row(2, "000858", "五粮液", "7.23", "200", "200000", "2026Q2", "不变")
            + "</tbody></table>"
        )
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result[0]["change"] == "减持"
        assert result[1]["change"] == "不变"

    def test_change_unknown_falls_back_to_unchanged(self):
        """未知变化标记 → 不变"""
        html = "<table><tbody>" + _em_row(1, "600519", "贵州茅台", "9.85", "100", "100000", "Q", "XYZ") + "</tbody></table>"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result[0]["change"] == "不变"

    def test_empty_weight_becomes_zero(self):
        """占净值比例为空 → weight 0.0"""
        html = "<table><tbody>" + _em_row(1, "600519", "贵州茅台", "", "100", "100000", "Q", "不变") + "</tbody></table>"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result[0]["weight"] == 0.0

    def test_dash_hold_amount_becomes_zero(self):
        """持股数/持仓市值为 '--' → 0.0"""
        html = "<table><tbody>" + _em_row(1, "600519", "贵州茅台", "9.85", "--", "--", "Q", "不变") + "</tbody></table>"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result[0]["hold_amount"] == 0.0
        assert result[0]["hold_value"] == 0.0

    def test_row_with_too_few_cells_skipped(self):
        """少于 8 个单元格的行应被跳过"""
        html = (
            "<table><tbody>"
            + "<tr><td>1</td><td>600519</td><td>贵州茅台</td></tr>"  # 仅 3 个单元格
            + _em_row(2, "000858", "五粮液", "7.23", "200", "200000", "Q", "不变")
            + "</tbody></table>"
        )
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert len(result) == 1
        assert result[0]["code"] == "000858"

    def test_unescape_backslash_sequences(self):
        """content 中的 \\/ 与 \\\\ 应被反转义"""
        # 在 name 中嵌入转义序列(不包含 " 避免破坏 content 提取)
        html = '<table><tbody><tr><td>1</td><td>600519</td><td>a\\/b\\\\c</td><td>9.85%</td><td>1</td><td>1</td><td>Q</td><td>不变</td></tr></tbody></table>'
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert len(result) == 1
        assert result[0]["name"] == "a/b\\c"

    def test_no_apidata_returns_empty(self):
        """响应无 apidata → []"""
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get("no apidata here")):
            result = _fetch_fund_holdings_em("110022")
        assert result == []

    def test_no_content_field_returns_empty(self):
        """apidata 无 content 字段 → []"""
        text = "var apidata={ arryear:[2026] };"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(text)):
            result = _fetch_fund_holdings_em("110022")
        assert result == []

    def test_get_exception_returns_empty(self):
        """HTTP 异常 → [](不外泄)"""
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", side_effect=ConnectionError("net")):
            result = _fetch_fund_holdings_em("110022")
        assert result == []

    def test_row_parse_exception_skipped(self):
        """某行 weight 非数字且非空 → 该行被跳过,不影响其他行"""
        html = (
            "<table><tbody>"
            + _em_row(1, "600519", "茅台", "abc", "1", "1", "Q", "不变")  # weight 非数字 → ValueError
            + _em_row(2, "000858", "五粮液", "7.23", "200", "200000", "Q", "不变")
            + "</tbody></table>"
        )
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert len(result) == 1
        assert result[0]["code"] == "000858"

    def test_strips_html_tags_in_cells(self):
        """单元格内含 HTML 标签应被去除"""
        html = (
            '<table><tbody><tr><td>1</td><td><a href="x">600519</a></td>'
            '<td><span>贵州茅台</span></td><td>9.85%</td><td>1</td><td>1</td><td>Q</td><td>不变</td></tr></tbody></table>'
        )
        # 注:单元格内出现 " 会破坏 content 提取,这里用 href='x' 单引号规避
        html = (
            "<table><tbody><tr><td>1</td><td><a href='x'>600519</a></td>"
            "<td><span>贵州茅台</span></td><td>9.85%</td><td>1</td><td>1</td><td>Q</td><td>不变</td></tr></tbody></table>"
        )
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert len(result) == 1
        assert result[0]["code"] == "600519"
        assert result[0]["name"] == "贵州茅台"


class TestFetchFundHoldingsAk:
    """_fetch_fund_holdings_ak akshare 兜底"""

    def test_no_akshare_returns_empty(self):
        """_HAS_AKSHARE 为 False → []"""
        with patch.object(fund_holdings, "_HAS_AKSHARE", False):
            result = _fetch_fund_holdings_ak("110022")
        assert result == []

    def test_parses_dataframe_rows(self):
        df = pd.DataFrame([
            {"股票代码": "600519", "股票名称": "贵州茅台", "占净值比例": 9.85, "持股数": 1234, "持仓市值": 1234567, "季度": "2026Q2"},
            {"股票代码": "000858", "股票名称": "五粮液", "占净值比例": 7.23, "持股数": 2000, "持仓市值": 2000000, "季度": "2026Q2"},
        ])
        with patch.object(fund_holdings, "_HAS_AKSHARE", True), \
             patch.object(fund_holdings, "ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = df
            result = _fetch_fund_holdings_ak("110022")
        assert len(result) == 2
        assert result[0]["code"] == "600519"
        assert result[0]["name"] == "贵州茅台"
        assert result[0]["weight"] == 9.85
        assert result[0]["change"] == "不变"

    def test_none_df_returns_empty(self):
        with patch.object(fund_holdings, "_HAS_AKSHARE", True), \
             patch.object(fund_holdings, "ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = None
            result = _fetch_fund_holdings_ak("110022")
        assert result == []

    def test_empty_df_returns_empty(self):
        with patch.object(fund_holdings, "_HAS_AKSHARE", True), \
             patch.object(fund_holdings, "ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = pd.DataFrame()
            result = _fetch_fund_holdings_ak("110022")
        assert result == []

    def test_truncates_to_top_ten(self):
        """超过 10 行应截断为前 10"""
        rows = [
            {"股票代码": f"{i:06d}", "股票名称": f"股{i}", "占净值比例": float(10 - i),
             "持股数": i, "持仓市值": i * 1000, "季度": "Q"}
            for i in range(15)
        ]
        df = pd.DataFrame(rows)
        with patch.object(fund_holdings, "_HAS_AKSHARE", True), \
             patch.object(fund_holdings, "ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = df
            result = _fetch_fund_holdings_ak("110022")
        assert len(result) == 10

    def test_exception_returns_empty(self):
        with patch.object(fund_holdings, "_HAS_AKSHARE", True), \
             patch.object(fund_holdings, "ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.side_effect = RuntimeError("akshare err")
            result = _fetch_fund_holdings_ak("110022")
        assert result == []

    def test_missing_fields_default_zero(self):
        """缺失字段应回退为 0"""
        df = pd.DataFrame([{"股票代码": "600519", "股票名称": "贵州茅台"}])
        with patch.object(fund_holdings, "_HAS_AKSHARE", True), \
             patch.object(fund_holdings, "ak") as mock_ak:
            mock_ak.fund_portfolio_hold_em.return_value = df
            result = _fetch_fund_holdings_ak("110022")
        assert len(result) == 1
        assert result[0]["weight"] == 0.0
        assert result[0]["hold_amount"] == 0.0


class TestGetSectorRotation:
    """get_sector_rotation 板块轮动总览"""

    @pytest.mark.asyncio
    async def test_empty_ranking_returns_default(self):
        with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=[]):
            result = await get_sector_rotation()
        assert result["top_gainers"] == []
        assert result["top_losers"] == []
        assert result["hot_sectors"] == []
        assert result["rotation_signal"] == "无数据"

    @pytest.mark.asyncio
    async def test_bull_market_rotation_signal(self):
        """涨板块 > 跌板块*2 → 普涨"""
        ranking = [{"name": f"涨{i}", "change_pct": 1.0, "amount": 100} for i in range(6)]
        ranking += [{"name": f"跌{i}", "change_pct": -1.0, "amount": 100} for i in range(2)]
        with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=ranking):
            result = await get_sector_rotation()
        assert result["rotation_signal"] == "普涨"
        assert result["gain_count"] == 6
        assert result["fall_count"] == 2
        assert result["total_sectors"] == 8

    @pytest.mark.asyncio
    async def test_bear_market_rotation_signal(self):
        """跌板块 > 涨板块*2 → 普跌"""
        ranking = [{"name": f"涨{i}", "change_pct": 1.0, "amount": 100} for i in range(2)]
        ranking += [{"name": f"跌{i}", "change_pct": -1.0, "amount": 100} for i in range(6)]
        with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=ranking):
            result = await get_sector_rotation()
        assert result["rotation_signal"] == "普跌"

    @pytest.mark.asyncio
    async def test_mixed_market_rotation_signal(self):
        """涨跌接近 → 分化"""
        ranking = [{"name": f"涨{i}", "change_pct": 1.0, "amount": 100} for i in range(4)]
        ranking += [{"name": f"跌{i}", "change_pct": -1.0, "amount": 100} for i in range(4)]
        with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=ranking):
            result = await get_sector_rotation()
        assert result["rotation_signal"] == "分化"

    @pytest.mark.asyncio
    async def test_top_gainers_and_losers_sorted(self):
        ranking = [
            {"name": "板块A", "change_pct": 3.0, "amount": 100},
            {"name": "板块B", "change_pct": -2.0, "amount": 100},
            {"name": "板块C", "change_pct": 1.5, "amount": 100},
            {"name": "板块D", "change_pct": -1.0, "amount": 100},
            {"name": "板块E", "change_pct": 0.5, "amount": 100},
            {"name": "板块F", "change_pct": -3.0, "amount": 100},
        ]
        with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=ranking):
            result = await get_sector_rotation()
        # top_gainers 按 change_pct 降序,首个为 A(3.0)
        assert result["top_gainers"][0]["name"] == "板块A"
        # top_losers 最差在前,F(-3.0) 应在首位
        assert result["top_losers"][0]["name"] == "板块F"
        assert len(result["top_gainers"]) == 5
        assert len(result["top_losers"]) == 5

    @pytest.mark.asyncio
    async def test_hot_sectors_by_amount(self):
        """热门板块按成交额降序"""
        ranking = [
            {"name": "低额", "change_pct": 1.0, "amount": 100},
            {"name": "高额", "change_pct": 1.0, "amount": 99999},
            {"name": "中额", "change_pct": 1.0, "amount": 5000},
        ]
        with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=ranking):
            result = await get_sector_rotation()
        assert result["hot_sectors"][0]["name"] == "高额"
        assert len(result["hot_sectors"]) <= 5


class TestCalcConcentrationEdgeCases:
    """_calc_concentration 边界补充"""

    def test_single_holding(self):
        result = _calc_concentration([{"weight": 40.0}])
        assert result["top5_weight"] == 40.0
        assert result["top10_weight"] == 40.0
        assert result["hhi"] == 1600.0
        assert result["level"] == "中等集中"

    def test_fewer_than_ten_holdings(self):
        """持仓少于 10 → top10 等于实际总和"""
        holdings = [{"weight": 10.0}, {"weight": 5.0}]
        result = _calc_concentration(holdings)
        assert result["top5_weight"] == 15.0
        assert result["top10_weight"] == 15.0

    def test_missing_weight_key_defaults_zero(self):
        """缺 weight 键 → 视为 0"""
        result = _calc_concentration([{}])
        assert result["top5_weight"] == 0.0
        assert result["level"] == "低集中"


class TestEstimateNavImpactExtra:
    """_estimate_nav_impact 边界补充"""

    def test_zero_change_neither_contributor_nor_dragger(self):
        """change_pct=0 → 既非 contributor 也非 dragger"""
        holdings = [{"code": "A", "name": "A", "weight": 10}]
        quotes = [{"code": "A", "change_pct": 0.0}]
        result = _estimate_nav_impact(holdings, quotes)
        assert result["estimated_change_pct"] == 0.0
        assert result["contributors"] == []
        assert result["draggers"] == []

    def test_only_holdings_no_quotes_returns_default(self):
        result = _estimate_nav_impact([{"code": "A", "weight": 10}], [])
        assert result["estimated_change_pct"] == 0.0
        assert result["note"] == "数据不足"

    def test_only_quotes_no_holdings_returns_default(self):
        result = _estimate_nav_impact([], [{"code": "A", "change_pct": 1.0}])
        assert result["estimated_change_pct"] == 0.0

    def test_contributors_capped_at_five(self):
        """contributors 应截断为前 5"""
        holdings = [{"code": f"S{i}", "name": f"S{i}", "weight": 10} for i in range(8)]
        quotes = [{"code": f"S{i}", "change_pct": 2.0} for i in range(8)]
        result = _estimate_nav_impact(holdings, quotes)
        assert len(result["contributors"]) == 5
        assert len(result["draggers"]) == 0

    def test_draggers_capped_at_five(self):
        """draggers 应截断为前 5"""
        holdings = [{"code": f"S{i}", "name": f"S{i}", "weight": 10} for i in range(8)]
        quotes = [{"code": f"S{i}", "change_pct": -2.0} for i in range(8)]
        result = _estimate_nav_impact(holdings, quotes)
        assert len(result["draggers"]) == 5
        assert result["contributors"] == []

    def test_note_includes_holding_count(self):
        holdings = [{"code": "A", "name": "A", "weight": 10}]
        quotes = [{"code": "A", "change_pct": 1.0}]
        result = _estimate_nav_impact(holdings, quotes)
        assert "1" in result["note"]


class TestGetFundHoldingsCaching:
    """get_fund_holdings 缓存写入补充"""

    def test_success_writes_cache(self):
        """抓取成功后应写缓存(ttl=12h)"""
        holdings = [{"code": "600519", "name": "贵州茅台", "weight": 9.85}]
        with patch.object(fund_holdings.ds2, "_cache_get", return_value=None), \
             patch.object(fund_holdings, "_fetch_fund_holdings_em", return_value=holdings), \
             patch.object(fund_holdings.ds2, "_cache_set") as mock_set:
            result = get_fund_holdings("110022")
            assert result == holdings
            mock_set.assert_called_once()
            args, kwargs = mock_set.call_args
            assert kwargs.get("ttl") == 12 * 3600  # ttl

    def test_em_empty_falls_back_to_ak(self):
        """em 返回空 → 调用 ak,ak 返回数据"""
        ak_holdings = [{"code": "000858", "name": "五粮液", "weight": 7.5}]
        with patch.object(fund_holdings.ds2, "_cache_get", return_value=None), \
             patch.object(fund_holdings, "_fetch_fund_holdings_em", return_value=[]), \
             patch.object(fund_holdings, "_fetch_fund_holdings_ak", return_value=ak_holdings) as mock_ak, \
             patch.object(fund_holdings.ds2, "_cache_set"):
            result = get_fund_holdings("110022")
            assert result == ak_holdings
            mock_ak.assert_called_once_with("110022")


# ===== 补充分支测试 =====


class TestInferSectorExtended:
    """_infer_sector 更多板块分支"""

    def test_real_estate_sector(self):
        assert _infer_sector("万科地产") == "房地产"
        assert _infer_sector("保利置业") == "房地产"

    def test_securities_sector(self):
        assert _infer_sector("中信证券") == "证券"
        assert _infer_sector("海通券商") == "证券"

    def test_insurance_sector(self):
        assert _infer_sector("中国平安保险") == "保险"

    def test_coal_sector(self):
        assert _infer_sector("中国煤炭") == "煤炭"

    def test_steel_sector(self):
        assert _infer_sector("宝钢钢铁") == "钢铁"

    def test_new_energy_sector(self):
        assert _infer_sector("宁德时代") == "电池"
        assert _infer_sector("比亚迪") == "汽车整车"
        assert _infer_sector("隆基股份") == "光伏"

    def test_military_sector(self):
        assert _infer_sector("中航沈飞") == "军工"

    def test_food_beverage_sector(self):
        assert _infer_sector("伊利乳业") == "食品饮料"
        assert _infer_sector("青岛啤酒") == "酿酒行业"

    def test_empty_string_returns_empty(self):
        assert _infer_sector("") == ""


class TestMapHoldingsEdgeCases:
    """_map_holdings_to_sectors 边界补充"""

    def test_missing_name_field_skipped(self):
        """缺 name 字段 : 跳过该股"""
        holdings = [
            {"code": "600519", "weight": 9.85},  # 无 name
            {"code": "000858", "name": "五粮液", "weight": 7.23},
        ]
        result = _map_holdings_to_sectors(holdings)
        assert len(result) == 1
        assert result[0]["sector"] == "白酒"

    def test_missing_weight_defaults_zero(self):
        """缺 weight 字段 : 视为 0"""
        holdings = [{"code": "600519", "name": "贵州茅台"}]
        result = _map_holdings_to_sectors(holdings)
        assert len(result) == 1
        assert result[0]["total_weight"] == 0.0

    def test_all_unknown_stocks_returns_empty(self):
        """全部为未知股票 : 返回空列表"""
        holdings = [
            {"code": "999999", "name": "未知ABC", "weight": 5.0},
            {"code": "888888", "name": "未知XYZ", "weight": 3.0},
        ]
        result = _map_holdings_to_sectors(holdings)
        assert result == []

    def test_weight_rounded_to_three_decimals(self):
        """权重应四舍五入到3位小数"""
        holdings = [
            {"code": "A", "name": "贵州茅台", "weight": 9.851234},
            {"code": "B", "name": "五粮液", "weight": 7.229999},
        ]
        result = _map_holdings_to_sectors(holdings)
        assert result[0]["total_weight"] == pytest.approx(17.081, abs=0.001)


class TestSectorRotationSignalExtended:
    """_build_sector_rotation_signal 补充分支"""

    def test_top_rank_negative_change_returns_anti_fall(self):
        """前20% 但 change_pct <= 0 : 抗跌"""
        sector_exposure = [{"sector": "白酒", "total_weight": 30}]
        # 10个板块,白酒排第1但跌幅最大
        sector_ranking = [
            {"name": "白酒", "change_pct": -0.3},  # 排第1(最抗跌)
        ] + [{"name": f"板块{i}", "change_pct": -1.5} for i in range(9)]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        # 排第1(前20%),change_pct=-0.3 < 0 : 抗跌
        assert result[0]["rank"] == 1
        assert result[0]["signal"] == "抗跌"

    def test_bottom_rank_positive_change_returns_stagnant(self):
        """后20% 但 change_pct >= 0 : 滞涨"""
        sector_exposure = [{"sector": "房地产", "total_weight": 20}]
        # 10个板块,房地产排最后但微涨
        sector_ranking = [
            {"name": f"板块{i}", "change_pct": 3.0} for i in range(9)
        ] + [{"name": "房地产", "change_pct": 0.2}]  # 排最后(涨幅最小)
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        # 排第10(后20%),change_pct=0.2 >= 0 : 滞涨
        assert result[0]["rank"] == 10
        assert result[0]["signal"] == "滞涨"

    def test_single_sector_total_sectors(self):
        """仅1个板块 : rank=1,因 1//5=0 前20%不满足,而 1*4//5=0 后20%满足(rank>=0) : 滞涨"""
        sector_exposure = [{"sector": "白酒", "total_weight": 30}]
        sector_ranking = [{"name": "白酒", "change_pct": 2.0}]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        assert result[0]["rank"] == 1
        assert result[0]["total_sectors"] == 1
        # 单板块时 rank=1, 前20%条件 rank<=0 不满足(1<=0为False)
        # 后20%条件 rank>=0 满足(1>=0为True), change_pct=2.0>=0 : 滞涨
        assert result[0]["signal"] == "滞涨"

    def test_partial_sector_exposure_empty_ranking(self):
        """sector_exposure 非空但 sector_ranking 为空 : 返回空列表"""
        sector_exposure = [{"sector": "白酒", "total_weight": 30}]
        result = _build_sector_rotation_signal(sector_exposure, [])
        assert result == []

    def test_weight_and_change_pct_in_result(self):
        """结果应包含 weight 和 change_pct 字段"""
        sector_exposure = [{"sector": "白酒", "total_weight": 25.5}]
        sector_ranking = [{"name": "白酒", "change_pct": 1.8}]
        result = _build_sector_rotation_signal(sector_exposure, sector_ranking)
        assert result[0]["weight"] == 25.5
        assert result[0]["change_pct"] == 1.8


class TestFetchFundHoldingsEmExtended:
    """_fetch_fund_holdings_em 补充分支"""

    def test_unescape_double_quote_sequence(self):
        """content 中的反斜杠引号应被反转义为普通引号"""
        # 构造含 \\" 的 content(用单引号包裹 HTML 避免破坏提取)
        html_content = '<table><tbody><tr><td>1</td><td>600519</td><td>测试\\"引号</td><td>9.85%</td><td>1</td><td>1</td><td>Q</td><td>不变</td></tr></tbody></table>'
        text = f'var apidata={{ content:"{html_content}", arryear:[2026] }};'
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(text)):
            result = _fetch_fund_holdings_em("110022")
        assert len(result) == 1
        assert result[0]["name"] == '测试"引号'

    def test_weight_with_percentage_sign(self):
        """weight 带 % 后缀应正确解析"""
        html = "<table><tbody>" + _em_row(1, "600519", "贵州茅台", "9.85%", "100", "100000", "Q", "不变") + "</tbody></table>"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result[0]["weight"] == 9.85

    def test_hold_amount_with_commas(self):
        """持股数带千分位逗号应正确解析"""
        html = "<table><tbody>" + _em_row(1, "600519", "贵州茅台", "9.85", "1,234,567", "9,876,543", "Q", "不变") + "</tbody></table>"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result[0]["hold_amount"] == 1234567.0
        assert result[0]["hold_value"] == 9876543.0

    def test_empty_html_returns_empty(self):
        """空 HTML content 返回空列表"""
        text = 'var apidata={ content:"", arryear:[2026] };'
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(text)):
            result = _fetch_fund_holdings_em("110022")
        assert result == []

    def test_only_header_row_returns_empty(self):
        """仅表头无数据行返回空列表"""
        html = "<table><tbody><tr><th>序号</th><th>代码</th></tr></tbody></table>"
        with patch.object(fund_holdings._EM_FUND_SESSION, "get", _mock_em_get(_em_text(html))):
            result = _fetch_fund_holdings_em("110022")
        assert result == []


class TestGetFundHoldingsExtended:
    """get_fund_holdings 补充分支"""

    def test_both_em_and_ak_empty_returns_empty(self):
        """em 和 ak 都返回空 : 返回空列表并缓存"""
        with patch.object(fund_holdings.ds2, "_cache_get", return_value=None), \
             patch.object(fund_holdings, "_fetch_fund_holdings_em", return_value=[]), \
             patch.object(fund_holdings, "_fetch_fund_holdings_ak", return_value=[]), \
             patch.object(fund_holdings.ds2, "_cache_set") as mock_set:
            result = get_fund_holdings("110022")
            assert result == []
            # 即使为空也应缓存(避免频繁请求)
            mock_set.assert_called_once()

    def test_em_returns_data_skips_ak(self):
        """em 返回数据 : 不调用 ak"""
        em_holdings = [{"code": "600519", "name": "贵州茅台", "weight": 9.85}]
        with patch.object(fund_holdings.ds2, "_cache_get", return_value=None), \
             patch.object(fund_holdings, "_fetch_fund_holdings_em", return_value=em_holdings), \
             patch.object(fund_holdings, "_fetch_fund_holdings_ak") as mock_ak, \
             patch.object(fund_holdings.ds2, "_cache_set"):
            result = get_fund_holdings("110022")
            assert result == em_holdings
            mock_ak.assert_not_called()


class TestAnalyzeFundHoldingsExtended:
    """analyze_fund_holdings 补充分支"""

    @pytest.mark.asyncio
    async def test_empty_stock_codes_no_quotes(self):
        """重仓股无 code : 不抓取行情,nav_impact 为默认"""
        mock_holdings = [
            {"code": "", "name": "未知", "weight": 5.0},
        ]
        with patch.object(fund_holdings, "get_fund_holdings", return_value=mock_holdings):
            with patch.object(fund_holdings.ds2, "get_realtime_quote_tencent") as mock_quote:
                with patch.object(fund_holdings.ds2, "get_sector_ranking", return_value=[]):
                    with patch.object(fund_holdings.ds2, "_parallel_fetch", return_value={"quotes": [], "sectors": []}):
                        result = await analyze_fund_holdings("110022")
        # 无有效 code : 不调用行情接口
        assert result["nav_impact"]["estimated_change_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_result_contains_update_time(self):
        """结果应包含 update_time 字段"""
        mock_holdings = [
            {"code": "600519", "name": "贵州茅台", "weight": 9.85},
        ]
        with patch.object(fund_holdings, "get_fund_holdings", return_value=mock_holdings):
            with patch.object(fund_holdings.ds2, "_parallel_fetch", return_value={"quotes": [], "sectors": []}):
                result = await analyze_fund_holdings("110022")
        assert "update_time" in result
        assert len(result["update_time"]) > 0

    @pytest.mark.asyncio
    async def test_result_contains_fund_code(self):
        """结果应包含 fund_code 字段"""
        with patch.object(fund_holdings, "get_fund_holdings", return_value=[]):
            result = await analyze_fund_holdings("999999")
        assert result["fund_code"] == "999999"

    @pytest.mark.asyncio
    async def test_diagnostic_source_unreachable(self):
        """P0 诊断:数据源不可达时返回 source_unreachable=True 与准确 hint"""
        with patch.object(fund_holdings, "get_fund_holdings", return_value=[]):
            with patch.object(fund_holdings, "_get_fetch_reason",
                              return_value="em接口响应格式异常(resp_len=0, 可能被IP封禁或接口变更)"):
                result = await analyze_fund_holdings("161725")
        assert result["holdings"] == []
        assert "diagnostic" in result
        assert result["diagnostic"]["source_unreachable"] is True
        assert "数据源" in result["diagnostic"]["hint"]
        assert "迁移" in result["diagnostic"]["hint"]

    @pytest.mark.asyncio
    async def test_diagnostic_no_data_when_source_ok(self):
        """P0 诊断:数据源可达但基金无数据时 source_unreachable=False"""
        with patch.object(fund_holdings, "get_fund_holdings", return_value=[]):
            with patch.object(fund_holdings, "_get_fetch_reason", return_value=""):
                result = await analyze_fund_holdings("999999")
        assert result["holdings"] == []
        assert result["diagnostic"]["source_unreachable"] is False
        assert "债基" in result["diagnostic"]["hint"] or "新基金" in result["diagnostic"]["hint"]

    def test_fetch_em_sets_reason_on_parse_failure(self):
        """_fetch_fund_holdings_em 解析失败时设置 fetch reason(按 fund_code 隔离)"""
        with patch.object(fund_holdings._EM_FUND_SESSION, "get") as mock_get:
            mock_get.return_value.text = "<html>not apidata here</html>"
            mock_get.return_value.status_code = 200
            fund_holdings._set_fetch_reason("161725", "")  # 清空:模拟新一轮抓取
            result = _fetch_fund_holdings_em("161725")
        assert result == []
        reason = fund_holdings._get_fetch_reason("161725")
        assert reason  # 非空
        assert "em接口" in reason

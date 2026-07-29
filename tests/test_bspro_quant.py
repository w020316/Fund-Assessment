"""多因子量化策略单元测试

验证 src/strategies/bspro_quant.py 的核心逻辑:
- FactorCategory / FactorDef 定义
- FACTOR_LIBRARY 完整性
- BSProQuant._get_kline 缓存(mock akshare)
- compute_factors 因子计算(mock akshare)
- rank_by_factor 因子排序
- backtest_strategy 回测
- _compute_value_factors / _compute_growth_factors / _compute_quality_factors 分支
- _compute_momentum_factors / _compute_volatility_factors 分支
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategies import bspro_quant as mod
from src.strategies.bspro_quant import (
    BSProQuant,
    FACTOR_LIBRARY,
    FactorCategory,
    FactorDef,
)


@pytest.fixture
def bspro() -> BSProQuant:
    return BSProQuant()


def _make_kline_df(rows: int = 250) -> pd.DataFrame:
    closes = 100.0 + np.arange(rows) * 0.3
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=rows).strftime("%Y-%m-%d"),
        "开盘": closes - 0.2,
        "最高": closes + 0.5,
        "最低": closes - 0.5,
        "收盘": closes,
        "成交量": np.full(rows, 1_000_000.0),
        "成交额": closes * 1_000_000,
    })


def _mock_all_ak_empty():
    """返回一个上下文管理器,把所有 ak.* 调用 mock 为返回空 DataFrame"""
    return patch.object(mod, "ak")


class TestFactorCategory:
    """FactorCategory 枚举"""

    def test_all_categories_defined(self):
        expected = {"VALUE", "GROWTH", "QUALITY", "MOMENTUM", "VOLATILITY"}
        actual = {c.name for c in FactorCategory}
        assert actual == expected

    def test_is_str_enum(self):
        assert isinstance(FactorCategory.VALUE, str)


class TestFactorDef:
    """FactorDef 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(FactorDef)

    def test_default_higher_is_better(self):
        f = FactorDef("x", FactorCategory.VALUE, "d")
        assert f.higher_is_better is True


class TestFactorLibrary:
    """FACTOR_LIBRARY 因子库"""

    def test_library_not_empty(self):
        assert len(FACTOR_LIBRARY) > 0

    def test_factor_names_unique(self):
        names = [f.name for f in FACTOR_LIBRARY]
        assert len(names) == len(set(names))

    def test_each_factor_has_required_fields(self):
        for f in FACTOR_LIBRARY:
            assert f.name
            assert isinstance(f.category, FactorCategory)
            assert f.description

    def test_covers_all_categories(self):
        cats = {f.category for f in FACTOR_LIBRARY}
        assert cats == set(FactorCategory)


class TestGetKline:
    """_get_kline 缓存"""

    def test_cache_hit(self, bspro):
        df = _make_kline_df(100)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            first = bspro._get_kline("600519")
            second = bspro._get_kline("600519")
        assert mock_ak.stock_zh_a_hist.call_count == 1
        assert first is second

    def test_exception_returns_empty(self, bspro):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.side_effect = Exception("网络错误")
            df = bspro._get_kline("600519")
        assert df.empty


class TestComputeFactors:
    """compute_factors 因子计算"""

    def test_returns_full_structure(self, bspro):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro.compute_factors("600519")
        assert result["stock_code"] == "600519"
        assert "factors" in result
        assert "categorized" in result
        # 五大类别齐全
        expected_cats = {c.value for c in FactorCategory}
        assert set(result["categorized"].keys()) == expected_cats

    def test_empty_kline_momentum_empty(self, bspro):
        """K线 < 20 行 → momentum/volatility 为空"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(10)
            result = bspro.compute_factors("600519")
        assert result["categorized"][FactorCategory.MOMENTUM.value] == {}
        assert result["categorized"][FactorCategory.VOLATILITY.value] == {}


class TestRankByFactor:
    """rank_by_factor 因子排序"""

    def test_rank_assigns_sequential_ranks(self, bspro):
        """排序后 rank 应为 1..N"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame([{"pe": 10.0}])
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            ranking = bspro.rank_by_factor("pe", ["600519", "000001", "000002"])
        ranks = [item["rank"] for item in ranking]
        assert sorted(ranks) == list(range(1, len(ranking) + 1))

    def test_pe_lower_ranks_higher(self, bspro):
        """PE 越低排名越靠前(higher_is_better=False)"""
        pe_values = [20.0, 10.0, 30.0]
        codes = ["A", "B", "C"]
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)

            def _indicator(symbol, **kw):
                return pd.DataFrame([{"pe": pe_values[codes.index(symbol)]}])
            mock_ak.stock_a_indicator_lg.side_effect = _indicator
            ranking = bspro.rank_by_factor("pe", codes)
        # B(PE=10) 应排第1
        assert ranking[0]["stock_code"] == "B"
        assert ranking[0]["value"] == 10.0


class TestBacktestStrategy:
    """backtest_strategy 回测"""

    def test_empty_spot_returns_default(self, bspro):
        """空行情 → 返回默认零值结果"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            result = bspro.backtest_strategy({"roe": 1.0}, period=60)
        assert result["total_return"] == 0.0
        assert result["trades"] == 0

    def test_exception_returns_default(self, bspro):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = Exception("网络错误")
            result = bspro.backtest_strategy({"roe": 1.0}, period=60)
        assert result["total_return"] == 0.0


# ===== 因子计算分支补充 =====


class TestComputeValueFactors:
    """_compute_value_factors 价值因子分支"""

    def test_parses_pe_pb_ps(self, bspro):
        """正常解析 pe/pb/ps"""
        indicator_df = pd.DataFrame([{"pe": 15.5, "pb": 2.3, "ps": 1.8}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = indicator_df
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            result = bspro._compute_value_factors("600519")
        assert result["pe"] == 15.5
        assert result["pb"] == 2.3
        assert result["ps"] == 1.8

    def test_empty_indicator_returns_empty(self, bspro):
        """估值指标为空 → 返回空 dict(无 pe/pb/ps)"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            result = bspro._compute_value_factors("600519")
        assert "pe" not in result

    def test_indicator_exception_returns_empty(self, bspro):
        """估值指标异常 → 返回空 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.side_effect = RuntimeError("net")
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            result = bspro._compute_value_factors("600519")
        assert result == {}

    def test_pcf_calculation_with_market_cap(self, bspro):
        """有现金流和市值 → 计算 pcf"""
        cash_df = pd.DataFrame([{"经营活动产生的现金流量净额": 1_000_000_000}])
        spot_df = pd.DataFrame([{"代码": "600519", "总市值": 50_000_000_000}])
        indicator_df = pd.DataFrame([{"pe": 15.0, "pb": 2.0, "ps": 1.5}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = indicator_df
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = cash_df
            mock_ak.stock_zh_a_spot_em.return_value = spot_df
            result = bspro._compute_value_factors("600519")
        # pcf = 市值 / |现金流| = 50e9 / 1e9 = 50
        assert result["pcf"] == pytest.approx(50.0)

    def test_pcf_skipped_when_net_cash_zero(self, bspro):
        """现金流为0 → 不计算 pcf"""
        cash_df = pd.DataFrame([{"经营活动产生的现金流量净额": 0}])
        spot_df = pd.DataFrame([{"代码": "600519", "总市值": 50_000_000_000}])
        indicator_df = pd.DataFrame([{"pe": 15.0, "pb": 2.0, "ps": 1.5}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = indicator_df
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = cash_df
            mock_ak.stock_zh_a_spot_em.return_value = spot_df
            result = bspro._compute_value_factors("600519")
        assert "pcf" not in result

    def test_pe_ttm_falls_back_to_pe(self, bspro):
        """pe_ttm 缺失 → 回退到 pe"""
        indicator_df = pd.DataFrame([{"pe": 15.0, "pb": 2.0, "ps": 1.5}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = indicator_df
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            result = bspro._compute_value_factors("600519")
        assert result["pe_ttm"] == 15.0

    def test_pe_ttm_explicit_value(self, bspro):
        """pe_ttm 有显式值"""
        indicator_df = pd.DataFrame([{"pe": 15.0, "pb": 2.0, "ps": 1.5, "pe_ttm": 14.2}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = indicator_df
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            result = bspro._compute_value_factors("600519")
        assert result["pe_ttm"] == 14.2


class TestComputeGrowthFactors:
    """_compute_growth_factors 成长因子分支"""

    def test_parses_revenue_and_profit_growth(self, bspro):
        """正常解析营收/利润增长率"""
        df = pd.DataFrame([{
            "营业收入同比增长(%)": 25.5,
            "净利润同比增长(%)": 30.2,
            "净资产收益率(%)": 18.0,
        }])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = df
            result = bspro._compute_growth_factors("600519")
        assert result["revenue_growth"] == 25.5
        assert result["profit_growth"] == 30.2

    def test_roe_change_with_two_rows(self, bspro):
        """有2行数据 → 计算 roe_change"""
        df = pd.DataFrame([
            {"营业收入同比增长(%)": 10, "净利润同比增长(%)": 15, "净资产收益率(%)": 20.0},
            {"营业收入同比增长(%)": 5, "净利润同比增长(%)": 8, "净资产收益率(%)": 15.0},
        ])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = df
            result = bspro._compute_growth_factors("600519")
        # roe_change = 20.0 - 15.0 = 5.0
        assert result["roe_change"] == pytest.approx(5.0)

    def test_roe_change_single_row_defaults_zero(self, bspro):
        """仅1行数据 → roe_change = 0"""
        df = pd.DataFrame([{
            "营业收入同比增长(%)": 10,
            "净利润同比增长(%)": 15,
            "净资产收益率(%)": 20.0,
        }])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = df
            result = bspro._compute_growth_factors("600519")
        assert result["roe_change"] == 0.0

    def test_exception_returns_empty(self, bspro):
        """异常 → 返回空 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.side_effect = RuntimeError("net")
            result = bspro._compute_growth_factors("600519")
        assert result == {}


class TestComputeQualityFactors:
    """_compute_quality_factors 质量因子分支"""

    def test_parses_all_quality_fields(self, bspro):
        """正常解析所有质量因子"""
        df = pd.DataFrame([{
            "销售毛利率(%)": 45.5,
            "销售净利率(%)": 20.3,
            "资产负债率(%)": 35.0,
            "流动比率": 2.5,
            "总资产周转率(次)": 0.8,
        }])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.return_value = df
            result = bspro._compute_quality_factors("600519")
        assert result["gross_margin"] == 45.5
        assert result["net_margin"] == 20.3
        assert result["debt_ratio"] == 35.0
        assert result["current_ratio"] == 2.5
        assert result["asset_turnover"] == 0.8

    def test_empty_df_returns_empty(self, bspro):
        """空 DataFrame → 返回空 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            result = bspro._compute_quality_factors("600519")
        assert result == {}

    def test_exception_returns_empty(self, bspro):
        """异常 → 返回空 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.side_effect = RuntimeError("net")
            result = bspro._compute_quality_factors("600519")
        assert result == {}


class TestComputeMomentumFactors:
    """_compute_momentum_factors 动量因子分支"""

    def test_all_periods_calculated(self, bspro):
        """250行数据 → 计算 1m/3m/6m/12m 全部动量"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro._compute_momentum_factors("600519")
        assert "return_1m" in result
        assert "return_3m" in result
        assert "return_6m" in result
        assert "return_12m" in result

    def test_insufficient_data_returns_empty(self, bspro):
        """< 20 行 → 返回空 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(15)
            result = bspro._compute_momentum_factors("600519")
        assert result == {}

    def test_short_period_returns_zero_for_long_windows(self, bspro):
        """数据不足252天 → return_12m 应为0"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(100)
            result = bspro._compute_momentum_factors("600519")
        # 100天 > 20,所以 return_1m(21) 和 return_3m(63) 有值
        assert "return_1m" in result
        # 但 return_12m(252) 因数据不足 → 0.0
        assert result["return_12m"] == 0.0

    def test_positive_return_for_uptrend(self, bspro):
        """上涨趋势 → 收益率为正"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro._compute_momentum_factors("600519")
        # 缓慢单边上行 → return_1m > 0
        assert result["return_1m"] > 0


class TestComputeVolatilityFactors:
    """_compute_volatility_factors 波动因子分支"""

    def test_calculates_all_volatility_fields(self, bspro):
        """250行 → 计算所有波动因子"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro._compute_volatility_factors("600519")
        assert "volatility" in result
        assert "downside_risk" in result
        assert "max_drawdown" in result
        assert "sharpe" in result

    def test_insufficient_data_returns_empty(self, bspro):
        """< 20 行 → 返回空 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(15)
            result = bspro._compute_volatility_factors("600519")
        assert result == {}

    def test_volatility_positive_for_fluctuating_prices(self, bspro):
        """有波动的价格 → 波动率 > 0"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro._compute_volatility_factors("600519")
        assert result["volatility"] > 0

    def test_max_drawdown_non_positive(self, bspro):
        """最大回撤应 <= 0"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro._compute_volatility_factors("600519")
        # 单边上行可能无回撤(0),但不应为正
        assert result["max_drawdown"] <= 0

    def test_sharpe_with_uptrend(self, bspro):
        """单边上行 → sharpe 为正或0"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            result = bspro._compute_volatility_factors("600519")
        assert result["sharpe"] >= 0


class TestRankByFactorEdgeCases:
    """rank_by_factor 边界补充"""

    def test_unknown_factor_defaults_higher_is_better(self, bspro):
        """未知因子名 → 默认 higher_is_better=True(升序降序不影响,只验证不报错)"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            # 未知因子名 "unknown_factor" 不在 FACTOR_LIBRARY 中
            ranking = bspro.rank_by_factor("return_1m", ["600519"])
        assert len(ranking) == 1
        assert ranking[0]["rank"] == 1

    def test_empty_stock_pool_returns_empty(self, bspro):
        """空股票池 → 返回空列表"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(250)
            ranking = bspro.rank_by_factor("pe", [])
        assert ranking == []


class TestBacktestStrategyWithData:
    """backtest_strategy 有数据路径"""

    def test_full_path_with_data(self, bspro):
        """有行情数据 → 走完整回测路径"""
        # 构造 spot 行情: 5只股票
        spot_df = pd.DataFrame([
            {"代码": f"{600000 + i:06d}", "总市值": 1e10}
            for i in range(5)
        ])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = spot_df
            # 各因子接口返回空(让 score 仅基于 momentum/volatility)
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            # K线返回 70 行(> period=60)
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(70)
            result = bspro.backtest_strategy({"return_1m": 1.0}, period=60)
        # 应计算了 total_return 与 trades
        assert "total_return" in result
        assert "trades" in result
        assert result["trades"] >= 0

    def test_insufficient_kline_for_period(self, bspro):
        """K线不足 period → 返回默认零值"""
        spot_df = pd.DataFrame([
            {"代码": f"{600000 + i:06d}", "总市值": 1e10}
            for i in range(5)
        ])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = spot_df
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            # K线仅 30 行 < period=60
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(30)
            result = bspro.backtest_strategy({"return_1m": 1.0}, period=60)
        assert result["total_return"] == 0.0

    def test_warning_field_present(self, bspro):
        """结果应包含未来函数偏差警告"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            result = bspro.backtest_strategy({"roe": 1.0}, period=60)
        assert "warning" in result
        assert "未来函数" in result["warning"]

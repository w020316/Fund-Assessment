"""多因子量化策略单元测试

验证 src/strategies/bspro_quant.py 的核心逻辑:
- FactorCategory / FactorDef 定义
- FACTOR_LIBRARY 完整性
- BSProQuant._get_kline 缓存(mock akshare)
- compute_factors 因子计算(mock akshare)
- rank_by_factor 因子排序
- backtest_strategy 回测
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

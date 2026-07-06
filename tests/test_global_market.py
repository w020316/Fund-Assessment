"""国际股市数据单元测试

验证 src/core/data_source_v2.py 的国际股市函数:
- _tencent_global_quote:腾讯接口解析(字段索引/兜底)
- get_global_indices:国际指数(市场标记/币种补全)
- get_us_stock_realtime:美股(代码清洗/币种)
- get_hk_stock_realtime:港股(代码补齐 5 位/币种)
- get_global_market_overview:并行总览
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core import data_source_v2 as ds2


# 腾讯接口返回的模拟数据(美股 AAPL)
_MOCK_US_AAPL_RESPONSE = (
    'v_usAAPL="200~苹果~AAPL.OQ~308.63~294.38~294.12~75400626~0~0~308.44~200~0~0~0~0~0~0~0~0~308.47~40~0~0~0~0~0~0~0~0~~2026-07-02 16:00:01~14.25~4.84~309.42~293.68~USD~75400626~23114110202~";'
)

# 腾讯接口返回的模拟数据(港股 00700)
_MOCK_HK_00700_RESPONSE = (
    'v_hk00700="100~腾讯控股~00700~431.200~430.200~433.000~24957296.0~0~0~431.200~0~0~0~0~0~0~0~0~0~431.200~0~0~0~0~0~0~0~0~0~24957296.0~2026/07/03 16:08:18~1.000~0.23~445.800~431.200~431.200~24957296.0~10897851806.210~0~15.75~~0~0~3.39~39205.7166~39205.7166~TENCENT~1.23~677.700~411.000~0.71~57.81~0~0~0~0~0~14.72~3.11~0.27~100~-27.37~2.33~GP~20.59~11.53~-3.19~-7.55~-12.23~9092234841.00~9092234841.00~14.90~5.315~436.660~-35.47~HKD~1~50";'
)

# 腾讯接口返回的模拟数据(道琼斯指数)
_MOCK_DJI_RESPONSE = (
    'v_usDJI="200~道琼斯~.DJI~52900.07~52305.24~52395.22~548821105~0~0~52612.94~0~0~0~0~0~0~0~0~0~52992.32~0~0~0~0~0~0~0~0~0~~2026-07-02 16:42:50~594.83~1.14~52903.85~52395.22~USD~548821105~28931093155451~";'
)


class TestTencentGlobalQuote:
    """_tencent_global_quote 腾讯接口解析"""

    def test_parses_us_stock_correctly(self):
        """美股:正确解析字段"""
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = type("R", (), {
                "text": _MOCK_US_AAPL_RESPONSE,
                "encoding": "utf-8",
            })()
            mock_get.return_value = mock_resp
            # 清缓存
            ds2._cache.clear()
            result = ds2._tencent_global_quote(["usAAPL"])
        assert len(result) == 1
        item = result[0]
        assert item["name"] == "苹果"
        assert item["price"] == pytest.approx(308.63)
        assert item["prev_close"] == pytest.approx(294.38)
        assert item["change"] == pytest.approx(14.25)
        assert item["change_pct"] == pytest.approx(4.84)
        assert item["high"] == pytest.approx(309.42)
        assert item["low"] == pytest.approx(293.68)
        assert item["currency"] == "USD"

    def test_parses_hk_stock_correctly(self):
        """港股:正确解析字段"""
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = type("R", (), {
                "text": _MOCK_HK_00700_RESPONSE,
                "encoding": "utf-8",
            })()
            mock_get.return_value = mock_resp
            ds2._cache.clear()
            result = ds2._tencent_global_quote(["hk00700"])
        assert len(result) == 1
        item = result[0]
        assert item["name"] == "腾讯控股"
        assert item["price"] == pytest.approx(431.2)
        assert item["change_pct"] == pytest.approx(0.23)

    def test_empty_codes_returns_empty(self):
        """空代码列表返回空"""
        assert ds2._tencent_global_quote([]) == []

    def test_change_fallback_when_zero(self):
        """涨跌额为 0 时用 price-prev_close 兜底"""
        # 构造 change=0 但 price != prev_close 的数据
        resp = 'v_usTEST="200~测试~TEST~100.0~98.0~99.0~1000~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~~2026-01-01~0~0~101.0~97.0~USD~1000~0~";'
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = type("R", (), {"text": resp, "encoding": "utf-8"})()
            mock_get.return_value = mock_resp
            ds2._cache.clear()
            result = ds2._tencent_global_quote(["usTEST"])
        assert len(result) == 1
        # change 应该兜底为 100 - 98 = 2
        assert result[0]["change"] == pytest.approx(2.0)
        # change_pct 应该兜底为 (100-98)/98*100 ≈ 2.041
        assert result[0]["change_pct"] == pytest.approx(2.041, abs=0.01)

    def test_currency_numeric_is_cleared(self):
        """港股指数 parts[35] 是数字(价格)时应置空"""
        # 恒生指数的 parts[35] 是 23350.030(价格),不是币种
        resp = 'v_hkHSI="100~恒生指数~HSI~23350.030~23055.030~23240.850~30495384~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~~2026/07/03~295.000~1.28~23516.700~23226.200~23350.030~30495384~0~";'
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = type("R", (), {"text": resp, "encoding": "utf-8"})()
            mock_get.return_value = mock_resp
            ds2._cache.clear()
            result = ds2._tencent_global_quote(["hkHSI"])
        assert len(result) == 1
        # currency 应该被置空(因为 23350.030 是数字)
        assert result[0]["currency"] == ""


class TestGetGlobalIndices:
    """get_global_indices 国际指数"""

    def test_returns_list_with_market_tag(self):
        """返回的指数有 market 标记"""
        mock_resp_data = [
            {"code": "DJI", "name": "道琼斯", "price": 52900, "change_pct": 1.14, "currency": "USD"},
            {"code": "HSI", "name": "恒生指数", "price": 23350, "change_pct": 1.28, "currency": ""},
        ]
        ds2._cache.clear()
        with patch.object(ds2, "_tencent_global_quote", return_value=mock_resp_data):
            result = ds2.get_global_indices()
        assert len(result) == 2
        # 道琼斯 → US
        dji = next(r for r in result if "道琼斯" in r.get("name", ""))
        assert dji["market"] == "US"
        assert dji["currency"] == "USD"
        # 恒生 → HK + HKD 补全
        hsi = next(r for r in result if "恒生" in r.get("name", ""))
        assert hsi["market"] == "HK"
        assert hsi["currency"] == "HKD"

    def test_empty_response_returns_empty(self):
        """空响应返回空列表"""
        ds2._cache.clear()
        with patch.object(ds2, "_tencent_global_quote", return_value=[]):
            result = ds2.get_global_indices()
        assert result == []


class TestGetUsStockRealtime:
    """get_us_stock_realtime 美股"""

    def test_strips_exchange_suffix(self):
        """去掉交易所后缀(AAPL.OQ → AAPL)"""
        mock_data = [{"code": "AAPL.OQ", "name": "苹果", "price": 308.63, "currency": "USD"}]
        ds2._cache.clear()
        with patch.object(ds2, "_tencent_global_quote", return_value=mock_data):
            result = ds2.get_us_stock_realtime(["AAPL"])
        assert len(result) == 1
        assert result[0]["code"] == "AAPL"
        assert result[0]["market"] == "US"

    def test_empty_symbols_returns_empty(self):
        assert ds2.get_us_stock_realtime([]) == []

    def test_currency_defaults_to_usd(self):
        """币种为空时默认 USD"""
        mock_data = [{"code": "TSLA", "name": "特斯拉", "price": 393.45, "currency": ""}]
        ds2._cache.clear()
        with patch.object(ds2, "_tencent_global_quote", return_value=mock_data):
            result = ds2.get_us_stock_realtime(["TSLA"])
        assert result[0]["currency"] == "USD"


class TestGetHkStockRealtime:
    """get_hk_stock_realtime 港股"""

    def test_pads_code_to_5_digits(self):
        """代码补齐 5 位(700 → 00700)"""
        mock_data = [{"code": "00700", "name": "腾讯控股", "price": 431.2, "currency": "HKD"}]
        ds2._cache.clear()
        with patch.object(ds2, "_tencent_global_quote", return_value=mock_data):
            result = ds2.get_hk_stock_realtime(["00700"])
        assert len(result) == 1
        assert result[0]["code"] == "00700"
        assert result[0]["market"] == "HK"

    def test_empty_codes_returns_empty(self):
        assert ds2.get_hk_stock_realtime([]) == []

    def test_currency_defaults_to_hkd(self):
        mock_data = [{"code": "09988", "name": "阿里巴巴", "price": 94.1, "currency": ""}]
        ds2._cache.clear()
        with patch.object(ds2, "_tencent_global_quote", return_value=mock_data):
            result = ds2.get_hk_stock_realtime(["09988"])
        assert result[0]["currency"] == "HKD"


class TestGetGlobalMarketOverview:
    """get_global_market_overview 并行总览"""

    def test_aggregates_three_sources(self):
        """并行聚合指数 + 美股 + 港股"""
        ds2._cache.clear()
        mock_fetch_result = {
            "indices": [{"name": "道琼斯", "price": 52900}],
            "us_hot": [{"code": "AAPL", "price": 308}],
            "hk_hot": [{"code": "00700", "price": 431}],
        }
        with patch.object(ds2, "_parallel_fetch", return_value=mock_fetch_result):
            result = ds2.get_global_market_overview()
        assert len(result["indices"]) == 1
        assert len(result["us_hot"]) == 1
        assert len(result["hk_hot"]) == 1

    def test_handles_empty_fetch(self):
        """全部数据源失败时返回空列表"""
        ds2._cache.clear()
        with patch.object(ds2, "_parallel_fetch", return_value={}):
            result = ds2.get_global_market_overview()
        assert result["indices"] == []
        assert result["us_hot"] == []
        assert result["hk_hot"] == []

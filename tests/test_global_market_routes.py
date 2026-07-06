"""国际市场路由单元测试

验证 web/routes/global_market.py 的 6 个端点:
- GET /indices 国际指数
- GET /us_hot 美股热门
- GET /hk_hot 港股热门
- GET /us_realtime 美股实时
- GET /hk_realtime 港股实时
- GET /overview 并行总览

通过 mock data_source_v2 的网络函数 + 清文件缓存,避免真实网络请求与缓存干扰。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_global_cache():
    """每个测试前清空 global_market 路由的文件缓存,避免 mock 被跳过"""
    from web.routes import global_market
    global_market.cache.clear()
    yield
    global_market.cache.clear()


# 模拟数据(含路由 Pydantic 模型必需字段)
_MOCK_INDICES = [
    {"code": "DJI", "name": "道琼斯", "price": 52900.07, "prev_close": 52305.24,
     "change": 594.83, "change_pct": 1.14, "high": 52903.85, "low": 52395.22,
     "market": "us", "currency": "USD"},
    {"code": "HSI", "name": "恒生指数", "price": 23350.03, "prev_close": 23450.53,
     "change": -100.5, "change_pct": -0.43, "high": 23400.0, "low": 23300.0,
     "market": "hk", "currency": "HKD"},
]

_MOCK_US_HOT = [
    {"code": "AAPL", "name": "苹果", "price": 308.63, "prev_close": 294.38,
     "change": 14.25, "change_pct": 4.84, "high": 309.42, "low": 293.68,
     "volume": 75400626, "currency": "USD"},
]

_MOCK_HK_HOT = [
    {"code": "00700", "name": "腾讯控股", "price": 431.2, "prev_close": 430.2,
     "change": 1.0, "change_pct": 0.23, "high": 445.8, "low": 431.2,
     "volume": 24957296, "currency": "HKD"},
]


class TestIndices:
    """GET /api/global/indices"""

    def test_returns_indices_list(self, client):
        """返回国际指数列表"""
        with patch("web.routes.global_market.ds2.get_global_indices", return_value=_MOCK_INDICES):
            resp = client.get("/api/global/indices")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["code"] == "DJI"
        assert data["data"][0]["price"] == 52900.07

    def test_response_has_meta(self, client):
        """响应包含 _meta"""
        with patch("web.routes.global_market.ds2.get_global_indices", return_value=_MOCK_INDICES):
            resp = client.get("/api/global/indices")
        assert "_meta" in resp.json()
        assert resp.json()["_meta"]["data_source"] == "tencent"

    def test_empty_indices_returns_empty_list(self, client):
        """空数据返回空列表"""
        with patch("web.routes.global_market.ds2.get_global_indices", return_value=[]):
            resp = client.get("/api/global/indices")
        assert resp.json()["data"] == []


class TestUsHot:
    """GET /api/global/us_hot"""

    def test_returns_us_hot_stocks(self, client):
        """返回美股热门列表"""
        with patch("web.routes.global_market.ds2.get_us_hot_stocks", return_value=_MOCK_US_HOT):
            resp = client.get("/api/global/us_hot")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "AAPL"
        assert data["data"][0]["currency"] == "USD"


class TestHkHot:
    """GET /api/global/hk_hot"""

    def test_returns_hk_hot_stocks(self, client):
        """返回港股热门列表"""
        with patch("web.routes.global_market.ds2.get_hk_hot_stocks", return_value=_MOCK_HK_HOT):
            resp = client.get("/api/global/hk_hot")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "00700"
        assert data["data"][0]["currency"] == "HKD"


class TestUsRealtime:
    """GET /api/global/us_realtime?codes=AAPL"""

    def test_returns_us_realtime(self, client):
        """查询美股实时行情"""
        with patch("web.routes.global_market.ds2.get_us_stock_realtime", return_value=_MOCK_US_HOT):
            resp = client.get("/api/global/us_realtime", params={"codes": "AAPL"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "AAPL"

    def test_empty_codes_returns_empty(self, client):
        """空 codes 返回空列表(不报错)"""
        with patch("web.routes.global_market.ds2.get_us_stock_realtime", return_value=[]):
            resp = client.get("/api/global/us_realtime", params={"codes": ""})
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestHkRealtime:
    """GET /api/global/hk_realtime?codes=00700"""

    def test_returns_hk_realtime(self, client):
        """查询港股实时行情"""
        with patch("web.routes.global_market.ds2.get_hk_stock_realtime", return_value=_MOCK_HK_HOT):
            resp = client.get("/api/global/hk_realtime", params={"codes": "00700"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "00700"


class TestOverview:
    """GET /api/global/overview"""

    def test_returns_overview_with_all_sections(self, client):
        """总览包含 indices/us_hot/hk_hot 三部分"""
        with patch("web.routes.global_market.ds2.get_global_market_overview",
                   return_value={"indices": _MOCK_INDICES, "us_hot": _MOCK_US_HOT, "hk_hot": _MOCK_HK_HOT}):
            resp = client.get("/api/global/overview")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "indices" in data
        assert "us_hot" in data
        assert "hk_hot" in data
        assert len(data["indices"]) == 2
        assert len(data["us_hot"]) == 1
        assert len(data["hk_hot"]) == 1

    def test_overview_empty_when_all_fail(self, client):
        """所有数据源失败时返回空结构"""
        with patch("web.routes.global_market.ds2.get_global_market_overview",
                   return_value={"indices": [], "us_hot": [], "hk_hot": []}):
            resp = client.get("/api/global/overview")
        data = resp.json()["data"]
        assert data["indices"] == []
        assert data["us_hot"] == []
        assert data["hk_hot"] == []

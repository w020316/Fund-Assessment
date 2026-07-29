"""行情总览路由单元测试

验证 web/routes/market.py 的端点(选取代表性子集):
- GET /api/market/stock_realtime  股票实时行情
- GET /api/market/index_realtime  指数实时行情
- GET /api/market/hot_stocks      涨跌/成交排行
- GET /api/market/sector_flow     板块资金流
- GET /api/market/heatmap         市场热力图
- GET /api/market/status          市场开闭盘状态
- GET /api/market/search          股票搜索
- GET /api/market/northbound      北向资金
- GET /api/market/thermometer     大盘温度计

通过 mock data_source_v2(ds2) 与 market_assessment 的函数 + 清文件缓存,避免真实网络请求。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_market_cache():
    """每个测试前清空 market 路由的文件缓存,避免 mock 被跳过"""
    from web.routes import market
    market.cache.clear()
    yield
    market.cache.clear()


@pytest.fixture(autouse=True)
def mock_validator():
    """mock 数据质量校验器,避免校验逻辑干扰路由测试"""
    mock_v = MagicMock()
    mock_v.validate_quote.return_value = MagicMock(quality_score=80.0)
    mock_v.validate_kline.return_value = MagicMock(quality_score=80.0)
    mock_v.validate_analysis_data.return_value = MagicMock(quality_score=80.0)
    with patch("web.routes.market.get_data_validator", return_value=mock_v):
        yield


# 模拟行情数据
_MOCK_STOCK_QUOTE = [
    {"code": "600519", "name": "贵州茅台", "price": 1688.0, "change": 18.5,
     "change_pct": 1.11, "volume": 32567890, "amount": 5498765432.0,
     "high": 1695.0, "low": 1670.0, "open": 1675.0, "prev_close": 1669.5},
]

_MOCK_INDEX = [
    {"code": "000001", "name": "上证指数", "price": 3200.0, "change": 15.0,
     "change_pct": 0.47, "volume": 350000000, "amount": 450000000000.0},
]

_MOCK_RANKING = [
    {"code": "600519", "name": "贵州茅台", "price": 1688.0,
     "change_pct": 5.2, "volume": 32567890, "amount": 5498765432.0},
]

_MOCK_SECTOR = [
    {"name": "白酒", "change_pct": 2.5, "main_net_inflow": 50000000, "main_inflow_pct": 52.6},
    {"name": "半导体", "change_pct": -1.2, "main_net_inflow": -30000000, "main_inflow_pct": 48.0},
]

_MOCK_SEARCH = [
    {"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": 1.11},
]

_MOCK_NORTHBOUND = {
    "date": "2026-07-29",
    "total_net_inflow": 80000000.0,
    "sh_net_inflow": 50000000.0,
    "sz_net_inflow": 30000000.0,
}

_MOCK_THERMOMETER = {
    "score": 72,
    "label": "偏热",
    "trend": "上行",
    "components": {"index_trend": 75, "sector_rotation": 68, "capital_flow": 70, "sentiment": 75},
}


class TestStockRealtime:
    """GET /api/market/stock_realtime"""

    def test_returns_realtime_quotes(self, client):
        """返回股票实时行情"""
        with patch("web.routes.market.ds2.get_realtime_quote_tencent",
                   return_value=_MOCK_STOCK_QUOTE):
            resp = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "600519"
        assert data["data"][0]["price"] == 1688.0
        assert data["_meta"]["data_source"] == "tencent"

    def test_cached_on_second_call(self, client):
        """第二次调用命中缓存"""
        mock_fn = MagicMock(return_value=_MOCK_STOCK_QUOTE)
        with patch("web.routes.market.ds2.get_realtime_quote_tencent", new=mock_fn):
            first = client.get("/api/market/stock_realtime", params={"codes": "600519"})
            second = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert first.json()["_meta"]["cached"] is False
        assert second.json()["_meta"]["cached"] is True
        assert mock_fn.call_count == 1


class TestIndexRealtime:
    """GET /api/market/index_realtime"""

    def test_returns_index_data(self, client):
        """返回指数实时行情"""
        with patch("web.routes.market.ds2.get_index_realtime", return_value=_MOCK_INDEX):
            resp = client.get("/api/market/index_realtime")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "000001"
        assert data["data"][0]["name"] == "上证指数"
        assert data["_meta"]["data_source"] == "eastmoney"


class TestHotStocks:
    """GET /api/market/hot_stocks"""

    def test_returns_three_rankings(self, client):
        """返回涨跌/成交三个排行"""
        with patch("web.routes.market.ds2.get_stock_ranking_em",
                   side_effect=[_MOCK_RANKING, _MOCK_RANKING, _MOCK_RANKING]):
            resp = client.get("/api/market/hot_stocks")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "top_gainers" in data
        assert "top_losers" in data
        assert "top_volume" in data
        assert len(data["top_gainers"]) == 1
        assert data["top_gainers"][0]["code"] == "600519"


class TestSectorFlow:
    """GET /api/market/sector_flow"""

    def test_returns_sector_flow(self, client):
        """返回板块资金流数据"""
        with patch("web.routes.market.ds2.get_sector_ranking", return_value=_MOCK_SECTOR):
            resp = client.get("/api/market/sector_flow")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["sector"] == "白酒"
        assert data["data"][0]["main_net_inflow"] == 50000000


class TestHeatmap:
    """GET /api/market/heatmap"""

    def test_heatmap_generates_colors(self, client):
        """涨跌生成对应颜色(红涨绿跌)"""
        with patch("web.routes.market.ds2.get_sector_ranking", return_value=_MOCK_SECTOR):
            resp = client.get("/api/market/heatmap")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 2
        # 白酒上涨 → 红色 rgb(r,0,0)
        assert items[0]["color"].startswith("rgb(")
        assert "255,0,0" in items[0]["color"] or items[0]["color"].startswith("rgb(6")
        # 半导体下跌 → 绿色 rgb(0,g,0)
        assert items[1]["color"].startswith("rgb(0,")


class TestMarketStatus:
    """GET /api/market/status"""

    def test_status_returns_fields(self, client):
        """返回市场状态结构"""
        resp = client.get("/api/market/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "is_open" in data
        assert "session" in data
        assert "current_time" in data
        assert isinstance(data["is_open"], bool)
        assert data["session"] in ("closed", "morning", "afternoon", "lunch_break", "weekend")
        assert resp.json()["_meta"]["data_source"] == "local"


class TestStockSearch:
    """GET /api/market/search"""

    def test_search_returns_results(self, client):
        """股票搜索返回结果"""
        with patch("web.routes.market.ds2.search_stock", return_value=_MOCK_SEARCH):
            resp = client.get("/api/market/search", params={"q": "茅台"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "600519"
        assert data["data"][0]["name"] == "贵州茅台"


class TestNorthbound:
    """GET /api/market/northbound"""

    def test_returns_northbound_flow(self, client):
        """返回北向资金数据"""
        with patch("web.routes.market.ds2.get_northbound_flow_realtime",
                   return_value=_MOCK_NORTHBOUND):
            resp = client.get("/api/market/northbound")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["date"] == "2026-07-29"
        assert data["total_net_inflow"] == 80000000.0
        assert data["sh_net_inflow"] == 50000000.0

    def test_northbound_empty_returns_default(self, client):
        """无数据时返回默认空结构"""
        with patch("web.routes.market.ds2.get_northbound_flow_realtime", return_value=None):
            resp = client.get("/api/market/northbound")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_net_inflow"] == 0.0
        assert resp.json()["_meta"]["quality_score"] == 0.0


class TestThermometer:
    """GET /api/market/thermometer"""

    def test_returns_thermometer(self, client):
        """返回大盘温度计"""
        with patch("src.analysis.market_assessment.get_market_thermometer",
                   new=AsyncMock(return_value=_MOCK_THERMOMETER)):
            resp = client.get("/api/market/thermometer")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["score"] == 72
        assert data["label"] == "偏热"
        assert resp.json()["_meta"]["data_source"] == "aggregator"

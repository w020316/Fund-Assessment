"""基金重仓股板块分析路由单元测试

验证 web/routes/holdings.py 的端点:
- GET /api/holdings/{fund_code}            基金重仓股分析(持仓/板块/集中度/净值影响)
- GET /api/holdings/sector-rotation/overview  板块轮动总览

通过 mock src.analysis.fund_holdings 的函数 + 清文件缓存,避免真实网络请求与缓存干扰。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_holdings_cache():
    """每个测试前清空 holdings 路由的文件缓存,避免 mock 被跳过"""
    from web.routes import holdings
    holdings.cache.clear()
    yield
    holdings.cache.clear()


# 模拟重仓股分析结果
_MOCK_HOLDINGS = {
    "fund_code": "110022",
    "fund_name": "易方达消费",
    "holdings": [
        {"code": "600519", "name": "贵州茅台", "weight": 9.5, "change_pct": 1.2},
        {"code": "000858", "name": "五粮液", "weight": 8.3, "change_pct": 0.8},
    ],
    "sector_exposure": {"白酒": 35.0, "食品": 15.0},
    "concentration": {"top5": 42.0, "top10": 65.0, "hhi": 850},
    "nav_impact": 0.45,
    "sector_rotation": {"signal": "白酒走强", "strength": "中"},
}

_MOCK_SECTOR_ROTATION = {
    "sectors": [
        {"name": "白酒", "change_pct": 2.5, "trend": "up", "main_net_inflow": 50000000},
        {"name": "半导体", "change_pct": -1.2, "trend": "down", "main_net_inflow": -30000000},
    ],
    "rotation_signal": "白酒板块资金流入,半导体流出",
    "top_sectors": ["白酒", "新能源"],
}


class TestFundHoldings:
    """GET /api/holdings/{fund_code}"""

    def test_returns_holdings_analysis(self, client):
        """返回重仓股分析结果"""
        with patch("web.routes.holdings.analyze_fund_holdings",
                   new=AsyncMock(return_value=_MOCK_HOLDINGS)):
            resp = client.get("/api/holdings/110022")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["fund_code"] == "110022"
        assert len(data["data"]["holdings"]) == 2
        assert data["data"]["concentration"]["top5"] == 42.0
        assert data["_meta"]["data_source"] == "em_fund+akshare"
        assert data["_meta"]["cached"] is False

    def test_response_has_quality_score(self, client):
        """未缓存时质量评分为 80"""
        with patch("web.routes.holdings.analyze_fund_holdings",
                   new=AsyncMock(return_value=_MOCK_HOLDINGS)):
            resp = client.get("/api/holdings/110022")
        assert resp.json()["_meta"]["quality_score"] == 80.0

    def test_cache_second_call_is_cached(self, client):
        """第二次调用命中缓存,质量评分 100"""
        mock_fn = AsyncMock(return_value=_MOCK_HOLDINGS)
        with patch("web.routes.holdings.analyze_fund_holdings", new=mock_fn):
            first = client.get("/api/holdings/110022")
            second = client.get("/api/holdings/110022")
        assert first.json()["_meta"]["cached"] is False
        assert second.json()["_meta"]["cached"] is True
        assert second.json()["_meta"]["quality_score"] == 100.0
        # mock 只被调用一次(第二次命中缓存)
        assert mock_fn.call_count == 1

    def test_refresh_bypasses_cache(self, client):
        """refresh=True 时忽略缓存并重新获取"""
        mock_fn = AsyncMock(return_value=_MOCK_HOLDINGS)
        with patch("web.routes.holdings.analyze_fund_holdings", new=mock_fn):
            client.get("/api/holdings/110022")
            client.get("/api/holdings/110022", params={"refresh": True})
        # refresh 强制刷新,mock 应被调用两次
        assert mock_fn.call_count == 2


class TestSectorRotation:
    """GET /api/holdings/sector-rotation/overview"""

    def test_returns_sector_rotation(self, client):
        """返回板块轮动总览"""
        with patch("web.routes.holdings.get_sector_rotation",
                   new=AsyncMock(return_value=_MOCK_SECTOR_ROTATION)):
            resp = client.get("/api/holdings/sector-rotation/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["sectors"]) == 2
        assert data["data"]["rotation_signal"] == "白酒板块资金流入,半导体流出"
        assert data["_meta"]["data_source"] == "em_sector_ranking"

    def test_sector_rotation_cached(self, client):
        """板块轮动总览也走缓存"""
        mock_fn = AsyncMock(return_value=_MOCK_SECTOR_ROTATION)
        with patch("web.routes.holdings.get_sector_rotation", new=mock_fn):
            client.get("/api/holdings/sector-rotation/overview")
            client.get("/api/holdings/sector-rotation/overview")
        assert mock_fn.call_count == 1

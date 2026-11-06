"""R-03/R-04 回归:股票检索、个股详情、资金面入口与缺失值语义。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from web.api import app

client = TestClient(app)
INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def test_search_missing_price_returns_null():
    with patch("web.routes.market.ds2.search_stock", return_value=[
        {"code": "sh600519", "name": "贵州茅台"},
    ]):
        resp = client.get("/api/market/search?q=茅台")
    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["code"] == "sh600519"
    assert item["price"] is None
    assert item["change_pct"] is None


def test_capital_flow_entry_shape():
    payload = {
        "northbound": {"total_net_inflow": 12.3, "sh_net_inflow": 5.1, "sz_net_inflow": 7.2, "date": "2026-11-06"},
        "sector_main_flow": {
            "total_net_inflow": 3.4,
            "top_inflow": [{"name": "半导体", "change_pct": 2.1, "main_net_inflow": 1.2}],
            "top_outflow": [{"name": "白酒", "change_pct": -1.0, "main_net_inflow": -0.8}],
        },
        "update_time": "2026-11-06 15:00:00",
    }
    with patch("src.analysis.market_assessment.get_capital_flow_overview", new=AsyncMock(return_value=payload)):
        resp = client.get("/api/market/capital-flow")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["northbound"]["total_net_inflow"] == 12.3
    assert data["sector_main_flow"]["top_inflow"][0]["name"] == "半导体"


def test_frontend_has_search_and_capital_flow_entries():
    text = INDEX.read_text(encoding="utf-8")
    assert 'id="stockSearchInput"' in text
    assert 'id="stockDetailPanel"' in text
    assert "async function loadStockDetail" in text
    assert 'id="capitalFlowBody"' in text
    assert "async function loadMarketCapitalFlow" in text
    assert "loadMarketCapitalFlow()" in text


def test_assessment_entry_shape():
    payload = {
        "thermometer": {"score": 62.0, "level": "偏强", "note": ""},
        "assessment": "市场偏强，可适度参与",
        "sector_signal": "板块分化",
        "capital_signal": "北向资金小幅净流入",
        "update_time": "2026-11-06 15:00:00",
    }
    with patch("src.analysis.market_assessment.get_market_assessment", new=AsyncMock(return_value=payload)):
        resp = client.get("/api/market/assessment")
    assert resp.status_code == 200
    assert resp.json()["data"]["assessment"].startswith("市场偏强")


def test_wide_stats_and_sentiment_shape():
    with patch("web.routes.market.ds2.get_market_wide_stats", return_value={
        "margin_balance": 1.5e11, "block_trades_count": 12, "avg_shareholder_change_pct": -2.3,
    }), patch("web.routes.market.ds2.get_market_sentiment", return_value={
        "rise_count": 3200, "fall_count": 1800, "flat_count": 200, "sentiment": "偏多", "ratio": 0.615,
    }):
        wide = client.get("/api/market/market_wide_stats").json()["data"]
        sentiment = client.get("/api/market/market_sentiment").json()["data"]
    assert wide["margin_balance"] == 1.5e11
    assert sentiment["sentiment"] == "偏多"


def test_frontend_has_market_assessment_entry():
    text = INDEX.read_text(encoding="utf-8")
    assert 'id="marketAssessmentCard"' in text
    assert "async function loadMarketAssessment" in text
    assert "loadMarketAssessment()" in text


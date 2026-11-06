"""R-12 回归:股票话术匹配 / 生成 / 分类统计入口。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.analysis import script_library as sl
from web.api import app

client = TestClient(app)
INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def test_categories_returns_counts():
    resp = client.get("/api/scripts/categories")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["stock"]["count"] > 0
    assert data["total"] == data["stock"]["count"] + data["fund"]["count"]


def test_generate_returns_content():
    script_id = sl.list_scripts(category="stock")[0]["id"]
    resp = client.post("/api/scripts/generate", json={"script_id": script_id, "variables": {}})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == script_id
    assert data["content"]


def test_match_stock_returns_scripts():
    resp = client.post(
        "/api/scripts/match/stock",
        json={"stock_data": {"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": 1.5}},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data
    assert all(item["content"] for item in data)


def test_frontend_has_stock_script_entry():
    text = INDEX.read_text(encoding="utf-8")
    assert 'id="stockScriptCode"' in text
    assert "async function matchStockScripts" in text
    assert "async function generateScriptPreview" in text
    assert "async function loadScriptCategoryStats" in text
    assert 'id="scriptCategoryStats"' in text

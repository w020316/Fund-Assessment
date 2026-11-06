"""R-11 回归:策略参数读取/保存入口与前端扁平化编辑器。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from web.api import app
from web.routes import config as config_route

client = TestClient(app)
INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def test_strategies_get_returns_nested_config(monkeypatch):
    fake = {"trading_quant": {"weights": {"technical": 0.25}, "signals": {"buy": 65}}}
    monkeypatch.setattr(config_route, "_load_yaml", lambda _name: dict(fake))
    resp = client.get("/api/config/strategies")
    assert resp.status_code == 200
    assert resp.json()["strategies"]["trading_quant"]["signals"]["buy"] == 65


def test_strategies_put_deep_merges_and_saves(monkeypatch):
    existing = {"trading_quant": {"weights": {"technical": 0.25}}}
    saved = {}

    monkeypatch.setattr(config_route, "_load_yaml", lambda _name: existing)
    monkeypatch.setattr(config_route, "_save_yaml", lambda name, data: saved.update({name: data}))

    resp = client.put(
        "/api/config/strategies",
        json={"strategies": {"trading_quant": {"weights": {"technical": 0.30}}}},
    )
    assert resp.status_code == 200
    assert resp.json()["strategies"]["trading_quant"]["weights"]["technical"] == 0.30
    assert saved["strategies.yaml"]["trading_quant"]["weights"]["technical"] == 0.30


def test_frontend_has_strategy_params_editor():
    text = INDEX.read_text(encoding="utf-8")
    assert 'id="strategyParamsForm"' in text
    assert "async function loadStrategyParams" in text
    assert "async function saveStrategyParams" in text
    assert "function _flattenStrategy" in text
    assert "loadStrategyParams();" in text

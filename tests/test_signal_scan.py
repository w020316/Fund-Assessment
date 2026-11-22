"""个股多信号扫描的单测：趋势方向、降级语义与路由契约。"""
from fastapi.testclient import TestClient

from src.analysis.signal_scan import scan_signals
from web.api import app

client = TestClient(app)


def _bar(date, o, h, low, c, v=1_000_000):
    return {"date": date, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _trend(n, step):
    rows = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + step
        rows.append(_bar(f"2026-03-{i+1:02d}", o, max(o, c) + 1, min(o, c) - 1, c,
                         v=1_500_000 + i * 10_000))
        price = c
    return rows


def test_uptrend_produces_bullish_stance():
    out = scan_signals(_trend(90, 0.6), code="600519")
    assert out["available"] is True
    s = out["summary"]
    assert s["bull_score"] > s["bear_score"]
    assert s["stance"] == "偏多"
    keys = {x["key"] for x in out["signals"]}
    assert "ma_bull_align" in keys
    assert all(x["strength"] > 0 for x in out["signals"])


def test_downtrend_produces_bearish_stance():
    out = scan_signals(_trend(90, -0.6), code="600519")
    assert out["available"] is True
    s = out["summary"]
    assert s["bear_score"] > s["bull_score"]
    assert s["stance"] == "偏空"
    assert any(x["direction"] == "bear" for x in out["signals"])


def test_insufficient_bars_is_honest():
    out = scan_signals(_trend(20, 0.5), code="600519")
    assert out["available"] is False
    assert out["signals"] == []
    assert out["summary"] is None
    assert "不足" in out["reason"]


def test_flat_market_has_no_fake_direction():
    rows = [_bar(f"2026-04-{i+1:02d}", 100, 101, 99, 100) for i in range(90)]
    out = scan_signals(rows, code="600519")
    assert out["available"] is True
    assert out["summary"]["stance"] == "中性"
    assert 0 <= out["summary"]["bull_score"] <= 1000


def test_signals_route_shape(monkeypatch):
    rows = _trend(90, 0.4)
    monkeypatch.setattr("web.routes.market.ds2.get_kline_mootdx", lambda *a, **k: rows)
    from web.routes import market as market_route
    market_route.cache.delete("market:signals:600001:daily:120")

    resp = client.get("/api/market/signals?code=600001&period=daily&count=120")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["available"] is True
    assert data["summary"]["stance"] in ("偏多", "偏空", "中性")
    assert isinstance(data["signals"], list)
    assert body["_meta"]["data_source"] == "mootdx"

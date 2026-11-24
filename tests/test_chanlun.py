"""简化缠论结构分析的单测（纯函数 + 主入口降级语义）。"""
import math

from fastapi.testclient import TestClient

from src.analysis.chanlun import (
    _build_pivots,
    _build_strokes,
    _find_fractals,
    _merge_inclusion,
    analyze_chanlun,
)
from web.api import app

client = TestClient(app)


def _bar(date, o, h, low, c):
    return {"date": date, "open": o, "high": h, "low": low, "close": c}


def test_merge_inclusion_collapses_contained_bar():
    bars = [
        _bar("d1", 10, 12, 9, 11),
        _bar("d2", 10.5, 11.5, 9.5, 11),   # 完全被 d1 包含
        _bar("d3", 11, 13, 10, 12),        # 向上突破
    ]
    merged = _merge_inclusion(bars)
    assert len(merged) == 2
    # 向上处理包含关系：取高高、低低中的较高者
    assert merged[0]["high"] == 12 and merged[0]["low"] == 9.5
    assert merged[1]["high"] == 13


def test_find_top_and_bottom_fractals():
    merged = [
        {"high": 10, "low": 8, "high_date": "a", "low_date": "a", "end_orig": 0},
        {"high": 12, "low": 10, "high_date": "b", "low_date": "b", "end_orig": 1},
        {"high": 9, "low": 7, "high_date": "c", "low_date": "c", "end_orig": 2},
    ]
    out = _find_fractals(merged)
    assert len(out) == 1
    assert out[0]["type"] == "top" and out[0]["price"] == 12


def test_build_strokes_requires_gap_and_alternation():
    fractals = [
        {"idx": 0, "type": "bottom", "price": 10, "date": "d0"},
        {"idx": 2, "type": "top", "price": 12, "date": "d2"},      # 间隔不足，忽略
        {"idx": 6, "type": "top", "price": 13, "date": "d6"},
        {"idx": 7, "type": "top", "price": 14, "date": "d7"},      # 同型更极端，替换
        {"idx": 12, "type": "bottom", "price": 11, "date": "d12"},
    ]
    strokes = _build_strokes(fractals)
    assert len(strokes) == 2
    assert strokes[0]["direction"] == "up"
    assert strokes[0]["end_price"] == 14
    assert strokes[1]["direction"] == "down"


def test_build_pivots_from_three_overlapping_strokes():
    strokes = [
        {"direction": "up", "start_date": "d1", "end_date": "d2", "start_price": 10.0, "end_price": 12.0},
        {"direction": "down", "start_date": "d2", "end_date": "d3", "start_price": 12.0, "end_price": 10.5},
        {"direction": "up", "start_date": "d3", "end_date": "d4", "start_price": 10.5, "end_price": 11.8},
    ]
    pivots = _build_pivots(strokes)
    assert len(pivots) == 1
    assert pivots[0]["zg"] == 11.8
    assert pivots[0]["zd"] == 10.5
    assert pivots[0]["strokes"] == 3


def test_build_pivots_ignores_non_overlap():
    strokes = [
        {"direction": "up", "start_date": "d1", "end_date": "d2", "start_price": 10.0, "end_price": 12.0},
        {"direction": "down", "start_date": "d2", "end_date": "d3", "start_price": 12.0, "end_price": 11.5},
        {"direction": "up", "start_date": "d3", "end_date": "d4", "start_price": 11.5, "end_price": 20.0},
    ]
    # 三笔区间 [10,12] / [11.5,12] / [11.5,20] 仍重叠，故应识别；用完全分离样本验证反例
    separated = [
        {"direction": "up", "start_date": "d1", "end_date": "d2", "start_price": 10.0, "end_price": 12.0},
        {"direction": "down", "start_date": "d2", "end_date": "d3", "start_price": 20.0, "end_price": 18.0},
        {"direction": "up", "start_date": "d3", "end_date": "d4", "start_price": 18.0, "end_price": 19.0},
    ]
    assert len(_build_pivots(strokes)) == 1
    assert _build_pivots(separated) == []


def test_analyze_chanlun_insufficient_bars_is_honest():
    rows = [_bar(f"d{i}", 10, 11, 9, 10) for i in range(10)]
    out = analyze_chanlun(rows, code="600519")
    assert out["available"] is False
    assert out["signals"] == []
    assert "不足" in out["reason"]


def test_analyze_chanlun_downtrend_does_not_fake_zero():
    rows = []
    for i in range(60):
        price = 100 - i * 0.5 + math.sin(i / 3)
        rows.append(_bar(f"2026-01-{i+1:02d}", price, price + 1, price - 1, price))
    out = analyze_chanlun(rows, code="600519")
    assert out["available"] is True
    assert out["summary"]["close"] is not None
    assert len(out["bars"]) <= 160
    # 结构字段是列表/None，不允许把缺失写成 0
    assert isinstance(out["pivots"], list)
    assert out["divergence"] is None or isinstance(out["divergence"], dict)
    assert "简化缠论" in out["note"]


def test_chanlun_route_returns_structure(monkeypatch):
    rows = []
    for i in range(80):
        price = 100 + math.sin(i / 4) * 5
        rows.append(_bar(f"2026-02-{i+1:02d}", price, price + 2, price - 2, price))

    monkeypatch.setattr("web.routes.market.ds2.get_kline_mootdx",
                        lambda *a, **k: rows)
    from web.routes import market as market_route
    market_route.cache.delete("market:chanlun:600000:daily:120")

    resp = client.get("/api/market/chanlun?code=600000&period=daily&count=120")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["available"] is True
    assert data["code"] == "600000"
    assert isinstance(data["strokes"], list) and isinstance(data["pivots"], list)
    assert body["_meta"]["data_source"] == "mootdx"


def test_chanlun_route_rejects_non_numeric_code(monkeypatch):
    """S9-04: /chanlun、/signals 的 code 必须是 6 位数字, 非法串不得打进 pytdx。"""
    monkeypatch.setattr(
        "web.routes.market.ds2.get_kline_mootdx",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("非法代码不应触达上游")),
    )
    for url in ("/api/market/chanlun?code=600519%22%29+OR+%281%3D1",
                "/api/market/chanlun?code=60051a",
                "/api/market/signals?code=sh600519"):
        resp = client.get(url)
        assert resp.status_code == 422, url
        assert resp.json()["detail"]["code"] == "INVALID_STOCK_CODE"


def test_chanlun_route_upstream_hang_times_out(monkeypatch):
    """S9-01: 上游挂起必须在 15s(测试缩至 0.2s)内返回降级态, 不得占死线程池外请求。"""
    import time as _time

    def _hang(*a, **k):
        _time.sleep(1.0)
        return []

    monkeypatch.setattr("web.routes.market.ds2.get_kline_mootdx", _hang)
    from web.routes import market as market_route
    monkeypatch.setattr(market_route, "_KLINE_TIMEOUT_S", 0.2)
    market_route.cache.delete("market:chanlun:600001:daily:120")

    resp = client.get("/api/market/chanlun?code=600001&period=daily&count=120")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert "K 线获取失败" in data["reason"]
    assert data["bars"] == [] and data["signals"] == []


def test_chanlun_route_upstream_exception_degrades(monkeypatch):
    """上游抛异常 → available=False 带原因, 不补 0、不写缓存(S9-08 异常路径)。"""
    def _boom(*a, **k):
        raise RuntimeError("mootdx socket reset")

    monkeypatch.setattr("web.routes.market.ds2.get_kline_mootdx", _boom)
    from web.routes import market as market_route
    market_route.cache.delete("market:chanlun:600002:daily:120")

    resp = client.get("/api/market/chanlun?code=600002&period=daily&count=120")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert resp.json()["_meta"]["quality_score"] == 0.0
    assert market_route.cache.get("market:chanlun:600002:daily:120") is None

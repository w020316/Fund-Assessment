"""十大流通股东归类与汇总单测。"""
from fastapi.testclient import TestClient

from src.analysis.institution_holdings import analyze_holders, classify_holder, parse_change
from web.api import app

client = TestClient(app)


def _holder(rank, name, htype, ratio, change="不变", hold=1000000):
    return {
        "rank": rank, "name": name, "holder_type": htype,
        "shares_type": "A股", "hold_num": hold, "free_ratio": ratio,
        "change_raw": change, "change_ratio": None,
    }


def test_classify_national_team_and_foreign():
    assert classify_holder("中央汇金资产管理有限责任公司", "其它") == "国家队"
    assert classify_holder("全国社会保障基金一一三组合", "社保基金") == "国家队"
    assert classify_holder("香港中央结算有限公司", "其它") == "外资"
    assert classify_holder("MORGAN STANLEY & CO. INTERNATIONAL PLC", "QFII") == "外资"


def test_classify_institution_industry_person():
    assert classify_holder("招商银行股份有限公司-某某灵活配置混合型基金", "基金") == "机构"
    assert classify_holder("中国贵州茅台酒厂(集团)有限责任公司", "其它") == "产业资本"
    assert classify_holder("张三", "个人") == "个人"
    assert classify_holder("某不明股东", "其它") == "其他"


def test_parse_change_kinds():
    assert parse_change("新进")["kind"] == "new"
    assert parse_change("不变")["kind"] == "flat"
    assert parse_change("1552026")["kind"] == "increase"
    assert parse_change("-2000")["kind"] == "decrease"
    assert parse_change("")["kind"] == "flat"
    assert parse_change("未知表述")["kind"] == "unknown"


def test_analyze_holders_summary():
    payload = {
        "end_date": "2026-09-30",
        "holders": [
            _holder(1, "中央汇金资产管理有限责任公司", "其它", 10.0),
            _holder(2, "香港中央结算有限公司", "其它", 4.0, "1552026"),
            _holder(3, "某某成长混合型证券投资基金", "基金", 5.0, "新进"),
            _holder(4, "某某集团有限公司", "其它", 54.0, "-2000"),
        ],
    }
    out = analyze_holders(payload)
    assert out["available"] is True
    s = out["summary"]
    assert s["total_ratio"] == 73.0
    assert s["categories"]["国家队"]["ratio"] == 10.0
    assert s["categories"]["外资"]["ratio"] == 4.0
    assert s["categories"]["机构"]["ratio"] == 5.0
    assert s["categories"]["产业资本"]["ratio"] == 54.0
    assert s["changes"]["new"] == 1
    assert s["changes"]["flat"] == 1
    assert s["top_changes"][0]["name"] == "香港中央结算有限公司"


def test_analyze_holders_missing_ratio_is_none():
    out = analyze_holders({"holders": [_holder(1, "中央汇金", "其它", None)]})
    assert out["available"] is True
    assert out["summary"]["total_ratio"] is None
    assert out["summary"]["categories"]["国家队"]["ratio"] is None


def test_analyze_holders_empty():
    out = analyze_holders({})
    assert out["available"] is False
    assert "暂无" in out["reason"]


def test_institution_holdings_route(monkeypatch):
    payload = {
        "end_date": "2026-09-30",
        "holders": [_holder(1, "中央汇金", "其它", 12.5)],
    }
    monkeypatch.setattr("web.routes.market.ds2.get_top10_free_holders", lambda code: payload)
    from web.routes import market as market_route
    market_route.cache.delete("market:institution_holdings:600002")

    resp = client.get("/api/market/institution_holdings?code=600002")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["summary"]["categories"]["国家队"]["ratio"] == 12.5


def test_institution_holdings_failure_ttl_is_short(monkeypatch):
    """S9-10: available=False 的失败结果只允许 60s 短缓存, 不得固化 30 分钟。"""
    captured = {}

    monkeypatch.setattr("web.routes.market.ds2.get_top10_free_holders", lambda code: {})
    from web.routes import market as market_route
    orig_set = market_route.cache.set
    monkeypatch.setattr(
        market_route.cache, "set",
        lambda key, value, ttl=300: (captured.__setitem__(key, ttl), orig_set(key, value, ttl=ttl))[1],
    )
    market_route.cache.delete("market:institution_holdings:600009")

    resp = client.get("/api/market/institution_holdings?code=600009")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False
    assert captured.get("market:institution_holdings:600009") == 60, "失败结果被长缓存固化"


def test_institution_holdings_success_ttl_keeps_long(monkeypatch):
    """成功季报数据仍享受 1800s 长 TTL（防瞬时故障与长缓存两个语义都要保住）。"""
    payload = {"end_date": "2026-09-30", "holders": [_holder(1, "中央汇金", "其它", 12.5)]}
    captured = {}

    monkeypatch.setattr("web.routes.market.ds2.get_top10_free_holders", lambda code: payload)
    from web.routes import market as market_route
    orig_set = market_route.cache.set
    monkeypatch.setattr(
        market_route.cache, "set",
        lambda key, value, ttl=300: (captured.__setitem__(key, ttl), orig_set(key, value, ttl=ttl))[1],
    )
    market_route.cache.delete("market:institution_holdings:600010")

    resp = client.get("/api/market/institution_holdings?code=600010")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is True
    assert captured.get("market:institution_holdings:600010") == 1800

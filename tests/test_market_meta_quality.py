"""A-07 + M-03(2026-11-17):空数据不得报 80 分健康。

根因: `_build_meta` 的 `quality_score` 缺省值是**乐观默认 80**(cached=100),
而 `degraded = resolved_quality < 70`。凡是"取数返回空列表但仍然不传 quality_score"
的端点, 前端会同时拿到 `degraded=false` 和空列表 —— 无法区分
"确实没有数据" 与 "抓取失败", 这正是本项目数据诚实红线的反面。

本文件锁定 4 个此前漏掉的端点: /hot_stocks、/hot_stocks_signal、/news、/global_news。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from web.api import app
from web.routes import market

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_market_cache():
    market.cache.clear()
    yield
    market.cache.clear()


def _meta(payload: dict) -> dict:
    assert "data" in payload and "_meta" in payload
    return payload["_meta"]


# ---------------------------------------------------------------- 空数据 → 降级

def test_hot_stocks_all_three_boards_empty_is_degraded(monkeypatch):
    empty = {"top_gainers": [], "top_losers": [], "top_volume": []}
    monkeypatch.setattr(market, "_fetch_hot_stocks", AsyncMock(return_value=empty))

    payload = client.get("/api/market/hot_stocks").json()
    meta = _meta(payload)

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True, "三个榜单全空仍报健康分 → 前端把抓取失败读成今天没有"


def test_hot_stocks_signal_empty_is_degraded(monkeypatch):
    monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", lambda: [])
    monkeypatch.setattr(market.ds2, "get_hot_stocks_signal_fallback", lambda: [])

    meta = _meta(client.get("/api/market/hot_stocks_signal").json())

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


def test_news_empty_is_degraded(monkeypatch):
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: [])

    meta = _meta(client.get("/api/market/news").json())

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


def test_global_news_empty_is_degraded(monkeypatch):
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: [])

    meta = _meta(client.get("/api/market/global_news").json())

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


# ---------------------------------------------------------------- 非空 → 正常

def test_hot_stocks_partial_board_is_not_degraded(monkeypatch):
    """三个榜单只有一个有数据 → 不得判空(避免过度降级)。"""
    payload = {
        "top_gainers": [{"code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 9.9}],
        "top_losers": [],
        "top_volume": [],
    }
    monkeypatch.setattr(market, "_fetch_hot_stocks", AsyncMock(return_value=payload))

    meta = _meta(client.get("/api/market/hot_stocks").json())

    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False


def test_hot_stocks_signal_non_empty_is_ok(monkeypatch):
    rows = [{
        "code": "600519", "name": "贵州茅台", "price": 1700.0, "change_pct": 9.9,
        "volume": 1.0, "reason": "涨停", "limit_up_time": "09:35", "open_times": 0,
    }]
    monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", lambda: rows)

    meta = _meta(client.get("/api/market/hot_stocks_signal").json())

    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False


def test_news_non_empty_is_ok(monkeypatch):
    rows = [{
        "title": "标题", "content": "正文", "url": "https://example.invalid/1",
        "source": "eastmoney", "publish_time": "2026-11-17 09:00:00",
    }]
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: rows)

    meta = _meta(client.get("/api/market/news").json())

    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False


# ---------------------------------------------------------------- 缓存命中

def test_cached_empty_news_is_still_degraded(monkeypatch):
    """缓存里存的是空列表时, 命中缓存也必须是降级态(不能因为 cached=True 就满分)。"""
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: [])
    client.get("/api/market/global_news")  # 首次请求写入空列表缓存

    meta = _meta(client.get("/api/market/global_news").json())

    assert meta["cached"] is True
    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


def test_cached_non_empty_news_keeps_full_score(monkeypatch):
    """缓存命中且非空 → 仍沿用既有 cached=100 约定(不得把健康缓存降到 80)。"""
    rows = [{
        "title": "标题", "content": "正文", "url": "https://example.invalid/1",
        "source": "eastmoney", "publish_time": "2026-11-17 09:00:00",
    }]
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: rows)
    client.get("/api/market/global_news")

    meta = _meta(client.get("/api/market/global_news").json())

    assert meta["cached"] is True
    assert meta["quality_score"] == 100.0
    assert meta["degraded"] is False


# ---------------------------------------------------------------- 默认值本身

def test_default_quality_constant_is_optimistic_and_named():
    """乐观默认必须被显式命名(_DEFAULT_QUALITY), 不再是散落的魔法数字 80。"""
    assert market._DEFAULT_QUALITY == 80.0
    meta = market._build_meta("eastmoney", cached=False)
    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False
    # 显式传 0 时必须是降级
    assert market._build_meta("x", quality_score=0.0)["degraded"] is True


# ===== M-04(2026-11-17):上游 `-` 占位不得被伪造成 0 =====

class TestMissingQuoteFieldsStayNone:
    """实测东财 clist 100 行样本中 21 行 `f2='-'`(PT金田A/国华退/ST星源…)。
    旧实现 `_safe_float(item.get("f2", 0))` 一律填 0 → 前端渲染"价格 0.00 / 涨幅 0.00%"。"""

    def test_dash_placeholder_becomes_none(self, monkeypatch):
        import src.core.data_source_v2 as ds2

        class _Resp:
            def json(self):
                return {"data": {"diff": [
                    {"f12": "000001", "f14": "PT金田A", "f2": "-", "f3": "-", "f4": "-",
                     "f5": "-", "f6": "-", "f15": "-", "f16": "-", "f17": "-", "f18": "-"},
                    {"f12": "600519", "f14": "贵州茅台", "f2": 1435.26, "f3": 1.77, "f4": 25.0,
                     "f5": 1000, "f6": 1.4e9, "f15": 1440.0, "f16": 1420.0,
                     "f17": 1430.0, "f18": 1410.26},
                ]}}

        ds2._cache.clear()
        monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _Resp())

        rows = ds2.get_stock_ranking_em("f3", 0, 2)

        assert rows[0]["code"] == "000001"          # code/name 仍是字符串
        assert rows[0]["price"] is None, "`-` 被填成了 0.0"
        assert rows[0]["change_pct"] is None
        assert rows[0]["amount"] is None
        # 正常数据数值不变(回归保护)
        assert rows[1]["price"] == 1435.26
        assert rows[1]["change_pct"] == 1.77

    def test_hot_stocks_endpoint_keeps_none(self, monkeypatch):
        async def fake_fetch():
            return {
                "top_gainers": [{"code": "000001", "name": "PT金田A",
                                 "price": None, "change_pct": None,
                                 "volume": None, "amount": None}],
                "top_losers": [],
                "top_volume": [],
            }

        monkeypatch.setattr(market, "_fetch_hot_stocks", fake_fetch)
        data = client.get("/api/market/hot_stocks").json()["data"]

        row = data["top_gainers"][0]
        assert row["price"] is None and row["change_pct"] is None
        assert row["volume"] is None and row["amount"] is None

    def test_scan_new_high_skips_rows_without_change_pct(self, monkeypatch):
        """缺 change_pct 的行既不能比较(null >= 3 会 TypeError), 也不该算"新高"。"""
        from web.routes import strategy

        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", False)
        monkeypatch.setattr(strategy.ds2, "get_stock_ranking_em", lambda **kw: [
            {"code": "000001", "name": "PT金田A", "change_pct": None,
             "volume": None, "amount": None},
            {"code": "600519", "name": "贵州茅台", "change_pct": 5.2,
             "volume": 1000, "amount": 1.4e9},
        ])

        resp = client.get("/api/strategy/scan/new_high")

        assert resp.status_code == 200
        rows = resp.json()
        assert [r["stock_code"] for r in rows] == ["600519"]


# ===== C-02 收尾(2026-11-17):stock_realtime / index_realtime / sector_flow /
# heatmap 四处 `_safe_float(change_pct)` 改 opt_float, 缺失 → None =====

class TestFourQuoteEndpointsKeepNone:
    """批次 C(8c3d835)只打通了热点榜链; 本类收尾剩下 4 条链。

    上游是腾讯/新浪/交易所, 未见 `-` 占位, 但空串/缺失字段同样存在 ——
    `_safe_float(item.get("change_pct"))` 一律兜 0 会把"没拿到"画成"0.00%"。
    口径与 TestMissingQuoteFieldsStayNone 一致: 缺失 → None, 正常数值不变。"""

    def test_stock_realtime_missing_fields_stay_none(self, monkeypatch):
        """个股行情: 缺失字段 → None; 正常行回归保护; 涨跌额缺失但价/昨收齐全时如实推导。"""
        rows = [
            {"code": "000001", "name": "PT金田A", "price": "", "change": None,
             "change_pct": "-", "volume": None, "amount": "", "high": None,
             "low": "", "open": None, "prev_close": ""},
            {"code": "600519", "name": "贵州茅台", "price": 1688.0, "change": None,
             "change_pct": 1.77, "volume": 1000, "amount": 1.4e9, "high": 1690.0,
             "low": 1670.0, "open": 1680.0, "prev_close": 1658.6},
        ]
        monkeypatch.setattr(market.ds2, "get_realtime_quote_tencent", lambda codes: rows)

        market.cache.clear()
        data = client.get("/api/market/stock_realtime", params={"codes": "000001,600519"}).json()["data"]

        missing = data[0]
        assert missing["price"] is None, "空串价格被兜成了 0.0"
        assert missing["change_pct"] is None, "`-` 被兜成了 0.0"
        assert missing["change"] is None, "价/昨收也缺失时涨跌额不得伪造成 0"
        assert missing["volume"] is None and missing["amount"] is None
        assert missing["high"] is None and missing["low"] is None
        # 正常行数值不变(回归保护); 涨跌额缺失但价/昨收齐全 → 用真实数据推导
        ok = data[1]
        assert ok["price"] == 1688.0 and ok["change_pct"] == 1.77
        assert ok["change"] == round(1688.0 - 1658.6, 2)

    def test_index_realtime_missing_fields_stay_none(self, monkeypatch):
        monkeypatch.setattr(market.ds2, "get_index_realtime", lambda: [
            {"code": "000001", "name": "上证指数", "price": None, "change": "",
             "change_pct": None, "volume": None, "amount": None},
            {"code": "399001", "name": "深证成指", "price": 10800.5, "change": 35.2,
             "change_pct": 0.33, "volume": 1.2e8, "amount": 9.8e11},
        ])

        market.cache.clear()
        data = client.get("/api/market/index_realtime").json()["data"]

        assert data[0]["price"] is None and data[0]["change_pct"] is None
        assert data[0]["change"] is None and data[0]["volume"] is None
        assert data[1]["price"] == 10800.5 and data[1]["change_pct"] == 0.33

    def test_sector_flow_missing_fields_stay_none(self, monkeypatch):
        """板块资金流缺失 → None; None 同时被 _flow_field_quality 如实计入完整度。"""
        monkeypatch.setattr(market.ds2, "get_sector_ranking", lambda: [
            {"name": "缺失板块", "change_pct": None, "main_net_inflow": None,
             "main_inflow_pct": None},
            {"name": "正常板块", "change_pct": 1.2, "main_net_inflow": 5.6e8,
             "main_inflow_pct": 12.5},
        ])

        market.cache.clear()
        payload = client.get("/api/market/sector_flow").json()
        data = payload["data"]

        assert data[0]["change_pct"] is None
        assert data[0]["main_net_inflow"] is None
        assert data[0]["large_order_ratio"] is None
        assert data[1]["change_pct"] == 1.2 and data[1]["main_net_inflow"] == 5.6e8
        # None 行不算 non-zero → 完整度 0.5, 字段降级信息必须下发(不许静默);
        # extra 由 _build_meta 平铺进 _meta(而非嵌套)
        meta = payload["_meta"]
        assert meta["field_completeness"]["main_net_inflow"] == 0.5

    def test_heatmap_missing_change_pct_neutral_and_null(self, monkeypatch):
        """缺失涨跌幅: color 显式降级为中性灰(与"持平"同色), change_pct 保持 null,
        不冒充"0.00% 持平"。"""
        monkeypatch.setattr(market.ds2, "get_sector_ranking", lambda: [
            {"name": "涨", "change_pct": 2.0},
            {"name": "缺", "change_pct": None},
        ])

        market.cache.clear()
        data = client.get("/api/market/heatmap").json()["data"]

        assert data[0]["change_pct"] == 2.0 and data[0]["color"] == "rgb(51,0,0)"
        assert data[1]["change_pct"] is None
        assert data[1]["color"] == "rgb(128,128,128)"

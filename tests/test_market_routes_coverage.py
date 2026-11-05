"""web/routes/market.py 覆盖率补测(阶段二)。

补充既有 tests/test_market_routes.py 未覆盖的端点分支:
- 数据源成功 / 空数据 / 异常三条路径
- 缓存命中分支(二次调用)
- 每个端点返回结构与 _meta 降级标识

全部通过 mock data_source_v2(ds2) / akshare 与清文件缓存,不发起真实网络请求。
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from web.routes import market

QUOTE_ITEM = {
    "code": "600519",
    "name": "贵州茅台",
    "price": 1680.0,
    "change": 12.0,
    "change_pct": 0.72,
    "volume": 1000.0,
    "amount": 1680000.0,
    "high": 1690.0,
    "low": 1660.0,
    "open": 1670.0,
    "prev_close": 1668.0,
}


@pytest.fixture
def client():
    from web.api import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_market_cache():
    market.cache.clear()
    yield
    market.cache.clear()


def _raise(*_args, **_kwargs):
    raise RuntimeError("boom")


# ===== 股票实时 / K 线 =====


class TestStockRealtime:
    def test_success_and_cached(self, client, monkeypatch):
        monkeypatch.setattr(market.ds2, "get_realtime_quote_tencent", lambda codes: [QUOTE_ITEM])
        r1 = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert r1.status_code == 200
        assert r1.json()["data"][0]["change_pct"] == 0.72
        # 二次调用走缓存分支
        r2 = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert r2.json()["_meta"]["cached"] is True

    def test_change_backfilled_from_prev_close(self, client, monkeypatch):
        item = dict(QUOTE_ITEM, change=0.0)
        monkeypatch.setattr(market.ds2, "get_realtime_quote_tencent", lambda codes: [item])
        resp = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert resp.json()["data"][0]["change"] == 12.0

    def test_too_many_codes_returns_400(self, client):
        codes = ",".join(str(600000 + i) for i in range(21))
        assert client.get("/api/market/stock_realtime", params={"codes": codes}).status_code == 400

    def test_datasource_error_returns_empty(self, client, monkeypatch):
        monkeypatch.setattr(market.ds2, "get_realtime_quote_tencent", _raise)
        resp = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []
        assert resp.json()["_meta"]["degraded"] is True


class TestStockKline:
    def test_success_and_cached(self, client, monkeypatch):
        rows = [
            {"date": "2026-10-0%d" % i, "open": 10, "high": 11, "low": 9, "close": 10 + i, "volume": 100, "amount": 1000}
            for i in range(1, 6)
        ]
        monkeypatch.setattr(market.ds2, "get_kline_mootdx", lambda code, period, count: rows)
        r1 = client.get("/api/market/stock_kline", params={"code": "600519", "count": 5})
        assert r1.status_code == 200
        assert len(r1.json()["data"]) == 5
        r2 = client.get("/api/market/stock_kline", params={"code": "600519", "count": 5})
        assert r2.json()["_meta"]["cached"] is True

    def test_error_returns_empty(self, client, monkeypatch):
        monkeypatch.setattr(market.ds2, "get_kline_mootdx", _raise)
        resp = client.get("/api/market/stock_kline", params={"code": "600519"})
        assert resp.json()["data"] == []


# ===== 基金 =====


def _fake_ak_nav_df(rows: list[tuple[str, float]]):
    return pd.DataFrame(
        {
            "净值日期": [d for d, _ in rows],
            "单位净值": [v for _, v in rows],
            "累计净值": [v for _, v in rows],
        }
    )


class TestFundRoutes:
    def test_fund_realtime_akshare_success(self, client, monkeypatch):
        df = _fake_ak_nav_df([("2026-10-28", 1.0), ("2026-10-29", 1.02)])
        monkeypatch.setattr(market, "_HAS_AKSHARE", True)
        monkeypatch.setattr(
            market, "ak", types.SimpleNamespace(fund_em_open_fund_info=lambda code, indicator: df)
        )
        resp = client.get("/api/market/fund_realtime", params={"codes": "161725"})
        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["nav"] == pytest.approx(1.02)
        assert item["change_pct"] == pytest.approx(2.0)

    def test_fund_realtime_akshare_empty_then_tencent(self, client, monkeypatch):
        monkeypatch.setattr(market, "_HAS_AKSHARE", True)
        monkeypatch.setattr(
            market, "ak", types.SimpleNamespace(fund_em_open_fund_info=lambda code, indicator: pd.DataFrame())
        )
        monkeypatch.setattr(
            market.ds2,
            "get_fund_realtime_tencent",
            lambda codes: [
                {
                    "code": codes[0],
                    "name": "白酒",
                    "nav": 1.5,
                    "estimated_nav": 1.5,
                    "change": 0.01,
                    "change_pct": 0.67,
                    "update_time": "2026-10-29",
                }
            ],
        )
        resp = client.get("/api/market/fund_realtime", params={"codes": "161725"})
        assert resp.status_code == 200
        assert resp.json()["data"][0]["nav"] == 1.5

    def test_fund_realtime_akshare_exception_falls_back(self, client, monkeypatch):
        monkeypatch.setattr(market, "_HAS_AKSHARE", True)
        monkeypatch.setattr(
            market, "ak", types.SimpleNamespace(fund_em_open_fund_info=_raise)
        )
        monkeypatch.setattr(market.ds2, "get_fund_realtime_tencent", lambda codes: [])
        resp = client.get("/api/market/fund_realtime", params={"codes": "161725"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_fund_history_akshare_period_filter(self, client, monkeypatch):
        df = _fake_ak_nav_df(
            [("2020-01-01", 1.0), ("2026-10-27", 1.1), ("2026-10-28", 1.2), ("2026-10-29", 1.3)]
        )
        monkeypatch.setattr(market, "_HAS_AKSHARE", True)
        monkeypatch.setattr(
            market, "ak", types.SimpleNamespace(fund_em_open_fund_info=lambda code, indicator: df)
        )
        resp = client.get("/api/market/fund_history", params={"code": "161725", "period": "1m"})
        data = resp.json()["data"]
        # 2020 年的行被周期过滤掉,只保留近 30 天
        assert len(data) == 3
        # 首行无前值 → change_pct 为 0,后续行按前一日净值算涨跌幅
        assert data[0]["change_pct"] == 0.0
        assert data[1]["change_pct"] == pytest.approx(9.09)
        assert data[-1]["date"] == "2026-10-29"

    def test_fund_history_empty_uses_tencent_fallback(self, client, monkeypatch):
        monkeypatch.setattr(market, "_HAS_AKSHARE", True)
        monkeypatch.setattr(
            market, "ak", types.SimpleNamespace(fund_em_open_fund_info=lambda code, indicator: pd.DataFrame())
        )
        monkeypatch.setattr(
            market.ds2,
            "get_fund_history_tencent",
            lambda code, period="1y": [
                {"date": "2026-10-29", "nav": 1.2, "acc_nav": 1.2, "change_pct": 0.0}
            ],
        )
        resp = client.get("/api/market/fund_history", params={"code": "161725"})
        assert resp.json()["data"][0]["nav"] == 1.2

    def test_fund_history_akshare_error_uses_fallback(self, client, monkeypatch):
        monkeypatch.setattr(market, "_HAS_AKSHARE", True)
        monkeypatch.setattr(market, "ak", types.SimpleNamespace(fund_em_open_fund_info=_raise))
        monkeypatch.setattr(market.ds2, "get_fund_history_tencent", lambda code, period="1y": [])
        resp = client.get("/api/market/fund_history", params={"code": "161725"})
        assert resp.status_code == 200


# ===== 指数 / 排行 / 板块 =====


class TestIndexAndRanking:
    def test_index_realtime_success_and_error(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2,
            "get_index_realtime",
            lambda: [{"code": "000001", "name": "上证指数", "price": 3000.0, "change": 10.0,
                      "change_pct": 0.33, "volume": 1.0, "amount": 2.0}],
        )
        resp = client.get("/api/market/index_realtime")
        assert resp.json()["data"][0]["code"] == "000001"
        assert client.get("/api/market/index_realtime").json()["_meta"]["cached"] is True

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_index_realtime", _raise)
        resp = client.get("/api/market/index_realtime")
        assert resp.json()["data"] == []

    def test_hot_stocks_success_and_error(self, client, monkeypatch):
        row = {"code": "600519", "name": "贵州茅台", "price": 1680.0, "change_pct": 1.0, "volume": 1, "amount": 2}
        monkeypatch.setattr(market.ds2, "get_stock_ranking_em", lambda field, page, size: [row])
        resp = client.get("/api/market/hot_stocks")
        assert len(resp.json()["data"]["top_gainers"]) == 1

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_stock_ranking_em", _raise)
        resp = client.get("/api/market/hot_stocks")
        assert resp.json()["data"]["top_gainers"] == []

    def test_sector_flow_success_and_error(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2,
            "get_sector_ranking",
            lambda: [{"name": "半导体", "change_pct": 2.0, "main_net_inflow": 1e8, "main_inflow_pct": 3.0}],
        )
        resp = client.get("/api/market/sector_flow")
        assert resp.json()["data"][0]["sector"] == "半导体"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_sector_ranking", _raise)
        assert client.get("/api/market/sector_flow").json()["data"] == []

    def test_sector_ranking_success_and_error(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2,
            "get_sector_ranking",
            lambda: [
                {
                    "code": "BK1", "name": "半导体", "change_pct": 2.0, "price": 1.0,
                    "main_net_inflow": 1e8, "main_inflow_pct": 3.0, "super_large_net": 1.0,
                    "super_large_pct": 1.0, "large_net": 1.0, "large_pct": 1.0,
                    "medium_net": 1.0, "medium_pct": 1.0, "small_net": 1.0, "small_pct": 1.0,
                }
            ],
        )
        assert client.get("/api/market/sector_ranking").json()["data"][0]["name"] == "半导体"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_sector_ranking", _raise)
        assert client.get("/api/market/sector_ranking").json()["data"] == []


# ===== 研报 / 龙虎榜 / 两融 / 大宗 / 股东 / 新闻 =====


class TestResearchAndFlows:
    def test_research_reports_success_and_error(self, client, monkeypatch):
        item = {"title": "深度报告", "rating": "买入", "eps_predict": 1.2,
                "org_name": "中信证券", "publish_date": "2026-10-28"}
        monkeypatch.setattr(market.ds2, "get_research_reports", lambda code, page=1, page_size=10: [item])
        resp = client.get("/api/market/research_reports", params={"code": "600519"})
        assert resp.json()["data"][0]["rating"] == "买入"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_research_reports", _raise)
        assert client.get("/api/market/research_reports").json()["data"] == []

    def test_dragon_tiger_success_and_error(self, client, monkeypatch):
        item = {"code": "600519", "name": "贵州茅台", "price": 1680.0, "change_pct": 5.0,
                "reason": "涨幅偏离", "buy_amount": 1e6, "sell_amount": 2e5,
                "net_amount": 8e5, "trade_date": "2026-10-28"}
        monkeypatch.setattr(market.ds2, "get_dragon_tiger", lambda: [item])
        assert client.get("/api/market/dragon_tiger").json()["data"][0]["net_amount"] == 8e5

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_dragon_tiger", _raise)
        assert client.get("/api/market/dragon_tiger").json()["data"] == []

    def test_margin_empty_code(self, client):
        resp = client.get("/api/market/margin")
        assert resp.json()["data"]["code"] == ""

    def test_margin_success_none_and_error(self, client, monkeypatch):
        item = {"code": "600519", "trade_date": "2026-10-28", "margin_buy": 1.0,
                "margin_balance": 2.0, "short_sell": 3.0, "short_balance": 4.0, "total_balance": 5.0}
        monkeypatch.setattr(market.ds2, "get_margin_trading", lambda code: item)
        assert client.get("/api/market/margin", params={"code": "600519"}).json()["data"]["total_balance"] == 5.0

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_margin_trading", lambda code: {})
        resp = client.get("/api/market/margin", params={"code": "600519"})
        assert resp.json()["data"]["margin_buy"] == 0

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_margin_trading", _raise)
        assert client.get("/api/market/margin", params={"code": "600519"}).json()["_meta"]["degraded"] is True

    def test_block_trades_empty_success_error(self, client, monkeypatch):
        assert client.get("/api/market/block_trades").json()["data"] == []

        item = {"code": "600519", "name": "贵州茅台", "trade_date": "2026-10-28", "price": 1680.0,
                "volume": 100.0, "amount": 168000.0, "premium_pct": 1.0, "buyer": "A", "seller": "B"}
        monkeypatch.setattr(market.ds2, "get_block_trades", lambda code: [item])
        assert client.get("/api/market/block_trades", params={"code": "600519"}).json()["data"][0]["buyer"] == "A"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_block_trades", _raise)
        assert client.get("/api/market/block_trades", params={"code": "600519"}).json()["data"] == []

    def test_shareholder_empty_success_none_error(self, client, monkeypatch):
        assert client.get("/api/market/shareholder").json()["data"]["holder_num"] == 0

        item = {"code": "600519", "end_date": "2026-09-30", "holder_num": 100000, "change_pct": -2.0}
        monkeypatch.setattr(market.ds2, "get_shareholder_count", lambda code: item)
        assert client.get("/api/market/shareholder", params={"code": "600519"}).json()["data"]["holder_num"] == 100000

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_shareholder_count", lambda code: {})
        assert client.get("/api/market/shareholder", params={"code": "600519"}).json()["data"]["holder_num"] == 0

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_shareholder_count", _raise)
        assert client.get("/api/market/shareholder", params={"code": "600519"}).json()["_meta"]["degraded"] is True

    def test_news_global_and_stock_and_error(self, client, monkeypatch):
        item = {"title": "标题", "content": "内容", "url": "http://x", "source": "东财",
                "publish_time": "2026-10-28 10:00:00"}
        monkeypatch.setattr(market.ds2, "get_global_news", lambda: [item])
        monkeypatch.setattr(market.ds2, "get_stock_news", lambda code, page=1, page_size=10: [item])
        assert client.get("/api/market/news").json()["data"][0]["title"] == "标题"
        assert client.get("/api/market/news", params={"code": "600519"}).json()["data"][0]["title"] == "标题"
        assert client.get("/api/market/global_news").json()["data"][0]["source"] == "东财"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_global_news", _raise)
        assert client.get("/api/market/global_news").json()["data"] == []
        assert client.get("/api/market/news").json()["data"] == []


# ===== 热股信号 / 热力图 / 搜索 / 状态 =====


class TestSignalsAndUtilities:
    def test_hot_stocks_signal_ths(self, client, monkeypatch):
        item = {"code": "600519", "name": "贵州茅台", "price": 1680.0, "change_pct": 9.9,
                "volume": 1.0, "reason": "涨停", "limit_up_time": "09:35", "open_times": 0}
        monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", lambda: [item])
        resp = client.get("/api/market/hot_stocks_signal")
        assert resp.json()["data"][0]["reason"] == "涨停"

    def test_hot_stocks_signal_fallback_and_error(self, client, monkeypatch):
        item = {"code": "600519", "name": "贵州茅台", "price": 1680.0, "change_pct": 9.9,
                "volume": 1.0, "reason": "涨停", "limit_up_time": "", "open_times": 1}
        monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", lambda: [])
        monkeypatch.setattr(market.ds2, "get_hot_stocks_signal_fallback", lambda: [item])
        resp = client.get("/api/market/hot_stocks_signal")
        assert resp.json()["_meta"]["data_source"] == "eastmoney"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", _raise)
        assert client.get("/api/market/hot_stocks_signal").json()["data"] == []

    def test_heatmap_color_branches(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2,
            "get_sector_ranking",
            lambda: [
                {"name": "涨", "change_pct": 2.0},
                {"name": "跌", "change_pct": -2.0},
                {"name": "平", "change_pct": 0},
            ],
        )
        data = client.get("/api/market/heatmap").json()["data"]
        assert data[0]["color"] == "rgb(51,0,0)"
        assert data[1]["color"] == "rgb(0,51,0)"
        assert data[2]["color"] == "rgb(128,128,128)"

    def test_heatmap_error(self, client, monkeypatch):
        monkeypatch.setattr(market.ds2, "get_sector_ranking", _raise)
        assert client.get("/api/market/heatmap").json()["data"] == []

    def test_search_success_and_error(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2, "search_stock", lambda q: [{"code": "600519", "name": "贵州茅台", "price": 1680.0, "change_pct": 1.0}]
        )
        resp = client.get("/api/market/search", params={"q": "茅台"})
        assert resp.json()["data"][0]["code"] == "600519"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "search_stock", _raise)
        assert client.get("/api/market/search", params={"q": "茅台"}).json()["data"] == []

    def test_status_shape(self, client):
        payload = client.get("/api/market/status").json()
        assert payload["data"]["session"] in {"morning", "afternoon", "lunch_break", "closed", "weekend"}
        assert payload["_meta"]["quality_score"] == 100.0


# ===== 个股详情 / 北向 / 情绪 / 统计 =====


class TestDetailAndSentiment:
    def test_stock_detail_success_and_error(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2,
            "get_stock_detail",
            lambda code: {
                "code": code, "name": "贵州茅台", "quote": {"price": 1680.0},
                "financial": {"pe": 30.0}, "capital_flow": {"main": 1.0}, "kline_summary": {"close": 1680.0},
            },
        )
        resp = client.get("/api/market/stock_detail", params={"code": "600519"})
        assert resp.json()["data"]["name"] == "贵州茅台"

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_stock_detail", _raise)
        resp = client.get("/api/market/stock_detail", params={"code": "600519"})
        assert resp.json()["data"]["name"] == ""
        assert resp.json()["_meta"]["degraded"] is True

    def test_northbound_success_empty_error(self, client, monkeypatch):
        monkeypatch.setattr(
            market.ds2,
            "get_northbound_flow_realtime",
            lambda: {"date": "2026-10-28", "total_net_inflow": 1e9, "sh_net_inflow": 6e8, "sz_net_inflow": 4e8},
        )
        assert client.get("/api/market/northbound").json()["data"]["total_net_inflow"] == 1e9

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_northbound_flow_realtime", lambda: {})
        assert client.get("/api/market/northbound").json()["data"]["total_net_inflow"] == 0.0

        market.cache.clear()
        monkeypatch.setattr(market.ds2, "get_northbound_flow_realtime", _raise)
        resp = client.get("/api/market/northbound")
        assert resp.status_code == 200
        assert resp.json()["data"]["total_net_inflow"] == 0.0

    def test_sentiment_and_wide_stats(self, client, monkeypatch):
        monkeypatch.setattr(market.ds2, "get_market_sentiment", lambda: {"score": 60})
        monkeypatch.setattr(
            market.ds2,
            "get_market_wide_stats",
            lambda: {"margin_balance": 1.0, "block_trades_count": "3", "avg_shareholder_change_pct": -1.0},
        )
        assert client.get("/api/market/market_sentiment").json()["data"]["score"] == 60
        assert client.get("/api/market/market_sentiment").json()["_meta"]["cached"] is True
        stats = client.get("/api/market/market_wide_stats").json()["data"]
        assert stats["block_trades_count"] == 3


# ===== 聚合端点(温度计 / 资金流 / 综合研判 / 数据质量) =====


class TestAggregators:
    @pytest.fixture(autouse=True)
    def _patch_assessment(self, monkeypatch):
        import src.analysis.market_assessment as ma

        monkeypatch.setattr(ma, "get_market_thermometer", AsyncMock(return_value={"score": 70}))
        monkeypatch.setattr(ma, "get_capital_flow_overview", AsyncMock(return_value={"north": 1.0}))
        monkeypatch.setattr(ma, "get_market_assessment", AsyncMock(return_value={"verdict": "中性"}))

    def test_thermometer(self, client):
        assert client.get("/api/market/thermometer").json()["data"]["score"] == 70
        assert client.get("/api/market/thermometer").json()["_meta"]["cached"] is True

    def test_capital_flow_overview(self, client):
        assert client.get("/api/market/capital-flow").json()["data"]["north"] == 1.0

    def test_assessment(self, client):
        assert client.get("/api/market/assessment").json()["data"]["verdict"] == "中性"

    def test_data_quality_endpoint(self, client, monkeypatch):
        import src.core.data_source_v2 as ds2_mod

        monkeypatch.setattr(
            ds2_mod, "get_realtime_quote_tencent", lambda codes: [{"code": codes[0], "price": 10.0}]
        )
        monkeypatch.setattr(
            ds2_mod,
            "get_kline_mootdx",
            lambda code, period="daily", count=30: [
                {"date": "2026-10-%02d" % i, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "amount": 1}
                for i in range(1, 11)
            ],
        )
        monkeypatch.setattr(ds2_mod, "get_capital_flow_detail", _raise)  # 异常隔离
        monkeypatch.setattr(ds2_mod, "get_financial_snapshot", lambda code: {"pe": 20.0})

        payload = client.get("/api/market/data-quality/600519").json()
        assert payload["stock_code"] == "600519"
        assert payload["data_dimensions"] == {
            "quote": True, "kline": True, "capital_flow": False, "financial": True
        }
        assert isinstance(payload["quality_score"], (int, float))

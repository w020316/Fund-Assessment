"""东财 datacenter 报表契约回归测试

2026-11-15 实测发现东财悄悄下线/改名了多张报表, 导致融资融券、大宗交易、
股东户数、龙虎榜四个接口静默返回空列表。本测试锁定新的报表名、筛选列与
字段映射, 防止上游再次变更时无声退化。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core import data_source_v2 as ds2


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ok(rows, count=None):
    return _FakeResp({"success": True, "result": {"data": rows, "count": count if count is not None else len(rows)}})


def test_margin_trading_uses_current_report_and_fields(monkeypatch):
    captured = {}

    def fake_em_get(url, params=None, **kwargs):
        captured.update(params or {})
        return _ok([{
            "SCODE": "600519", "DATE": "2026-11-13 00:00:00",
            "RZMRE": 3.6e8, "RZYE": 1.5e10, "RQMCL": 10400,
            "RQYE": 3.1e8, "RZRQYE": 1.53e10,
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_margin_trading("600519")

    assert captured["reportName"] == "RPTA_WEB_RZRQ_GGMX"
    assert 'SCODE="600519"' in captured["filter"]
    assert captured["sortColumns"] == "DATE"
    assert result == {
        "code": "600519",
        "trade_date": "2026-11-13",
        "margin_buy": 3.6e8,
        "margin_balance": 1.5e10,
        "short_sell": 10400,
        "short_balance": 3.1e8,
        "total_balance": 1.53e10,
    }


def test_block_trades_uses_current_report_and_fields(monkeypatch):
    captured = {}

    def fake_em_get(url, params=None, **kwargs):
        captured.update(params or {})
        return _ok([{
            "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
            "TRADE_DATE": "2026-11-12 00:00:00", "DEAL_PRICE": 1410.27,
            "DEAL_VOLUME": 16900, "DEAL_AMT": 2.38e7, "PREMIUM_RATIO": -1.5,
            "BUYER_NAME": "机构专用", "SELLER_NAME": "机构专用",
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_block_trades("600519")

    assert captured["reportName"] == "RPT_DATA_BLOCKTRADE"
    assert len(result) == 1
    assert result[0]["volume"] == 16900
    assert result[0]["premium_pct"] == -1.5
    assert result[0]["name"] == "贵州茅台"


def test_shareholder_count_uses_renamed_fields(monkeypatch):
    def fake_em_get(url, params=None, **kwargs):
        return _ok([
            {"SECURITY_CODE": "600519", "END_DATE": "2026-09-30 00:00:00",
             "HOLDER_TOTAL_NUM": 278364, "TOTAL_NUM_RATIO": -6.0863},
            {"SECURITY_CODE": "600519", "END_DATE": "2026-06-30 00:00:00",
             "HOLDER_TOTAL_NUM": 296400, "TOTAL_NUM_RATIO": 1.2},
        ])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_shareholder_count("600519")

    assert result == {
        "code": "600519",
        "end_date": "2026-09-30",
        "holder_num": 278364,
        "change_pct": -6.09,
    }


def test_dragon_tiger_single_request_and_latest_date(monkeypatch):
    """只发一次请求(避免 Render 上两次往返超时),并按返回集里的最近交易日截取。"""
    calls = []

    def fake_em_get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return _ok([
            {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
             "CLOSE_PRICE": 1435.26, "CHANGE_RATE": 1.77,
             "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
             "TOTAL_BUY": 1.2e8, "TOTAL_SELL": 9.1e7, "TOTAL_NET": 2.9e7,
             "TRADE_DATE": "2026-11-13 00:00:00"},
            {"SECURITY_CODE": "000858", "SECURITY_NAME_ABBR": "五粮液",
             "CLOSE_PRICE": 130.0, "CHANGE_RATE": 0.5,
             "EXPLANATION": "旧交易日数据",
             "TOTAL_BUY": 1.0, "TOTAL_SELL": 1.0, "TOTAL_NET": 0.0,
             "TRADE_DATE": "2026-11-12 00:00:00"},
        ])

    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", fake_em_get)

    result = ds2.get_dragon_tiger()

    assert len(calls) == 1
    assert calls[0]["reportName"] == "RPT_BILLBOARD_DAILYDETAILS"
    assert "filter" not in calls[0]
    assert len(result) == 1, "应只保留最近交易日(2026-11-13)的行"
    assert result == [{
        "code": "600519",
        "name": "贵州茅台",
        "price": 1435.26,
        "change_pct": 1.77,
        "reason": "日涨幅偏离值达到7%的前5只证券",
        "buy_amount": 1.2e8,
        "sell_amount": 9.1e7,
        "net_amount": 2.9e7,
        "trade_date": "2026-11-13",
    }]


def test_market_wide_stats_shareholder_uses_median(monkeypatch):
    def fake_em_get(url, params=None, **kwargs):
        report = (params or {}).get("reportName")
        if report == "RPT_DATA_BLOCKTRADE" and "filter" not in (params or {}):
            return _ok([{"TRADE_DATE": "2026-11-13 00:00:00"}])
        if report == "RPT_DATA_BLOCKTRADE":
            return _ok([{"TRADE_DATE": "2026-11-13 00:00:00"}], count=72)
        if report == "RPT_F10_EH_HOLDERNUM":
            return _ok([
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": -6.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": -2.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": 0.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": 2.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": 4.0},
                {"HOLDER_TOTAL_NUM": 19835, "TOTAL_NUM_RATIO": 46027.9},
            ])
        raise AssertionError(f"unexpected report {report}")

    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    monkeypatch.setattr(
        ds2, "_fetch_market_margin",
        lambda: {"trade_date": "2026-11-12", "margin_balance": 2.29e12},
    )

    result = ds2.get_market_wide_stats()

    assert result["block_trades_count"] == 72
    assert result["avg_shareholder_change_pct"] == 1.0
    assert result["margin_balance"] == 2.29e12
    assert result["margin_trade_date"] == "2026-11-12"


def test_market_wide_stats_margin_none_when_exchanges_unavailable(monkeypatch):
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _ok([]))
    monkeypatch.setattr(ds2, "_fetch_market_margin", lambda: None)

    result = ds2.get_market_wide_stats()

    assert result["margin_balance"] is None
    assert result["margin_trade_date"] is None


class _MarketMarginFakeGet:
    """按 URL 分派沪深交易所两融接口;szse_dates 之外的日期返回空数据。"""

    def __init__(self, sse_rows, szse_by_date):
        self.sse_rows = sse_rows
        self.szse_by_date = szse_by_date
        self.szse_queries: list[str] = []

    def __call__(self, url, params=None, **kwargs):
        if url == ds2._SSE_MARGIN_URL:
            return _FakeResp({"pageHelp": {"data": self.sse_rows}})
        if url == ds2._SZSE_MARGIN_URL:
            date = (params or {}).get("txtDate")
            self.szse_queries.append(date)
            row = self.szse_by_date.get(date)
            return _FakeResp([{"data": [row] if row else []}])
        raise AssertionError(f"unexpected url {url}")


def test_market_margin_aligns_latest_common_trade_date(monkeypatch):
    """深交所晚一天发布:必须退到两市都有数据的 11-12,而不是把 11-13 的沪市加 11-12 的深市。"""
    fake = _MarketMarginFakeGet(
        sse_rows=[
            {"opDate": "20261113", "rzrqjyzl": 1.0e12, "rzye": 9.9e11, "rqylje": 1.0e10},
            {"opDate": "20261112", "rzrqjyzl": 0.9e12, "rzye": 8.9e11, "rqylje": 1.0e10},
        ],
        szse_by_date={"2026-11-12": {"jrrzrjye": "10.00"}},
    )
    monkeypatch.setattr(ds2.requests, "get", fake)

    result = ds2._fetch_market_margin()

    assert fake.szse_queries == ["2026-11-13", "2026-11-12"]
    assert result == {"trade_date": "2026-11-12", "margin_balance": 901_000_000_000.0}


def test_market_margin_uses_same_day_when_szse_published(monkeypatch):
    fake = _MarketMarginFakeGet(
        sse_rows=[{"opDate": "20261113", "rzrqjyzl": 1.0e12}],
        szse_by_date={"2026-11-13": {"jrrzrjye": "5.00"}},
    )
    monkeypatch.setattr(ds2.requests, "get", fake)

    assert ds2._fetch_market_margin() == {
        "trade_date": "2026-11-13",
        "margin_balance": 1.0005e12,  # 5.00 亿元 = 5e8 元
    }


def test_market_margin_none_without_common_date(monkeypatch):
    fake = _MarketMarginFakeGet(
        sse_rows=[{"opDate": "20261113", "rzrqjyzl": 1.0e12},
                  {"opDate": "20261112", "rzrqjyzl": 0.9e12}],
        szse_by_date={},
    )
    monkeypatch.setattr(ds2.requests, "get", fake)

    assert ds2._fetch_market_margin() is None


def test_northbound_net_discontinued_returns_empty(monkeypatch):
    """报表只有成交额、净额字段为 None 时返回空,不得把 None 当 0 净流入卖出去。"""
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _FakeResp({"result": {"data": [{
        "TRADE_DATE": "2026-11-13 00:00:00", "DEAL_AMT": 111733.79,
        "NET_DEAL_AMT": None, "FUND_INFLOW": None,
        "BUY_AMT": None, "SELL_AMT": None,
    }]}}))

    assert ds2.get_northbound_flow_realtime() == {}


def test_northbound_resumes_with_total_only(monkeypatch):
    """上游若恢复净额但无沪深拆分, 返回合计并把 sh_sz_split_available 标 False。"""
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _FakeResp({"result": {"data": [{
        "TRADE_DATE": "2026-11-13 00:00:00", "DEAL_AMT": 111733.79,
        "NET_DEAL_AMT": 5.0e9, "FUND_INFLOW": None,
    }]}}))

    result = ds2.get_northbound_flow_realtime()

    assert result["total_net_inflow"] == 5.0e9
    assert result["date"] == "2026-11-13"
    assert result["sh_sz_split_available"] is False
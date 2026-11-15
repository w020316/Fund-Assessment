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


def test_dragon_tiger_uses_latest_trade_date(monkeypatch):
    calls = []

    def fake_em_get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        if "filter" not in (params or {}):
            return _ok([{"TRADE_DATE": "2026-11-13 00:00:00"}])
        return _ok([{
            "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
            "CLOSE_PRICE": 1435.26, "CHANGE_RATE": 1.77,
            "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
            "TOTAL_BUY": 1.2e8, "TOTAL_SELL": 9.1e7, "TOTAL_NET": 2.9e7,
            "TRADE_DATE": "2026-11-13 00:00:00",
        }])

    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", fake_em_get)

    result = ds2.get_dragon_tiger()

    assert len(calls) == 2
    assert calls[0]["reportName"] == "RPT_BILLBOARD_DAILYDETAILS"
    assert "filter" not in calls[0], "第一次请求应取最近交易日, 不带日期过滤"
    assert calls[1]["reportName"] == "RPT_BILLBOARD_DAILYDETAILS"
    assert calls[1]["filter"] == "(TRADE_DATE='2026-11-13')"
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

    result = ds2.get_market_wide_stats()

    assert result["block_trades_count"] == 72
    assert result["avg_shareholder_change_pct"] == 1.0
    assert result["margin_balance"] is None
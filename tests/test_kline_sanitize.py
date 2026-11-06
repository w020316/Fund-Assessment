"""R-08 回归:K 线与搜索缺失值不得静默转成 0。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core import data_source_v2 as ds2


class TestKlineSanitize:
    def test_drops_zero_and_negative_close(self):
        rows = [
            {"date": "2026-01-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
            {"date": "2026-01-02", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
            {"date": "2026-01-03", "open": 0, "high": 0, "low": 0, "close": -1, "volume": 0},
        ]
        clean = ds2._sanitize_kline_rows(rows)
        assert len(clean) == 1
        assert clean[0]["date"] == "2026-01-01"

    def test_fills_missing_ohlc_with_close(self):
        rows = [{"date": "2026-01-01", "open": 0, "high": None, "low": "", "close": 12.5, "volume": None}]
        clean = ds2._sanitize_kline_rows(rows)
        assert clean[0]["open"] == 12.5
        assert clean[0]["high"] == 12.5
        assert clean[0]["low"] == 12.5
        assert clean[0]["volume"] == 0.0


class TestSearchMissingPrice:
    def test_missing_quote_returns_none_not_zero(self, monkeypatch):
        resp = SimpleNamespace(encoding="gbk", text='"sh600519^贵州茅台^GP-A"')
        monkeypatch.setattr(ds2.requests, "get", lambda *_a, **_k: resp)
        monkeypatch.setattr(ds2, "get_realtime_quote_tencent", lambda _codes: [])

        rows = ds2._search_stock_tencent("茅台")
        assert rows and rows[0]["code"] == "sh600519"
        assert rows[0]["price"] is None
        assert rows[0]["change_pct"] is None

    def test_tencent_tilde_format_is_parsed(self, monkeypatch):
        """阶段四回归：腾讯 smartbox 实际用 "~" 分隔，前缀与代码分列。"""
        resp = SimpleNamespace(
            encoding="gbk",
            text='v_hint="sh~510300~沪深300ETF华泰柏瑞~hs300etfhtbr~ETF"',
        )
        monkeypatch.setattr(ds2.requests, "get", lambda *_a, **_k: resp)
        monkeypatch.setattr(ds2, "get_realtime_quote_tencent", lambda _codes: [])

        rows = ds2._search_stock_tencent("510300")
        assert rows and rows[0]["code"] == "510300"
        assert rows[0]["name"] == "沪深300ETF华泰柏瑞"

    def test_tencent_escaped_name_is_decoded(self, monkeypatch):
        """腾讯把中文名以字面 \\uXXXX 返回，必须还原，否则 UI 会显示转义串。"""
        resp = SimpleNamespace(
            encoding="gbk",
            text=r'v_hint="sh~510300~\u6caa\u6df1300ETF~hs300etfhtbr~ETF"',
        )
        monkeypatch.setattr(ds2.requests, "get", lambda *_a, **_k: resp)
        monkeypatch.setattr(ds2, "get_realtime_quote_tencent", lambda _codes: [])

        rows = ds2._search_stock_tencent("510300")
        assert rows and rows[0]["name"] == "沪深300ETF"


class TestSearchSourceAvailability:
    """阶段四 U1/W1：区分"数据源故障"与"确实无匹配"。"""

    def test_all_sources_down_raises(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("network down")

        monkeypatch.setattr(ds2.requests, "get", _boom)
        with pytest.raises(ds2.DataSourceUnavailable):
            ds2.search_stock("茅台")

    def test_upstream_empty_is_not_an_error(self, monkeypatch):
        # 新浪与腾讯都正常响应但无匹配 → 返回空列表，不抛异常
        resp = SimpleNamespace(encoding="gbk", text='""')
        monkeypatch.setattr(ds2.requests, "get", lambda *_a, **_k: resp)
        assert ds2.search_stock("不存在的标的") == []

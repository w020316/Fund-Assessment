"""R-08 回归:K 线与搜索缺失值不得静默转成 0。"""
from __future__ import annotations

from types import SimpleNamespace

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

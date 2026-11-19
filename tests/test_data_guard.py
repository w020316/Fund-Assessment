# -*- coding: utf-8 -*-
"""QF-DataGuard M1+M2 测试(docs/quality-research-2026-11-19-r7-stage6.md §3.1)。

覆盖:
- 契约层 0 值兜底结构性拒绝(红线机制化) + 缺失=None 语义
- tencent/eastmoney 适配器: 门面转发 + 规范化, 快照逐位一致
- SourceRouter: 降级编排/全失败不伪造/degraded 标记
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.data_guard import (
    KlineContract,
    KlineProvider,
    QuoteContract,
    QuoteProvider,
    SourceRouter,
    normalize_kline_row,
    normalize_quote,
)
from src.core.data_guard.providers.eastmoney import EastmoneyProvider
from src.core.data_guard.providers.tencent import TencentProvider, TencentQuoteListProvider

# ── 快照样本: 与 data_source_v2.get_realtime_quote_tencent 输出结构逐字段一致 ──

SAMPLE_QUOTE = {
    "code": "600519", "name": "贵州茅台", "price": 1499.0, "prev_close": 1490.0,
    "open": 1491.0, "change": 9.0, "change_pct": 0.6, "high": 1505.0, "low": 1488.0,
    "volume": 23150.0, "amount": 3478123456.0, "turnover": 0.18,
    "pe_ttm": 21.5, "pb": 8.3, "total_market_value": 1880317300000.0,
    "circ_market_value": 1880317300000.0, "high_limit": 1639.0, "low_limit": 1341.0,
}

SAMPLE_KLINE_ROW = {
    "date": "2026-11-18", "open": 1487.0, "high": 1502.0,
    "low": 1480.0, "close": 1499.0, "volume": 23150.0,
}


class TestQuoteContract:
    """契约层: 0 值兜底结构性拒绝(红线机制化)。"""

    def test_valid_quote_accepted(self):
        q = QuoteContract(code="600519", source="tencent", price=10.0, prev_close=9.8)
        assert q.price == 10.0 and q.degraded is False

    def test_zero_price_rejected(self):
        """price=0 是伪数据兜底 → 直接 ValidationError(不是静默接受)。"""
        with pytest.raises(ValidationError):
            QuoteContract(code="600519", source="tencent", price=0.0)

    def test_zero_prev_close_rejected(self):
        with pytest.raises(ValidationError):
            QuoteContract(code="600519", source="tencent", prev_close=0.0)

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError):
            QuoteContract(code="600519", source="tencent", price=-1.0)

    def test_missing_price_as_none_allowed(self):
        """缺失必须显式 None —— 这才是契约要求的数据缺失表达。"""
        q = QuoteContract(code="600519", source="tencent", price=None)
        assert q.price is None

    def test_zero_volume_allowed(self):
        """成交量 0 是停牌合法值, 不在拒绝之列。"""
        q = QuoteContract(code="600519", source="tencent", volume=0.0)
        assert q.volume == 0.0

    def test_negative_volume_rejected(self):
        with pytest.raises(ValidationError):
            QuoteContract(code="600519", source="tencent", volume=-5.0)

    def test_kline_zero_close_rejected(self):
        with pytest.raises(ValidationError):
            KlineContract(date="2026-11-18", source="tencent", close=0.0)

    def test_unknown_field_rejected(self):
        """extra=forbid: 上游多出的字段必须在适配器层显式处理, 不静默吞掉。"""
        with pytest.raises(ValidationError):
            QuoteContract(code="600519", source="tencent", unexpected_field=1)


class TestNormalizeQuote:
    """normalize_quote: 原始层 _safe_float 伪 0 → None 的缺失语义恢复。"""

    def test_full_sample_roundtrip_exact(self):
        """快照: 非零样本规范化后数值字段与原始 dict 逐位一致。"""
        q = normalize_quote(SAMPLE_QUOTE, source="tencent")
        for key in ("price", "prev_close", "open", "high", "low",
                    "change", "change_pct", "volume", "amount", "turnover",
                    "high_limit", "low_limit"):
            assert getattr(q, key) == SAMPLE_QUOTE[key], key
        assert q.code == "600519" and q.name == "贵州茅台"

    def test_zero_becomes_none_for_price_fields(self):
        raw = dict(SAMPLE_QUOTE, price=0.0, prev_close=0.0, open=0.0, high=0.0, low=0.0)
        q = normalize_quote(raw, source="tencent")
        assert q.price is None and q.prev_close is None
        assert q.open is None and q.high is None and q.low is None
        # 非价格字段 0 合法保留
        assert q.volume == SAMPLE_QUOTE["volume"]

    def test_empty_name_becomes_none(self):
        q = normalize_quote(dict(SAMPLE_QUOTE, name=""), source="tencent")
        assert q.name is None


class TestNormalizeKlineRow:
    def test_roundtrip_exact(self):
        k = normalize_kline_row(SAMPLE_KLINE_ROW, source="eastmoney")
        assert k.date == "2026-11-18"
        assert (k.open, k.high, k.low, k.close, k.volume) == (
            1487.0, 1502.0, 1480.0, 1499.0, 23150.0)

    def test_zero_price_becomes_none(self):
        k = normalize_kline_row(dict(SAMPLE_KLINE_ROW, close=0.0), source="eastmoney")
        assert k.close is None


class TestTencentProvider:
    def test_is_quote_provider(self):
        assert isinstance(TencentProvider(), QuoteProvider)

    def test_fetch_quote_snapshot(self):
        """快照: 转发 get_realtime_quote_tencent, 数值字段逐位一致。"""
        with patch("src.core.data_source_v2.get_realtime_quote_tencent",
                   return_value=[SAMPLE_QUOTE]):
            q = TencentProvider().fetch_quote("600519")
        assert q is not None
        assert q.code == "600519" and q.source == "tencent"
        assert q.price == 1499.0 and q.prev_close == 1490.0
        assert q.degraded is False

    def test_empty_result_returns_none(self):
        with patch("src.core.data_source_v2.get_realtime_quote_tencent", return_value=[]):
            assert TencentProvider().fetch_quote("600519") is None

    def test_exception_propagates(self):
        """适配器不吞异常: 降级决策权在 SourceRouter。"""
        with patch("src.core.data_source_v2.get_realtime_quote_tencent",
                   side_effect=RuntimeError("network down")):
            with pytest.raises(RuntimeError):
                TencentProvider().fetch_quote("600519")

    def test_batch_provider(self):
        with patch("src.core.data_source_v2.get_realtime_quote_tencent",
                   return_value=[SAMPLE_QUOTE]):
            items = TencentQuoteListProvider().fetch_quotes(["600519"])
        assert items is not None and len(items) == 1
        assert items[0].price == 1499.0

    def test_batch_empty_returns_none(self):
        with patch("src.core.data_source_v2.get_realtime_quote_tencent", return_value=[]):
            assert TencentQuoteListProvider().fetch_quotes(["600519"]) is None


class TestEastmoneyProvider:
    def test_is_kline_provider(self):
        assert isinstance(EastmoneyProvider(), KlineProvider)

    def test_fetch_kline_snapshot(self):
        rows = [SAMPLE_KLINE_ROW, dict(SAMPLE_KLINE_ROW, date="2026-11-19", close=1505.0)]
        with patch("src.core.data_source_v2._kline_em_fallback", return_value=rows):
            items = EastmoneyProvider().fetch_kline("600519", 2)
        assert items is not None and len(items) == 2
        assert items[0].close == 1499.0 and items[1].close == 1505.0
        assert items[0].source == "eastmoney" and items[0].degraded is False

    def test_empty_returns_none(self):
        with patch("src.core.data_source_v2._kline_em_fallback", return_value=[]):
            assert EastmoneyProvider().fetch_kline("600519", 5) is None

    def test_exception_propagates(self):
        with patch("src.core.data_source_v2._kline_em_fallback",
                   side_effect=RuntimeError("em down")):
            with pytest.raises(RuntimeError):
                EastmoneyProvider().fetch_kline("600519", 5)


class _FakeProvider:
    def __init__(self, name: str, priority: int, *, quote=None, kline=None, error=None):
        self.name = name
        self.priority = priority
        self._quote, self._kline, self._error = quote, kline, error

    def fetch_quote(self, code: str):
        if self._error:
            raise self._error
        return self._quote

    def fetch_kline(self, symbol: str, count: int):
        if self._error:
            raise self._error
        return self._kline


class TestSourceRouter:
    def test_primary_success_not_degraded(self):
        primary = _FakeProvider("a", 1, quote=normalize_quote(SAMPLE_QUOTE, source="a"))
        router = SourceRouter([primary])
        q = router.fetch_quote("600519")
        assert q is not None and q.degraded is False and q.price == 1499.0

    def test_priority_ordering(self):
        """乱序传入也按 priority 升序执行。"""
        low = _FakeProvider("b", 99, quote=normalize_quote(SAMPLE_QUOTE, source="b"))
        high = _FakeProvider("a", 1, quote=normalize_quote(SAMPLE_QUOTE, source="a"))
        router = SourceRouter([low, high])
        q = router.fetch_quote("600519")
        assert q is not None and q.source == "a" and q.degraded is False

    def test_primary_none_falls_back_with_degraded(self):
        """首选源明确无数据 → 降级次选并标 degraded。"""
        secondary = _FakeProvider("b", 2, quote=normalize_quote(SAMPLE_QUOTE, source="b"))
        primary = _FakeProvider("a", 1, quote=None)
        router = SourceRouter([primary, secondary])
        q = router.fetch_quote("600519")
        assert q is not None and q.degraded is True and q.source == "b"

    def test_primary_error_falls_back_with_degraded(self):
        """首选源异常 → 记日志降级次选(单源失败不中断降级链)。"""
        secondary = _FakeProvider("b", 2, quote=normalize_quote(SAMPLE_QUOTE, source="b"))
        primary = _FakeProvider("a", 1, error=RuntimeError("a down"))
        router = SourceRouter([primary, secondary])
        q = router.fetch_quote("600519")
        assert q is not None and q.degraded is True

    def test_all_fail_returns_none_no_fabrication(self):
        """全部失败 → None, 绝不返回伪造数据(数据真实性红线)。"""
        primary = _FakeProvider("a", 1, quote=None)
        secondary = _FakeProvider("b", 2, error=RuntimeError("b down"))
        router = SourceRouter([primary, secondary])
        assert router.fetch_quote("600519") is None

    def test_no_provider_supports_kind_returns_none(self):
        """无任何 provider 实现该能力 → None(不抛错)。"""
        quote_only = _FakeProvider("a", 1, quote=normalize_quote(SAMPLE_QUOTE, source="a"))
        router = SourceRouter([quote_only])
        assert router.fetch_kline("600519", 5) is None

    def test_kline_degraded_marks_every_row(self):
        """K 线降级: 列表中每一根都标 degraded。"""
        rows = [normalize_kline_row(SAMPLE_KLINE_ROW, source="b"),
                normalize_kline_row(SAMPLE_KLINE_ROW, source="b")]
        secondary = _FakeProvider("b", 2, kline=rows)
        primary = _FakeProvider("a", 1, kline=None)
        router = SourceRouter([primary, secondary])
        items = router.fetch_kline("600519", 2)
        assert items is not None and all(i.degraded for i in items)


# ── M3 绞杀迁移快照: 迁移前后行为逐位一致 ──────────────────────────────

def _tencent_segment(code: str, name: str) -> str:
    """构造腾讯原始响应段: `v_sh<code>="f0~f1~...";` 按解析索引精确对齐。

    索引映射(见 providers/tencent.fetch_quotes_raw): 1=名称 2=代码 3=现价
    4=昨收 5=今开 31=涨跌 32=涨跌% 33=最高 34=最低 36=成交量 37=成交额
    38=换手 39=PE 44=流通市值 45=总市值 46=PB 47=涨停 48=跌停。
    """
    values = {
        1: name, 2: code, 3: "1499.00", 4: "1490.00", 5: "1491.00",
        6: "20261119150959", 31: "9.00", 32: "0.60", 33: "1505.00",
        34: "1488.00", 36: "23150", 37: "3478123456.00", 38: "0.18",
        39: "21.50", 44: "1880317300000.00", 45: "1880317300000.00",
        46: "8.30", 47: "1639.00", 48: "1341.00",
    }
    fields = [values.get(i, "0") for i in range(50)]
    return f'v_sh{code}="{"~".join(fields)}";'


class TestMigrationSnapshotTencent:
    """M3-A: 解析体迁入 providers/tencent 后, 门面输出逐位一致 + 缓存行为不变。"""

    def test_snapshot_exact_and_cache_hit(self):
        from src.core import data_source_v2 as ds2
        from src.core.data_guard.providers import tencent as tp

        # 用不常见代码避免与其他测试/线上残留缓存串键
        code = "999777"
        seg = _tencent_segment(code, "快照测试股")
        fake_resp = type("R", (), {"encoding": "", "text": f"{seg};"})()
        with patch.object(tp.requests, "get", return_value=fake_resp) as mock_get:
            out1 = ds2.get_realtime_quote_tencent([code])
            out2 = ds2.get_realtime_quote_tencent([code])  # TTL 5s 内 → 缓存命中
        assert mock_get.call_count == 1, "第二次调用必须命中缓存, 不得重复请求"

        assert len(out1) == 1
        row = out1[0]
        # 逐位一致(数值以 _safe_float 转换后的值断言)
        assert row["code"] == code and row["name"] == "快照测试股"
        assert row["price"] == 1499.0
        assert row["prev_close"] == 1490.0
        assert row["open"] == 1491.0
        assert row["high"] == 1505.0 and row["low"] == 1488.0
        assert row["change"] == 9.0 and row["change_pct"] == 0.60
        assert row["volume"] == 23150.0
        assert row["amount"] == 3478123456.0
        assert row["turnover"] == 0.18
        assert row["pe_ttm"] == 21.5 and row["pb"] == 8.3
        assert row["total_market_value"] == 1880317300000.0
        assert row["circ_market_value"] == 1880317300000.0
        assert row["high_limit"] == 1639.0 and row["low_limit"] == 1341.0
        # 缓存命中返回同一份数据
        assert out2 == out1

    def test_provider_raw_matches_facade(self):
        """provider.fetch_quotes_raw 与门面(绕过缓存差异)输出一致。"""
        from src.core import data_source_v2 as ds2
        from src.core.data_guard.providers.tencent import fetch_quotes_raw

        code = "999778"
        seg = _tencent_segment(code, "一致性股")
        fake_resp = type("R", (), {"encoding": "", "text": f"{seg};"})()
        with patch.object(tp_requests(), "get", return_value=fake_resp):
            via_provider = fetch_quotes_raw([code])
        with patch.object(tp_requests(), "get", return_value=fake_resp):
            via_facade = ds2.get_realtime_quote_tencent([code])
        assert via_provider == via_facade


def tp_requests():
    """返回 provider 模块的 requests(供 patch), 避免测试模块顶层 import 时序问题。"""
    from src.core.data_guard.providers import tencent as tp
    return tp.requests


class TestMigrationSnapshotEastmoney:
    """M3-B: 东财 K 线解析体迁移后行为逐位一致(含异常返回 [] 的旧契约)。"""

    def _fake_em_get(self, klines: list[str]):
        fake = type("R", (), {})()
        fake.json = lambda: {"data": {"klines": klines}}
        return fake

    def test_snapshot_exact(self):
        from src.core import data_source_v2 as ds2

        klines = [
            "2026-11-18,1487.00,1499.00,1502.00,1480.00,23150,3478123456.00",
            "2026-11-19,1499.00,1505.00,1508.00,1495.00,18000,2700000000.00",
        ]
        with patch("src.core.data_source_v2.em_get",
                   return_value=self._fake_em_get(klines)):
            rows = ds2._kline_em_fallback("600519", "daily", 2)
        assert len(rows) == 2
        assert rows[0] == {
            "date": "2026-11-18", "open": 1487.0, "close": 1499.0,
            "high": 1502.0, "low": 1480.0, "volume": 23150.0,
            "amount": 3478123456.0,
        }
        assert rows[1]["close"] == 1505.0 and rows[1]["date"] == "2026-11-19"

    def test_exception_returns_empty_list(self):
        """旧契约: em_get 异常 → warning + [](不抛出)。"""
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")):
            assert ds2._kline_em_fallback("600519", "daily", 5) == []


# ── M3-C/D/E 绞杀迁移快照 ──────────────────────────────────────────────

class TestMigrationSnapshotSina:
    """M3-C: 新浪 K 线解析体迁移后行为逐位一致。"""

    def test_snapshot_exact(self):
        import json as _json

        from src.core import data_source_v2 as ds2

        items = [
            {"day": "2026-11-18 15:00:00", "open": "1487.00", "high": "1502.00",
             "low": "1480.00", "close": "1499.00", "volume": "23150"},
            {"day": "2026-11-19 15:00:00", "open": "1499.00", "high": "1508.00",
             "low": "1495.00", "close": "1505.00", "volume": "18000"},
        ]
        text = f'var =({ _json.dumps(items) });'
        fake_resp = type("R", (), {"text": text})()
        with patch("src.core.data_guard.providers.sina.requests.get",
                   return_value=fake_resp):
            rows = ds2._kline_sina_fallback("600519", "daily", 2)
        assert len(rows) == 2
        # date 截断到 10 位, amount 恒 0.0(新浪接口无成交额字段)
        assert rows[0] == {
            "date": "2026-11-18", "open": 1487.0, "high": 1502.0,
            "low": 1480.0, "close": 1499.0, "volume": 23150.0, "amount": 0.0,
        }
        assert rows[1]["date"] == "2026-11-19" and rows[1]["close"] == 1505.0

    def test_no_jsonp_parens_returns_empty(self):
        from src.core import data_source_v2 as ds2

        fake_resp = type("R", (), {"text": "no parens here"})()
        with patch("src.core.data_guard.providers.sina.requests.get",
                   return_value=fake_resp):
            assert ds2._kline_sina_fallback("600519", "daily", 5) == []

    def test_exception_returns_empty_list(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_guard.providers.sina.requests.get",
                   side_effect=RuntimeError("sina down")):
            assert ds2._kline_sina_fallback("600519", "daily", 5) == []


class TestMigrationSnapshotMootdx:
    """M3-D: mootdx 解析体迁移后行为逐位一致(含 sanitize 0 价行丢弃)。"""

    def _fake_df(self):
        import pandas as pd
        return pd.DataFrame([
            {"datetime": "2026-11-18 15:00", "open": 1487.0, "high": 1502.0,
             "low": 1480.0, "close": 1499.0, "vol": 23150, "amount": 3.4e9},
            {"datetime": "2026-11-19 15:00", "open": 0.0, "high": 0.0,
             "low": 0.0, "close": 0.0, "vol": 0, "amount": 0.0},  # 停牌 0 价行
        ])

    def test_parse_bars_snapshot(self):
        from src.core.data_guard.providers.mootdx import parse_bars

        rows = parse_bars(self._fake_df())
        assert rows[0] == {
            "date": "2026-11-18", "open": 1487.0, "high": 1502.0,
            "low": 1480.0, "close": 1499.0, "volume": 23150.0,
            "amount": 3.4e9,
        }
        assert rows[1]["close"] == 0.0  # parse 层原样保留 0(与迁移前一致)

    def test_facade_sanitizes_zero_rows(self):
        """门面 sanitize: 0 价行丢弃, 存活行 OHLC 缺失用 close 兜底。"""
        from src.core import data_source_v2 as ds2

        client = type("C", (), {})()
        client.bars = lambda **kw: self._fake_df()
        with patch.object(ds2, "_mootdx_client", return_value=client):
            rows = ds2.get_kline_mootdx("600519", "daily", 5)
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-11-18" and rows[0]["close"] == 1499.0

    def test_client_none_falls_back_to_em(self):
        """client 不可用 → 东财降级(不触新浪)。"""
        from src.core import data_source_v2 as ds2

        em_rows = [{"date": "2026-11-18", "open": 1487.0, "close": 1499.0,
                    "high": 1502.0, "low": 1480.0, "volume": 23150.0,
                    "amount": 3.4e9}]
        with patch.object(ds2, "_mootdx_client", return_value=None), \
             patch.object(ds2, "_kline_em_fallback", return_value=em_rows) as m_em, \
             patch.object(ds2, "_kline_sina_fallback") as m_sina:
            rows = ds2.get_kline_mootdx("600519", "daily", 5)
        assert len(rows) == 1 and rows[0]["close"] == 1499.0
        m_em.assert_called_once()
        m_sina.assert_not_called()

    def test_em_empty_falls_back_to_sina(self):
        """东财空 → 新浪兜底。"""
        from src.core import data_source_v2 as ds2

        sina_rows = [{"date": "2026-11-18", "open": 1487.0, "close": 1499.0,
                      "high": 1502.0, "low": 1480.0, "volume": 23150.0,
                      "amount": 0.0}]
        with patch.object(ds2, "_mootdx_client", return_value=None), \
             patch.object(ds2, "_kline_em_fallback", return_value=[]), \
             patch.object(ds2, "_kline_sina_fallback", return_value=sina_rows):
            rows = ds2.get_kline_mootdx("600519", "daily", 5)
        assert len(rows) == 1 and rows[0]["close"] == 1499.0


class TestMigrationSnapshotIndex:
    """M3-E: 腾讯指数解析体迁移后行为逐位一致(含 data_health 副作用口径)。"""

    def _index_segment(self, code: str, name: str) -> str:
        values = {
            1: name, 2: code, 3: "3549.10", 31: "-38.73", 32: "-1.08",
            36: "347812345", 37: "47812345678.00",
        }
        fields = [values.get(i, "0") for i in range(45)]
        return f'v_sh{code}="{"~".join(fields)}";'

    def test_snapshot_exact_and_health_success(self):
        from src.core import data_source_v2 as ds2

        text = ";".join([
            self._index_segment("sh000001", "上证指数"),
            self._index_segment("sz399001", "深证成指"),
        ]) + ";"
        fake_resp = type("R", (), {"encoding": "", "text": text})()
        with patch("src.core.data_guard.providers.tencent.requests.get",
                   return_value=fake_resp), \
             patch("src.core.data_source_v2.data_health") as mock_health, \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_index_realtime()
        assert len(rows) == 2
        assert rows[0] == {
            "code": "sh000001", "name": "上证指数", "price": 3549.10,
            "change": -38.73, "change_pct": -1.08,
            "volume": 347812345.0, "amount": 47812345678.00,
        }
        mock_health.record_success.assert_called_once_with("index_realtime", 2)

    def test_empty_result_records_failure(self):
        from src.core import data_source_v2 as ds2

        fake_resp = type("R", (), {"encoding": "", "text": "no segments"})()
        with patch("src.core.data_guard.providers.tencent.requests.get",
                   return_value=fake_resp), \
             patch("src.core.data_source_v2.data_health") as mock_health, \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_index_realtime()
        assert rows == []
        mock_health.record_failure.assert_called_once_with("index_realtime", "empty result")

    def test_exception_records_failure_and_returns_empty(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_guard.providers.tencent.requests.get",
                   side_effect=RuntimeError("net down")), \
             patch("src.core.data_source_v2.data_health") as mock_health, \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_index_realtime()
        assert rows == []
        mock_health.record_failure.assert_called_once_with("index_realtime", "net down")


# ── M4-A 绞杀迁移快照: 榜单/研报/热搜/北向 ──────────────────────────────

class TestMigrationSnapshotRankingEM:
    """M4-A: 东财榜单迁移(含 M-04 '-'→None 语义与新浪降级编排)。"""

    def test_snapshot_with_delisted_dash_becomes_none(self):
        from src.core import data_source_v2 as ds2

        diff = [
            {"f12": "600519", "f14": "贵州茅台", "f2": 1499.0, "f3": 0.6,
             "f4": 9.0, "f5": 23150, "f6": 3478123456.0, "f15": 1505.0,
             "f16": 1488.0, "f17": 1491.0, "f18": 1490.0},
            {"f12": "000007", "f14": "退市股", "f2": "-", "f3": "-",
             "f4": "-", "f5": "-", "f6": "-", "f15": "-", "f16": "-",
             "f17": "-", "f18": "-"},
        ]
        fake = type("R", (), {"json": lambda s: {"data": {"diff": diff}}})()
        with patch("src.core.data_source_v2.em_get", return_value=fake), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_stock_ranking_em("f3", 0, 2)
        assert rows[0]["price"] == 1499.0 and rows[0]["change_pct"] == 0.6
        # M-04: 退市/停牌股数值缺失 → None, 不填 0
        assert rows[1]["price"] is None and rows[1]["change_pct"] is None
        assert rows[1]["code"] == "000007" and rows[1]["name"] == "退市股"

    def test_em_error_falls_back_to_sina_with_source_mark(self):
        from src.core import data_source_v2 as ds2

        sina_rows = [{"code": "000001", "name": "平安银行", "price": 10.0}]
        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")), \
             patch("src.core.data_guard.providers.sina.requests.get"), \
             patch("src.core.data_guard.providers.sina.fetch_stock_ranking_raw",
                   return_value=sina_rows), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"), \
             patch.object(ds2, "_mark_source") as mock_mark:
            rows = ds2.get_stock_ranking_em("f3", 0, 1)
        assert rows == sina_rows
        mock_mark.assert_any_call("stock_ranking", "sina")


class TestMigrationSnapshotRankingSina:
    """M4-A: 新浪榜单迁移(data_health 三态口径)。"""

    def _sina_items(self):
        return [{"code": "600519", "name": "贵州茅台", "trade": "1499.00",
                 "changepercent": "0.60", "pricechange": "9.00",
                 "volume": "23150", "amount": "3478123456",
                 "high": "1505.00", "low": "1488.00", "open": "1491.00",
                 "settlement": "1490.00"}]

    def test_snapshot_exact_and_health_success(self):
        import json as _json

        from src.core import data_source_v2 as ds2

        fake_resp = type("R", (), {"json": lambda s: _json.loads(_json.dumps(self._sina_items()))})()
        fake_resp.encoding = ""
        with patch("src.core.data_guard.providers.sina.requests.get",
                   return_value=fake_resp), \
             patch("src.core.data_source_v2.data_health") as mock_health:
            rows = ds2._get_stock_ranking_sina("f3", 0, 1)
        assert rows[0] == {
            "code": "600519", "name": "贵州茅台", "price": 1499.0,
            "change_pct": 0.6, "change": 9.0, "volume": 23150.0,
            "amount": 3478123456.0, "high": 1505.0, "low": 1488.0,
            "open": 1491.0, "prev_close": 1490.0,
        }
        mock_health.record_success.assert_called_once_with("stock_ranking_sina", 1)

    def test_exception_records_failure(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_guard.providers.sina.requests.get",
                   side_effect=RuntimeError("sina down")), \
             patch("src.core.data_source_v2.data_health") as mock_health:
            rows = ds2._get_stock_ranking_sina("f3", 0, 1)
        assert rows == []
        mock_health.record_failure.assert_called_once_with("stock_ranking_sina", "sina down")


class TestMigrationSnapshotResearch:
    """M4-A: 东财研报迁移。"""

    def test_snapshot_exact(self):
        from src.core import data_source_v2 as ds2

        payload = {"data": [{
            "title": "白酒龙头深度", "emRatingName": "买入",
            "predictNextTwoYearEps": 55.2, "orgSName": "中信证券",
            "publishDate": "2026-11-18 08:00",
        }]}
        fake = type("R", (), {"json": lambda s: payload})()
        with patch("src.core.data_source_v2.em_get", return_value=fake):
            rows = ds2.get_research_reports("600519", 1, 10)
        assert rows == [{
            "title": "白酒龙头深度", "rating": "买入", "eps_predict": 55.2,
            "org_name": "中信证券", "publish_date": "2026-11-18",
        }]

    def test_exception_returns_empty(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("report down")):
            assert ds2.get_research_reports("600519") == []


class TestMigrationSnapshotThs:
    """M4-A: 同花顺涨停池 + 北向迁移(status_code 异常/健康度/缓存口径)。"""

    def test_hot_stocks_snapshot_with_unix_seal_time(self):
        from datetime import datetime as _dt

        from src.core import data_source_v2 as ds2

        # Windows mktime 对 strptime 默认 1900 年会溢出, 用当天时间构造 epoch
        seal_ts = int(_dt.now().replace(hour=9, minute=35, second=0,
                                        microsecond=0).timestamp())
        payload = {"status_code": 0, "data": {"info": [{
            "code": "600519", "name": "贵州茅台", "latest": "1499.00",
            "change_rate": "10.00", "volume": "23150",
            "reason_type": "白酒概念", "first_limit_up_time": seal_ts,
            "open_num": 1,
        }]}}
        fake = type("R", (), {"json": lambda s: payload})()
        with patch("src.core.data_guard.providers.ths.requests.get", return_value=fake), \
             patch("src.core.data_source_v2.data_health") as mock_health, \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_hot_stocks_ths()
        assert rows == [{
            "code": "600519", "name": "贵州茅台", "price": 1499.0,
            "change_pct": 10.0, "volume": 23150.0, "reason": "白酒概念",
            "limit_up_time": "09:35:00", "open_times": 1,
        }]
        mock_health.record_success.assert_called_once_with("hot_stocks_ths", 1)

    def test_bad_status_code_records_failure(self):
        from src.core import data_source_v2 as ds2

        payload = {"status_code": 1, "status_msg": "filter参数不合法"}
        fake = type("R", (), {"json": lambda s: payload})()
        with patch("src.core.data_guard.providers.ths.requests.get", return_value=fake), \
             patch("src.core.data_source_v2.data_health") as mock_health, \
             patch.object(ds2, "_cache_get", return_value=None):
            rows = ds2.get_hot_stocks_ths()
        assert rows == []
        assert "status_code=1" in mock_health.record_failure.call_args.args[1]

    def test_northbound_snapshot(self):
        from src.core import data_source_v2 as ds2

        payload = {"data": {"list": [{
            "date": "2026-11-18", "sh_net_inflow": 12.5,
            "sz_net_inflow": -3.2, "net_inflow": 9.3,
        }]}}
        fake = type("R", (), {"json": lambda s: payload})()
        with patch("src.core.data_guard.providers.ths.requests.get", return_value=fake):
            assert ds2.get_northbound_flow() == {
                "date": "2026-11-18", "sh_net_inflow": 12.5,
                "sz_net_inflow": -3.2, "total_net_inflow": 9.3,
            }

    def test_northbound_empty_list_returns_empty_dict(self):
        from src.core import data_source_v2 as ds2

        fake = type("R", (), {"json": lambda s: {"data": {"list": []}}})()
        with patch("src.core.data_guard.providers.ths.requests.get", return_value=fake):
            assert ds2.get_northbound_flow() == {}

    def test_northbound_exception_returns_empty_dict(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_guard.providers.ths.requests.get",
                   side_effect=RuntimeError("ths down")):
            assert ds2.get_northbound_flow() == {}


# ── M4-B1 绞杀迁移快照: 最新交易日 + 龙虎榜 ────────────────────────────

def _em_json(payload: dict):
    return type("R", (), {"json": lambda s: payload})()


class TestMigrationSnapshotTradeDate:
    """M4-B1: 东财报表最近交易日迁移。"""

    def test_snapshot_exact(self):
        from src.core import data_source_v2 as ds2

        payload = {"result": {"data": [{"TRADE_DATE": "2026-11-18 00:00:00"}]}}
        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json(payload)):
            assert ds2._em_latest_trade_date("RPT_X") == "2026-11-18"

    def test_empty_data_returns_empty_string(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"result": None})):
            assert ds2._em_latest_trade_date("RPT_X") == ""

    def test_exception_returns_empty_string(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")):
            assert ds2._em_latest_trade_date("RPT_X") == ""


class TestMigrationSnapshotDragonTiger:
    """M4-B1: 龙虎榜迁移(最近交易日过滤 + 新浪诚实空降级)。"""

    def _payload(self, rows: list[dict]) -> dict:
        return {"result": {"data": rows}}

    def test_snapshot_filters_latest_trade_date(self):
        from src.core import data_source_v2 as ds2

        rows = [
            {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
             "CLOSE_PRICE": "1499.00", "CHANGE_RATE": "10.00",
             "EXPLANATION": "日涨幅偏离值达7%", "TOTAL_BUY": "1.2e8",
             "TOTAL_SELL": "3.0e7", "TOTAL_NET": "9.0e7",
             "TRADE_DATE": "2026-11-18 00:00:00"},
            {"SECURITY_CODE": "000001", "SECURITY_NAME_ABBR": "平安银行",
             "CLOSE_PRICE": "10.00", "CHANGE_RATE": "1.00",
             "EXPLANATION": "", "TOTAL_BUY": "0", "TOTAL_SELL": "0",
             "TOTAL_NET": "0",
             "TRADE_DATE": "2026-11-17 00:00:00"},  # 非最近交易日 → 过滤
        ]
        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json(self._payload(rows))), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            out = ds2.get_dragon_tiger()
        assert len(out) == 1
        assert out[0] == {
            "code": "600519", "name": "贵州茅台", "price": 1499.0,
            "change_pct": 10.0, "reason": "日涨幅偏离值达7%",
            "buy_amount": 1.2e8, "sell_amount": 3.0e7, "net_amount": 9.0e7,
            "trade_date": "2026-11-18",
        }

    def test_em_error_falls_back_to_honest_empty(self):
        """旧契约: 东财失败 → 新浪诚实空列表(不用涨幅榜冒充)。"""
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            assert ds2.get_dragon_tiger() == []


# ── M4-B2 绞杀迁移快照: 资金流/融资融券/大宗/股东/新闻 ──────────────────

class TestMigrationSnapshotCapitalFlow:
    """M4-B2: 个股资金流(空 klines 不走 fallback 的行为保持)。"""

    def test_snapshot_exact(self):
        from src.core import data_source_v2 as ds2

        klines = ["2026-11-18,1.2e8,-3e7,-2e7,6e7,6e7,5.5"]
        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"data": {"klines": klines}})), \
             patch.object(ds2, "_cache_get", return_value=None):
            out = ds2.get_capital_flow_detail("600519")
        assert out == {
            "date": "2026-11-18", "main_net_inflow": 1.2e8,
            "small_net_inflow": -3e7, "medium_net_inflow": -2e7,
            "large_net_inflow": 6e7, "super_large_net_inflow": 6e7,
            "main_inflow_pct": 5.5,
        }

    def test_empty_klines_returns_empty_no_fallback(self):
        """空 klines → {} 且不走估算 fallback(与迁移前一致)。"""
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"data": {"klines": []}})), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_get_capital_flow_fallback") as m_fb:
            assert ds2.get_capital_flow_detail("600519") == {}
        m_fb.assert_not_called()

    def test_exception_falls_back_and_caches(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"), \
             patch.object(ds2, "_get_capital_flow_fallback",
                          return_value={"date": "2026-11-18"}) as m_fb:
            out = ds2.get_capital_flow_detail("600519")
        assert out == {"date": "2026-11-18"}
        m_fb.assert_called_once()


class TestMigrationSnapshotMargin:
    """M4-B2: 融资融券(单位校准语义 + 诚实空 fallback)。"""

    def test_snapshot_short_sell_is_none(self):
        from src.core import data_source_v2 as ds2

        payload = {"result": {"data": [{
            "SCODE": "600519", "DATE": "2026-11-16 00:00:00",
            "RZMRE": "1.2e8", "RZYE": "14788586340", "RQMCL": "2900",
            "RQYE": "322111545", "RZRQYE": "15110700000",
        }]}}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)):
            out = ds2.get_margin_trading("600519")
        assert out["short_sell"] is None          # 金额语义上游无字段 → 诚实空
        assert out["short_sell_volume"] == 2900.0  # RQMCL 是股数
        assert out["margin_balance"] == 14788586340.0

    def test_invalid_code_rejected(self):
        from src.core import data_source_v2 as ds2

        assert ds2.get_margin_trading("bad!") == {}

    def test_exception_falls_back_to_honest_empty(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")):
            assert ds2.get_margin_trading("600519") == {}


class TestMigrationSnapshotBlockTrades:
    """M4-B2: 大宗交易(premium 小数×100 校准)。"""

    def test_snapshot_premium_percent(self):
        from src.core import data_source_v2 as ds2

        payload = {"result": {"data": [{
            "SECURITY_CODE": "300003", "SECURITY_NAME_ABBR": "乐普医疗",
            "TRADE_DATE": "2026-11-16 00:00:00", "DEAL_PRICE": "9.92",
            "DEAL_VOLUME": "500000", "DEAL_AMT": "4960000",
            "PREMIUM_RATIO": -0.174708818636,
            "BUYER_NAME": "机构专用", "SELLER_NAME": "中信上海",
        }]}}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)):
            rows = ds2.get_block_trades("300003")
        assert rows[0]["premium_pct"] == -17.47  # 小数 ×100, 不再透传
        assert rows[0]["volume"] == 500000.0

    def test_empty_falls_back_to_honest_empty(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"result": {"data": []}})), \
             patch.object(ds2, "_get_block_trades_fallback", return_value=[]) as m_fb:
            assert ds2.get_block_trades("300003") == []
        m_fb.assert_called_once()


class TestMigrationSnapshotShareholder:
    """M4-B2: 股东户数(环比缺失用上期计算)。"""

    def test_snapshot_with_ratio_field(self):
        from src.core import data_source_v2 as ds2

        payload = {"result": {"data": [{
            "SECURITY_CODE": "600519", "END_DATE": "2026-09-30 00:00:00",
            "HOLDER_TOTAL_NUM": "123456", "TOTAL_NUM_RATIO": -2.5,
        }]}}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)):
            out = ds2.get_shareholder_count("600519")
        assert out == {"code": "600519", "end_date": "2026-09-30",
                       "holder_num": 123456, "change_pct": -2.5}

    def test_zero_holders_falls_back_to_honest_empty(self):
        from src.core import data_source_v2 as ds2

        payload = {"result": {"data": [{"SECURITY_CODE": "600519",
                                        "HOLDER_TOTAL_NUM": "0"}]}}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)), \
             patch.object(ds2, "_get_shareholder_count_fallback", return_value={}) as m_fb:
            assert ds2.get_shareholder_count("600519") == {}
        m_fb.assert_called_once()


class TestMigrationSnapshotNews:
    """M4-B2: 个股新闻双源(东财 jsonp → 新浪) + _mark_source 编排。"""

    def test_eastmoney_success_marks_source(self):
        from src.core import data_source_v2 as ds2

        payload = {"result": {"cmsArticleWebOld": {"list": [{
            "title": "茅台公告", "content": "内容" * 120, "url": "https://x",
            "mediaName": "证券时报", "date": "2026-11-18",
        }]}}}
        import json as _json
        # cb 时间戳由 provider 实时生成 → 冻结 time.time + 预置匹配的 text
        fake = type("R", (), {
            "json": lambda s: payload,
            "text": "jQuery1000(" + _json.dumps(payload) + ")",
        })()
        with patch("src.core.data_source_v2.em_get", return_value=fake), \
             patch("time.time", return_value=1.0), \
             patch.object(ds2, "_mark_source") as m_mark:
            rows = ds2.get_stock_news("600519")
        assert rows[0]["title"] == "茅台公告"
        assert len(rows[0]["content"]) == 200  # 截断 200
        m_mark.assert_called_once_with("news", "eastmoney")

    def test_em_empty_falls_back_to_sina_and_marks(self):
        from src.core import data_source_v2 as ds2

        sina_items = [{"title": "新浪新闻", "intro": "简介", "url": "https://y",
                       "author": "新浪财经", "ctime": "1700000000"}]
        fake = type("R", (), {"json": lambda s: {"result": {"data": sina_items}}})()
        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"result": {}})), \
             patch("src.core.data_guard.providers.sina.requests.get",
                   return_value=fake), \
             patch.object(ds2, "_mark_source") as m_mark:
            rows = ds2.get_stock_news("600519")
        assert rows[0]["title"] == "新浪新闻"
        assert rows[0]["publish_time"].startswith("20")  # ctime 时间戳转换
        m_mark.assert_called_once_with("news", "sina")


class TestMigrationSnapshotCompanyInfo:
    """M4-B3: 公司资料(f170 双字段复用怪癖原样保留)。"""

    def test_snapshot_exact(self):
        from src.core import data_source_v2 as ds2

        payload = {"data": {
            "f57": "600519", "f58": "贵州茅台", "f84": "1256197800",
            "f116": "1880317300000", "f117": "1880317300000",
            "f162": "31.25", "f167": "21.5", "f170": "0.61",
            "f171": "1.14", "f168": "0.18", "f50": "1.35", "f187": "8.3",
        }}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)):
            out = ds2.get_company_info("600519")
        assert out == {
            "code": "600519", "name": "贵州茅台", "total_shares": 1256197800.0,
            "circ_market_value": 1880317300000.0, "total_market_value": 1880317300000.0,
            "roe": 31.25, "pe_ttm": 21.5,
            # 历史怪癖: f170 同时作为 5 分钟涨跌与当日涨跌返回
            "change_pct_5min": 0.61, "change_pct": 0.61,
            "amplitude": 1.14, "turnover_rate": 0.18, "volume_ratio": 1.35,
            "pb": 8.3,
        }

    def test_empty_data_returns_empty(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"data": {}})):
            assert ds2.get_company_info("600519") == {}

    def test_exception_returns_empty(self):
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   side_effect=RuntimeError("em down")):
            assert ds2.get_company_info("600519") == {}


class TestMigrationSnapshotFinancialEm:
    """M4-B3: EM 财务快照(f185→profit_yoy)与三级 failover 编排。"""

    def test_snapshot_profit_yoy_from_f185(self):
        from src.core import data_source_v2 as ds2

        payload = {"data": {"f9": 21.5, "f23": 8.3, "f20": "1880317300000",
                            "f115": "1880317300000", "f162": 31.25,
                            "f186": 91.5, "f187": 52.3, "f183": 15.2, "f185": 18.7}}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)), \
             patch.object(ds2, "_financial_snapshot_mootdx", return_value={}):
            out = ds2.get_financial_snapshot("600519")
        assert out["profit_yoy"] == 18.7   # f185 映射 profit_yoy
        assert out["revenue_yoy"] == 15.2  # f183 映射 revenue_yoy
        assert out["pe_ttm"] == 21.5

    def test_all_none_em_snapshot_falls_through_to_tencent(self):
        """EM 返回 data 为空 → 快照 {}, failover 继续走腾讯路径。"""
        from src.core import data_source_v2 as ds2

        with patch("src.core.data_source_v2.em_get",
                   return_value=_em_json({"data": {}})), \
             patch.object(ds2, "_financial_snapshot_mootdx", return_value={}), \
             patch.object(ds2, "_financial_snapshot_tencent",
                          return_value={"code": "600519", "pe_ttm": None,
                                        "pb": None, "total_mv": 1923.45,
                                        "circ_mv": 1923.45}):
            out = ds2.get_financial_snapshot("600519")
        assert out["total_mv"] == 1923.45  # 来自腾讯, 而非 EM 的空快照


class TestMigrationSnapshotDetailQuote:
    """M4-B3: 详情页行情字段映射(providers.tencent.map_detail_quote)。"""

    def test_provider_pure_mapping(self):
        from src.core.data_guard.providers.tencent import map_detail_quote

        out = map_detail_quote(SAMPLE_QUOTE)
        assert out["name"] == "贵州茅台"
        assert out["quote"] == SAMPLE_QUOTE  # 全字段透传, 0 兜底仅在缺失时生效

    def test_missing_fields_default_to_zero(self):
        from src.core.data_guard.providers.tencent import map_detail_quote

        out = map_detail_quote({"code": "600519", "name": "贵州茅台"})
        assert out["quote"]["price"] == 0.0       # 缺失字段 0 兜底(迁移前行为)
        assert out["quote"]["high_limit"] == 0.0
        assert out["name"] == "贵州茅台"

    def test_via_get_stock_detail(self):
        """端到端: 详情页 quote 区块经 provider 映射后结构逐位一致。"""
        from src.core import data_source_v2 as ds2

        with patch.object(ds2, "get_realtime_quote_tencent",
                          return_value=[SAMPLE_QUOTE]), \
             patch.object(ds2, "get_financial_snapshot", return_value={}), \
             patch.object(ds2, "get_capital_flow_detail", return_value={}), \
             patch.object(ds2, "get_kline_summary", return_value={}), \
             patch.object(ds2, "_cache_get", return_value=None):
            out = ds2.get_stock_detail("600519")
        assert out["quote"] == SAMPLE_QUOTE
        assert out["name"] == "贵州茅台"
        assert out["financial"] == {} and out["capital_flow"] == {}
        assert out["kline_summary"] == {}


class TestMigrationSnapshotFundRealtimeTencent:
    """M4-B3: 腾讯基金主源解析体(estimated_nav 兜底 + source 标注)。"""

    def test_snapshot_estimated_nav_falls_back_to_nav(self):
        """estimated_nav=0(未开盘估值缺失) → 回退为 nav, 不返回 0 估值。"""
        from src.core import data_source_v2 as ds2

        text = ('v_jj161725="161725~招商中证白酒~0.0000~x~x~1.20~2.50~1.50~'
                '2026-10-28 15:00:00~";')
        fake = type("R", (), {"text": text, "encoding": "gbk"})()
        with patch("src.core.data_guard.providers.tencent.requests.get",
                   return_value=fake), \
             patch.object(ds2._EM_SESSION, "get",
                          side_effect=RuntimeError("no fundgz")):
            rows = ds2.get_fund_realtime_tencent(["161725"])
        assert len(rows) == 1
        assert rows[0]["estimated_nav"] == 1.2  # 兜底为 nav 而非 0
        assert rows[0]["source"] == "tencent"
        assert rows[0]["change"] == pytest.approx(0.0177, abs=1e-4)  # 1.20-1.20/1.015 反推


class TestMigrationSnapshotFundHistory:
    """M4-B3: pingzhong 累计净值对齐 + lsjz PageSize 响应字段生效。"""

    def test_pingzhong_acc_nav_aligned_and_missing_pct_none(self):
        """acc 仅对单位净值已有日期回填; equityReturn 缺失 → None(排序后回填)。"""
        from src.core import data_source_v2 as ds2

        # 北京时间 2026-11-18 00:00 +08 → UTC 毫秒(验证 _CST 时区语义)
        import datetime as _dt
        cst = _dt.timezone(_dt.timedelta(hours=8))
        d2 = int(_dt.datetime(2026, 11, 18, tzinfo=cst).timestamp() * 1000)
        d1 = int(_dt.datetime(2026, 11, 17, tzinfo=cst).timestamp() * 1000)
        js = (f'Data_netWorthTrend = [{{"x":{d1},"y":2.808,"equityReturn":0.5}},'
              f'{{"x":{d2},"y":2.79,"equityReturn":""}}];'
              f'Data_ACWorthTrend = [[{d1},2.83],[{d2},2.81],[{d1 - 86400000},2.80]];')
        fake = type("R", (), {"text": js})()
        with patch.object(ds2._EM_SESSION, "get", return_value=fake):
            rows = ds2._fetch_fund_history_pingzhong("110022")
        by_date = {r["date"]: r for r in rows}
        assert by_date["2026-11-18"]["nav"] == 2.79
        assert by_date["2026-11-18"]["acc_nav"] == 2.81  # 累计净值按日期对齐
        assert by_date["2026-11-17"]["acc_nav"] == 2.83
        assert by_date["2026-11-18"]["change_pct"] is None  # 空串 → None(门面回填)
        # acc 中单位净值没有的日期(11-16)不产生新行
        assert "2026-11-16" not in by_date

    def test_lsjz_honors_payload_page_size(self):
        """响应 PageSize 非 20 时按响应值翻页(服务端分页参数生效语义)。"""
        import json as _json

        from src.core import data_source_v2 as ds2

        pages = {}

        def _fake_get(url, params=None, timeout=None, headers=None):
            idx = params["pageIndex"]
            body = {
                "TotalCount": 60, "PageSize": 50,
                "Data": {"LSJZList": [
                    {"FSRQ": f"2026-11-{16 - i:02d}", "DWJZ": "1.1",
                     "LJJZ": "1.2", "JZZZL": 0.5}
                    for i in range(50 if idx == 1 else 10)
                ]},
            }
            pages[idx] = len(body["Data"]["LSJZList"])
            return type("R", (), {"text": "jQuery(" + _json.dumps(body) + ")"})()

        with patch.object(ds2._EM_SESSION, "get", side_effect=_fake_get):
            rows = ds2._fetch_fund_history_lsjz("110022", None, "2026-11-19")
        assert set(pages) == {1, 2}      # 60 条 / 50 每页 → 2 页
        assert len(rows) == 60
        assert rows[0]["date"] == "2026-11-16"  # 首页(倒序)保持原始顺序


class TestMigrationSnapshotFinancialMootdxPatch:
    """M4-B3: mootdx 快照门面转发保持既有 patch 契约(ds2._mootdx_client)。"""

    def test_client_none_returns_empty(self):
        from src.core import data_source_v2 as ds2

        with patch.object(ds2, "_mootdx_client", return_value=None):
            assert ds2._financial_snapshot_mootdx("600519") == {}


_SECTOR_DIFF_ROW = {
    "f12": "BK0475", "f14": "银行", "f3": 1.2, "f2": 8.0, "f62": 1e8,
    "f184": 2.0, "f66": 0, "f69": 0, "f72": 0, "f75": 0,
    "f78": 0, "f81": 0, "f84": 0, "f87": 0,
}


class TestMigrationSnapshotSectorEM:
    """M4-C: 板块群东财两跳 clist(行业→概念)取数+解析迁移。"""

    def test_provider_snapshot_industry_fs(self):
        """provider 直测: fs 参数化 + _map_sector_row 映射逐位一致。"""
        from src.core.data_guard.providers.eastmoney import fetch_sector_ranking_em_raw

        resp = _em_json({"data": {"diff": [dict(_SECTOR_DIFF_ROW)]}})
        with patch("src.core.data_source_v2.em_get", return_value=resp):
            rows = fetch_sector_ranking_em_raw("m:90+t:2")
        assert rows == [{
            "code": "BK0475", "name": "银行", "change_pct": 1.2, "price": 8.0,
            "main_net_inflow": 1e8, "main_inflow_pct": 2.0,
            "super_large_net": 0.0, "super_large_pct": 0.0,
            "large_net": 0.0, "large_pct": 0.0,
            "medium_net": 0.0, "medium_pct": 0.0,
            "small_net": 0.0, "small_pct": 0.0,
        }]

    def test_industry_empty_falls_to_concept(self):
        """行业口径空 → 概念口径兜底, 来源仍标 eastmoney。"""
        from src.core import data_source_v2 as ds2

        calls: list[str] = []

        def fake_em_get(url, params=None, **kw):
            calls.append((params or {}).get("fs"))
            if (params or {}).get("fs") == "m:90+t:2":
                return _em_json({"data": {"diff": []}})
            return _em_json({"data": {"diff": [dict(_SECTOR_DIFF_ROW)]}})

        ds2._cache.clear()
        with patch.object(ds2, "em_get", side_effect=fake_em_get):
            rows = ds2.get_sector_ranking()
        assert calls == ["m:90+t:2", "m:90+t:3"], f"降级顺序异常: {calls}"
        assert rows[0]["name"] == "银行"
        assert ds2.get_last_source("sector_ranking") == "eastmoney"

    def test_em_both_empty_goes_sina_chain(self):
        """东财两跳全空 → 新浪板块资金链路接管, 来源标 sina。"""
        from src.core import data_source_v2 as ds2

        ds2._cache.clear()  # 上一个用例可能已把结果写进 30s TTL 缓存
        sina_rows = [{"category": "new_dl", "name": "电力", "avg_changeratio": "0.012",
                      "avg_price": "8.1", "netamount": "1e8", "ratioamount": "0.03"}]
        with patch.object(ds2, "em_get",
                          return_value=_em_json({"data": {"diff": []}})), \
             patch("src.core.data_guard.providers.sina.requests.get",
                   return_value=type("R", (), {"json": lambda s: sina_rows})()):
            rows = ds2.get_sector_ranking()
        assert rows[0]["name"] == "电力"
        assert rows[0]["main_net_inflow"] == 1e8
        assert ds2.get_last_source("sector_ranking") == "sina"


class TestMigrationSnapshotSectorSina:
    """M4-C: 板块群新浪三路(资金/旧接口/hangye_zjh)取数+解析迁移。"""

    def test_newfljk_parse_snapshot(self):
        """newFLJK 文本正则解析: 列表字段语义 + 涨跌幅降序 + top50 截断。"""
        from src.core.data_guard.providers.sina import fetch_sector_ranking_sina_raw

        text = ('var class="";'
                '参考Element1={"new_dl":"~,电力,8.10,8.10,0.012"};'
                '参考Element2={"new_xh":"~,券商,12.30,12.30,5.000"};')
        with patch("src.core.data_guard.providers.sina.requests.get",
                   return_value=type("R", (), {"text": text})()):
            rows = fetch_sector_ranking_sina_raw()
        assert [r["name"] for r in rows] == ["券商", "电力"]  # 按涨跌幅降序
        assert rows[0]["change_pct"] == 5.0 and rows[0]["price"] == 12.3
        assert rows[0]["main_net_inflow"] == 0  # 旧接口无资金字段

    def test_newfljk_empty_falls_to_v2(self):
        """旧接口无有效行 → hangye_zjh(v2)接管。"""
        from src.core import data_source_v2 as ds2

        v2_rows = [{"code": "new_dl", "name": "电力", "changepercent": "1.23",
                    "trade": "8.10", "symbol": "sh000001"}]
        with patch("src.core.data_guard.providers.sina.requests.get",
                   side_effect=[type("R", (), {"text": "var class=\"\";"})(),
                                type("R", (), {"json": lambda s: v2_rows})()]):
            rows = ds2._get_sector_ranking_sina()
        assert rows[0]["name"] == "电力"
        assert rows[0]["change_pct"] == 1.23

    def test_sector_page_raw_retry_then_none(self):
        """全量分页单页: 空页重试 1 次(共 2 请求)后仍空 → None(fail-closed)。"""
        from src.core.data_guard.providers.eastmoney import fetch_sector_page_raw

        calls: list[int] = []

        def fake_em_get(url, params=None, **kw):
            calls.append((params or {}).get("pn"))
            return _em_json({"data": {"total": 496, "diff": []}})

        with patch("src.core.data_source_v2.em_get", side_effect=fake_em_get), \
             patch("src.core.data_guard.providers.eastmoney.time.sleep"):
            out = fetch_sector_page_raw(1, attempts=2)
        assert out is None
        assert calls == [1, 1], "空页必须原地重试一次"

    def test_sector_page_raw_success(self):
        """单页成功 → (rows, total), 行结构经 _map_sector_row。"""
        from src.core.data_guard.providers.eastmoney import fetch_sector_page_raw

        resp = _em_json({"data": {"total": 496, "diff": [dict(_SECTOR_DIFF_ROW)]}})
        with patch("src.core.data_source_v2.em_get", return_value=resp):
            rows, total = fetch_sector_page_raw(1)
        assert total == 496
        assert rows[0]["name"] == "银行" and rows[0]["main_net_inflow"] == 1e8


# ── M4-D 绞杀迁移快照: 市场情绪/广度 + 分钟线 ──────────────────────────

class TestMigrationSnapshotMarketSentiment:
    """M4-D: ulist 聚合家数/北交所/total 与指数代理迁移后行为逐位一致。"""

    def test_provider_breadth_raw_snapshot(self):
        """provider 直测: ulist 聚合字段 f104/f105/f106 → (涨, 跌, 平)。"""
        from src.core.data_guard.providers.eastmoney import fetch_ulist_breadth_raw

        payload = {"data": {"diff": [{"f104": 598, "f105": 1688, "f106": 69}]}}
        with patch("src.core.data_source_v2.em_get", return_value=_em_json(payload)):
            assert fetch_ulist_breadth_raw("1.000001") == (598, 1688, 69)

    def test_facade_counts_snapshot_exact(self):
        """门面端到端: 沪 598/1688/69 + 深 638/2237/64 = 1236/3925/133,
        total 5564 减北交所 366 → coverage 95.1% → 偏空, ratio 0.233。"""
        from src.core import data_source_v2 as ds2

        def fake_em_get(url, params=None, **kw):
            if "ulist.np" in url:
                secids = str((params or {}).get("secids", ""))
                row = ({"f104": 598, "f105": 1688, "f106": 69} if secids.startswith("1.")
                       else {"f104": 638, "f105": 2237, "f106": 64})
                return _em_json({"data": {"diff": [row]}})
            if (params or {}).get("fs") == "m:0+t:81+s:2048":
                return _em_json({"data": {"total": 366, "diff": []}})
            return _em_json({"data": {"total": 5564, "diff": []}})

        with patch.object(ds2, "em_get", side_effect=fake_em_get), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set") as m_cache:
            out = ds2.get_market_sentiment()
        assert out == {
            "rise_count": 1236, "fall_count": 3925, "flat_count": 133,
            "sentiment": "偏空", "ratio": 0.233, "basis": "counts",
            "counts_available": True, "counts_basis": "counts",
        }
        m_cache.assert_called_once_with("market_sentiment", out, 30)

    def test_facade_index_proxy_snapshot(self):
        """东财全失败 → 腾讯 sh000001 -1.08% → index_proxy / 计数 None(非 0)。"""
        from src.core import data_source_v2 as ds2
        from src.core.data_guard.providers import tencent as tp

        values = ["0"] * 40
        values[32] = "-1.08"
        text = f'v_sh000001="{"~".join(values)}";'
        fake_resp = type("R", (), {"encoding": "", "text": text})()
        with patch.object(ds2, "em_get", side_effect=RuntimeError("em down")), \
             patch.object(tp.requests, "get", return_value=fake_resp), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            out = ds2.get_market_sentiment()
        assert out == {
            "rise_count": None, "fall_count": None, "flat_count": None,
            "sentiment": "偏空", "ratio": 0.35, "basis": "index_proxy",
            "counts_available": False, "counts_basis": "unavailable",
        }


class TestMigrationSnapshotGlobalQuote:
    """M4-E: 国际行情(腾讯)取数/解析/缓存与 ETF 映射迁移后逐位一致。"""

    def _segment(self, code: str) -> str:
        values = ["0"] * 37  # 末尾多留一格, 避免闭引号混入币种字段
        values[1] = "测试指数"
        values[2] = f"{code}.OQ"
        values[3] = "100.0"
        values[4] = "98.0"
        values[5] = "99.0"
        values[6] = "1000"
        values[31] = "2.0"
        values[32] = "2.04"
        values[33] = "101.0"
        values[34] = "97.0"
        values[35] = "USD"
        return f'v_us{code}="{"~".join(values)}";'

    def test_provider_parse_snapshot(self):
        """provider 直测: 字段索引/币种/涨跌额逐位一致。"""
        from src.core.data_guard.providers.tencent import fetch_global_quotes_raw

        fake_resp = type("R", (), {"encoding": "", "text": self._segment("ZZT1")})()
        with patch("src.core.data_guard.providers.tencent.requests.get",
                   return_value=fake_resp):
            rows = fetch_global_quotes_raw(["usZZT1"])
        assert rows == [{
            "code": "ZZT1.OQ", "name": "测试指数", "price": 100.0,
            "prev_close": 98.0, "open": 99.0, "high": 101.0, "low": 97.0,
            "change": 2.0, "change_pct": 2.04, "volume": 1000.0,
            "currency": "USD",
        }]

    def test_a_share_composite_currency_cleared(self):
        """A股指数 parts[35] 是「价格/成交量/成交额」复合串 → 必须置空(否则币种泄漏为脏串)。"""
        from src.core.data_guard.providers.tencent import fetch_global_quotes_raw

        values = ["0"] * 37
        values[1] = "上证指数"
        values[2] = "000001"
        values[3] = "3577.62"
        values[4] = "3587.83"
        values[5] = "3585.00"
        values[31] = "-10.21"
        values[32] = "-0.28"
        values[33] = "3584.28"
        values[34] = "3547.94"
        values[35] = "3577.62/412912755/636909057689"
        text = f'v_sh000001="{"~".join(values)}";'
        fake_resp = type("R", (), {"encoding": "", "text": text})()
        with patch("src.core.data_guard.providers.tencent.requests.get",
                   return_value=fake_resp):
            rows = fetch_global_quotes_raw(["sh000001"])
        assert rows[0]["currency"] == ""
        assert rows[0]["price"] == 3577.62

    def test_facade_cache_hit_single_request(self):
        """门面缓存: 10s TTL 内第二次调用不得重复请求上游。"""
        from src.core import data_source_v2 as ds2

        ds2._cache.clear()  # 防止其他用例的同键残留
        fake_resp = type("R", (), {"encoding": "", "text": self._segment("ZZT2")})()
        with patch("src.core.data_guard.providers.tencent.requests.get",
                   return_value=fake_resp) as mock_get:
            out1 = ds2._tencent_global_quote(["usZZT2"])
            out2 = ds2._tencent_global_quote(["usZZT2"])
        assert mock_get.call_count == 1, "第二次调用必须命中缓存"
        assert out2 == out1 and out1[0]["price"] == 100.0

    def test_sector_etf_mapping_missing_kept_none(self):
        """行业 ETF: 11 条固定; 上游缺失保持 None(不伪造 0)。"""
        from src.core import data_source_v2 as ds2

        quotes = [{"code": "XLK.AM", "name": "科技ETF-SPDR", "price": 100.0,
                   "change_pct": 1.01}]
        with patch.object(ds2, "_tencent_global_quote", return_value=quotes), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_global_sector_etfs()
        assert len(rows) == 11
        by_sector = {r["sector"]: r for r in rows}
        assert by_sector["科技"]["price"] == 100.0
        assert by_sector["科技"]["change_pct"] == 1.01
        assert by_sector["金融"]["price"] is None
        assert by_sector["金融"]["change_pct"] is None


class TestMigrationSnapshotGlobalPanel:
    """M4-E: 全球指数全景与全球快讯迁移后逐位一致。"""

    def test_world_indices_scaling_region_and_none(self):
        rows = [
            {"f1": 2, "f2": 5886043, "f3": -134, "f4": -80159,
             "f12": "N225", "f14": "日经225"},
            {"f1": 2, "f2": "-", "f3": "-", "f4": "-",
             "f12": "FTSE", "f14": "英国富时100"},
        ]
        from src.core import data_source_v2 as ds2

        ds2._cache.clear()
        with patch.object(ds2, "em_get",
                          return_value=_em_json({"data": {"diff": rows}})), \
             patch.object(ds2, "_cache_set"):
            out = ds2.get_global_indices_world()
        assert len(out) == len(ds2._GLOBAL_WORLD_INDICES)
        by_code = {r["code"]: r for r in out}
        assert by_code["N225"]["price"] == 58860.43
        assert by_code["N225"]["change_pct"] == -1.34
        assert by_code["N225"]["region"] == "asia"
        assert by_code["N225"]["region_label"] == "亚太"
        assert by_code["FTSE"]["price"] is None  # 缺失不填 0

    def test_global_news_em_success_then_sina_fallback(self):
        """东财快讯成功标 eastmoney; 东财空响应 → 新浪兜底标 sina。"""
        from src.core import data_source_v2 as ds2

        em_payload = {"data": {"news_list": [{
            "title": "快讯A", "digest": "摘要", "mediaName": "证券时报",
            "showTime": "2026-11-19 10:00", "newsUrl": "https://x",
        }]}}
        with patch.object(ds2, "em_get", return_value=_em_json(em_payload)), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_mark_source") as m_mark:
            rows = ds2.get_global_news()
        assert rows == [{
            "title": "快讯A", "content": "摘要", "source": "证券时报",
            "publish_time": "2026-11-19 10:00", "url": "https://x",
        }]
        m_mark.assert_called_once_with("global_news", "eastmoney")

        sina_payload = {"result": {"data": [{
            "title": "新浪快讯", "intro": "简介", "author": "新浪财经",
            "ctime": "1700000000", "url": "https://y",
        }]}}
        fake = type("R", (), {"encoding": "", "json": lambda s: sina_payload})()
        with patch.object(ds2, "em_get", return_value=_em_json({"data": {}})), \
             patch("src.core.data_guard.providers.sina.requests.get", return_value=fake), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_mark_source") as m_mark:
            rows = ds2.get_global_news()
        assert rows[0]["title"] == "新浪快讯"
        assert rows[0]["source"] == "新浪财经"
        m_mark.assert_called_once_with("global_news", "sina")

    def test_core_indices_cover_a_hk_us(self):
        """核心指数覆盖 A股/港股/美股: A股 → CN/CNY, 港/美币种各自补全。"""
        from src.core import data_source_v2 as ds2

        assert dict(ds2._GLOBAL_INDEX_CODES).get("sh000001") == "上证指数"
        quotes = [
            {"code": "000001", "name": "上证指数", "price": 3549.1, "change_pct": 0.5},
            {"code": "HSI", "name": "恒生指数", "price": 23350.0,
             "change_pct": 1.28, "currency": ""},
            {"code": "DJI", "name": "道琼斯", "price": 52900.0,
             "change_pct": 1.14, "currency": ""},
        ]
        with patch.object(ds2, "_tencent_global_quote", return_value=quotes), \
             patch.object(ds2, "_cache_get", return_value=None), \
             patch.object(ds2, "_cache_set"):
            rows = ds2.get_global_indices()
        by_name = {r["name"]: r for r in rows}
        assert by_name["上证指数"]["market"] == "CN"
        assert by_name["上证指数"]["currency"] == "CNY"
        assert by_name["恒生指数"]["market"] == "HK"
        assert by_name["恒生指数"]["currency"] == "HKD"
        assert by_name["道琼斯"]["market"] == "US"
        assert by_name["道琼斯"]["currency"] == "USD"


class TestMigrationSnapshotMinuteSeries:
    """M4-D: 三源分钟线取数+解析迁移后行为逐位一致。"""

    def test_em_snapshot_limit_scales_and_close_column(self):
        """东财: lmt=window+4, 取第 3 列收盘价, >15:00 补点剔除。"""
        from src.core import data_source_v2 as ds2

        captured = {}
        klines = [
            "2026-11-06 14:58,1451.00,1451.00,1451.00,1451.00,8,1160705.00",
            "2026-11-06 15:00,1451.00,1453.00,1453.00,1451.00,2826,410617808.00",
            "2026-11-06 15:30,1453.00,1453.00,1453.00,1453.00,0,0.00",
        ]

        def fake_em_get(url, params=None, **kw):
            captured.update(params or {})
            return _em_json({"data": {"klines": klines}})

        with patch.object(ds2, "em_get", side_effect=fake_em_get):
            prices, day = ds2._minutes_from_em("300118", 30)
        assert captured["lmt"] == "34"
        assert day == "2026-11-06"
        assert prices == {14 * 60 + 58: 1451.0, 15 * 60: 1453.0}

    def test_tencent_snapshot_accepted_without_fallback(self):
        """腾讯: 收盘后 1529 补点剔除; 窗口端点齐全 → 直接采纳不回退。"""
        from src.core import data_source_v2 as ds2
        from src.core.data_guard.providers import tencent as tp

        payload = {"data": {"sz300118": {"data": {"date": "20261106", "data": [
            "1455 10.00 1 1", "1500 10.30 2 2", "1529 10.30 2 2",
        ]}}}}
        fake_resp = type("R", (), {"json": lambda s: payload})()
        with patch.object(tp.requests, "get", return_value=fake_resp), \
             patch.object(ds2, "_minutes_from_em",
                          side_effect=AssertionError("端点齐全不应回退")), \
             patch.object(ds2, "_minutes_from_sina",
                          side_effect=AssertionError("端点齐全不应回退")):
            prices, source, day = ds2.get_stock_minute_series("300118", 5)
        assert source == "tencent" and day == "2026-11-06"
        assert prices == {14 * 60 + 55: 10.0, 15 * 60: 10.3}

    def test_sina_snapshot_gap_not_filled(self):
        """新浪: datalen=window+6, 零成交分钟跳空不插值。"""
        from src.core.data_guard.providers.sina import fetch_minute_series_raw

        bars = [
            {"day": "2026-11-06 14:56:00", "close": "1451.880"},
            {"day": "2026-11-06 14:57:00", "close": "1451.000"},
            {"day": "2026-11-06 15:00:00", "close": "1453.000"},
        ]
        captured = {}

        def fake_get(url, params=None, **kw):
            captured.update(params or {})
            return type("R", (), {"json": lambda s: bars})()

        with patch("src.core.data_guard.providers.sina.requests.get", side_effect=fake_get):
            prices, day = fetch_minute_series_raw("300118", 30)
        assert captured["datalen"] == "36"
        assert sorted(prices) == [14 * 60 + 56, 14 * 60 + 57, 15 * 60]
        assert (14 * 60 + 58) not in prices
        assert day == "2026-11-06"

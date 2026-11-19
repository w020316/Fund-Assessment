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
        with patch("src.core.data_guard.providers.eastmoney.em_get",
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

        with patch("src.core.data_guard.providers.eastmoney.em_get",
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

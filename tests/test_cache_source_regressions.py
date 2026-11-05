"""数据源缓存回归测试(阶段二覆盖率补测中发现的两个真实缺陷)。

缺陷 1: CacheSource._deserialize 对 history_kline 使用 pd.read_json(字符串),
pandas 3.x 起该用法会被当成文件路径,抛 FileNotFoundError 后被 except 吞掉,
缓存里的 K 线永远读不回来 → 降级路径拿不到历史数据。

缺陷 2: DataSourceManager._fallback 写缓存用的键是方法名(get_realtime_quote),
而 CacheSource._retrieve 读的键是 query_type(realtime_quote),两者不一致,
"刚成功取过、随后所有数据源故障"时仍抛 DataSourceError,缓存兜底形同虚设。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.core.data_source import CacheSource, DataSourceManager, KlineResult, QuoteResult


class TestKlineCacheRoundtrip:
    @pytest.fixture
    def cache(self, tmp_path):
        return CacheSource(db_path=tmp_path / "cache.db")

    def test_history_kline_cache_roundtrip(self, cache):
        kline = KlineResult(
            symbol="600519",
            data=pd.DataFrame([{"收盘": 10.0}, {"收盘": 11.0}]),
            source="mootdx",
            latency_ms=1.0,
        )
        cache.store("history_kline", "600519", kline)

        got = cache.get_history_kline("600519", "2026-01-01", "2026-10-29")
        assert isinstance(got, KlineResult)
        assert got.symbol == "600519"
        assert got.source == "mootdx"
        assert len(got.data) == 2
        assert got.latency_ms == 1.0

    def test_other_cache_types_unaffected(self, cache):
        from src.core.data_source import NewsResult

        cache.store(
            "news",
            "600519",
            [NewsResult(symbol="600519", title="t", content="c", publish_time=datetime.now(),
                        source="test", latency_ms=1.0)],
        )
        got = cache.get_news("600519")
        assert len(got) == 1


class _Source:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def get_realtime_quote(self, symbol):
        if self._error:
            raise self._error
        return self._result


class TestManagerCacheFallback:
    @pytest.fixture
    def manager(self, tmp_path):
        return DataSourceManager(db_path=tmp_path / "ds.db")

    def test_cache_written_by_success_used_after_all_sources_down(self, manager):
        quote = QuoteResult(
            symbol="600519", name="贵州茅台", price=1680.0, change_pct=1.0,
            volume=1.0, amount=2.0, timestamp=datetime.now(), source="fake", latency_ms=1.0,
        )
        manager._primary_sources = [_Source(result=quote)]
        assert manager.get_realtime_quote("600519") is quote

        manager._primary_sources = [_Source(error=RuntimeError("down"))]
        got = manager.get_realtime_quote("600519")
        assert got is not None
        assert got.price == 1680.0
        assert got.source == "fake"

    def test_no_cache_still_fails_closed(self, manager):
        from src.core.data_source import DataSourceError

        manager._primary_sources = [_Source(error=RuntimeError("down"))]
        with pytest.raises(DataSourceError):
            manager.get_realtime_quote("600519")

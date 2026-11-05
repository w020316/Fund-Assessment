"""数据源缓存回归测试(阶段二覆盖率补测中发现的两个真实缺陷)。

缺陷 1: CacheSource._deserialize 对 history_kline 使用 pd.read_json(字符串),
pandas 3.x 起该用法会被当成文件路径,抛 FileNotFoundError 后被 except 吞掉,
缓存里的 K 线永远读不回来 → 降级路径拿不到历史数据。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.core.data_source import CacheSource, KlineResult


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

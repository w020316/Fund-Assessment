"""src/core/data_source.py 覆盖率补测(阶段二)。

覆盖三个外部数据源(AkShare/Tushare/EastMoney)与本地缓存、降级管理器的
成功 / 空数据 / 异常三类分支。全部使用 fake akshare/tushare 与 tmp_path
数据库,不发起真实网络请求、不写入项目 data/ 目录。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.core import data_source as ds
from src.core.data_source import (
    AkShareSource,
    CacheSource,
    CapitalFlowResult,
    DataSourceError,
    DataSourceManager,
    EastMoneySource,
    KlineResult,
    NewsResult,
    NorthboundFlowResult,
    QuoteResult,
    TushareSource,
)


def _raise(*_args, **_kwargs):
    raise RuntimeError("boom")


def _quote_df(code="600519"):
    return pd.DataFrame(
        [{"代码": code, "名称": "贵州茅台", "最新价": 1680.0, "涨跌幅": 1.5,
          "成交量": 1000.0, "成交额": 1680000.0}]
    )


def _fund_flow_df():
    return pd.DataFrame(
        [{"主力净流入-净额": 1e8, "主力净流入-净流入": 2e8, "主力净流入-净流出": 1e8, "散户净流入-净额": -1e8}]
    )


def _news_df():
    return pd.DataFrame(
        [{"新闻标题": "标题A", "新闻内容": "内容A"}, {"新闻标题": "标题B", "新闻内容": "内容B"}]
    )


@pytest.fixture
def fake_ak(monkeypatch):
    """可自由替换方法的伪 akshare 模块"""
    fake = SimpleNamespace()
    monkeypatch.setattr(ds, "ak", fake)
    monkeypatch.setattr(ds, "_HAS_AKSHARE", True)
    return fake


class TestAkShareSource:
    def test_realtime_quote_success(self, fake_ak):
        fake_ak.stock_zh_a_spot_em = lambda: _quote_df()
        q = AkShareSource().get_realtime_quote("600519")
        assert q is not None and q.price == 1680.0 and q.source == "akshare"

    def test_realtime_quote_symbol_not_found(self, fake_ak):
        fake_ak.stock_zh_a_spot_em = lambda: _quote_df("000001")
        assert AkShareSource().get_realtime_quote("600519") is None

    def test_realtime_quote_exception(self, fake_ak):
        fake_ak.stock_zh_a_spot_em = _raise
        assert AkShareSource().get_realtime_quote("600519") is None

    def test_history_kline_success(self, fake_ak):
        fake_ak.stock_zh_a_hist = lambda **kwargs: pd.DataFrame([{"收盘": 10.0}])
        k = AkShareSource().get_history_kline("600519", "2026-01-01", "2026-10-29")
        assert k is not None and len(k.data) == 1 and k.source == "akshare"

    def test_history_kline_exception(self, fake_ak):
        fake_ak.stock_zh_a_hist = _raise
        assert AkShareSource().get_history_kline("600519", "2026-01-01", "2026-10-29") is None

    def test_capital_flow_sh_and_empty_and_error(self, fake_ak):
        fake_ak.stock_individual_fund_flow = lambda stock, market: _fund_flow_df()
        flow = AkShareSource().get_capital_flow("600519")
        assert flow is not None and flow.main_net_inflow == 1e8

        fake_ak.stock_individual_fund_flow = lambda stock, market: pd.DataFrame()
        assert AkShareSource().get_capital_flow("600519") is None

        fake_ak.stock_individual_fund_flow = _raise
        assert AkShareSource().get_capital_flow("600519") is None

    def test_northbound_success_empty_error(self, fake_ak):
        fake_ak.stock_hsgt_north_net_flow_in_em = lambda symbol: pd.DataFrame([{"当日净流入": 5e8}])
        nb = AkShareSource().get_northbound_flow()
        assert nb is not None and nb.total_net_inflow == 5e8

        fake_ak.stock_hsgt_north_net_flow_in_em = lambda symbol: pd.DataFrame()
        assert AkShareSource().get_northbound_flow() is None

        fake_ak.stock_hsgt_north_net_flow_in_em = _raise
        assert AkShareSource().get_northbound_flow() is None

    def test_news_success_empty_error(self, fake_ak):
        fake_ak.stock_news_em = lambda symbol: _news_df()
        news = AkShareSource().get_news("600519")
        assert len(news) == 2 and news[0].title == "标题A"

        fake_ak.stock_news_em = lambda symbol: pd.DataFrame()
        assert AkShareSource().get_news("600519") is None

        fake_ak.stock_news_em = _raise
        assert AkShareSource().get_news("600519") is None


class TestEastMoneySource:
    def test_realtime_quote_paths(self, fake_ak):
        fake_ak.stock_zh_a_spot_em = lambda: _quote_df()
        q = EastMoneySource().get_realtime_quote("600519")
        assert q is not None and q.source == "eastmoney"

        fake_ak.stock_zh_a_spot_em = lambda: _quote_df("000001")
        assert EastMoneySource().get_realtime_quote("600519") is None

        fake_ak.stock_zh_a_spot_em = _raise
        assert EastMoneySource().get_realtime_quote("600519") is None

    def test_history_kline_paths(self, fake_ak):
        fake_ak.stock_zh_a_hist_min_em = lambda **kwargs: pd.DataFrame([{"收盘": 10.0}])
        k = EastMoneySource().get_history_kline("600519", "2026-01-01", "2026-10-29")
        assert k is not None and k.source == "eastmoney"

        fake_ak.stock_zh_a_hist_min_em = _raise
        assert EastMoneySource().get_history_kline("600519", "2026-01-01", "2026-10-29") is None

    def test_capital_flow_paths(self, fake_ak):
        fake_ak.stock_individual_fund_flow = lambda stock, market: _fund_flow_df()
        assert EastMoneySource().get_capital_flow("000001").source == "eastmoney"

        fake_ak.stock_individual_fund_flow = lambda stock, market: pd.DataFrame()
        assert EastMoneySource().get_capital_flow("000001") is None

        fake_ak.stock_individual_fund_flow = _raise
        assert EastMoneySource().get_capital_flow("000001") is None

    def test_northbound_sums_two_legs(self, fake_ak):
        mapping = {
            "沪股通": pd.DataFrame([{"当日净流入": 3e8}]),
            "深股通": pd.DataFrame([{"当日净流入": 2e8}]),
        }
        fake_ak.stock_hsgt_north_net_flow_in_em = lambda symbol: mapping[symbol]
        nb = EastMoneySource().get_northbound_flow()
        assert nb.total_net_inflow == 5e8
        assert nb.sh_net_inflow == 3e8 and nb.sz_net_inflow == 2e8

    def test_northbound_empty_and_error(self, fake_ak):
        fake_ak.stock_hsgt_north_net_flow_in_em = lambda symbol: pd.DataFrame()
        nb = EastMoneySource().get_northbound_flow()
        assert nb is not None and nb.total_net_inflow == 0.0

        fake_ak.stock_hsgt_north_net_flow_in_em = _raise
        assert EastMoneySource().get_northbound_flow() is None

    def test_news_paths(self, fake_ak):
        fake_ak.stock_news_em = lambda symbol: _news_df()
        assert len(EastMoneySource().get_news("600519")) == 2

        fake_ak.stock_news_em = lambda symbol: pd.DataFrame()
        assert EastMoneySource().get_news("600519") is None

        fake_ak.stock_news_em = _raise
        assert EastMoneySource().get_news("600519") is None


class _FakePro:
    def __init__(self, df):
        self._df = df
        self.calls: list[tuple] = []

    def daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        return self._df

    def moneyflow(self, **kwargs):
        self.calls.append(("moneyflow", kwargs))
        return self._df

    def moneyflow_hsgt(self, **kwargs):
        self.calls.append(("hsgt", kwargs))
        return self._df


class TestTushareSource:
    def test_without_token_returns_none(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        src = TushareSource(token=None)
        assert src.pro is None
        assert src.get_realtime_quote("600519") is None
        assert src.get_history_kline("600519", "2026-01-01", "2026-10-29") is None
        assert src.get_capital_flow("600519") is None
        assert src.get_northbound_flow() is None
        assert src.get_news("600519") is None

    def test_token_from_env_and_pro_cached(self, monkeypatch):
        created = []

        def _pro_api():
            pro = _FakePro(pd.DataFrame([{"trade_date": "20261028", "open": 1, "close": 2,
                                          "high": 3, "low": 0.5, "vol": 10, "amount": 20}]))
            created.append(pro)
            return pro

        monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
        monkeypatch.setattr(ds, "ts", SimpleNamespace(set_token=lambda t: None, pro_api=_pro_api))
        src = TushareSource()
        assert src.pro is src.pro  # 懒加载后缓存
        assert len(created) == 1

    def test_history_kline_rename_and_empty(self, monkeypatch):
        df = pd.DataFrame([{"trade_date": "20261028", "open": 1, "close": 2,
                            "high": 3, "low": 0.5, "vol": 10, "amount": 20}])
        pro = _FakePro(df)
        src = TushareSource(token="t")
        monkeypatch.setattr(ds, "ts", SimpleNamespace(set_token=lambda t: None, pro_api=lambda: pro))
        k = src.get_history_kline("600519", "2026-01-01", "2026-10-29")
        assert k.source == "tushare" and "收盘" in k.data.columns
        assert pro.calls[0][1]["ts_code"] == "600519.SH"

        src._pro = _FakePro(pd.DataFrame())
        assert src.get_history_kline("600519", "2026-01-01", "2026-10-29") is None

        src._pro = _FakePro(object())
        src._pro.daily = _raise
        assert src.get_history_kline("600519", "2026-01-01", "2026-10-29") is None

    def test_capital_flow_and_error(self, monkeypatch):
        df = pd.DataFrame([{"buy_elg_vol": 100.0, "sell_elg_vol": 40.0}])
        pro = _FakePro(df)
        src = TushareSource(token="t")
        monkeypatch.setattr(ds, "ts", SimpleNamespace(set_token=lambda t: None, pro_api=lambda: pro))
        flow = src.get_capital_flow("000001")
        assert flow.main_net_inflow == 60.0 and flow.source == "tushare"
        assert pro.calls[0][1]["ts_code"] == "000001.SZ"

        src._pro = _FakePro(pd.DataFrame())
        assert src.get_capital_flow("000001") is None

        src._pro = _FakePro(pd.DataFrame([{"buy_elg_vol": "bad"}]))
        assert src.get_capital_flow("000001") is None

    def test_northbound_flow_and_error(self, monkeypatch):
        df = pd.DataFrame([{"north_money": 1e8}])
        pro = _FakePro(df)
        src = TushareSource(token="t")
        monkeypatch.setattr(ds, "ts", SimpleNamespace(set_token=lambda t: None, pro_api=lambda: pro))
        nb = src.get_northbound_flow()
        assert nb.total_net_inflow == 1e8

        src._pro = _FakePro(pd.DataFrame())
        assert src.get_northbound_flow() is None

        src._pro = _FakePro(pd.DataFrame([{"north_money": "bad"}]))
        assert src.get_northbound_flow() is None


class TestCacheSource:
    @pytest.fixture
    def cache(self, tmp_path):
        return CacheSource(db_path=tmp_path / "cache.db")

    def test_realtime_quote_roundtrip(self, cache):
        quote = QuoteResult(
            symbol="600519", name="贵州茅台", price=1680.0, change_pct=1.5,
            volume=1.0, amount=2.0, timestamp=datetime.now(), source="test", latency_ms=1.0,
        )
        cache.store("realtime_quote", "600519", quote, ttl_seconds=60)
        got = cache.get_realtime_quote("600519")
        assert isinstance(got, QuoteResult)
        assert got.price == 1680.0
        # 同 key 覆盖写(先 DELETE 再 INSERT)
        cache.store("realtime_quote", "600519", quote, ttl_seconds=60)
        assert cache.get_realtime_quote("600519") is not None

    def test_capital_flow_and_northbound_roundtrip(self, cache):
        flow = CapitalFlowResult(
            symbol="600519", main_net_inflow=1.0, main_inflow=2.0, main_outflow=1.0,
            retail_net_inflow=-1.0, date=date.today(), source="test", latency_ms=1.0,
        )
        cache.store("capital_flow", "600519", flow)
        assert cache.get_capital_flow("600519").date == date.today()

        nb = NorthboundFlowResult(
            date=date.today(), sh_net_inflow=1.0, sz_net_inflow=2.0,
            total_net_inflow=3.0, source="test", latency_ms=1.0,
        )
        cache.store("northbound_flow", "", nb)
        assert cache.get_northbound_flow().total_net_inflow == 3.0

    def test_news_list_roundtrip(self, cache):
        news = [
            NewsResult(symbol="600519", title="标题", content="内容", publish_time=datetime.now(),
                       source="test", latency_ms=1.0)
        ]
        cache.store("news", "600519", news)
        got = cache.get_news("600519")
        assert len(got) == 1 and got[0].title == "标题"

    def test_unknown_type_returns_raw(self, cache):
        cache.store("custom", "k", {"a": 1})
        assert cache._retrieve("custom", "k") == {"a": 1}

    def test_missing_key_returns_none(self, cache):
        assert cache.get_realtime_quote("999999") is None

    def test_deserialize_broken_json_returns_none(self, cache):
        assert cache._deserialize("realtime_quote", "not-json") is None


class _FakeSource:
    def __init__(self, name, result=None, error=None):
        self._name = name
        self._result = result
        self._error = error

    def __class_getitem__(cls, item):  # pragma: no cover - 仅避免误用
        return cls

    def get_realtime_quote(self, symbol):
        if self._error:
            raise self._error
        return self._result


class TestDataSourceManager:
    @pytest.fixture
    def manager(self, tmp_path):
        return DataSourceManager(db_path=tmp_path / "ds.db")

    def test_fallback_picks_first_success_and_caches(self, manager):
        quote = QuoteResult(
            symbol="600519", name="贵州茅台", price=1680.0, change_pct=1.0,
            volume=1.0, amount=2.0, timestamp=datetime.now(), source="fake", latency_ms=1.0,
        )
        manager._primary_sources = [
            _FakeSource("A", error=RuntimeError("down")),
            _FakeSource("B", result=quote),
        ]
        got = manager.get_realtime_quote("600519")
        assert got is quote
        assert manager._cache.get_realtime_quote("600519") is not None

    def test_all_sources_fail_then_cache_hit(self, manager):
        quote = QuoteResult(
            symbol="600519", name="贵州茅台", price=1680.0, change_pct=1.0,
            volume=1.0, amount=2.0, timestamp=datetime.now(), source="fake", latency_ms=1.0,
        )
        manager._cache.store("realtime_quote", "600519", quote)
        manager._primary_sources = [_FakeSource("A", error=RuntimeError("down"))]
        got = manager.get_realtime_quote("600519")
        assert got is not None and got.source == "fake"

    def test_all_sources_fail_raises(self, manager):
        manager._primary_sources = [_FakeSource("A", error=RuntimeError("down"))]
        with pytest.raises(DataSourceError):
            manager.get_realtime_quote("600519")

    def test_list_result_uses_first_item_latency(self, manager):
        news = [NewsResult(symbol="600519", title="t", content="c", publish_time=datetime.now(),
                           source="fake", latency_ms=2.0)]

        class _NewsSource:
            def get_news(self, symbol):
                return news

        manager._primary_sources = [_NewsSource()]
        assert manager.get_news("600519") is news

        with sqlite3.connect(manager._db_path) as conn:
            rows = conn.execute("SELECT source, latency_ms FROM source_log").fetchall()
        assert rows and rows[-1][1] == 2.0

    def test_get_history_kline_forwards_args(self, manager, tmp_path):
        captured = {}

        class _Src:
            def get_history_kline(self, symbol, start_date, end_date, period="daily"):
                captured.update(symbol=symbol, start=start_date, end=end_date, period=period)
                return KlineResult(
                    symbol=symbol, data=pd.DataFrame([{"收盘": 1.0}]), source="fake", latency_ms=1.0
                )

        manager._primary_sources = [_Src()]
        k = manager.get_history_kline("600519", "2026-01-01", "2026-10-29", period="weekly")
        assert k is not None
        assert captured == {"symbol": "600519", "start": "2026-01-01", "end": "2026-10-29", "period": "weekly"}

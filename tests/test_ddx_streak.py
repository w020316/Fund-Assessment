"""资金连增（DDX 连增口径）单元测试。

覆盖：
- compute_streak 连增计算（连续为正 / 中断 / 单日 / 空数据 / 缺列 / NaN）
- market_of 市场前缀映射
- build_ddx_streak 并行取数的排序、阈值过滤与失败计数
- GET /api/market/ddx_streak 的响应结构与降级
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.analysis import ddx_streak as ddx
from src.analysis.ddx_streak import build_ddx_streak, compute_streak, market_of


def _make_df(main_values: list[float], ratios: list[float] | None = None) -> pd.DataFrame:
    n = len(main_values)
    dates = [f"2026-11-{10 + i:02d}" for i in range(n)]
    return pd.DataFrame({
        "日期": dates,
        "主力净流入-净额": main_values,
        "主力净流入-净占比": ratios if ratios is not None else [1.0] * n,
    })


class TestComputeStreak:
    def test_counts_trailing_positive_days(self):
        df = _make_df([1e7, -2e7, 3e7, 4e7])
        out = compute_streak(df)
        assert out is not None
        assert out["streak"] == 2
        assert out["net_today"] == pytest.approx(4e7)
        assert out["net_sum"] == pytest.approx(7e7)
        assert out["start_date"] == "2026-11-12"
        assert out["end_date"] == "2026-11-13"

    def test_streak_breaks_on_zero(self):
        df = _make_df([1e7, 0.0, 4e7])
        assert compute_streak(df)["streak"] == 1

    def test_all_negative_returns_none(self):
        assert compute_streak(_make_df([-1e7, -2e7])) is None

    def test_empty_returns_none(self):
        assert compute_streak(pd.DataFrame()) is None
        assert compute_streak(None) is None

    def test_missing_main_column_returns_none(self):
        df = pd.DataFrame({"日期": ["2026-11-13"], "涨跌幅": [1.0]})
        assert compute_streak(df) is None

    def test_nan_latest_returns_none(self):
        df = _make_df([1e7, 2e7])
        df.loc[1, "主力净流入-净额"] = float("nan")
        assert compute_streak(df) is None

    def test_ratio_and_date_optional(self):
        df = pd.DataFrame({"主力净流入": [1e7, 2e7]})
        out = compute_streak(df)
        assert out["streak"] == 2
        assert out["ratio_today"] is None
        assert out["start_date"] is None

    def test_alternative_column_name(self):
        df = pd.DataFrame({"日期": ["2026-11-13"], "主力净流入": [8e7]})
        assert compute_streak(df)["streak"] == 1


class TestMarketOf:
    @pytest.mark.parametrize("code,expected", [
        ("600519", "sh"), ("000001", "sz"), ("300750", "sz"),
        ("830799", "bj"), ("920001", "bj"), ("", "sz"),
    ])
    def test_market_mapping(self, code, expected):
        assert market_of(code) == expected


class TestBuildDdxStreak:
    def test_filters_by_min_days_and_sorts(self):
        stats = {
            "600519": {"streak": 3, "net_today": 1e8, "net_sum": 3e8, "ratio_today": 2.0, "start_date": "d1", "end_date": "d3"},
            "000001": {"streak": 5, "net_today": 2e7, "net_sum": 9e7, "ratio_today": 1.0, "start_date": "d1", "end_date": "d3"},
            "300750": {"streak": 1, "net_today": 3e8, "net_sum": 3e8, "ratio_today": 5.0, "start_date": "d3", "end_date": "d3"},
        }
        candidates = [
            {"code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 1.0},
            {"code": "000001", "name": "平安银行", "price": 2.0, "change_pct": 2.0},
            {"code": "300750", "name": "宁德时代", "price": 3.0, "change_pct": 3.0},
        ]
        with patch.object(ddx, "fetch_streak", side_effect=lambda c: stats[c]):
            out = build_ddx_streak(candidates, min_days=2)
        assert out["sampled"] == 3
        assert out["failed"] == 0
        assert [i["code"] for i in out["items"]] == ["000001", "600519"]

    def test_failed_fetch_is_counted_not_fabricated(self):
        candidates = [{"code": "600519", "name": "贵州茅台"}]
        with patch.object(ddx, "fetch_streak", return_value=None):
            out = build_ddx_streak(candidates, min_days=2)
        assert out["items"] == []
        assert out["failed"] == 1

    def test_empty_candidates(self):
        assert build_ddx_streak([], min_days=2) == {"items": [], "sampled": 0, "failed": 0}

    def test_counts_sources_for_successful_items(self):
        candidates = [
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
            {"code": "300750", "name": "宁德时代"},
        ]
        stats = {
            "600519": {"streak": 3, "net_today": 1e8, "net_sum": 3e8, "source": "eastmoney", "history_available": True},
            "000001": {"streak": 2, "net_today": 2e7, "net_sum": 4e7, "source": "akshare", "history_available": True},
            "300750": None,
        }
        with patch.object(ddx, "fetch_streak", side_effect=lambda c: stats[c]):
            out = build_ddx_streak(candidates, min_days=2)
        assert out["sources"] == {"eastmoney": 1, "akshare": 1}
        assert out["failed"] == 1


class TestFlowSources:
    def test_em_history_wins_and_marks_history(self, monkeypatch):
        monkeypatch.setattr(ddx, "_fetch_em_history", lambda code: _make_df([1e7, 2e7, 3e7]))
        out = ddx.fetch_flow_streak("600519")
        assert out["source"] == "eastmoney"
        assert out["history_available"] is True
        assert out["streak"] == 3

    def test_akshare_used_when_em_empty(self, monkeypatch):
        monkeypatch.setattr(ddx, "_fetch_em_history", lambda code: None)
        monkeypatch.setattr(ddx, "_fetch_akshare_history", lambda code: _make_df([1e7, 2e7]))
        out = ddx.fetch_flow_streak("600519")
        assert out["source"] == "akshare"
        assert out["streak"] == 2

    def test_sina_snapshot_used_when_history_unavailable(self, monkeypatch):
        monkeypatch.setattr(ddx, "_fetch_em_history", lambda code: None)
        monkeypatch.setattr(ddx, "_fetch_akshare_history", lambda code: None)
        monkeypatch.setattr(ddx, "_fetch_sina_snapshot", lambda code: {
            "streak": None, "net_today": 7.4e8, "net_sum": None, "ratio_today": None,
            "start_date": None, "end_date": None, "source": "sina", "history_available": False,
        })
        out = ddx.fetch_flow_streak("600519")
        assert out["source"] == "sina"
        assert out["history_available"] is False
        assert out["streak"] is None
        # 快照只能说明当日，绝不能当成连增进入候选榜
        assert ddx.fetch_streak("600519") is None

    def test_latest_negative_returns_zero_streak_not_fake(self, monkeypatch):
        monkeypatch.setattr(ddx, "_fetch_em_history", lambda code: _make_df([1e7, -2e7]))
        out = ddx.fetch_flow_streak("600519")
        assert out["streak"] == 0
        assert out["history_available"] is True
        assert out["net_today"] == pytest.approx(-2e7)
        assert ddx.fetch_streak("600519") is None

    def test_sina_snapshot_parses_netamount(self, monkeypatch):
        class _Resp:
            def json(self):
                return {"netamount": "741446243.01", "r0": "1", "r1": "2", "r2": "3"}

        monkeypatch.setattr(ddx.requests, "get", lambda *a, **k: _Resp())
        out = ddx._fetch_sina_snapshot("600519")
        assert out["source"] == "sina"
        assert out["net_today"] == pytest.approx(741446243.01)

    def test_sina_all_zero_is_not_zero_inflow(self, monkeypatch):
        class _Resp:
            def json(self):
                return {"netamount": "0", "r0": "0", "r1": "0", "r2": "0"}

        monkeypatch.setattr(ddx.requests, "get", lambda *a, **k: _Resp())
        assert ddx._fetch_sina_snapshot("600519") is None


@pytest.fixture
def client():
    from web.api import app
    with TestClient(app) as c:
        yield c


def test_endpoint_returns_items(client, monkeypatch):
    import web.routes.market as market_route

    async def fake_candidates(limit=18):
        return [{"code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 1.0}]

    def fake_build(candidates, min_days, workers, deadline):
        return {
            "items": [{
                "code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 1.0,
                "streak": 3, "net_today": 5e8, "net_sum": 9e8, "ratio_today": 4.5,
                "start_date": "2026-11-11", "end_date": "2026-11-13",
            }],
            "sampled": 1, "failed": 0,
        }

    monkeypatch.setattr(market_route, "_ddx_candidates", fake_candidates)
    monkeypatch.setattr(market_route, "build_ddx_streak", fake_build)
    monkeypatch.setattr(market_route, "_ddx_failed_at", 0.0)
    monkeypatch.setattr(market_route.cache, "get_with_stale", lambda key, max_stale=300: (None, False))
    monkeypatch.setattr(market_route.cache, "set", lambda *a, **k: None)

    r = client.get("/api/market/ddx_streak?limit=5&min_days=2")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["sampled"] == 1
    assert body["data"]["items"][0]["streak"] == 3
    assert body["data"]["trade_date"] == "2026-11-13"
    assert body["_meta"]["degraded"] is False


def test_endpoint_degrades_when_all_fetches_fail(client, monkeypatch):
    import web.routes.market as market_route

    async def fake_candidates(limit=18):
        return [{"code": "600519", "name": "贵州茅台"}]

    def fake_build(candidates, min_days, workers, deadline):
        return {"items": [], "sampled": 2, "failed": 2}

    monkeypatch.setattr(market_route, "_ddx_candidates", fake_candidates)
    monkeypatch.setattr(market_route, "build_ddx_streak", fake_build)
    monkeypatch.setattr(market_route, "_ddx_failed_at", 0.0)
    monkeypatch.setattr(market_route.cache, "get_with_stale", lambda key, max_stale=300: (None, False))
    monkeypatch.setattr(market_route.cache, "set", lambda *a, **k: None)

    r = client.get("/api/market/ddx_streak")
    body = r.json()
    assert body["data"]["items"] == []
    assert body["data"]["failed"] == 2
    assert body["_meta"]["degraded"] is True


def test_endpoint_survives_exception(client, monkeypatch):
    import web.routes.market as market_route

    async def boom(limit=18):
        raise RuntimeError("network down")

    monkeypatch.setattr(market_route, "_ddx_candidates", boom)
    monkeypatch.setattr(market_route, "_ddx_failed_at", 0.0)
    monkeypatch.setattr(market_route.cache, "get_with_stale", lambda key, max_stale=300: (None, False))
    monkeypatch.setattr(market_route.cache, "set", lambda *a, **k: None)

    r = client.get("/api/market/ddx_streak")
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []


def test_endpoint_cooldown_returns_explicit_flag(client, monkeypatch):
    """冷却期必须显式标记，避免前端把限流误读成"筛选无结果"。"""
    import time as _time
    import web.routes.market as market_route

    monkeypatch.setattr(market_route, "_ddx_failed_at", _time.monotonic())
    monkeypatch.setattr(market_route.cache, "get_with_stale", lambda key, max_stale=300: (None, False))

    body = client.get("/api/market/ddx_streak").json()
    assert body["data"]["cooldown"] is True
    assert body["data"]["items"] == []
    assert body["_meta"]["degraded"] is True


def test_endpoint_serves_stale_snapshot_while_refreshing(client, monkeypatch):
    """有旧快照时立即返回（SWR），不阻塞在逐股取数上。"""
    import web.routes.market as market_route

    snapshot = {
        "items": [{"code": "600519", "name": "贵州茅台", "streak": 3,
                   "net_today": 1e8, "net_sum": 2e8, "ratio_today": 1.5,
                   "start_date": "2026-11-11", "end_date": "2026-11-13"}],
        "sampled": 1, "failed": 0, "min_days": 2, "trade_date": "2026-11-13",
    }
    refreshed = []

    def fake_schedule(cache_key, limit, min_days):
        refreshed.append((cache_key, limit, min_days))

    monkeypatch.setattr(market_route.cache, "get_with_stale",
                        lambda key, max_stale=300: (snapshot, True))
    monkeypatch.setattr(market_route, "_schedule_ddx_refresh", fake_schedule)

    r = client.get("/api/market/ddx_streak")
    body = r.json()
    assert body["data"]["items"][0]["streak"] == 3
    assert body["_meta"]["stale"] is True
    assert refreshed, "过期快照应触发后台刷新"


class TestStockFlowStreakEndpoint:
    def _patch_cache(self, monkeypatch, market_route):
        monkeypatch.setattr(market_route.cache, "get", lambda key: None)
        monkeypatch.setattr(market_route.cache, "set", lambda *a, **k: None)

    def test_returns_history_streak(self, client, monkeypatch):
        import web.routes.market as market_route

        stats = {
            "streak": 3, "net_today": 5e8, "net_sum": 9e8, "ratio_today": 4.5,
            "start_date": "2026-11-11", "end_date": "2026-11-13",
            "source": "eastmoney", "history_available": True,
        }
        monkeypatch.setattr(market_route, "fetch_flow_streak", lambda code: stats)
        self._patch_cache(monkeypatch, market_route)

        body = client.get("/api/market/stock_flow_streak?code=600519").json()
        assert body["data"]["streak"] == 3
        assert body["data"]["source"] == "eastmoney"
        assert body["data"]["history_available"] is True
        assert body["_meta"]["quality_score"] == 100
        assert body["_meta"]["degraded"] is False

    def test_snapshot_is_degraded_and_flagged(self, client, monkeypatch):
        import web.routes.market as market_route

        snapshot = {
            "streak": None, "net_today": 7.4e8, "net_sum": None, "ratio_today": None,
            "start_date": None, "end_date": None,
            "source": "sina", "history_available": False,
        }
        monkeypatch.setattr(market_route, "fetch_flow_streak", lambda code: snapshot)
        self._patch_cache(monkeypatch, market_route)

        body = client.get("/api/market/stock_flow_streak?code=600519").json()
        assert body["data"]["history_available"] is False
        assert body["data"]["streak"] is None
        assert body["data"]["net_today"] == pytest.approx(7.4e8)
        assert body["_meta"]["quality_score"] == 60
        assert body["_meta"]["degraded"] is True

    def test_unavailable_is_not_zero(self, client, monkeypatch):
        import web.routes.market as market_route

        monkeypatch.setattr(market_route, "fetch_flow_streak", lambda code: None)
        self._patch_cache(monkeypatch, market_route)

        body = client.get("/api/market/stock_flow_streak?code=600519").json()
        assert body["data"]["net_today"] is None
        assert body["data"]["streak"] is None
        assert body["data"]["source"] == "unavailable"
        assert body["_meta"]["quality_score"] == 0
        assert body["_meta"]["degraded"] is True

    def test_rejects_bad_code(self, client):
        assert client.get("/api/market/stock_flow_streak?code=abc").status_code == 422

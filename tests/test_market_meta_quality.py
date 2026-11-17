"""A-07 + M-03(2026-11-17):空数据不得报 80 分健康。

根因: `_build_meta` 的 `quality_score` 缺省值是**乐观默认 80**(cached=100),
而 `degraded = resolved_quality < 70`。凡是"取数返回空列表但仍然不传 quality_score"
的端点, 前端会同时拿到 `degraded=false` 和空列表 —— 无法区分
"确实没有数据" 与 "抓取失败", 这正是本项目数据诚实红线的反面。

本文件锁定 4 个此前漏掉的端点: /hot_stocks、/hot_stocks_signal、/news、/global_news。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from web.api import app
from web.routes import market

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_market_cache():
    market.cache.clear()
    yield
    market.cache.clear()


def _meta(payload: dict) -> dict:
    assert "data" in payload and "_meta" in payload
    return payload["_meta"]


# ---------------------------------------------------------------- 空数据 → 降级

def test_hot_stocks_all_three_boards_empty_is_degraded(monkeypatch):
    empty = {"top_gainers": [], "top_losers": [], "top_volume": []}
    monkeypatch.setattr(market, "_fetch_hot_stocks", AsyncMock(return_value=empty))

    payload = client.get("/api/market/hot_stocks").json()
    meta = _meta(payload)

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True, "三个榜单全空仍报健康分 → 前端把抓取失败读成今天没有"


def test_hot_stocks_signal_empty_is_degraded(monkeypatch):
    monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", lambda: [])
    monkeypatch.setattr(market.ds2, "get_hot_stocks_signal_fallback", lambda: [])

    meta = _meta(client.get("/api/market/hot_stocks_signal").json())

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


def test_news_empty_is_degraded(monkeypatch):
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: [])

    meta = _meta(client.get("/api/market/news").json())

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


def test_global_news_empty_is_degraded(monkeypatch):
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: [])

    meta = _meta(client.get("/api/market/global_news").json())

    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


# ---------------------------------------------------------------- 非空 → 正常

def test_hot_stocks_partial_board_is_not_degraded(monkeypatch):
    """三个榜单只有一个有数据 → 不得判空(避免过度降级)。"""
    payload = {
        "top_gainers": [{"code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 9.9}],
        "top_losers": [],
        "top_volume": [],
    }
    monkeypatch.setattr(market, "_fetch_hot_stocks", AsyncMock(return_value=payload))

    meta = _meta(client.get("/api/market/hot_stocks").json())

    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False


def test_hot_stocks_signal_non_empty_is_ok(monkeypatch):
    rows = [{
        "code": "600519", "name": "贵州茅台", "price": 1700.0, "change_pct": 9.9,
        "volume": 1.0, "reason": "涨停", "limit_up_time": "09:35", "open_times": 0,
    }]
    monkeypatch.setattr(market.ds2, "get_hot_stocks_ths", lambda: rows)

    meta = _meta(client.get("/api/market/hot_stocks_signal").json())

    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False


def test_news_non_empty_is_ok(monkeypatch):
    rows = [{
        "title": "标题", "content": "正文", "url": "https://example.invalid/1",
        "source": "eastmoney", "publish_time": "2026-11-17 09:00:00",
    }]
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: rows)

    meta = _meta(client.get("/api/market/news").json())

    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False


# ---------------------------------------------------------------- 缓存命中

def test_cached_empty_news_is_still_degraded(monkeypatch):
    """缓存里存的是空列表时, 命中缓存也必须是降级态(不能因为 cached=True 就满分)。"""
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: [])
    client.get("/api/market/global_news")  # 首次请求写入空列表缓存

    meta = _meta(client.get("/api/market/global_news").json())

    assert meta["cached"] is True
    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True


def test_cached_non_empty_news_keeps_full_score(monkeypatch):
    """缓存命中且非空 → 仍沿用既有 cached=100 约定(不得把健康缓存降到 80)。"""
    rows = [{
        "title": "标题", "content": "正文", "url": "https://example.invalid/1",
        "source": "eastmoney", "publish_time": "2026-11-17 09:00:00",
    }]
    monkeypatch.setattr(market.ds2, "get_global_news", lambda: rows)
    client.get("/api/market/global_news")

    meta = _meta(client.get("/api/market/global_news").json())

    assert meta["cached"] is True
    assert meta["quality_score"] == 100.0
    assert meta["degraded"] is False


# ---------------------------------------------------------------- 默认值本身

def test_default_quality_constant_is_optimistic_and_named():
    """乐观默认必须被显式命名(_DEFAULT_QUALITY), 不再是散落的魔法数字 80。"""
    assert market._DEFAULT_QUALITY == 80.0
    meta = market._build_meta("eastmoney", cached=False)
    assert meta["quality_score"] == market._DEFAULT_QUALITY
    assert meta["degraded"] is False
    # 显式传 0 时必须是降级
    assert market._build_meta("x", quality_score=0.0)["degraded"] is True

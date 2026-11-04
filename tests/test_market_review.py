"""短线复盘看板与情绪周期测试"""
from __future__ import annotations

import pytest

from src.analysis import market_review


def _limit(code: str, name: str, streak: int = 1, industry: str = "半导体", seal_amount: float = 8_000_000, break_count: int = 0, seal_time: str = "09:35", change_pct: float = 10.0) -> dict:
    return {
        "code": code,
        "name": name,
        "change_pct": change_pct,
        "price": 10.0,
        "amount": 1_000_000_000,
        "turnover": 8.0,
        "seal_amount": seal_amount,
        "first_seal_time": seal_time,
        "last_seal_time": seal_time,
        "break_count": break_count,
        "streak": streak,
        "industry": industry,
    }


@pytest.fixture(autouse=True)
def _clear_review_cache():
    market_review.clear_cache()
    yield
    market_review.clear_cache()


def test_build_review_board_with_injected_data():
    pool = [
        _limit("600001", "甲", streak=4, seal_amount=300_000_000),
        _limit("600002", "乙", streak=2, industry="医药"),
        _limit("600003", "丙", streak=1, industry="医药"),
    ]
    broken = [_limit("600004", "丁", streak=1, break_count=2, seal_amount=1_000_000)]
    down = [_limit("600005", "戊", change_pct=-10)]
    market = {
        "sentiment": {"rise_count": 3000, "fall_count": 1500, "flat_count": 500, "sentiment": "偏多", "ratio": 0.6},
        "indices": [
            {"name": "上证指数", "price": 3300, "change_pct": 1.2, "amount": 5e11},
            {"name": "深证成指", "price": 10500, "change_pct": 0.8, "amount": 7e11},
        ],
        "sectors": [
            {"name": "半导体", "change_pct": 4.2},
            {"name": "医药", "change_pct": 2.1},
        ],
    }

    board = market_review.build_review_board(
        "20261104",
        limit_pool=pool,
        broken_pool=broken,
        limit_down_pool=down,
        yesterday_limit=[_limit("600006", "己", streak=2)],
        yesterday_broken=[],
        market=market,
    )

    assert board["available"] is True
    assert board["limit"]["limit_up_count"] == 3
    assert board["limit"]["limit_down_count"] == 1
    assert board["limit"]["broken_count"] == 1
    assert board["limit"]["broken_rate"] == pytest.approx(0.25)
    assert board["limit"]["ladder"]["4板+"] == 1
    assert board["limit"]["ladder"]["2板"] == 1
    assert board["limit"]["ladder"]["首板"] == 1
    assert board["limit"]["max_streak"] == 4
    assert board["market"]["rise_ratio"] == pytest.approx(0.6)
    assert board["market"]["index_avg_change_pct"] == pytest.approx(1.0)
    assert 0 <= board["temperature"]["score"] <= 100
    assert board["temperature"]["level"] in {"高潮", "发酵", "分歧", "修复", "冰点"}
    assert board["yesterday"]["limit_up_delta"] == 2
    assert board["tonight"]["stance"] in {"防守", "精选", "观察", "试错", "等待"}
    assert len(board["tonight"]["risk"]) >= 1
    assert board["review"]["phase"] in {"升温", "降温", "震荡"}


def test_build_review_board_returns_empty_without_fabrication():
    board = market_review.build_review_board(
        "20261104",
        limit_pool=[],
        broken_pool=[],
        limit_down_pool=[],
        yesterday_limit=[],
        yesterday_broken=[],
        market={},
    )
    assert board["available"] is False
    assert board["data_quality"] == "empty"
    assert board["limit"]["limit_up_count"] == 0
    assert board["market"]["rise_count"] == 0
    assert board["tonight"]["risk"]


def test_compute_temperature_clamps_to_range():
    hot = market_review.compute_temperature(200, 10, 0.0, 0.9, 5.0)
    cold = market_review.compute_temperature(0, 0, 1.0, 0.0, -5.0)
    assert hot["score"] <= 100
    assert cold["score"] >= 0
    assert hot["score"] > cold["score"]


def test_sentiment_cycle_with_injected_pools(monkeypatch):
    dates = ["20261104", "20261103", "20261031", "20261030", "20261029"]
    monkeypatch.setattr(market_review, "_recent_trade_dates", lambda days, end=None: dates)
    pools = {
        "20261104": [_limit("600001", "甲", streak=3), _limit("600002", "乙", streak=1)],
        "20261103": [_limit("600003", "丙", streak=1)],
        "20261031": [],
        "20261030": [_limit("600004", "丁", streak=2)],
        "20261029": [_limit("600005", "戊", streak=1)],
    }
    cycle = market_review.get_sentiment_cycle(5, pools=pools)
    assert cycle["days"] == 5
    assert len(cycle["series"]) == 5
    # 旧→新:20261029 在前,20261104 在后
    assert cycle["series"][0]["date"] == "2026-10-29"
    assert cycle["series"][-1]["date"] == "2026-11-04"
    assert cycle["trend"] in {"升温", "降温", "震荡", "数据不足"}


def test_analyze_first_boards_filters_and_ranks():
    pool = [
        _limit("600001", "甲", streak=1, seal_amount=200_000_000, seal_time="09:25"),
        _limit("600002", "乙", streak=1, seal_amount=5_000_000, break_count=3, seal_time="14:30"),
        _limit("600003", "丙", streak=2),
        _limit("600004", "丁", streak=1, seal_amount=60_000_000, seal_time="10:10"),
    ]
    result = market_review.analyze_first_boards("20261104", limit_pool=pool, top=10)
    assert result["count"] == 3
    codes = [item["code"] for item in result["items"]]
    assert "600003" not in codes
    assert result["items"][0]["code"] == "600001"
    assert result["items"][0]["watch_level"] == "强"
    assert result["items"][-1]["watch_level"] == "弱"
    assert result["industries"][0]["count"] == 3


def test_fetch_pool_without_akshare(monkeypatch):
    monkeypatch.setattr(market_review, "_HAS_AKSHARE", False)
    assert market_review.fetch_limit_pool("20261104") == []

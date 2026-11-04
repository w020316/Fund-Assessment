"""复盘 / 三问 / 技能调度 路由测试"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_review_cache():
    from web.routes import review
    review.cache.clear()
    yield
    review.cache.clear()


_SAMPLE_BOARD = {
    "date": "2026-11-04",
    "available": True,
    "data_quality": "normal",
    "market": {"rise_count": 3000, "fall_count": 1500, "flat_count": 500, "rise_ratio": 0.6},
    "limit": {
        "limit_up_count": 60,
        "limit_down_count": 4,
        "broken_count": 20,
        "broken_rate": 0.25,
        "broken_rate_pct": 25.0,
        "max_streak": 5,
        "ladder": {"首板": 40, "2板": 12, "3板": 6, "4板+": 2},
        "top_sectors": [{"name": "半导体", "limit_up_count": 12}],
        "sector_ranking_top": [{"name": "半导体", "change_pct": 4.2}],
    },
    "temperature": {"score": 66.5, "level": "发酵", "breakdown": {}},
    "yesterday": {"limit_up_count": 55, "broken_count": 18, "broken_rate_pct": 24.6, "max_streak": 4, "temperature": 62.0, "limit_up_delta": 5},
    "tonight": {"stance": "精选", "summary": "情绪偏暖", "focus": [], "risk": ["注意高位股"]},
    "review": {"phase": "升温", "summary": "情绪升温", "verify": []},
}


def test_review_board_endpoint(client, monkeypatch):
    from web.routes import review as review_route
    monkeypatch.setattr(review_route, "build_review_board", lambda date=None: _SAMPLE_BOARD)
    response = client.get("/api/review/board")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["limit"]["limit_up_count"] == 60
    assert body["_meta"]["data_source"] == "akshare+tencent"


def test_review_sentiment_endpoint(client, monkeypatch):
    from web.routes import review as review_route
    sample = {"days": 5, "trend": "升温", "series": [], "temperature_delta": 6.0}
    monkeypatch.setattr(review_route, "get_sentiment_cycle", lambda days: sample)
    response = client.get("/api/review/sentiment?days=5")
    assert response.status_code == 200
    assert response.json()["data"]["trend"] == "升温"


def test_review_first_board_endpoint(client, monkeypatch):
    from web.routes import review as review_route
    sample = {"date": "2026-11-04", "count": 2, "items": [], "industries": []}
    monkeypatch.setattr(review_route, "analyze_first_boards", lambda date, top=20: sample)
    response = client.get("/api/review/first_board?top=20")
    assert response.status_code == 200
    assert response.json()["data"]["count"] == 2


def test_three_question_endpoint_all_pass(client):
    payload = {
        "stock_code": "600519",
        "mode": "manual",
        "prev_close": 10.0,
        "break_close": 10.5,
        "auction_price": 11.0,
        "sector_change_pct": 4.0,
        "sector_limit_up_count": 6,
        "is_sector_leader": True,
    }
    response = client.post("/api/review/three_question", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["passed"] is True
    assert len(data["questions"]) == 3


def test_three_question_endpoint_missing_data_fails_closed(client):
    payload = {"stock_code": "600519", "mode": "manual"}
    response = client.post("/api/review/three_question", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["passed"] is False
    assert set(data["missing"]) == {"身份", "断板", "竞价"}


def test_three_question_endpoint_rejects_bad_code(client):
    response = client.post(
        "/api/review/three_question",
        json={"stock_code": "abc", "mode": "manual"},
    )
    assert response.status_code == 422


def test_skills_catalog_endpoint(client):
    response = client.get("/api/agent/skills?category=市场复盘")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stats"]["total"] > 20
    assert all(item["category"] == "市场复盘" for item in data["skills"])


def test_skills_categories_endpoint(client):
    response = client.get("/api/agent/skills/categories")
    assert response.status_code == 200
    assert len(response.json()["data"]["categories"]) == 9


def test_dispatch_endpoint(client):
    response = client.post("/api/agent/dispatch", json={"query": "帮我复盘今天大盘"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent"]["skill_id"] == "daily_review"
    assert data["execution"] == "plan_only"


def test_strategy_validate_endpoint(client):
    returns = [0.01, -0.004] * 50
    response = client.post(
        "/api/strategy/validate",
        json={"returns": returns, "train_size": 40, "test_size": 20, "runs": 200},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert data["monte_carlo"]["runs"] == 200


def test_strategy_validate_rejects_empty_returns(client):
    response = client.post("/api/strategy/validate", json={"returns": []})
    assert response.status_code == 422


def test_journal_roundtrip(client, monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    payload = {
        "action": "buy",
        "stock_code": "600519",
        "price": 10.0,
        "quantity": 100,
        "gate_passed": True,
        "gate_mode": "manual",
        "note": "三问全过",
    }
    response = client.post("/api/review/journal", json=payload)
    assert response.status_code == 200
    entry = response.json()["data"]
    assert entry["action"] == "buy"
    assert entry["gate"]["passed"] is True

    report = client.get("/api/review/journal").json()["data"]
    assert report["stats"]["total"] == 1
    assert report["entries"][0]["stock_code"] == "600519"


def test_journal_rejects_negative_price(client, monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    response = client.post(
        "/api/review/journal",
        json={"action": "buy", "stock_code": "600519", "price": -1},
    )
    assert response.status_code == 400

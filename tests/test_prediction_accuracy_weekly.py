"""tests/test_prediction_accuracy_weekly.py — roadmap N-1 准确率周报验收。

口径锚点(与 test_prediction_accuracy_readonly / compute_accuracy 一致):
- 同一验证行双通道(rule 直取 hit / llm 按 LLM 方向用同一实际涨跌重判);
- 样本 <5 → hit_rate=None(不展示数字, insufficient_sample=True);
- GET /api/prediction/accuracy/weekly 公开读 + 纯只读, 绝不落库;
- 空周如实输出 total=0, 不用插值掩盖断档。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core import prediction_service as svc  # noqa: E402
from src.core.prediction_store import PredictionStore  # noqa: E402
from web.api import app  # noqa: E402

client = TestClient(app)


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PredictionStore:
    s = PredictionStore(tmp_path / "prediction.db")
    s.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: s)
    yield s
    s.close()


def _week_dates(today: str | None = None) -> tuple[str, str, str]:
    """返回 (本周一, 上周二, 今日) —— 两个跨周采样点, 相对今日计算防用例腐化。"""
    today = today or svc.beijing_today_str()
    d = _dt.datetime.strptime(today, "%Y-%m-%d").date()
    monday = d - _dt.timedelta(days=d.weekday())
    last_tue = (monday - _dt.timedelta(days=6)).strftime("%Y-%m-%d")
    return monday.strftime("%Y-%m-%d"), last_tue, today


def _seed_prediction(
    store: PredictionStore, trade_date: str, benchmark: str,
    direction: str = "up", llm_direction: str = "up",
) -> int:
    p = store.upsert_prediction(
        trade_date=trade_date, target_date=trade_date, benchmark=benchmark,
        direction=direction, confidence=60.0, rule_direction=direction,
        rule_confidence=60.0, rule_reasons=[], llm_direction=llm_direction,
        llm_confidence=60.0, llm_reason="", conflict_flag=0,
    )
    assert p is not None
    return int(p["id"]) if isinstance(p, dict) else int(p)


def _seed_verified(
    store: PredictionStore, trade_date: str, kind: str, benchmark: str,
    expected: str, actual: str, hit: int, actual_return: float,
    llm_direction: str | None = None,
) -> int:
    """种一条验证行; llm_direction 非空时同时种对应 prediction 行(供 llm 重判)。"""
    if kind == "prediction" and llm_direction is not None:
        pid = _seed_prediction(store, trade_date, benchmark,
                              direction=expected, llm_direction=llm_direction)
    else:
        pid = _seed_prediction(store, trade_date, benchmark, direction=expected)
    v = store.upsert_verification(trade_date, kind, pid, expected, actual,
                                  hit, actual_return)
    assert v is not None
    return pid


# ===== 服务层 =====


def test_weekly_empty_store_reports_zero_and_no_write(store: PredictionStore) -> None:
    """空库: 每周 total=0 / hit_rate=None, 且不产生任何落库副作用。"""
    before = store.list_accuracy_stats()
    out = svc.get_accuracy_weekly(store=store, weeks=8)

    assert len(out["weeks"]) == 8
    for w in out["weeks"]:
        for kind in ("prediction", "recommendation"):
            assert w[kind]["rule"]["total"] == 0
            assert w[kind]["rule"]["hit_rate"] is None
    assert out["insufficient_sample"] is True
    assert out["stat_end"] == svc.beijing_today_str()
    assert store.list_accuracy_stats() == before == [], "周报产生了写副作用"


def test_weekly_groups_two_weeks_with_correct_rates(store: PredictionStore) -> None:
    """跨周分组: 本周 5 中 → rate=1.0; 上周 3 条(<5) → rate=None 不展示。"""
    this_mon, last_tue, today = _week_dates()

    # 本周: 5 条 prediction 全部命中
    for i in range(5):
        _seed_verified(store, this_mon, "prediction", f"P{i}",
                       "up", "up", 1, 0.8)
    # 上周: 3 条 recommendation, 2 中 1 失(样本不足, rate 必须为 None)
    for i, hit in enumerate((1, 1, 0)):
        _seed_verified(store, last_tue, "recommendation", f"R{i}",
                       "up", "up" if hit else "down", hit, 0.5 if hit else -0.5)

    out = svc.get_accuracy_weekly(store=store, weeks=8)
    by_start = {w["week_start"]: w for w in out["weeks"]}

    cur = by_start[_monday_str(this_mon)]
    assert cur["prediction"]["rule"] == {"hits": 5, "total": 5, "hit_rate": 1.0}
    assert cur["label"].startswith(this_mon[5:])

    prev = by_start[_monday_str(last_tue)]
    assert prev["recommendation"]["rule"]["total"] == 3
    assert prev["recommendation"]["rule"]["hit_rate"] is None
    assert prev["prediction"]["rule"]["total"] == 0

    assert out["insufficient_sample"] is True
    # 空周不缺位: 8 周全部输出(如实呈现断档)
    assert len(out["weeks"]) == 8


def test_weekly_llm_rejudgement_differs_from_rule(store: PredictionStore) -> None:
    """llm 口径: 验证行 rule 未中(expected up/actual down), LLM 方向 down 命中。"""
    this_mon, _last_tue, _today = _week_dates()
    for i in range(5):
        _seed_verified(store, this_mon, "prediction", f"L{i}",
                       "up", "down", 0, -1.0, llm_direction="down")

    out = svc.get_accuracy_weekly(store=store, weeks=8)
    cur = next(w for w in out["weeks"] if w["prediction"]["rule"]["total"] == 5)
    assert cur["prediction"]["rule"]["hit_rate"] == 0.0
    assert cur["prediction"]["llm"] == {"hits": 5, "total": 5, "hit_rate": 1.0}


def _monday_str(date_str: str) -> str:
    d = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d - _dt.timedelta(days=d.weekday())).strftime("%Y-%m-%d")


# ===== 路由层 =====


def test_route_weekly_public_read_and_readonly(store: PredictionStore) -> None:
    """公开读(无 token 可达) + 两次调用不落库。"""
    assert store.list_accuracy_stats() == []
    r1 = client.get("/api/prediction/accuracy/weekly")
    r2 = client.get("/api/prediction/accuracy/weekly?weeks=4")

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["code"] == 0
    assert len(r2.json()["data"]["weeks"]) == 4
    assert r2.json()["data"]["weeks_requested"] == 4
    assert store.list_accuracy_stats() == []


@pytest.mark.parametrize("bad", [0, 13])
def test_route_weekly_rejects_out_of_range_weeks(store: PredictionStore, bad: int) -> None:
    """weeks 越界(0 / 13)被 422 拒绝。"""
    resp = client.get(f"/api/prediction/accuracy/weekly?weeks={bad}")
    assert resp.status_code == 422

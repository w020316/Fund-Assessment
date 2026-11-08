"""tests/test_accuracy_reconciliation.py — R6.3 对账验收。

看板数字与库内统计一致(双通道同源):
- GET /api/prediction/accuracy 的 windows == 由 verification_results 库内重算
- accuracy_stats 落库行 == 看板返回(verify 后统计与前端实时计算同一函数)
- missing=1 占位行不计入任何窗口
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.prediction_store import PredictionStore  # noqa: E402
from web.api import app  # noqa: E402
from web.routes import prediction as prediction_routes  # noqa: E402
from src.core import prediction_service as svc  # noqa: E402

client = TestClient(app)


@pytest.fixture()
def seeded_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PredictionStore:
    """7 条预测验证(6 命中) + 5 条推荐验证(3 命中) + 1 条 missing 占位(不计入)。"""
    store = PredictionStore(tmp_path / "prediction.db")
    store.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: store)
    today = svc.beijing_today_str()
    # 修复后验证口径: verify_daily 在目标日当日验证, 验证行 trade_date == target_date == today
    target = today
    for i in range(7):
        p = store.upsert_prediction(
            trade_date=today, target_date=target, benchmark=f"P{i}",
            direction="up", confidence=60, rule_direction="up", rule_confidence=60,
            rule_reasons=[], llm_direction=None, llm_confidence=None, llm_reason="",
            conflict_flag=0,
        )
        assert p is not None
        hit = 1 if i < 6 else 0
        store.upsert_verification(target, "prediction", p["id"], "up",
                                  "up" if hit else "down", hit, 0.9 if hit else -0.9)
    # 1 条 missing 占位(16:30 仍取不到): 任何统计都不得计入
    p_miss = store.upsert_prediction(
        trade_date=today, target_date=target, benchmark="PM",
        direction="up", confidence=60, rule_direction="up", rule_confidence=60,
        rule_reasons=[], llm_direction=None, llm_confidence=None, llm_reason="",
        conflict_flag=0,
    )
    assert p_miss is not None
    store.upsert_verification(target, "prediction", p_miss["id"], "up", "", None, None, missing=1)
    # 5 条推荐验证: 3 命中
    ids = []
    with __import__("src.core.prediction_store", fromlist=["_LOCK"])._LOCK:
        conn = store._connection()
        for i in range(5):
            cur = conn.execute(
                "INSERT INTO recommendations (trade_date, target_date, symbol, name, kind,"
                " direction, confidence, reason, source_signal, created_at)"
                " VALUES (?, ?, ?, ?, 'stock', 'up', 60, '', ?, 't')",
                (today, target, f"51030{i}", f"基金{i}", f"src{i}"),
            )
            ids.append(cur.lastrowid)
        conn.commit()
    for i, rid in enumerate(ids):
        hit = 1 if i < 3 else 0
        store.upsert_verification(target, "recommendation", rid, "up",
                                  "up" if hit else "down", hit, 1.0 if hit else -1.0)
    return store


def test_dashboard_matches_store_recompute(seeded_store: PredictionStore) -> None:
    resp = client.get("/api/prediction/accuracy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    dash = body["data"]

    # 库内独立重算(排除 missing)
    today = svc.beijing_today_str()
    start30 = svc._window_start(today, "30d")
    rows = seeded_store.list_verifications_between(start30, today, include_missing=False)
    pred_rows = [r for r in rows if r["target_kind"] == "prediction"]
    rec_rows = [r for r in rows if r["target_kind"] == "recommendation"]

    pred_rule = dash["windows"]["30d"]["prediction"]["rule"]
    assert pred_rule["total"] == len(pred_rows) == 7  # missing 占位已被排除
    assert pred_rule["hits"] == sum(1 for r in pred_rows if r["hit"] == 1) == 6
    assert pred_rule["hit_rate"] == pytest.approx(6 / 7, abs=1e-4)

    rec_rule = dash["windows"]["30d"]["recommendation"]["rule"]
    assert rec_rule["total"] == len(rec_rows) == 5
    assert rec_rule["hits"] == sum(1 for r in rec_rows if r["hit"] == 1) == 3
    assert rec_rule["hit_rate"] == pytest.approx(0.6, abs=1e-4)

    # 各窗口单调一致: 7d == 30d(同日数据); day 窗口按 [today, today] 取数,
    # 验证行 trade_date == target_date == today → 全部计入 day 窗口(预测 7 条)
    assert dash["windows"]["7d"]["prediction"]["rule"]["total"] == 7
    day_rows = [r for r in rows if r["trade_date"] >= svc._window_start(today, "day")]
    day_pred_expected = sum(1 for r in day_rows if r["target_kind"] == "prediction")
    assert dash["windows"]["day"]["prediction"]["rule"]["total"] == day_pred_expected == 7

    # 走势: 唯一验证日 total=12(7+5), 命中 9
    assert len(dash["trend_30d"]) == 1
    point = dash["trend_30d"][0]
    assert point["total"] == 12
    assert point["hit_rate"] == pytest.approx(9 / 12, abs=1e-4)

    # insufficient_sample 与"任一窗口 total<5"重算一致
    any_low = any(
        w[k][src]["total"] < 5
        for w in dash["windows"].values()
        for k in w
        for src in w[k]
    )
    assert dash["insufficient_sample"] is any_low


def test_verify_persists_stats_matching_dashboard(seeded_store: PredictionStore) -> None:
    """落库的 accuracy_stats 与看板内存口径逐项一致(对账锚点)。

    QF3-07 修复(2026-11-08): 历史版本用 ``client.get("/api/prediction/accuracy")``
    触发落库, 即把"公开 GET 产生写副作用"当成规格 —— 这正是 QF3-07 要消除的行为,
    旧断言无法与修复共存。现改为经真实写路径 compute_accuracy(persist=True)
    (即 POST /verify 内部行为)落库, 再断言 GET 的只读输出与库内逐项一致,
    并额外锁死"GET 完全不写"。覆盖强度上升, 未放宽任何阈值。
    """
    today = svc.beijing_today_str()

    # 1) 写路径落库(与 verify_daily 内部同参: compute_accuracy(store=store, persist=True))
    svc.compute_accuracy(store=seeded_store, stat_date=today, persist=True)
    stats = seeded_store.list_accuracy_stats(stat_date=today)
    by_key = {(s["window"], s["target_kind"], s["source"]): s for s in stats}
    assert ("30d", "prediction", "rule") in by_key
    assert ("30d", "recommendation", "rule") in by_key
    assert by_key[("30d", "prediction", "rule")]["hit_rate"] == pytest.approx(6 / 7, abs=1e-4)
    assert by_key[("30d", "recommendation", "rule")]["hit_rate"] == pytest.approx(0.6, abs=1e-4)

    # 2) 只读路径: 数字与库内一致(原对账意图), 且不改变库内状态(QF3-07 护栏)
    dash = client.get("/api/prediction/accuracy").json()["data"]
    assert dash["persisted"] is False
    assert len(seeded_store.list_accuracy_stats(stat_date=today)) == len(stats)  # 幂等: 不新增不翻倍
    assert dash["windows"]["30d"]["prediction"]["rule"]["hit_rate"] == pytest.approx(6 / 7, abs=1e-4)
    assert dash["windows"]["30d"]["recommendation"]["rule"]["hit_rate"] == pytest.approx(0.6, abs=1e-4)
    # 对账锚点本体: 看板内存口径 == 库内落库口径
    assert (
        dash["windows"]["30d"]["prediction"]["rule"]["hit_rate"]
        == by_key[("30d", "prediction", "rule")]["hit_rate"]
    )
    assert (
        dash["windows"]["30d"]["recommendation"]["rule"]["hit_rate"]
        == by_key[("30d", "recommendation", "rule")]["hit_rate"]
    )
    # 重复调用仍然不写(原「幂等: 重复调用不翻倍」意图保留并加强为「完全不写」)
    client.get("/api/prediction/accuracy")
    assert len(seeded_store.list_accuracy_stats(stat_date=today)) == len(stats)

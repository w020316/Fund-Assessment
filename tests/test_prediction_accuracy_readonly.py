"""tests/test_prediction_accuracy_readonly.py — QF3-07 公开 GET 写副作用验收。

缺陷(QF3-07): GET /api/prediction/accuracy 是公开、无鉴权的读接口, 但
compute_accuracy() 内部无条件 upsert_accuracy_stats 写 9 行。前端每次切到
monitor 页都会调用它 —— 匿名读请求产生写副作用, 既污染写路径也放大并发写锁争用。

修复口径:
- GET /accuracy → get_accuracy_dashboard() → compute_accuracy(persist=False):
  纯内存计算, 不落库, 响应带 persisted=False;
- 落库只发生在有鉴权的 POST /verify 之后的 verify_daily(compute_accuracy(persist=True))。
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

from src.core import data_source_v2 as ds2  # noqa: E402
from src.core import prediction_service as svc  # noqa: E402
from src.core.prediction_store import PredictionStore  # noqa: E402
from web.api import app  # noqa: E402

client = TestClient(app)

_ADMIN_TOKEN = "accuracy-readonly-token"


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PredictionStore:
    s = PredictionStore(tmp_path / "prediction.db")
    s.init_db()
    monkeypatch.setattr(svc, "get_store", lambda: s)
    yield s
    s.close()


def _seed_predictions(store: PredictionStore, today: str, n: int = 5) -> None:
    """seed n 条待验证预测(target_date=today), 不写验证行 —— 供 verify 用例。"""
    for i in range(n):
        p = store.upsert_prediction(
            trade_date=today, target_date=today, benchmark=f"B{i}",
            direction="up", confidence=60.0, rule_direction="up", rule_confidence=60.0,
            rule_reasons=[], llm_direction="up", llm_confidence=60.0,
            llm_reason="", conflict_flag=0,
        )
        assert p is not None


def _seed_verified(store: PredictionStore, today: str, n: int = 5) -> None:
    """seed n 条已验证的预测行, 让统计窗口有真实样本。"""
    _seed_predictions(store, today, n)
    for p in store.list_predictions_for_target(today):
        store.upsert_verification(today, "prediction", p["id"], "up", "up", 1, 0.8)


# ===== GET 只读 =====


def test_get_accuracy_twice_does_not_write(store: PredictionStore) -> None:
    """连续调用 GET /accuracy 两次, accuracy_stats 行数不变(核心断言)。"""
    assert store.list_accuracy_stats() == []

    first = client.get("/api/prediction/accuracy")
    second = client.get("/api/prediction/accuracy")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["code"] == 0 and second.json()["code"] == 0
    assert store.list_accuracy_stats() == [], "GET /accuracy 产生了写副作用"


def test_get_accuracy_reports_persisted_false(store: PredictionStore) -> None:
    """GET 响应必须显式标注 persisted=False, 让调用方知道本次未落库。"""
    body = client.get("/api/prediction/accuracy").json()["data"]
    assert body["persisted"] is False
    assert set(body) >= {"windows", "insufficient_sample", "trend_30d", "stat_date"}


def test_get_accuracy_readonly_even_with_samples(store: PredictionStore) -> None:
    """有真实验证样本时 GET 仍不落库, 且返回的统计数字正确。"""
    today = svc.beijing_today_str()
    _seed_verified(store, today, n=5)

    before = store.list_accuracy_stats()
    data = client.get("/api/prediction/accuracy").json()["data"]

    assert store.list_accuracy_stats() == before == []
    rule30 = data["windows"]["30d"]["prediction"]["rule"]
    assert rule30["hits"] == 5 and rule30["total"] == 5
    assert rule30["hit_rate"] == pytest.approx(1.0)


# ===== POST /verify 才落库 =====


def test_verify_with_admin_token_persists_stats(
    store: PredictionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /verify(带 admin token)成功后, accuracy_stats 行数增加。"""
    monkeypatch.setenv("ADMIN_TOKEN", _ADMIN_TOKEN)
    # QF3-04 起 verify 有 15:00 时点守卫, 本用例必须固定时钟, 否则在盘中运行时
    # 会被判 too_early(与断言无关的环境依赖)。固定为交易日 15:40。
    fixed_now = _dt.datetime(2026, 11, 9, 15, 40, tzinfo=_dt.timezone(_dt.timedelta(hours=8)))
    monkeypatch.setattr(svc, "beijing_now", lambda: fixed_now)
    today = svc.beijing_today_str()
    _seed_predictions(store, today, n=5)  # 待验证, 尚无验证行

    # 交易日探针 + 沪深300 实际涨跌(verify 需要真实取数)
    monkeypatch.setattr(
        ds2, "get_fund_realtime_tencent",
        lambda codes, **kw: [{"code": "018957", "update_time": f"{today} 15:00"}],
    )
    monkeypatch.setattr(
        ds2, "get_index_realtime",
        lambda: [{"code": "000300", "name": "沪深300", "price": 4000.0, "change_pct": 0.8}],
    )

    assert store.list_accuracy_stats() == []
    resp = client.post(
        "/api/prediction/verify", headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"}
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    assert payload["data"]["verified"] == 5

    stats = store.list_accuracy_stats(stat_date=today)
    assert len(stats) == 9, "verify 后应落 9 行统计(3 窗口 × [rule+llm / rule])"
    pair = {(s["window"], s["target_kind"], s["source"]): s for s in stats}
    assert pair[("30d", "prediction", "rule")]["hits"] == 5


def test_verify_without_token_does_not_write(
    store: PredictionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 token 的 POST /verify 被 401 拦截, 不得产生任何统计行。"""
    monkeypatch.setenv("ADMIN_TOKEN", _ADMIN_TOKEN)
    today = svc.beijing_today_str()
    _seed_verified(store, today, n=5)

    resp = client.post("/api/prediction/verify")
    assert resp.status_code == 401
    assert store.list_accuracy_stats() == []


# ===== 服务层口径 =====


def test_dashboard_readonly_but_compute_accuracy_default_persists(
    store: PredictionStore,
) -> None:
    """服务层: get_accuracy_dashboard 不写; compute_accuracy 默认仍写(verify 依赖)。"""
    today = svc.beijing_today_str()
    _seed_verified(store, today, n=5)

    dash = svc.get_accuracy_dashboard()
    assert dash["persisted"] is False
    assert store.list_accuracy_stats() == []

    direct = svc.compute_accuracy(store=store, stat_date=today)
    assert direct["persisted"] is True
    assert len(store.list_accuracy_stats(stat_date=today)) == 9

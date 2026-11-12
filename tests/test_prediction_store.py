"""tests/test_prediction_store.py — T01 数据层验收。

覆盖:
- 建表幂等(init_db 重复调用)
- snapshot / prediction / verification / accuracy UNIQUE 幂等 upsert 覆盖
- 保留策略 cleanup: 不误删当日, 超期行被清理, stats 窗口独立
- trade.db / risk.db 零改动(mtime + sha256 断言, 设计红线 §六 #9)
- export_bytes 输出可用 SQLite 重新打开
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.prediction_store import (  # noqa: E402
    DETAIL_RETENTION_DAYS,
    STATS_RETENTION_DAYS,
    PredictionStore,
    resolve_db_path,
)
from tests.conftest import trading_days_ago  # noqa: E402


def _file_state(p: Path) -> tuple[int, str] | None:
    if not p.exists():
        return None
    st = p.stat()
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    return (st.st_mtime_ns, digest)


@pytest.fixture()
def store(tmp_path: Path) -> PredictionStore:
    s = PredictionStore(tmp_path / "prediction.db")
    assert s.init_db() is True
    yield s
    s.close()


def _old_date(days_ago: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_resolve_db_path_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PREDICTION_DB_PATH", str(tmp_path / "x.db"))
    assert resolve_db_path() == tmp_path / "x.db"
    monkeypatch.delenv("PREDICTION_DB_PATH")
    assert resolve_db_path(tmp_path / "y.db") == tmp_path / "y.db"


def test_init_db_idempotent(store: PredictionStore) -> None:
    assert store.init_db() is True
    assert store.init_db() is True
    with sqlite3.connect(str(store.db_path)) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "session_snapshots",
        "predictions",
        "recommendations",
        "verification_results",
        "accuracy_stats",
    } <= tables


def test_upsert_snapshot_idempotent(store: PredictionStore) -> None:
    first = store.upsert_snapshot(
        "2026-11-09", "morning", {"indices": []}, [], "规则摘要", "LLM 摘要"
    )
    assert first is not None
    second = store.upsert_snapshot(
        "2026-11-09", "morning", {"indices": [{"name": "沪深300"}]}, ["sectors"], "新规则摘要", ""
    )
    assert second is not None
    rows = store.list_snapshots(trade_date="2026-11-09")
    assert len(rows) == 1  # UNIQUE(trade_date, session) 覆盖而非追加
    assert rows[0]["rule_summary"] == "新规则摘要"
    assert rows[0]["payload"] == {"indices": [{"name": "沪深300"}]}
    assert rows[0]["missing_fields"] == ["sectors"]


def test_snapshot_payload_corrupt_json_falls_back(store: PredictionStore) -> None:
    store.upsert_snapshot("2026-11-09", "noon", {"a": 1}, [], "s", "")
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute("UPDATE session_snapshots SET payload_json = '{broken'")
        conn.commit()
    row = store.get_snapshot("2026-11-09", "noon")
    assert row is not None
    assert row["payload"] == {}
    assert row["rule_summary"] == "s"


def test_upsert_prediction_idempotent(store: PredictionStore) -> None:
    # 日期取自真实时钟的相对偏移: list_predictions(days=30) 以 date('now') 为基准,
    # 硬编码日期会随时间滑出窗口而静默丢行(日期炸弹)。
    trade_date = trading_days_ago(1)
    target_date = trading_days_ago(0)
    p1 = store.upsert_prediction(
        trade_date=trade_date,
        target_date=target_date,
        benchmark="HS300",
        direction="up",
        confidence=66.0,
        rule_direction="up",
        rule_confidence=66.0,
        rule_reasons=[{"signal": "breadth", "value": 0.62, "verdict": "bullish"}],
        llm_direction="down",
        llm_confidence=55.0,
        llm_reason="LLM 看空",
        conflict_flag=1,
    )
    assert p1 is not None and p1["id"] > 0
    p2 = store.upsert_prediction(
        trade_date=trade_date,
        target_date=target_date,
        benchmark="HS300",
        direction="flat",
        confidence=50.0,
        rule_direction="flat",
        rule_confidence=50.0,
        rule_reasons=[],
        llm_direction=None,
        llm_confidence=None,
        llm_reason="",
        conflict_flag=0,
    )
    assert p2 is not None
    assert p2["id"] == p1["id"]  # 同日同基准覆盖
    assert p2["direction"] == "flat"
    assert store.get_prediction(trade_date)["conflict_flag"] == 0
    assert store.list_predictions(days=30)[0]["trade_date"] == trade_date


def test_replace_recommendations(store: PredictionStore) -> None:
    items_v1 = [
        {"symbol": "512480", "name": "半导体ETF", "kind": "stock", "direction": "up",
         "confidence": 70.0, "reason": "r", "source_signal": "sector_momentum:半导体|change_pct=2.1"},
    ]
    items_v2 = items_v1 + [
        {"symbol": "510300", "name": "沪深300ETF", "kind": "stock", "direction": "up",
         "confidence": 60.0, "reason": "r2", "source_signal": "sector_momentum:沪深300|change_pct=1.2"},
    ]
    assert store.replace_recommendations("2026-11-09", "2026-11-10", items_v1) == 1
    assert store.replace_recommendations("2026-11-09", "2026-11-10", items_v2) == 2
    rows = store.list_recommendations(trade_date="2026-11-09")
    assert len(rows) == 2  # 整日覆盖, 不残留 v1 重复行
    target_rows = store.list_recommendations_for_target("2026-11-10")
    assert {r["symbol"] for r in target_rows} == {"512480", "510300"}


def test_verification_upsert_and_retry_overwrite(store: PredictionStore) -> None:
    # 相对日期(conftest.trading_days_ago): 避免未来日期字面量随时间漂移成 warning/红测
    day = trading_days_ago(2)
    window_start, window_end = trading_days_ago(30), trading_days_ago(0)
    v1 = store.upsert_verification(day, "prediction", 42, "up", "up", 1, 0.8)
    assert v1 is not None and v1["hit"] == 1
    # 16:30 重试: missing 占位覆盖原结果(幂等 upsert)
    v2 = store.upsert_verification(
        day, "prediction", 42, "up", "", None, None, missing=1
    )
    assert v2 is not None
    assert v2["missing"] == 1 and v2["hit"] is None
    rows = store.list_verifications(days=30)
    assert len(rows) == 1
    # 统计取数口径: 默认排除 missing
    assert store.list_verifications_between(window_start, window_end) == []
    assert len(store.list_verifications_between(window_start, window_end, include_missing=True)) == 1


def test_accuracy_stats_upsert(store: PredictionStore) -> None:
    rows = [
        {"window": "day", "target_kind": "prediction", "source": "rule",
         "hits": 3, "total": 4, "hit_rate": 0.75},
        {"window": "7d", "target_kind": "recommendation", "source": "rule",
         "hits": 2, "total": 3, "hit_rate": 2 / 3},
    ]
    assert store.upsert_accuracy_stats(rows, stat_date="2026-11-10") == 2
    rows2 = [
        {"window": "day", "target_kind": "prediction", "source": "rule",
         "hits": 1, "total": 1, "hit_rate": 1.0},
    ]
    store.upsert_accuracy_stats(rows2, stat_date="2026-11-10")
    stats = store.list_accuracy_stats(stat_date="2026-11-10")
    assert len(stats) == 2
    day_rule = [s for s in stats if s["window"] == "day"][0]
    assert day_rule["hits"] == 1 and day_rule["total"] == 1  # 覆盖生效
    assert day_rule["hit_rate"] == pytest.approx(1.0)


def test_cleanup_keeps_today_and_recent(store: PredictionStore) -> None:
    today = _dt.date.today().strftime("%Y-%m-%d")
    recent = _old_date(DETAIL_RETENTION_DAYS - 1)
    stale = _old_date(DETAIL_RETENTION_DAYS + 5)
    store.upsert_snapshot(today, "morning", {}, [], "t", "")
    store.upsert_prediction(stale, stale, "HS300", "up", 60, "up", 60, [], None, None, "", 0)
    store.upsert_prediction(recent, recent, "HS300", "up", 60, "up", 60, [], None, None, "", 0)
    store.replace_recommendations(stale, stale, [
        {"symbol": "512480", "name": "", "kind": "stock", "direction": "up",
         "confidence": 60, "reason": "", "source_signal": "x"},
    ])
    store.upsert_verification(stale, "prediction", 1, "up", "up", 1, 0.5)
    store.upsert_accuracy_stats(
        [{"window": "day", "target_kind": "prediction", "source": "rule",
          "hits": 0, "total": 0, "hit_rate": None}],
        stat_date=_old_date(STATS_RETENTION_DAYS + 5),
    )
    result = store.cleanup(today=today)
    assert result["predictions"] == 1  # 只删 stale, recent 保留
    assert result["session_snapshots"] == 0
    assert result["accuracy_stats"] == 1
    assert store.get_prediction(recent) is not None
    assert store.get_prediction(stale) is None
    assert store.get_snapshot(today, "morning") is not None  # 当日绝不误删
    # stale(185 天前)超过 180 天保留期 → 明细与验证行都应被清理
    assert store.get_verification(stale, "prediction", 1) is None
    recent_ver_date = _old_date(10)
    store.upsert_verification(recent_ver_date, "prediction", 1, "up", "up", 1, 0.5)
    store.cleanup(today=today)
    assert store.get_verification(recent_ver_date, "prediction", 1) is not None


def test_trade_and_risk_db_untouched(tmp_path: Path) -> None:
    """红线: 任何 prediction_store 操作不得改动 trade.db / risk.db。"""
    real_data = Path(_PROJECT_ROOT) / "data"
    trade_before = _file_state(real_data / "trade.db")
    risk_before = _file_state(real_data / "risk.db")
    s = PredictionStore(tmp_path / "prediction.db")
    s.init_db()
    s.upsert_snapshot("2026-11-09", "morning", {}, [], "s", "")
    s.upsert_prediction("2026-11-09", "2026-11-10", "HS300", "up", 60, "up", 60, [], None, None, "", 0)
    s.cleanup()
    s.close()
    assert _file_state(real_data / "trade.db") == trade_before
    assert _file_state(real_data / "risk.db") == risk_before


def test_export_bytes_roundtrip(store: PredictionStore) -> None:
    store.upsert_snapshot("2026-11-09", "close", {"indices": [{"name": "沪深300"}]}, [], "s", "")
    blob = store.export_bytes()
    assert isinstance(blob, bytes) and len(blob) > 0
    tmp = store.db_path.parent / "export_check.db"
    tmp.write_bytes(blob)
    conn = sqlite3.connect(str(tmp))
    try:
        n = conn.execute("SELECT COUNT(*) FROM session_snapshots").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
    tmp.unlink(missing_ok=True)

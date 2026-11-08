"""tests/test_prediction_store_tx.py — QF3-08 写事务回滚 + QF3-17 入参可追溯性验收。

缺陷(QF3-08): prediction_store 全库 7 处 commit()、0 处 rollback()。
replace_recommendations() 先 DELETE 当日行再逐条 INSERT, 只在末尾 commit()。
Python sqlite3 在 DML 前开隐式事务, 所以 INSERT 中途抛异常时:
  - 事务既不提交也不回滚, DELETE 挂在未提交事务里;
  - 该连接是 check_same_thread=False 的单例长连接, 持写锁;
  - 下一个任意写方法的 commit() 会把这条 DELETE 一起提交
    → 当日推荐被静默清空, 调用方只看到异常或 0。

缺陷(QF3-17): replace_recommendations 用 direction="up" / confidence=50.0 兜底,
把"无法追溯的条目"伪造成合规推荐, 违反 PRD R4.1「无法追溯的条目自动丢弃并记日志」。

修复口径: 写失败必回滚后抛出(不留半开事务); 不可追溯条目丢弃并 warning。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from loguru import logger as _logger

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.prediction_store import PredictionStore  # noqa: E402

_TRADE_DATE = "2026-11-09"
_TARGET_DATE = "2026-11-10"


class _FlakyConn:
    """包装真实连接: 第 fail_on 次 execute 抛 sqlite3.OperationalError。

    用于在 DELETE 已执行、INSERT 未完成的位置精确注入故障, 复现 QF3-08 的
    "半截事务"现场。其余属性一律透传给真实连接。
    """

    def __init__(self, real: sqlite3.Connection, fail_on: int = 2) -> None:
        self._real = real
        self._fail_on = fail_on
        self.calls = 0

    def execute(self, sql: str, *params: Any):  # noqa: ANN201
        self.calls += 1
        if self.calls == self._fail_on:
            raise sqlite3.OperationalError("disk I/O error(模拟 INSERT 失败)")
        return self._real.execute(sql, *params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _item(symbol: str, direction: str = "up", confidence: float = 70.0) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": f"标的{symbol}",
        "kind": "stock",
        "direction": direction,
        "confidence": confidence,
        "reason": "r",
        "source_signal": f"sector_momentum:板块{symbol}|change_pct=+2.00",
    }


@pytest.fixture()
def store(tmp_path: Path) -> PredictionStore:
    s = PredictionStore(tmp_path / "prediction.db")
    assert s.init_db() is True
    yield s
    s.close()


def _capture_warnings() -> tuple[list[str], int]:
    """挂一个 loguru sink 收集 WARNING 以上日志, 返回 (记录列表, sink_id)。"""
    records: list[str] = []
    sink_id = _logger.add(lambda m: records.append(m), level="WARNING")
    return records, sink_id


# ===== 核心: 回滚护栏 =====


def test_replace_recommendations_rollback_keeps_old_rows(store, monkeypatch) -> None:
    """DELETE 已执行、INSERT 失败 → 回滚后旧 3 条必须原样保留(核心断言)。

    若没有 rollback: DELETE 留在未提交事务里, 同连接可见 → 查询结果为 0 条。
    """
    old = [_item("512480"), _item("510300"), _item("512690")]
    assert store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, old) == 3

    # 先建立 self._conn(真实连接), 再让 _connection() 返回会注入故障的包装体
    real_conn = store._connection()
    flaky = _FlakyConn(real_conn, fail_on=2)  # #1=DELETE, #2=第一条 INSERT → 抛
    monkeypatch.setattr(store, "_connection", lambda: flaky)

    with pytest.raises(sqlite3.OperationalError):
        store.replace_recommendations(
            _TRADE_DATE, _TARGET_DATE, [_item("000001"), _item("000002")]
        )
    assert flaky.calls == 2  # 确认故障点落在 INSERT 上, DELETE 已执行

    monkeypatch.undo()  # 恢复真实连接
    rows = store.list_recommendations(trade_date=_TRADE_DATE)
    assert len(rows) == 3, "DELETE 未被回滚, 旧推荐丢失"
    assert {r["symbol"] for r in rows} == {"512480", "510300", "512690"}
    # 新数据一条都不该留下(整批原子)
    assert "000001" not in {r["symbol"] for r in rows}


def test_failed_delete_not_committed_by_next_write(store, monkeypatch) -> None:
    """根因复现: 失败后紧跟另一个写方法的 commit(), 旧推荐不得被顺带清空。

    这正是 QF3-08 的数据丢失路径 —— 单看返回值发现不了, 必须验证库内状态。
    """
    old = [_item("512480"), _item("510300"), _item("512690")]
    assert store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, old) == 3

    real_conn = store._connection()
    flaky = _FlakyConn(real_conn, fail_on=2)
    monkeypatch.setattr(store, "_connection", lambda: flaky)
    with pytest.raises(sqlite3.OperationalError):
        store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, [_item("000001")])
    monkeypatch.undo()

    # 后续无关写操作(会 commit) —— 修复前会把挂起的 DELETE 一起提交
    assert store.upsert_snapshot(_TRADE_DATE, "close", {}, [], "s", "") is not None

    rows = store.list_recommendations(trade_date=_TRADE_DATE)
    assert len(rows) == 3, "半开事务被后续 commit 顺带提交, 当日推荐被清空"


def test_connection_usable_after_failure(store, monkeypatch) -> None:
    """异常后连接仍可正常写入(证明回滚释放了写锁, 无残留事务)。"""
    assert store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, [_item("512480")]) == 1

    real_conn = store._connection()
    flaky = _FlakyConn(real_conn, fail_on=2)
    monkeypatch.setattr(store, "_connection", lambda: flaky)
    with pytest.raises(sqlite3.OperationalError):
        store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, [_item("000001")])
    monkeypatch.undo()

    # 回滚后同一连接可继续写: 整日覆盖成功
    assert store.replace_recommendations(
        _TRADE_DATE, _TARGET_DATE, [_item("510300"), _item("512690")]
    ) == 2
    assert {r["symbol"] for r in store.list_recommendations(trade_date=_TRADE_DATE)} == {
        "510300", "512690",
    }


# ===== QF3-17: 不可追溯条目丢弃 =====


def test_untraceable_items_dropped_with_warning(store) -> None:
    """缺 direction / confidence 的条目被丢弃并记 warning, 其余正常写入。"""
    items = [
        _item("512480"),
        {"symbol": "510300", "kind": "stock", "confidence": 60.0, "source_signal": "s"},
        {"symbol": "512690", "kind": "stock", "direction": "up", "source_signal": "s"},
        {"symbol": "000001", "kind": "stock", "direction": "sideways",
         "confidence": 60.0, "source_signal": "s"},
    ]
    records, sink_id = _capture_warnings()
    try:
        n = store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, items)
    finally:
        _logger.remove(sink_id)

    assert n == 1
    assert [r["symbol"] for r in store.list_recommendations(trade_date=_TRADE_DATE)] == ["512480"]

    joined = "\n".join(records)
    assert "丢弃 3 条" in joined
    for sym in ("510300", "512690", "000001"):
        assert sym in joined  # 被丢弃的 symbol 逐条可查
    assert "direction" in joined and "confidence" in joined


def test_untraceable_items_never_fabricated_as_up_fifty(store) -> None:
    """回归护栏: 缺字段条目绝不能被伪造成 direction=up / confidence=50。"""
    store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, [
        {"symbol": "510300", "kind": "stock", "source_signal": "s"},
    ])
    assert store.list_recommendations(trade_date=_TRADE_DATE) == []


def test_all_items_invalid_clears_day_without_exception(store) -> None:
    """全部条目非法 → 不抛异常, 当日推荐为空(整日清空是合法语义), 且记 warning。"""
    assert store.replace_recommendations(_TRADE_DATE, _TARGET_DATE, [_item("512480")]) == 1

    records, sink_id = _capture_warnings()
    try:
        n = store.replace_recommendations(
            _TRADE_DATE, _TARGET_DATE, [{"symbol": "x"}, {"symbol": "y", "direction": "up"}]
        )
    finally:
        _logger.remove(sink_id)

    assert n == 0
    assert store.list_recommendations(trade_date=_TRADE_DATE) == []
    assert "当日推荐将被清空" in "\n".join(records)


# ===== 其它写方法同样不留半开事务 =====


def test_upsert_prediction_failure_rolls_back_and_raises(store, monkeypatch) -> None:
    """单语句 upsert 失败也回滚并抛出, 不静默返回 None。"""
    real_conn = store._connection()
    flaky = _FlakyConn(real_conn, fail_on=1)
    monkeypatch.setattr(store, "_connection", lambda: flaky)

    with pytest.raises(sqlite3.OperationalError):
        store.upsert_prediction(
            _TRADE_DATE, _TARGET_DATE, "HS300", "up", 60.0, "up", 60.0, [], None, None, "", 0
        )
    monkeypatch.undo()

    # 回滚后库内无残留, 且连接可继续使用
    assert store.get_prediction(_TRADE_DATE) is None
    assert store.upsert_prediction(
        _TRADE_DATE, _TARGET_DATE, "HS300", "up", 60.0, "up", 60.0, [], None, None, "", 0
    ) is not None

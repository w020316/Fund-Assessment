"""prediction.db 存储层(设计文档 docs/design-prediction-2026-11-07.md §1.3 / T01)。

职责:
- data/prediction.db(与 trade.db 同盘)的建表/索引/幂等 upsert/查询/保留清理
- 参照 portfolio_store.py 的锁模式: 模块级 threading.Lock + 单连接 check_same_thread=False

铁律(设计文档 §六 #9):
- 只写 data/prediction.db, 绝不触碰 trade.db / risk.db(测试有 hash 断言)
- 写入失败由调用方容错, 本层方法不抛裸异常给路由层(统一返回 None/空列表 + logger)
- UNIQUE 约束承担幂等: snapshot(trade_date,session) / prediction(trade_date,benchmark) /
  verification(trade_date,target_kind,target_id) / accuracy(stat_date,window,kind,source)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from loguru import logger

# 项目根目录(参照 portfolio_store.py 的定位方式)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "prediction.db"

# 测试/运维可通过环境变量覆盖库路径(如 tmp 目录)
ENV_DB_PATH = "PREDICTION_DB_PATH"

# 明细三表 + verification 保留 180 天; accuracy_stats 保留 365 天(R5.2)
DETAIL_RETENTION_DAYS = 180
STATS_RETENTION_DAYS = 365

_LOCK = threading.Lock()

# ===== DDL(与设计文档 §1.3 逐字一致) =====

_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS session_snapshots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date    TEXT NOT NULL,
        session       TEXT NOT NULL CHECK (session IN ('morning','noon','close')),
        collected_at  TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        missing_fields TEXT NOT NULL DEFAULT '',
        rule_summary  TEXT NOT NULL DEFAULT '',
        llm_summary   TEXT NOT NULL DEFAULT '',
        UNIQUE (trade_date, session)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ss_date ON session_snapshots (trade_date)",
    """
    CREATE TABLE IF NOT EXISTS predictions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date      TEXT NOT NULL,
        target_date     TEXT NOT NULL,
        benchmark       TEXT NOT NULL DEFAULT 'HS300',
        direction       TEXT NOT NULL CHECK (direction IN ('up','down','flat')),
        confidence      REAL NOT NULL,
        rule_direction  TEXT NOT NULL,
        rule_confidence REAL NOT NULL,
        rule_reasons    TEXT NOT NULL DEFAULT '[]',
        llm_direction   TEXT,
        llm_confidence  REAL,
        llm_reason      TEXT NOT NULL DEFAULT '',
        conflict_flag   INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        UNIQUE (trade_date, benchmark)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pred_date ON predictions (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_pred_target ON predictions (target_date)",
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date      TEXT NOT NULL,
        target_date     TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        name            TEXT NOT NULL DEFAULT '',
        kind            TEXT NOT NULL CHECK (kind IN ('stock','fund')),
        direction       TEXT NOT NULL CHECK (direction IN ('up','down')),
        confidence      REAL NOT NULL,
        reason          TEXT NOT NULL DEFAULT '',
        source_signal   TEXT NOT NULL,
        realized_return REAL,
        created_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_rec_target ON recommendations (target_date)",
    """
    CREATE TABLE IF NOT EXISTS verification_results (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date    TEXT NOT NULL,
        target_kind   TEXT NOT NULL CHECK (target_kind IN ('prediction','recommendation')),
        target_id     INTEGER NOT NULL,
        expected      TEXT NOT NULL DEFAULT '',
        actual        TEXT NOT NULL DEFAULT '',
        hit           INTEGER,
        actual_return REAL,
        missing       INTEGER NOT NULL DEFAULT 0,
        verified_at   TEXT NOT NULL,
        UNIQUE (trade_date, target_kind, target_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ver_date ON verification_results (trade_date)",
    """
    CREATE TABLE IF NOT EXISTS accuracy_stats (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        stat_date   TEXT NOT NULL,
        window      TEXT NOT NULL CHECK (window IN ('day','7d','30d')),
        target_kind TEXT NOT NULL CHECK (target_kind IN ('prediction','recommendation')),
        source      TEXT NOT NULL CHECK (source IN ('rule','llm','mixed')),
        hits        INTEGER NOT NULL DEFAULT 0,
        total       INTEGER NOT NULL DEFAULT 0,
        hit_rate    REAL,
        created_at  TEXT NOT NULL,
        UNIQUE (stat_date, window, target_kind, source)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_acc_date ON accuracy_stats (stat_date)",
)

# accuracy_stats 的 upsert 冲突键(供 INSERT ... ON CONFLICT 使用)
_ACC_CONFLICT = "(stat_date, window, target_kind, source)"


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """解析库文件路径: 显式参数 > 环境变量 > 默认 data/prediction.db。"""
    if db_path is not None:
        return Path(db_path)
    env = os.getenv(ENV_DB_PATH, "").strip()
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def _utc_now_iso() -> str:
    """ISO 8601 UTC 时间戳(设计口径: collected_at/created_at/verified_at 均为 UTC)。"""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def beijing_today_str() -> str:
    """北京时间日期字符串 YYYY-MM-DD(Render 为 UTC, 一律显式换算)。"""
    cst = _dt.timezone(_dt.timedelta(hours=8))
    return _dt.datetime.now(cst).strftime("%Y-%m-%d")


class PredictionStore:
    """prediction.db 的读写门面(线程安全: 全部操作经模块级锁串行化)。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = resolve_db_path(db_path)
        self._conn: sqlite3.Connection | None = None

    # ===== 连接管理 =====

    def _connection(self) -> sqlite3.Connection:
        """惰性建连(WAL + busy_timeout); 连接允许跨线程使用(锁内串行)。"""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=10,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._conn = conn
        return self._conn

    def init_db(self) -> bool:
        """建表 + 索引(幂等, 可重复调用; app 启动与 store 首次使用时都会跑)。"""
        try:
            with _LOCK:
                conn = self._connection()
                for ddl in _DDL_STATEMENTS:
                    conn.execute(ddl)
                conn.commit()
            return True
        except Exception as e:  # noqa: BLE001 — 存储层不向调用方抛裸异常
            logger.error(f"prediction_store.init_db failed: {e}")
            return False

    def close(self) -> None:
        with _LOCK:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ===== 时段快照 =====

    def upsert_snapshot(
        self,
        trade_date: str,
        session: str,
        payload: dict[str, Any],
        missing_fields: list[str],
        rule_summary: str,
        llm_summary: str,
        collected_at: str | None = None,
    ) -> dict[str, Any] | None:
        """写入/覆盖一个时段快照(UNIQUE(trade_date, session) 幂等, R1.4 5 分钟幂等的存储侧)。"""
        collected_at = collected_at or _utc_now_iso()
        try:
            with _LOCK:
                conn = self._connection()
                conn.execute(
                    """
                    INSERT INTO session_snapshots
                        (trade_date, session, collected_at, payload_json,
                         missing_fields, rule_summary, llm_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (trade_date, session) DO UPDATE SET
                        collected_at = excluded.collected_at,
                        payload_json = excluded.payload_json,
                        missing_fields = excluded.missing_fields,
                        rule_summary = excluded.rule_summary,
                        llm_summary = excluded.llm_summary
                    """,
                    (
                        trade_date,
                        session,
                        collected_at,
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(missing_fields, ensure_ascii=False),
                        rule_summary,
                        llm_summary,
                    ),
                )
                conn.commit()
            return self.get_snapshot(trade_date, session)
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.upsert_snapshot failed: {e}")
            return None

    def get_snapshot(self, trade_date: str, session: str) -> dict[str, Any] | None:
        try:
            with _LOCK:
                conn = self._connection()
                row = conn.execute(
                    "SELECT * FROM session_snapshots WHERE trade_date = ? AND session = ?",
                    (trade_date, session),
                ).fetchone()
            return self._snapshot_row_to_dict(row) if row else None
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.get_snapshot failed: {e}")
            return None

    def list_snapshots(self, trade_date: str | None = None, days: int = 5) -> list[dict[str, Any]]:
        """近 N 日快照(指定 trade_date 时只返回该日); 按日期倒序。"""
        try:
            with _LOCK:
                conn = self._connection()
                if trade_date:
                    rows = conn.execute(
                        "SELECT * FROM session_snapshots WHERE trade_date = ? ORDER BY session",
                        (trade_date,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM session_snapshots
                        WHERE trade_date >= date('now', ?)
                        ORDER BY trade_date DESC, session
                        """,
                        (f"-{max(int(days), 1) - 1} days",),
                    ).fetchall()
            return [self._snapshot_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_snapshots failed: {e}")
            return []

    @staticmethod
    def _snapshot_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            payload = {}
        try:
            missing = json.loads(row["missing_fields"] or "[]")
        except (ValueError, TypeError):
            missing = []
        return {
            "id": int(row["id"]),
            "trade_date": row["trade_date"],
            "session": row["session"],
            "collected_at": row["collected_at"],
            "payload": payload,
            "missing_fields": missing if isinstance(missing, list) else [],
            "rule_summary": row["rule_summary"],
            "llm_summary": row["llm_summary"],
        }

    # ===== 次日预测 =====

    def upsert_prediction(
        self,
        trade_date: str,
        target_date: str,
        benchmark: str,
        direction: str,
        confidence: float,
        rule_direction: str,
        rule_confidence: float,
        rule_reasons: list[dict[str, Any]],
        llm_direction: str | None,
        llm_confidence: float | None,
        llm_reason: str,
        conflict_flag: int,
        created_at: str | None = None,
    ) -> dict[str, Any] | None:
        """写入/覆盖当日预测(UNIQUE(trade_date, benchmark) 幂等覆盖, R3.3)。"""
        created_at = created_at or _utc_now_iso()
        try:
            with _LOCK:
                conn = self._connection()
                conn.execute(
                    """
                    INSERT INTO predictions
                        (trade_date, target_date, benchmark, direction, confidence,
                         rule_direction, rule_confidence, rule_reasons,
                         llm_direction, llm_confidence, llm_reason,
                         conflict_flag, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (trade_date, benchmark) DO UPDATE SET
                        target_date = excluded.target_date,
                        direction = excluded.direction,
                        confidence = excluded.confidence,
                        rule_direction = excluded.rule_direction,
                        rule_confidence = excluded.rule_confidence,
                        rule_reasons = excluded.rule_reasons,
                        llm_direction = excluded.llm_direction,
                        llm_confidence = excluded.llm_confidence,
                        llm_reason = excluded.llm_reason,
                        conflict_flag = excluded.conflict_flag,
                        created_at = excluded.created_at
                    """,
                    (
                        trade_date,
                        target_date,
                        benchmark,
                        direction,
                        float(confidence),
                        rule_direction,
                        float(rule_confidence),
                        json.dumps(rule_reasons, ensure_ascii=False),
                        llm_direction,
                        llm_confidence,
                        llm_reason,
                        int(conflict_flag),
                        created_at,
                    ),
                )
                conn.commit()
            return self.get_prediction(trade_date, benchmark)
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.upsert_prediction failed: {e}")
            return None

    @staticmethod
    def _prediction_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        try:
            reasons = json.loads(row["rule_reasons"] or "[]")
        except (ValueError, TypeError):
            reasons = []
        return {
            "id": int(row["id"]),
            "trade_date": row["trade_date"],
            "target_date": row["target_date"],
            "benchmark": row["benchmark"],
            "direction": row["direction"],
            "confidence": float(row["confidence"]),
            "rule_direction": row["rule_direction"],
            "rule_confidence": float(row["rule_confidence"]),
            "rule_reasons": reasons if isinstance(reasons, list) else [],
            "llm_direction": row["llm_direction"],
            "llm_confidence": None if row["llm_confidence"] is None else float(row["llm_confidence"]),
            "llm_reason": row["llm_reason"],
            "conflict_flag": int(row["conflict_flag"]),
            "created_at": row["created_at"],
        }

    def get_prediction(self, trade_date: str, benchmark: str = "HS300") -> dict[str, Any] | None:
        try:
            with _LOCK:
                conn = self._connection()
                row = conn.execute(
                    "SELECT * FROM predictions WHERE trade_date = ? AND benchmark = ?",
                    (trade_date, benchmark),
                ).fetchone()
            return self._prediction_row_to_dict(row) if row else None
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.get_prediction failed: {e}")
            return None

    def list_predictions(self, days: int = 30) -> list[dict[str, Any]]:
        try:
            with _LOCK:
                conn = self._connection()
                rows = conn.execute(
                    """
                    SELECT * FROM predictions
                    WHERE trade_date >= date('now', ?)
                    ORDER BY trade_date DESC
                    """,
                    (f"-{max(int(days), 1) - 1} days",),
                ).fetchall()
            return [self._prediction_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_predictions failed: {e}")
            return []

    def list_predictions_for_target(self, target_date: str) -> list[dict[str, Any]]:
        """某目标日(待验证日)的全部预测行(供 verify_daily 取数)。"""
        try:
            with _LOCK:
                conn = self._connection()
                rows = conn.execute(
                    "SELECT * FROM predictions WHERE target_date = ?",
                    (target_date,),
                ).fetchall()
            return [self._prediction_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_predictions_for_target failed: {e}")
            return []

    # ===== 推荐 =====

    def replace_recommendations(
        self, trade_date: str, target_date: str, items: list[dict[str, Any]]
    ) -> int:
        """整日推荐幂等覆盖: 先删当日旧行再整批插入(R4.1 同日重跑口径)。"""
        created_at = _utc_now_iso()
        try:
            with _LOCK:
                conn = self._connection()
                conn.execute("DELETE FROM recommendations WHERE trade_date = ?", (trade_date,))
                for it in items:
                    conn.execute(
                        """
                        INSERT INTO recommendations
                            (trade_date, target_date, symbol, name, kind, direction,
                             confidence, reason, source_signal, realized_return, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trade_date,
                            target_date,
                            str(it.get("symbol", "")),
                            str(it.get("name", "")),
                            str(it.get("kind", "stock")),
                            str(it.get("direction", "up")),
                            float(it.get("confidence", 50.0)),
                            str(it.get("reason", "")),
                            str(it.get("source_signal", "")),
                            it.get("realized_return"),
                            created_at,
                        ),
                    )
                conn.commit()
                cur = conn.execute(
                    "SELECT COUNT(*) AS n FROM recommendations WHERE trade_date = ?",
                    (trade_date,),
                )
                return int(cur.fetchone()["n"])
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.replace_recommendations failed: {e}")
            return 0

    @staticmethod
    def _recommendation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "trade_date": row["trade_date"],
            "target_date": row["target_date"],
            "symbol": row["symbol"],
            "name": row["name"],
            "kind": row["kind"],
            "direction": row["direction"],
            "confidence": float(row["confidence"]),
            "reason": row["reason"],
            "source_signal": row["source_signal"],
            "realized_return": None if row["realized_return"] is None else float(row["realized_return"]),
            "created_at": row["created_at"],
        }

    def list_recommendations(
        self, trade_date: str | None = None, days: int = 30
    ) -> list[dict[str, Any]]:
        try:
            with _LOCK:
                conn = self._connection()
                if trade_date:
                    rows = conn.execute(
                        "SELECT * FROM recommendations WHERE trade_date = ? ORDER BY confidence DESC",
                        (trade_date,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM recommendations
                        WHERE trade_date >= date('now', ?)
                        ORDER BY trade_date DESC, confidence DESC
                        """,
                        (f"-{max(int(days), 1) - 1} days",),
                    ).fetchall()
            return [self._recommendation_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_recommendations failed: {e}")
            return []

    def list_recommendations_for_target(self, target_date: str) -> list[dict[str, Any]]:
        try:
            with _LOCK:
                conn = self._connection()
                rows = conn.execute(
                    "SELECT * FROM recommendations WHERE target_date = ?",
                    (target_date,),
                ).fetchall()
            return [self._recommendation_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_recommendations_for_target failed: {e}")
            return []

    # ===== 验证结果 =====

    def upsert_verification(
        self,
        trade_date: str,
        target_kind: str,
        target_id: int,
        expected: str,
        actual: str,
        hit: int | None,
        actual_return: float | None,
        missing: int = 0,
        verified_at: str | None = None,
    ) -> dict[str, Any] | None:
        """写入/覆盖一条验证(UNIQUE(trade_date,target_kind,target_id) 幂等, 16:30 重试覆盖)。"""
        verified_at = verified_at or _utc_now_iso()
        try:
            with _LOCK:
                conn = self._connection()
                conn.execute(
                    """
                    INSERT INTO verification_results
                        (trade_date, target_kind, target_id, expected, actual,
                         hit, actual_return, missing, verified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (trade_date, target_kind, target_id) DO UPDATE SET
                        expected = excluded.expected,
                        actual = excluded.actual,
                        hit = excluded.hit,
                        actual_return = excluded.actual_return,
                        missing = excluded.missing,
                        verified_at = excluded.verified_at
                    """,
                    (
                        trade_date,
                        target_kind,
                        int(target_id),
                        expected,
                        actual,
                        hit,
                        actual_return,
                        int(missing),
                        verified_at,
                    ),
                )
                conn.commit()
            return self.get_verification(trade_date, target_kind, target_id)
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.upsert_verification failed: {e}")
            return None

    @staticmethod
    def _verification_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "trade_date": row["trade_date"],
            "target_kind": row["target_kind"],
            "target_id": int(row["target_id"]),
            "expected": row["expected"],
            "actual": row["actual"],
            "hit": None if row["hit"] is None else int(row["hit"]),
            "actual_return": None if row["actual_return"] is None else float(row["actual_return"]),
            "missing": int(row["missing"]),
            "verified_at": row["verified_at"],
        }

    def get_verification(
        self, trade_date: str, target_kind: str, target_id: int
    ) -> dict[str, Any] | None:
        try:
            with _LOCK:
                conn = self._connection()
                row = conn.execute(
                    """
                    SELECT * FROM verification_results
                    WHERE trade_date = ? AND target_kind = ? AND target_id = ?
                    """,
                    (trade_date, target_kind, int(target_id)),
                ).fetchone()
            return self._verification_row_to_dict(row) if row else None
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.get_verification failed: {e}")
            return None

    def list_verifications(self, days: int = 30) -> list[dict[str, Any]]:
        try:
            with _LOCK:
                conn = self._connection()
                rows = conn.execute(
                    """
                    SELECT * FROM verification_results
                    WHERE trade_date >= date('now', ?)
                    ORDER BY trade_date DESC, target_kind, target_id
                    """,
                    (f"-{max(int(days), 1) - 1} days",),
                ).fetchall()
            return [self._verification_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_verifications failed: {e}")
            return []

    def list_verifications_between(
        self, start_date: str, end_date: str, include_missing: bool = False
    ) -> list[dict[str, Any]]:
        """闭区间验证行(准确率统计双通道的取数口径, 排除 missing=1 占位)。"""
        try:
            with _LOCK:
                conn = self._connection()
                sql = (
                    "SELECT * FROM verification_results "
                    "WHERE trade_date >= ? AND trade_date <= ?"
                )
                params: list[Any] = [start_date, end_date]
                if not include_missing:
                    sql += " AND missing = 0"
                sql += " ORDER BY trade_date, target_kind, target_id"
                rows = conn.execute(sql, params).fetchall()
            return [self._verification_row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_verifications_between failed: {e}")
            return []

    # ===== 准确率统计 =====

    def upsert_accuracy_stats(self, rows: list[dict[str, Any]], stat_date: str | None = None) -> int:
        """整批 upsert accuracy_stats(UNIQUE(stat_date,window,kind,source) 幂等)。"""
        stat_date = stat_date or beijing_today_str()
        created_at = _utc_now_iso()
        written = 0
        try:
            with _LOCK:
                conn = self._connection()
                for r in rows:
                    conn.execute(
                        f"""
                        INSERT INTO accuracy_stats
                            (stat_date, window, target_kind, source, hits, total, hit_rate, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT {_ACC_CONFLICT} DO UPDATE SET
                            hits = excluded.hits,
                            total = excluded.total,
                            hit_rate = excluded.hit_rate,
                            created_at = excluded.created_at
                        """,
                        (
                            stat_date,
                            str(r.get("window", "day")),
                            str(r.get("target_kind", "prediction")),
                            str(r.get("source", "rule")),
                            int(r.get("hits", 0)),
                            int(r.get("total", 0)),
                            r.get("hit_rate"),
                            created_at,
                        ),
                    )
                    written += 1
                conn.commit()
            return written
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.upsert_accuracy_stats failed: {e}")
            return written

    def list_accuracy_stats(self, stat_date: str | None = None) -> list[dict[str, Any]]:
        try:
            with _LOCK:
                conn = self._connection()
                if stat_date:
                    rows = conn.execute(
                        "SELECT * FROM accuracy_stats WHERE stat_date = ?",
                        (stat_date,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM accuracy_stats ORDER BY stat_date DESC"
                    ).fetchall()
            return [
                {
                    "id": int(r["id"]),
                    "stat_date": r["stat_date"],
                    "window": r["window"],
                    "target_kind": r["target_kind"],
                    "source": r["source"],
                    "hits": int(r["hits"]),
                    "total": int(r["total"]),
                    "hit_rate": None if r["hit_rate"] is None else float(r["hit_rate"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.list_accuracy_stats failed: {e}")
            return []

    # ===== 保留策略(R5.2) =====

    def cleanup(
        self,
        detail_days: int = DETAIL_RETENTION_DAYS,
        stats_days: int = STATS_RETENTION_DAYS,
        today: str | None = None,
    ) -> dict[str, int]:
        """删除超期明细(180 天)与超期统计(365 天)。

        SQL 统一带 ``trade_date < cutoff`` 条件, 今日与未来数据绝不误删;
        accuracy_stats 按 stat_date 清理。
        """
        today = today or beijing_today_str()
        detail_cutoff = (
            _dt.datetime.strptime(today, "%Y-%m-%d") - _dt.timedelta(days=detail_days)
        ).strftime("%Y-%m-%d")
        stats_cutoff = (
            _dt.datetime.strptime(today, "%Y-%m-%d") - _dt.timedelta(days=stats_days)
        ).strftime("%Y-%m-%d")
        deleted: dict[str, int] = {}
        try:
            with _LOCK:
                conn = self._connection()
                for table, col, cutoff in (
                    ("session_snapshots", "trade_date", detail_cutoff),
                    ("predictions", "trade_date", detail_cutoff),
                    ("recommendations", "trade_date", detail_cutoff),
                    ("verification_results", "trade_date", detail_cutoff),
                    ("accuracy_stats", "stat_date", stats_cutoff),
                ):
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE {col} < ?",  # noqa: S608 — 表名为内部常量
                        (cutoff,),
                    )
                    deleted[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                conn.commit()
            return deleted
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.cleanup failed: {e}")
            return deleted

    # ===== 导出(主理人补充: Render 免费层磁盘非持久 → Action 每日备份快照) =====

    def export_bytes(self) -> bytes | None:
        """用 SQLite backup API 导出一致性快照字节流(WAL 下也安全)。"""
        try:
            with _LOCK:
                src = self._connection()
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
                    tmp_path = tf.name
                try:
                    dst = sqlite3.connect(tmp_path)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                    return Path(tmp_path).read_bytes()
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        except Exception as e:  # noqa: BLE001
            logger.error(f"prediction_store.export_bytes failed: {e}")
            return None


# ===== 模块级单例 =====

_store: PredictionStore | None = None
_store_lock = threading.Lock()


def get_store(db_path: str | Path | None = None) -> PredictionStore:
    """返回进程级共享 store(首次调用时建表)。"""
    global _store
    with _store_lock:
        if _store is None or db_path is not None:
            _store = PredictionStore(db_path)
            _store.init_db()
        return _store

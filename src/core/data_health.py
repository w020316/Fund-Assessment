"""数据源健康度状态(借鉴 zvt 的 recorder 运行状态模型)。

背景:数据源失败此前只写 loguru 日志,页面/运维都看不到"哪一路数据断了、
断了多久",roadmap #11 因此长期不可见。

状态只存在进程内存:
- 行情类接口最快每 5s 抓一次,逐次原子写盘对 Render 512MB 小实例没有收益;
- 进程重启后回到 unknown 是诚实语义,比读到"重启前最后一次成功"更不容易
  误导用户以为数据还是新的。

线程安全:``_lock`` 保护全部读写;``data_source_v2`` 的线程池会并发上报。
状态推导见 :func:`snapshot`。
"""
from __future__ import annotations

import threading
import time
from typing import Any

#: 数据源中文名(接口/前端展示用);未登记的 name 也允许上报,label 回落为 name
SOURCE_LABELS: dict[str, str] = {
    "index_realtime": "指数实时行情",
    "sector_ranking": "板块资金排行",
    "global_news": "全球财经快讯",
    "fund_realtime": "基金实时估值",
    "fund_history": "基金历史净值",
}

#: 连续失败达到该次数即判定 down(前 1~2 次视为瞬时抖动 → degraded)
DOWN_THRESHOLD: int = 3

_MAX_ERROR_LEN = 160
_MAX_SOURCES = 50

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}


def _now_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _entry(name: str) -> dict[str, Any]:
    entry = _state.get(name)
    if entry is None:
        if len(_state) >= _MAX_SOURCES:
            return {}
        entry = {
            "last_success_ts": None,
            "last_attempt_ts": None,
            "last_ok": None,
            "last_error": "",
            "last_count": 0,
            "consecutive_failures": 0,
            "total_success": 0,
            "total_failure": 0,
        }
        _state[name] = entry
    return entry


def record_success(name: str, count: int = 0) -> None:
    """记录一次成功抓取(count 为本次返回的数据条数)。"""
    now = time.time()
    with _lock:
        entry = _entry(name)
        if not entry:
            return
        entry["last_success_ts"] = now
        entry["last_attempt_ts"] = now
        entry["last_ok"] = True
        entry["last_error"] = ""
        entry["last_count"] = int(count) if isinstance(count, int) and count > 0 else 0
        entry["consecutive_failures"] = 0
        entry["total_success"] += 1


def record_failure(name: str, error: str = "") -> None:
    """记录一次失败抓取(error 截断保存,不落任何凭据)。"""
    now = time.time()
    msg = str(error or "")[:_MAX_ERROR_LEN]
    with _lock:
        entry = _entry(name)
        if not entry:
            return
        entry["last_attempt_ts"] = now
        entry["last_ok"] = False
        entry["last_error"] = msg
        entry["last_count"] = 0
        entry["consecutive_failures"] += 1
        entry["total_failure"] += 1


def _status(entry: dict[str, Any]) -> str:
    if entry["last_attempt_ts"] is None:
        return "unknown"
    if entry["last_ok"]:
        return "ok"
    if entry["last_success_ts"] is None:
        return "down"
    return "down" if entry["consecutive_failures"] >= DOWN_THRESHOLD else "degraded"


def snapshot() -> dict[str, Any]:
    """导出全部数据源状态(按登记顺序优先,未登记源按名称排序追加)。"""
    now = time.time()
    with _lock:
        names = [n for n in SOURCE_LABELS if n in _state]
        names += sorted(n for n in _state if n not in SOURCE_LABELS)
        sources: list[dict[str, Any]] = []
        for name in names:
            entry = _state[name]
            last_ok_ts = entry["last_success_ts"]
            sources.append({
                "name": name,
                "label": SOURCE_LABELS.get(name, name),
                "status": _status(entry),
                "last_success_at": _now_iso(last_ok_ts),
                "last_success_age_s": (
                    int(now - last_ok_ts) if last_ok_ts is not None else None
                ),
                "last_attempt_at": _now_iso(entry["last_attempt_ts"]),
                "last_error": entry["last_error"],
                "last_count": entry["last_count"],
                "consecutive_failures": entry["consecutive_failures"],
                "total_success": entry["total_success"],
                "total_failure": entry["total_failure"],
            })
    statuses = {s["status"] for s in sources}
    if not sources or statuses == {"unknown"}:
        overall = "unknown"
    elif "down" in statuses:
        overall = "down"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "ok"
    return {
        "generated_at": _now_iso(now),
        "overall": overall,
        "sources": sources,
    }


def reset() -> None:
    """清空状态(测试与手工排障用)。"""
    with _lock:
        _state.clear()

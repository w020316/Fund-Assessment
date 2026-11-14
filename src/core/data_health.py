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
    "ddx_em": "资金连增·东财直连",
    "ddx_akshare": "资金连增·akshare",
    "ddx_sina": "资金连增·新浪快照",
    "stock_ranking_sina": "股票排行·新浪兜底",
    "hot_stocks_ths": "涨停池·同花顺",
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


def _blank_source(name: str) -> dict[str, Any]:
    """已登记但本进程从未上报过的源(重启后/未使用时的诚实状态)。"""
    return {
        "name": name,
        "label": SOURCE_LABELS.get(name, name),
        "status": "unknown",
        "last_success_at": None,
        "last_success_age_s": None,
        "last_attempt_at": None,
        "last_error": "",
        "last_count": 0,
        "consecutive_failures": 0,
        "total_success": 0,
        "total_failure": 0,
    }


def snapshot() -> dict[str, Any]:
    """导出全部数据源状态。

    2026-11-07 修复监控盲区:此前只输出 ``_state`` 里已有的源,而 ``_state`` 是在
    源第一次被调用时才创建的。后果是——进程重启后,凡是本进程还没被调用过的源
    (最典型的是 ``fund_realtime`` / ``fund_history``:没持仓就不会有人查基金)
    **完全不出现在健康页**,``overall`` 仍报 ``ok``。即"基金数据源挂了也看不出来"。
    现在改为:先按 ``SOURCE_LABELS`` 登记顺序铺满全部已知源,未上报的标记
    ``unknown``(前端已渲染为"未探测/本进程尚未抓取"),再追加运行期新出现的源。
    ``unknown`` 不计入故障判定,因此不影响 ``overall``;另返回 ``unverified``
    列出这些源的名称,便于运维区分"没验过"与"验过且正常"。
    """
    now = time.time()
    with _lock:
        names = list(SOURCE_LABELS.keys())
        names += sorted(n for n in _state if n not in SOURCE_LABELS)
        sources: list[dict[str, Any]] = []
        for name in names:
            entry = _state.get(name)
            if entry is None:
                sources.append(_blank_source(name))
                continue
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
        "unverified": [s["name"] for s in sources if s["status"] == "unknown"],
        "sources": sources,
    }


def reset() -> None:
    """清空状态(测试与手工排障用)。"""
    with _lock:
        _state.clear()

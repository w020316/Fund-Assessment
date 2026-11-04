"""交易日志与纪律归因

补齐短线复盘链路中“交易日志 / 自我体检 / 纪律归因”环节:
- 记录每一次买入、卖出、放弃、观察,以及当时的三问校验结果
- 用记录反推纪律执行情况:出手是否先过三问、有没有未校验就下单、是否过度交易
- 数据落盘为 JSONL(追加写),不覆盖历史;损坏行自动跳过

设计原则:
- 只记录用户主动提交的行为,不推断、不补造
- 统计口径可解释,每个扣分项都能对应到具体记录
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from loguru import logger

_ALLOWED_ACTIONS = {"buy", "sell", "abandon", "watch"}
_ALLOWED_EMOTIONS = {"", "冷静", "犹豫", "冲动", "恐惧", "贪婪"}
_CODE_RE = re.compile(r"^\d{6}$")
_LOCK = threading.Lock()

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trade_journal.jsonl"


def _journal_path(path: Path | str | None = None) -> Path:
    """日志文件路径:显式参数 > 环境变量 > 默认 data/trade_journal.jsonl"""
    if path:
        return Path(path)
    env_path = os.getenv("TRADE_JOURNAL_PATH")
    return Path(env_path) if env_path else _DEFAULT_PATH


def _safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError("价格/数量必须是数字")
    if not math.isfinite(num) or num < 0:
        raise ValueError("价格/数量不能为负数")
    return num


def validate_entry(payload: dict) -> dict:
    """校验并归一化一条日志,非法输入抛 ValueError"""
    if not isinstance(payload, dict):
        raise ValueError("日志内容必须是对象")

    action = str(payload.get("action", "")).strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"action 必须是 {'/'.join(sorted(_ALLOWED_ACTIONS))}")

    stock_code = str(payload.get("stock_code", "")).strip()
    if not _CODE_RE.match(stock_code):
        raise ValueError("stock_code 必须是 6 位数字")

    trade_date = str(payload.get("trade_date", "")).strip() or date.today().strftime("%Y-%m-%d")
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError("trade_date 格式必须是 YYYY-MM-DD") from e

    note = str(payload.get("note", "")).strip()
    if len(note) > 300:
        raise ValueError("note 最多 300 字")

    emotion = str(payload.get("emotion", "")).strip()
    if emotion not in _ALLOWED_EMOTIONS:
        raise ValueError(f"emotion 必须是 {'/'.join(sorted(x for x in _ALLOWED_EMOTIONS if x))} 之一或留空")

    gate_passed = payload.get("gate_passed")
    if gate_passed is not None and not isinstance(gate_passed, bool):
        raise ValueError("gate_passed 必须是布尔值或空")

    gate_mode = str(payload.get("gate_mode", "")).strip()
    if gate_mode not in ("", "auto", "manual"):
        raise ValueError("gate_mode 必须是 auto/manual 或留空")

    gate = None
    if gate_passed is not None or gate_mode:
        gate = {
            "passed": gate_passed,
            "mode": gate_mode or "manual",
            "missing": list(payload.get("gate_missing") or []),
            "failed": list(payload.get("gate_failed") or []),
        }

    return {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": trade_date,
        "action": action,
        "stock_code": stock_code,
        "stock_name": str(payload.get("stock_name", "")).strip()[:40],
        "price": _safe_float(payload.get("price")),
        "quantity": _safe_float(payload.get("quantity")),
        "gate": gate,
        "note": note,
        "emotion": emotion,
    }


def append_entry(payload: dict, path: Path | str | None = None) -> dict:
    """追加一条日志并返回归一化后的记录"""
    entry = validate_entry(payload)
    target = _journal_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False)
    with _LOCK:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return entry


def load_entries(path: Path | str | None = None, limit: int = 200) -> list[dict]:
    """读取日志(新→旧),损坏行跳过"""
    target = _journal_path(path)
    if not target.exists():
        return []
    entries: list[dict] = []
    try:
        with target.open("r", encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("trade_journal: 跳过一条损坏记录")
                    continue
                if isinstance(item, dict) and item.get("stock_code"):
                    entries.append(item)
    except OSError as e:
        logger.warning(f"trade_journal: 读取失败 {e}")
        return []

    # 文件是追加写,直接反转为新→旧;同一秒内多条记录也能保持先后顺序
    entries.reverse()
    return entries[: max(1, min(1000, int(limit or 200)))]


def compute_stats(entries: list[dict]) -> dict:
    """纪律归因统计"""
    total = len(entries)
    by_action = {action: 0 for action in sorted(_ALLOWED_ACTIONS)}
    for item in entries:
        action = item.get("action", "")
        if action in by_action:
            by_action[action] += 1

    buys = [x for x in entries if x.get("action") == "buy"]
    sells = [x for x in entries if x.get("action") == "sell"]
    abandoned = by_action.get("abandon", 0)

    checked = [x for x in entries if isinstance(x.get("gate"), dict) and x["gate"].get("passed") is not None]
    gate_pass = [x for x in checked if x["gate"].get("passed") is True]
    gate_pass_rate = round(len(gate_pass) / len(checked), 4) if checked else None

    buys_with_gate_pass = [x for x in buys if isinstance(x.get("gate"), dict) and x["gate"].get("passed") is True]
    buys_with_gate_fail = [x for x in buys if isinstance(x.get("gate"), dict) and x["gate"].get("passed") is False]
    buys_without_gate = [x for x in buys if not isinstance(x.get("gate"), dict)]
    compliance_rate = round(len(buys_with_gate_pass) / len(buys), 4) if buys else None

    trade_actions = buys + sells
    checked_trades = [
        x for x in checked
        if x.get("action") in ("buy", "sell")
    ]
    check_coverage = round(len(checked_trades) / len(trade_actions), 4) if trade_actions else None

    # 每日买入次数:识别过度交易
    buys_per_day: dict[str, int] = {}
    for item in buys:
        day = str(item.get("trade_date", ""))
        buys_per_day[day] = buys_per_day.get(day, 0) + 1
    max_buys_per_day = max(buys_per_day.values(), default=0)
    overtrade_days = sorted(day for day, count in buys_per_day.items() if count > 3)

    # 同一标的反复买入:识别重复犯错
    buys_per_stock: dict[str, int] = {}
    for item in buys:
        code = str(item.get("stock_code", ""))
        buys_per_stock[code] = buys_per_stock.get(code, 0) + 1
    repeated = sorted(
        ({"stock_code": code, "buy_count": count} for code, count in buys_per_stock.items() if count >= 3),
        key=lambda x: x["buy_count"],
        reverse=True,
    )[:5]

    issues: list[str] = []
    if buys_with_gate_fail:
        issues.append(f"{len(buys_with_gate_fail)} 笔买入发生在三问不通过时")
    if buys_without_gate:
        issues.append(f"{len(buys_without_gate)} 笔买入没有三问校验记录")
    if overtrade_days:
        issues.append(f"{len(overtrade_days)} 个交易日买入超过 3 笔,注意过度交易")
    if repeated:
        issues.append("存在同一标的反复买入(≥3 次),复盘是否重复犯错")

    # 纪律评分:只在样本足够时给出
    sample_sufficient = total >= 3
    score: float | None = None
    grade = "样本不足"
    if sample_sufficient:
        score = 100.0
        if buys:
            score -= 50.0 * len(buys_with_gate_fail) / len(buys)
            score -= 30.0 * len(buys_without_gate) / len(buys)
        if max_buys_per_day > 3:
            score -= 10.0
        score = round(max(0.0, min(100.0, score)), 1)
        if score >= 90:
            grade = "优秀"
        elif score >= 75:
            grade = "良好"
        elif score >= 60:
            grade = "待改进"
        else:
            grade = "风险"

    recent_dates = sorted({str(x.get("trade_date", "")) for x in entries if x.get("trade_date")}, reverse=True)[:10]
    daily = []
    for day in sorted(recent_dates):
        day_items = [x for x in entries if x.get("trade_date") == day]
        daily.append({
            "date": day,
            "entries": len(day_items),
            "buys": sum(1 for x in day_items if x.get("action") == "buy"),
            "abandons": sum(1 for x in day_items if x.get("action") == "abandon"),
        })

    return {
        "total": total,
        "by_action": by_action,
        "gate_checked": len(checked),
        "gate_pass_rate": gate_pass_rate,
        "compliance_rate": compliance_rate,
        "check_coverage": check_coverage,
        "abandon_count": abandoned,
        "buy_count": len(buys),
        "sell_count": len(sells),
        "buys_with_gate_fail": len(buys_with_gate_fail),
        "buys_without_gate": len(buys_without_gate),
        "max_buys_per_day": max_buys_per_day,
        "overtrade_days": overtrade_days,
        "repeated_stocks": repeated,
        "score": score,
        "grade": grade,
        "issues": issues,
        "daily": daily,
        "sample_sufficient": sample_sufficient,
    }


def journal_report(path: Path | str | None = None, limit: int = 200) -> dict:
    """一次性返回日志与统计,供前端渲染"""
    entries = load_entries(path=path, limit=limit)
    return {
        "entries": entries,
        "stats": compute_stats(entries),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

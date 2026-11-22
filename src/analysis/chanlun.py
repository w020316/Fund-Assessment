"""简化缠论结构分析：分型 / 笔 / 中枢 / MACD 面积背驰。

范围声明（必须如实展示给用户）：
- 已实现：K 线包含关系处理 → 顶底分型 → 笔 → 中枢 → MACD 面积背驰 → 一/三类买卖点观察；
- 未实现：线段、级别递归、走势类型分解、区间套；
- 输出只描述结构，不预测收益、不构成买卖建议；数据不足时返回 None / 空列表，不补 0。
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.analysis.technical import macd

MIN_BARS = 40
MIN_STROKE_GAP = 4  # 两个分型之间至少间隔的合并 K 线数（简化口径）


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _clean_bars(rows: Any) -> list[dict[str, Any]]:
    """把上游 K 线清洗成统一结构；任何缺失/非法值直接丢弃该根。"""
    if not isinstance(rows, list):
        return []
    bars: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        o, h, low, c = (_num(r.get("open")), _num(r.get("high")),
                        _num(r.get("low")), _num(r.get("close")))
        if None in (o, h, low, c) or h < low or h <= 0 or low <= 0:
            continue
        bars.append({
            "date": str(r.get("date") or ""),
            "open": o, "high": h, "low": low, "close": c,
        })
    return bars


def _merge_inclusion(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """处理包含关系：向上取高高、向下取低低（简化标准处理）。"""
    merged: list[dict[str, Any]] = []
    direction = 1
    for i, b in enumerate(bars):
        if not merged:
            merged.append({
                "high": b["high"], "low": b["low"],
                "high_date": b["date"], "low_date": b["date"],
                "start_date": b["date"], "end_date": b["date"],
                "end_orig": i,
            })
            continue
        last = merged[-1]
        contained = (
            (b["high"] <= last["high"] and b["low"] >= last["low"])
            or (b["high"] >= last["high"] and b["low"] <= last["low"])
        )
        if contained:
            if direction >= 0:
                if b["high"] > last["high"]:
                    last["high"] = b["high"]
                    last["high_date"] = b["date"]
                if b["low"] > last["low"]:
                    last["low"] = b["low"]
                    last["low_date"] = b["date"]
            else:
                if b["high"] < last["high"]:
                    last["high"] = b["high"]
                    last["high_date"] = b["date"]
                if b["low"] < last["low"]:
                    last["low"] = b["low"]
                    last["low_date"] = b["date"]
            last["end_date"] = b["date"]
            last["end_orig"] = i
        else:
            direction = 1 if b["high"] > last["high"] else -1
            merged.append({
                "high": b["high"], "low": b["low"],
                "high_date": b["date"], "low_date": b["date"],
                "start_date": b["date"], "end_date": b["date"],
                "end_orig": i,
            })
    return merged


def _find_fractals(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """顶分型：中间高点最高且低点最高；底分型相反。"""
    out: list[dict[str, Any]] = []
    for i in range(1, len(merged) - 1):
        prev, cur, nxt = merged[i - 1], merged[i], merged[i + 1]
        if cur["high"] > prev["high"] and cur["high"] > nxt["high"] \
                and cur["low"] > prev["low"] and cur["low"] > nxt["low"]:
            out.append({"idx": i, "type": "top", "price": cur["high"],
                        "date": cur["high_date"]})
        elif cur["low"] < prev["low"] and cur["low"] < nxt["low"] \
                and cur["high"] < prev["high"] and cur["high"] < nxt["high"]:
            out.append({"idx": i, "type": "bottom", "price": cur["low"],
                        "date": cur["low_date"]})
    return out


def _build_strokes(fractals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """相邻异型分型连成笔；同型分型保留更极端的一个，间隔不足则忽略。"""
    picked: list[dict[str, Any]] = []
    for f in fractals:
        if not picked:
            picked.append(dict(f))
            continue
        last = picked[-1]
        if f["type"] == last["type"]:
            more_extreme = (f["price"] > last["price"]) if f["type"] == "top" \
                else (f["price"] < last["price"])
            if more_extreme:
                picked[-1] = dict(f)
            continue
        if f["idx"] - last["idx"] < MIN_STROKE_GAP:
            continue
        picked.append(dict(f))

    strokes: list[dict[str, Any]] = []
    for a, b in zip(picked, picked[1:]):
        strokes.append({
            "direction": "up" if b["price"] > a["price"] else "down",
            "start_date": a["date"], "end_date": b["date"],
            "start_price": a["price"], "end_price": b["price"],
            "start_type": a["type"], "end_type": b["type"],
        })
    return strokes


def _build_pivots(strokes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """三笔重叠构成中枢；后续笔仍与区间重叠则延伸。"""
    pivots: list[dict[str, Any]] = []
    i = 0
    while i + 2 < len(strokes):
        trio = strokes[i:i + 3]
        highs = [max(s["start_price"], s["end_price"]) for s in trio]
        lows = [min(s["start_price"], s["end_price"]) for s in trio]
        zg, zd = min(highs), max(lows)
        if zg <= zd:
            i += 1
            continue
        pivot = {
            "zg": round(zg, 3), "zd": round(zd, 3),
            "gg": round(max(highs), 3), "dd": round(min(lows), 3),
            "start_date": trio[0]["start_date"], "end_date": trio[-1]["end_date"],
            "strokes": 3, "direction": trio[0]["direction"],
        }
        j = i + 3
        while j < len(strokes):
            s = strokes[j]
            sh = max(s["start_price"], s["end_price"])
            sl = min(s["start_price"], s["end_price"])
            if sh < zd or sl > zg:
                break
            pivot["end_date"] = s["end_date"]
            pivot["strokes"] += 1
            j += 1
        pivots.append(pivot)
        i = j
    return pivots[-3:]


def _macd_area(macd_df: pd.DataFrame, date_to_idx: dict[str, int],
               start_date: str, end_date: str) -> float | None:
    a, b = date_to_idx.get(start_date), date_to_idx.get(end_date)
    if a is None or b is None or b <= a or "MACD_hist" not in macd_df:
        return None
    seg = macd_df["MACD_hist"].iloc[a:b + 1].dropna()
    if seg.empty:
        return None
    return float(seg.abs().sum())


def _detect_divergence(bars: list[dict[str, Any]], strokes: list[dict[str, Any]],
                       macd_df: pd.DataFrame) -> dict[str, Any] | None:
    """同向相邻两笔比较：价格新高/新低但 MACD 面积缩小 → 背驰。"""
    if len(strokes) < 4:
        return None
    date_to_idx = {b["date"]: i for i, b in enumerate(bars)}
    same = [s for s in strokes if s["direction"] == strokes[-1]["direction"]]
    if len(same) < 2:
        return None
    prev, cur = same[-2], same[-1]
    a1 = _macd_area(macd_df, date_to_idx, prev["start_date"], prev["end_date"])
    a2 = _macd_area(macd_df, date_to_idx, cur["start_date"], cur["end_date"])
    if a1 is None or a2 is None or a1 <= 0 or a2 >= a1 * 0.95:
        return None
    if cur["direction"] == "up" and cur["end_price"] > prev["end_price"]:
        return {"type": "top", "text": "顶背驰：价格新高但 MACD 面积缩小",
                "date": cur["end_date"], "price": round(cur["end_price"], 3)}
    if cur["direction"] == "down" and cur["end_price"] < prev["end_price"]:
        return {"type": "bottom", "text": "底背驰：价格新低但 MACD 面积缩小",
                "date": cur["end_date"], "price": round(cur["end_price"], 3)}
    return None


def _signals(bars: list[dict[str, Any]], strokes: list[dict[str, Any]],
             pivots: list[dict[str, Any]], divergence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """只输出可解释的观察点，不输出“必买/必卖”。"""
    out: list[dict[str, Any]] = []
    close = bars[-1]["close"]
    if divergence:
        label = "一买观察" if divergence["type"] == "bottom" else "一卖观察"
        out.append({"type": divergence["type"], "label": label,
                    "date": divergence["date"], "price": divergence["price"],
                    "note": divergence["text"] + "（需后续笔确认）"})
    if pivots and strokes:
        p = pivots[-1]
        if close > p["zg"]:
            out.append({"type": "above", "label": "三买观察",
                        "date": bars[-1]["date"], "price": round(close, 3),
                        "note": "价格位于最近中枢上沿 %.2f 之上，回踩不破上沿可继续观察" % p["zg"]})
        elif close < p["zd"]:
            out.append({"type": "below", "label": "三卖观察",
                        "date": bars[-1]["date"], "price": round(close, 3),
                        "note": "价格位于最近中枢下沿 %.2f 之下，反抽不上沿需谨慎" % p["zd"]})
    return out[-3:]


def analyze_chanlun(rows: Any, code: str = "", period: str = "daily") -> dict[str, Any]:
    """主入口：输入 K 线列表，输出简化缠论结构。"""
    bars = _clean_bars(rows)
    base = {
        "code": str(code or ""),
        "period": str(period or "daily"),
        "note": "简化缠论：含分型/笔/中枢/MACD 面积背驰，未实现线段与级别递归；仅供结构参考，不构成买卖建议。",
        "bars": bars[-160:],
        "fractals": [],
        "strokes": [],
        "pivots": [],
        "divergence": None,
        "signals": [],
        "summary": None,
        "available": False,
    }
    if len(bars) < MIN_BARS:
        base["reason"] = f"K 线不足 {MIN_BARS} 根，无法做结构分析"
        return base

    merged = _merge_inclusion(bars)
    fractals = _find_fractals(merged)
    strokes = _build_strokes(fractals)
    pivots = _build_pivots(strokes)

    closes = pd.Series([b["close"] for b in bars])
    macd_df = macd(closes)
    divergence = _detect_divergence(bars, strokes, macd_df)
    signals = _signals(bars, strokes, pivots, divergence)

    summary: dict[str, Any] = {"close": round(bars[-1]["close"], 3), "date": bars[-1]["date"]}
    if strokes:
        s = strokes[-1]
        summary["last_stroke"] = {
            "direction": s["direction"],
            "start_date": s["start_date"], "end_date": s["end_date"],
            "start_price": round(s["start_price"], 3),
            "end_price": round(s["end_price"], 3),
        }
    if pivots:
        p = pivots[-1]
        close = bars[-1]["close"]
        pos = "中枢上方" if close > p["zg"] else ("中枢下方" if close < p["zd"] else "中枢内")
        summary["last_pivot"] = {
            "zg": p["zg"], "zd": p["zd"], "gg": p["gg"], "dd": p["dd"],
            "start_date": p["start_date"], "end_date": p["end_date"],
            "position": pos,
        }
    summary["divergence"] = divergence["text"] if divergence else "未识别到同向笔背驰"

    base.update({
        "available": True,
        "merged_count": len(merged),
        "fractals": fractals,
        "strokes": strokes,
        "pivots": pivots,
        "divergence": divergence,
        "signals": signals,
        "summary": summary,
    })
    return base

"""个股多信号扫描（纯规则引擎，不含 LLM、不做收益预测）。

口径：
- 每个信号都有明确触发条件与证据文本；strength 是规则权重，不是上涨概率；
- 数据不足或指标 NaN 时跳过该信号，不补 0；
- 多空强度 = 触发信号权重之和，只用于横向比较，不构成买卖建议。
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.analysis.technical import compute_indicators

MIN_BARS = 60
MAX_BARS = 500


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _clean(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        o, h, low, c = (_num(r.get("open")), _num(r.get("high")),
                        _num(r.get("low")), _num(r.get("close")))
        # S9-14: 缺失的成交量保持 None(经 DataFrame 变 NaN)——补 0 会把
        # VOL_MA20 归零, 量能类信号被静默判成"不满足"而非"不可判"。
        vol = _num(r.get("volume"))
        if None in (o, h, low, c) or h < low or h <= 0 or low <= 0:
            continue
        out.append({
            "date": str(r.get("date") or ""),
            "open": o, "high": h, "low": low, "close": c, "volume": vol,
        })
    return out


def _val(row: pd.Series | None, key: str) -> float | None:
    if row is None or key not in row.index:
        return None
    return _num(row.get(key))


def _fmt(v: float | None) -> str:
    return '—' if v is None else f"{v:.2f}"


def _add(out: list[dict[str, Any]], key: str, name: str, direction: str,
         strength: int, evidence: str) -> None:
    out.append({
        "key": key, "name": name, "direction": direction,
        "strength": int(strength), "evidence": evidence,
    })


def _scan(df: pd.DataFrame) -> list[dict[str, Any]]:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    out: list[dict[str, Any]] = []

    close = _val(last, "close")
    ma5, ma10, ma20, ma60 = (_val(last, "MA5"), _val(last, "MA10"),
                             _val(last, "MA20"), _val(last, "MA60"))
    p_ma5, p_ma10 = _val(prev, "MA5"), _val(prev, "MA10")
    p_close = _val(prev, "close")

    if None not in (ma5, ma10, p_ma5, p_ma10):
        if p_ma5 <= p_ma10 and ma5 > ma10:
            _add(out, "ma_golden", "MA5 上穿 MA10", "bull", 70,
                 f"MA5 {_fmt(ma5)} 上穿 MA10 {_fmt(ma10)}")
        if p_ma5 >= p_ma10 and ma5 < ma10:
            _add(out, "ma_dead", "MA5 下穿 MA10", "bear", 70,
                 f"MA5 {_fmt(ma5)} 下穿 MA10 {_fmt(ma10)}")
    if None not in (ma5, ma10, ma20, ma60):
        if ma5 > ma10 > ma20 > ma60:
            _add(out, "ma_bull_align", "均线多头排列", "bull", 65,
                 f"MA5 {_fmt(ma5)} > MA10 {_fmt(ma10)} > MA20 {_fmt(ma20)} > MA60 {_fmt(ma60)}")
        if ma5 < ma10 < ma20 < ma60:
            _add(out, "ma_bear_align", "均线空头排列", "bear", 65,
                 f"MA5 {_fmt(ma5)} < MA10 {_fmt(ma10)} < MA20 {_fmt(ma20)} < MA60 {_fmt(ma60)}")

    if close is not None and ma20 is not None and p_close is not None:
        p_ma20 = _val(prev, "MA20")
        if p_ma20 is not None:
            if p_close <= p_ma20 and close > ma20:
                _add(out, "price_cross_ma20", "收盘上穿 MA20", "bull", 55,
                     f"收盘 {_fmt(close)} 上穿 MA20 {_fmt(ma20)}")
            if p_close >= p_ma20 and close < ma20:
                _add(out, "price_cross_ma20_down", "收盘下穿 MA20", "bear", 55,
                     f"收盘 {_fmt(close)} 下穿 MA20 {_fmt(ma20)}")

    macd_v, macd_sig = _val(last, "MACD"), _val(last, "MACD_signal")
    p_macd, p_sig = _val(prev, "MACD"), _val(prev, "MACD_signal")
    if None not in (macd_v, macd_sig, p_macd, p_sig):
        if p_macd <= p_sig and macd_v > macd_sig:
            _add(out, "macd_golden", "MACD 金叉", "bull", 75,
                 f"DIF {_fmt(macd_v)} 上穿 DEA {_fmt(macd_sig)}")
        if p_macd >= p_sig and macd_v < macd_sig:
            _add(out, "macd_dead", "MACD 死叉", "bear", 75,
                 f"DIF {_fmt(macd_v)} 下穿 DEA {_fmt(macd_sig)}")
    hist, p_hist = _val(last, "MACD_hist"), _val(prev, "MACD_hist")
    if hist is not None and p_hist is not None:
        if p_hist <= 0 < hist:
            _add(out, "macd_hist_turn_up", "MACD 柱由负转正", "bull", 50,
                 f"柱值 {_fmt(p_hist)} → {_fmt(hist)}")
        if p_hist >= 0 > hist:
            _add(out, "macd_hist_turn_down", "MACD 柱由正转负", "bear", 50,
                 f"柱值 {_fmt(p_hist)} → {_fmt(hist)}")

    rsi = _val(last, "RSI")
    if rsi is not None:
        if rsi < 20:
            _add(out, "rsi_extreme_oversold", "RSI 极度超卖", "bull", 65, f"RSI {rsi:.1f} < 20")
        elif rsi < 30:
            _add(out, "rsi_oversold", "RSI 超卖", "bull", 55, f"RSI {rsi:.1f} < 30")
        elif rsi > 80:
            _add(out, "rsi_extreme_overbought", "RSI 极度超买", "bear", 65, f"RSI {rsi:.1f} > 80")
        elif rsi > 70:
            _add(out, "rsi_overbought", "RSI 超买", "bear", 55, f"RSI {rsi:.1f} > 70")

    k, d = _val(last, "KDJ_K"), _val(last, "KDJ_D")
    p_k, p_d = _val(prev, "KDJ_K"), _val(prev, "KDJ_D")
    if None not in (k, d, p_k, p_d):
        if p_k <= p_d and k > d and k < 40:
            _add(out, "kdj_golden", "KDJ 低位金叉", "bull", 60,
                 f"K {_fmt(k)} 上穿 D {_fmt(d)}，K < 40")
        if p_k >= p_d and k < d and k > 60:
            _add(out, "kdj_dead", "KDJ 高位死叉", "bear", 60,
                 f"K {_fmt(k)} 下穿 D {_fmt(d)}，K > 60")

    bu, bl = _val(last, "BOLL_upper"), _val(last, "BOLL_lower")
    if close is not None and bu is not None and close > bu:
        _add(out, "boll_break_up", "突破布林上轨", "bull", 60,
             f"收盘 {_fmt(close)} > 上轨 {_fmt(bu)}")
    if close is not None and bl is not None and close < bl:
        _add(out, "boll_break_down", "跌破布林下轨", "bear", 60,
             f"收盘 {_fmt(close)} < 下轨 {_fmt(bl)}")

    vol = _val(last, "volume")
    vol_ma = _val(last, "VOL_MA20")
    if vol is None or vol_ma is None:
        # S9-14: 量能缺失不是"不满足", 必须显式告知不可判; neutral/0 不影响多空分
        _add(out, "volume_unavailable", "量能数据缺失", "neutral", 0,
             "原因：上游未返回成交量或 20 日均量样本不足，放量类信号本次不可判")
    elif vol_ma > 0:
        ratio = vol / vol_ma
        c_open = _val(last, "open")
        if ratio >= 1.5 and close is not None and c_open is not None and close > c_open:
            _add(out, "volume_surge_up", "放量上涨", "bull", 60,
                 f"成交量 {ratio:.1f}× 20 日均量，收阳")
        if ratio >= 1.5 and close is not None and c_open is not None and close < c_open:
            _add(out, "volume_surge_down", "放量下跌", "bear", 60,
                 f"成交量 {ratio:.1f}× 20 日均量，收阴")

    if len(df) >= 21 and close is not None:
        prior_high = _num(df["high"].iloc[-21:-1].max())
        prior_low = _num(df["low"].iloc[-21:-1].min())
        if prior_high is not None and close >= prior_high:
            _add(out, "high_20", "创 20 日新高", "bull", 50,
                 f"收盘 {_fmt(close)} ≥ 前 20 日最高 {_fmt(prior_high)}")
        if prior_low is not None and close <= prior_low:
            _add(out, "low_20", "创 20 日新低", "bear", 50,
                 f"收盘 {_fmt(close)} ≤ 前 20 日最低 {_fmt(prior_low)}")

    if len(df) >= 5:
        ups = downs = 0
        for i in range(len(df) - 1, max(0, len(df) - 6), -1):
            c1, c0 = _val(df.iloc[i], "close"), _val(df.iloc[i - 1], "close")
            if c1 is None or c0 is None:
                break
            if c1 > c0:
                ups += 1
                if downs:
                    break
            elif c1 < c0:
                downs += 1
                if ups:
                    break
            else:
                break
        if ups >= 3:
            _add(out, "streak_up", f"连续 {ups} 日上涨", "bull", 45, f"最近 {ups} 个交易日收盘逐日抬升")
        if downs >= 3:
            _add(out, "streak_down", f"连续 {downs} 日下跌", "bear", 45, f"最近 {downs} 个交易日收盘逐日走低")

    return sorted(out, key=lambda x: x["strength"], reverse=True)


def scan_signals(rows: Any, code: str = "", period: str = "daily") -> dict[str, Any]:
    bars = _clean(rows)
    base = {
        "code": str(code or ""),
        "period": str(period or "daily"),
        "available": False,
        "bars": len(bars),
        "signals": [],
        "summary": None,
        "note": "规则信号：strength 为规则权重而非上涨概率；不含 LLM，不构成买卖建议。",
    }
    if len(bars) < MIN_BARS:
        base["reason"] = f"K 线不足 {MIN_BARS} 根，无法扫描"
        return base

    bars = bars[-MAX_BARS:]
    df = pd.DataFrame(bars)
    df["VOL_MA20"] = df["volume"].rolling(20, min_periods=20).mean()
    df = compute_indicators(df)

    signals = _scan(df)
    bull = [s for s in signals if s["direction"] == "bull"]
    bear = [s for s in signals if s["direction"] == "bear"]
    bull_score = sum(s["strength"] for s in bull)
    bear_score = sum(s["strength"] for s in bear)
    total = bull_score + bear_score
    net = bull_score - bear_score

    if total == 0:
        stance = "中性"
    elif bull_score >= bear_score * 1.2:
        stance = "偏多"
    elif bear_score >= bull_score * 1.2:
        stance = "偏空"
    else:
        stance = "中性"

    base.update({
        "available": True,
        "signals": signals,
        "summary": {
            "date": bars[-1]["date"],
            "close": round(bars[-1]["close"], 3),
            "stance": stance,
            "bull_count": len(bull),
            "bear_count": len(bear),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "net": net,
            "bull_share": round(bull_score / total, 3) if total else None,
        },
    })
    return base

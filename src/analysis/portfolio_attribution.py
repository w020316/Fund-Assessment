"""组合收益归因计算(阶段六批次 B,功能 #5)——纯函数,零副作用。

口径(架构文档 §3.4):
- 区间贡献额 = shares_i × (期末累计净值 − 期初累计净值)
  用累计净值消除分红影响;份额固定为当前份额(忽略区间内申赎)
- 贡献率 = 贡献额 / 组合区间盈亏;组合区间收益 = Σ贡献额 / Σ期初市值
- 窗口:
  - ``mtd``: 本月月初起;期初取月初前最后一个净值点(缺历史则标记 partial)
  - ``ytd``: 本年年初起;口径同上
  - ``max``: 各基金可得历史的**共同起点**(最晚的首日)起算;
    起点不同直接相加会让组合收益混合不同区间,故统一对齐后再归因

铁律:历史不足时返回 None/partial + missing_funds,不编数据、不 500。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

WINDOWS: tuple[str, ...] = ("mtd", "ytd", "max")
METHOD: str = "static-shares"


def resolve_window_start(window: str, today: _dt.date | None = None) -> _dt.date | None:
    """窗口 → 期初目标日期;``max`` 返回 None(不设边界)。"""
    d = today or _dt.date.today()
    key = (window or "").strip().lower()
    if key == "mtd":
        return d.replace(day=1)
    if key == "ytd":
        return _dt.date(d.year, 1, 1)
    if key == "max":
        return None
    raise ValueError(f"非法窗口: {window!r},支持 {list(WINDOWS)}")


def _clean_series(series: list[tuple[str, float]] | list[dict]) -> list[tuple[str, float]]:
    """清洗净值序列:只保留合法日期/正净值,按日期升序。"""
    out: list[tuple[str, float]] = []
    for row in series or []:
        try:
            if isinstance(row, dict):
                date_s = str(row.get("date", ""))[:10]
                nav = float(row.get("acc_nav") or row.get("nav"))
            else:
                date_s = str(row[0])[:10]
                nav = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if date_s and nav > 0:
            out.append((date_s, nav))
    out.sort(key=lambda x: x[0])
    return out


def compute_attribution(
    series_map: dict[str, list[tuple[str, float]]],
    shares_map: dict[str, float],
    names_map: dict[str, str] | None = None,
    window: str = "mtd",
    today: _dt.date | None = None,
) -> dict[str, Any]:
    """计算按基金的区间贡献表。

    Args:
        series_map: {fund_code: [(date_str, acc_nav), ...]}
        shares_map: {fund_code: 当前份额};份额 ≤0 的基金跳过
        names_map: {fund_code: 基金名称}
        window: mtd | ytd | max
        today: 注入日期(测试用,默认系统日期)

    Returns:
        dict(portfolio, funds, missing_funds, partial, method, ...)。
        ``funds`` 中贡献额与贡献率保留 2 位/6 位小数;历史不足的基金
        contribution=None 并进入 missing_funds。
    """
    names = names_map or {}
    target = resolve_window_start(window, today)
    target_s = target.isoformat() if target else None

    funds: list[dict[str, Any]] = []
    missing: list[str] = []
    partial_any = False
    total_start_value = 0.0
    total_pnl = 0.0
    window_starts: list[str] = []
    window_ends: list[str] = []

    cleaned: dict[str, list[tuple[str, float]]] = {}
    shares_by_code: dict[str, float] = {}
    for code, raw_series in (series_map or {}).items():
        code_s = str(code)
        shares = _safe_positive(shares_map.get(code_s))
        if shares <= 0:
            continue
        cleaned[code_s] = _clean_series(raw_series)
        shares_by_code[code_s] = shares

    # MAX 窗口没有统一目标日:取各基金可得历史中最晚的首日为共同起点
    common_start_s: str | None = None
    if target_s is None:
        firsts = [seq[0][0] for seq in cleaned.values() if len(seq) >= 2]
        common_start_s = max(firsts) if firsts else None

    for code_s, raw_seq in cleaned.items():
        shares = shares_by_code[code_s]
        name = names.get(code_s, "")
        seq = raw_seq
        if len(seq) < 2:
            missing.append(code_s)
            partial_any = True
            funds.append(_empty_fund(code_s, name, shares, "history_insufficient"))
            continue

        if target_s is None:
            start = common_start_s if common_start_s is not None else seq[0][0]
            seq = [row for row in seq if row[0] >= start]
            if len(seq) < 2:
                missing.append(code_s)
                partial_any = True
                funds.append(_empty_fund(code_s, name, shares, "window_insufficient"))
                continue
            start_idx = 0
            fund_partial = False
        else:
            before = [i for i, (d, _) in enumerate(seq) if d < target_s]
            if before:
                start_idx = before[-1]
                fund_partial = False
            else:
                # 历史起点落在窗口内:按可得区间计算,显式标记 partial
                start_idx = 0
                fund_partial = True

        if start_idx >= len(seq) - 1:
            missing.append(code_s)
            partial_any = True
            funds.append(_empty_fund(code_s, name, shares, "window_insufficient"))
            continue

        start_date, start_nav = seq[start_idx]
        end_date, end_nav = seq[-1]
        contribution = shares * (end_nav - start_nav)
        start_value = shares * start_nav

        total_start_value += start_value
        total_pnl += contribution
        window_starts.append(start_date)
        window_ends.append(end_date)
        if fund_partial:
            partial_any = True

        funds.append({
            "fund_code": code_s,
            "fund_name": name,
            "shares": round(shares, 2),
            "start_date": start_date,
            "end_date": end_date,
            "start_nav": round(start_nav, 4),
            "end_nav": round(end_nav, 4),
            "start_value": round(start_value, 2),
            "contribution": round(contribution, 2),
            "contribution_rate": None,  # 合计后回填
            "weight": None,
            "partial": fund_partial,
        })

    for f in funds:
        if f.get("contribution") is None:
            continue
        f["contribution_rate"] = (
            round(f["contribution"] / total_pnl, 6) if abs(total_pnl) > 1e-9 else None
        )
        f["weight"] = (
            round(f["start_value"] / total_start_value, 6) if total_start_value > 0 else None
        )

    # 贡献额降序(正贡献在前),样本不足的排最后
    funds.sort(key=lambda f: (f.get("contribution") is None, -(f.get("contribution") or 0.0)))

    total_return = (total_pnl / total_start_value) if total_start_value > 0 else None
    return {
        "window": window,
        "method": METHOD,
        "window_start": min(window_starts) if window_starts else None,
        "window_end": max(window_ends) if window_ends else None,
        "portfolio": {
            "start_value": round(total_start_value, 2),
            "end_value": round(total_start_value + total_pnl, 2),
            "pnl": round(total_pnl, 2),
            "total_return": round(total_return, 6) if total_return is not None else None,
            "fund_count": len([f for f in funds if f.get("contribution") is not None]),
        },
        "funds": funds,
        "missing_funds": missing,
        "partial": partial_any,
        "degraded": bool(missing),
        "note": (
            "按当前份额静态归因,贡献额=份额×(期末累计净值−期初累计净值),忽略区间申赎现金流"
            + (";MAX 窗口已按各基金共同起点对齐" if target_s is None else "")
            + (";部分基金历史不足,已标注" if partial_any else "")
        ),
    }


def _safe_positive(val: Any) -> float:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def _empty_fund(code: str, name: str, shares: float, reason: str) -> dict[str, Any]:
    return {
        "fund_code": code,
        "fund_name": name,
        "shares": round(shares, 2),
        "start_date": None,
        "end_date": None,
        "start_nav": None,
        "end_nav": None,
        "start_value": None,
        "contribution": None,
        "contribution_rate": None,
        "weight": None,
        "partial": True,
        "reason": reason,
    }

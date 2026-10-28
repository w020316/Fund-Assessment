"""再平衡建议计算(借鉴 PyPortfolioOpt 的权重→份额换算与 bt 的阈值再平衡)。

纯函数,不做 IO、不自动交易:
- "参与口径" = 目标清单内且有最新净值的基金;未配置目标的持仓不参与,
  避免用户只配置 3 只就把其余持仓判成清仓(在 skipped 中逐条说明)
- 当前权重 = 市值 / 参与口径市值合计;市值 = 份额 × 最新净值
- 偏离(pp) = 当前权重 − 目标权重;|偏离| **严格大于** 阈值才生成建议
- 金额 = |偏离| / 100 × 参与口径市值;份额 = 金额 / 最新净值

口径提示:金额按最新净值估算,基金申赎按 T+1 确认份额,输出仅为建议。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping


def _safe_shares(value: Any) -> float:
    try:
        shares = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return shares if shares > 0 else 0.0


def _empty(skipped: list[dict], degraded: bool, threshold: float) -> dict[str, Any]:
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "threshold_pct": threshold,
        "managed_value": 0.0,
        "total_buy": 0.0,
        "total_sell": 0.0,
        "positions": [],
        "suggestions": [],
        "skipped": skipped,
        "degraded": degraded,
        "note": "无可估值持仓或未配置目标权重;金额按最新净值估算,基金申赎按 T+1 确认份额",
    }


def build_preview(
    positions: Iterable[Mapping[str, Any]],
    navs: Mapping[str, float],
    targets: Mapping[str, float],
    threshold_pct: float,
) -> dict[str, Any]:
    """生成再平衡建议单。

    Args:
        positions: 持仓列表,读取 ``fund_code`` / ``fund_name`` / ``shares``
        navs: 基金代码 → 最新单位净值(缺失或 <=0 视为无净值)
        targets: 基金代码 → 目标权重(%)
        threshold_pct: 偏离阈值(百分点)
    """
    threshold = float(threshold_pct)
    held: dict[str, dict[str, Any]] = {}
    for p in positions:
        code = str(p.get("fund_code") or "").strip()
        shares = _safe_shares(p.get("shares"))
        if not code or shares <= 0:
            continue
        held[code] = {
            "code": code,
            "name": str(p.get("fund_name") or ""),
            "shares": shares,
        }

    target_codes = [str(c).strip() for c in targets]
    all_codes = list(dict.fromkeys(list(held) + target_codes))

    skipped: list[dict[str, Any]] = []
    degraded = False
    rows: list[dict[str, Any]] = []

    for code in all_codes:
        row_pos = held.get(code)
        name = row_pos["name"] if row_pos else ""
        if code not in targets:
            skipped.append({"code": code, "name": name,
                            "reason": "未配置目标权重,不参与本次再平衡"})
            continue
        nav = navs.get(code)
        try:
            nav_f = float(nav or 0)
        except (TypeError, ValueError):
            nav_f = 0.0
        if nav_f <= 0:
            degraded = True
            skipped.append({"code": code, "name": name,
                            "reason": "缺少最新净值,无法估算金额与份额"})
            continue
        shares = row_pos["shares"] if row_pos else 0.0
        rows.append({
            "code": code,
            "name": name,
            "shares": round(shares, 2),
            "nav": round(nav_f, 4),
            "value": round(shares * nav_f, 2),
            "target_pct": round(float(targets[code]), 2),
        })

    managed_value = sum(r["value"] for r in rows)
    if managed_value <= 0:
        return _empty(skipped, degraded, threshold)

    suggestions: list[dict[str, Any]] = []
    for r in rows:
        current_pct = r["value"] / managed_value * 100
        r["current_pct"] = round(current_pct, 2)
        r["drift_pct"] = round(current_pct - r["target_pct"], 2)
        drift = r["drift_pct"]
        if abs(drift) <= threshold:
            r["status"] = "hold"
            skipped.append({
                "code": r["code"], "name": r["name"],
                "reason": f"偏离 {drift:+.2f}pp 未超阈值 {threshold:.2f}%",
            })
            continue
        action = "buy" if drift < 0 else "sell"
        amount = abs(drift) / 100 * managed_value
        r["status"] = action
        suggestions.append({
            "code": r["code"],
            "name": r["name"],
            "action": action,
            "shares": round(amount / r["nav"], 2),
            "amount": round(amount, 2),
            "nav": r["nav"],
            "current_pct": r["current_pct"],
            "target_pct": r["target_pct"],
            "drift_pct": drift,
            "reason": f"{'超' if drift > 0 else '低于'}目标 {drift:+.2f}pp",
        })

    suggestions.sort(key=lambda s: abs(s["drift_pct"]), reverse=True)
    total_buy = sum(s["amount"] for s in suggestions if s["action"] == "buy")
    total_sell = sum(s["amount"] for s in suggestions if s["action"] == "sell")
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "threshold_pct": round(threshold, 2),
        "managed_value": round(managed_value, 2),
        "total_buy": round(total_buy, 2),
        "total_sell": round(total_sell, 2),
        "positions": rows,
        "suggestions": suggestions,
        "skipped": skipped,
        "degraded": degraded,
        "note": "仅建议不自动交易;金额按最新净值估算,基金申赎按 T+1 确认份额",
    }

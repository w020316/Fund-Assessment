"""十大流通股东归类与汇总（东财 F10 数据）。

用途：把「国家队 / 机构 / 外资 / 产业资本 / 个人 / 其他」拆开，回答“谁在拿筹码”。
约束：
- 分类是**名称 + 原始股东类型**的规则近似，可能误判，页面必须同时展示原始类型；
- 比例缺失时返回 None，不补 0；比例按“占流通股本”口径；
- 仅做持仓结构描述，不构成投资建议；季度披露存在滞后。
"""
from __future__ import annotations

import math
from typing import Any

CATEGORIES = ["国家队", "机构", "外资", "产业资本", "个人", "其他"]

NATIONAL_TEAM = [
    "中国证券金融", "证金公司", "中央汇金",
    "全国社会保障基金", "社保基金", "基本养老保险基金",
    "国家集成电路产业投资基金", "国家制造业转型升级基金",
    "国新投资", "中国国新", "中国投资有限责任公司", "国家外汇管理局",
]

FOREIGN = [
    "香港中央结算", "陆股通", "沪股通", "深股通", "QFII", "合格境外",
    "摩根", "MORGAN", "高盛", "GOLDMAN", "瑞银", "UBS", "巴克莱", "BARCLAYS",
    "瑞士信贷", "CREDIT SUISSE", "花旗", "CITIGROUP", "德意志", "DEUTSCHE",
    "MERRILL", "JPMORGAN", "BLACKROCK", "VANGUARD", "FIDELITY", "SCHRODER",
    "INVESCO", "NORGES", "TEMASEK", "GIC", "新加坡政府", "阿布达比",
    "挪威中央银行", "加拿大年金", "淡马锡",
]

INDUSTRY = ["集团", "控股", "实业", "国有资本运营", "投资控股", "国资"]

INSTITUTION = ["基金", "保险", "信托", "证券", "资管", "资产管理", "私募", "银行", "理财"]


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def classify_holder(name: str, holder_type: str = "") -> str:
    """按名称关键词 + 原始股东类型归类；名称优先于类型。"""
    n = str(name or "").upper()
    t = str(holder_type or "")
    if any(k.upper() in n for k in NATIONAL_TEAM):
        return "国家队"
    if any(k.upper() in n for k in FOREIGN):
        return "外资"
    if "个人" in t or "自然人" in t:
        return "个人"
    if any(k in n for k in INDUSTRY):
        return "产业资本"
    if any(k in n for k in INSTITUTION) or t in ("基金", "保险", "信托", "QFII", "投资公司"):
        return "机构"
    return "其他"


def parse_change(raw: Any) -> dict[str, Any]:
    """把 HOLD_NUM_CHANGE 解析为 新进 / 不变 / 增持 / 减持 / 未知。"""
    s = str(raw or "").strip()
    if s in ("新进", "新进股东"):
        return {"kind": "new", "label": "新进", "shares": None}
    if s in ("不变", "0", ""):
        return {"kind": "flat", "label": "不变" if s == "不变" else "未披露", "shares": None}
    n = _num(s.replace(",", ""))
    if n is None:
        return {"kind": "unknown", "label": s, "shares": None}
    if n > 0:
        return {"kind": "increase", "label": "增持", "shares": n}
    if n < 0:
        return {"kind": "decrease", "label": "减持", "shares": n}
    return {"kind": "flat", "label": "不变", "shares": 0.0}


def analyze_holders(payload: dict | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    rows = data.get("holders") or []
    if not rows:
        return {
            "available": False,
            "reason": "暂无十大流通股东数据（可能尚未披露或上游未返回）",
            "note": "季度披露有滞后；缺失时不补 0。",
        }

    totals = {c: {"ratio": 0.0, "count": 0, "has_ratio": False} for c in CATEGORIES}
    total_ratio = 0.0
    has_total_ratio = False
    changes = {"new": 0, "increase": 0, "decrease": 0, "flat": 0, "unknown": 0}
    enriched: list[dict[str, Any]] = []

    for h in rows:
        name = str(h.get("name") or "")
        htype = str(h.get("holder_type") or "")
        cat = classify_holder(name, htype)
        ratio = _num(h.get("free_ratio"))
        ch = parse_change(h.get("change_raw"))
        changes[ch["kind"]] = changes.get(ch["kind"], 0) + 1

        totals[cat]["count"] += 1
        if ratio is not None:
            totals[cat]["ratio"] += ratio
            totals[cat]["has_ratio"] = True
            total_ratio += ratio
            has_total_ratio = True

        enriched.append({
            "rank": h.get("rank"),
            "name": name,
            "holder_type": htype,
            "shares_type": h.get("shares_type") or "",
            "category": cat,
            "hold_num": _num(h.get("hold_num")),
            "free_ratio": ratio,
            "change_kind": ch["kind"],
            "change_label": ch["label"],
            "change_shares": ch["shares"],
            "change_ratio": _num(h.get("change_ratio")),
        })

    category_summary = {
        c: {
            "count": v["count"],
            "ratio": round(v["ratio"], 2) if v["has_ratio"] else None,
        }
        for c, v in totals.items()
    }

    top_changes = sorted(
        [x for x in enriched if x["change_shares"] is not None],
        key=lambda x: abs(x["change_shares"]),
        reverse=True,
    )[:3]

    return {
        "available": True,
        "end_date": data.get("end_date") or "",
        "holders": enriched,
        "summary": {
            "total_ratio": round(total_ratio, 2) if has_total_ratio else None,
            "categories": category_summary,
            "changes": changes,
            "top_changes": [
                {
                    "name": x["name"],
                    "category": x["category"],
                    "change_shares": x["change_shares"],
                    "change_label": x["change_label"],
                }
                for x in top_changes
            ],
        },
        "note": "分类按股东名称与原始类型规则近似，可能误判；比例口径为占流通股本，季度披露有滞后，不构成投资建议。",
    }

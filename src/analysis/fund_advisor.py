"""基金建议规则引擎

根据大盘 / 板块 / 基金净值波动,生成基金操作建议(加仓 / 减仓 / 持有 / 定投 / 止盈)。

设计原则:
- 规则可解释:每条建议附带 reason,说明触发的规则与数据
- 不造假:数据不可用时返回"数据不足,暂无建议",不编造信号
- 保守优先:无明确信号时默认"持有"

规则体系:
1. 大盘信号(基于上证/深证/创业板涨跌幅)
   - 跌 > 2%:  定投信号(逢低分批加仓)
   - 跌 1-2%: 关注信号(持有,可小额定投)
   - 涨 1-2%: 持有信号
   - 涨 > 2%: 止盈信号(分批减仓)
2. 板块信号(基金名称关键词 → 板块映射 → 板块涨跌幅)
   - 板块跌 > 2%: 加仓信号(板块超跌)
   - 板块涨 > 3%: 止盈信号(板块过热)
3. 基金盈亏信号(基于持仓成本 vs 当前净值)
   - 浮盈 > 20%: 止盈信号(分批减仓锁定收益)
   - 浮亏 > 15%: 关注信号(持有,可补仓摊低成本)
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.data_source_v2 import (
    _parallel_fetch,
    get_fund_realtime_tencent,
    get_index_realtime,
    get_sector_ranking,
)

# 基金名称关键词 → 板块名称映射(用于从 sector_ranking 中匹配板块涨跌幅)
# 板块名称以东方财富行业板块为准
_FUND_NAME_SECTOR_MAP: list[tuple[str, list[str]]] = [
    ("白酒", ["白酒"]),
    ("消费", ["食品饮料", "商业百货", "消费"]),
    ("医药", ["医药", "医疗", "生物制品", "中药"]),
    ("医疗", ["医药", "医疗", "生物制品"]),
    ("生物", ["生物制品", "医药"]),
    ("科技", ["电子信息", "软件", "半导体", "电子元件"]),
    ("芯片", ["半导体", "电子信息"]),
    ("半导体", ["半导体"]),
    ("新能源", ["新能源", "光伏", "电力"]),
    ("光伏", ["光伏"]),
    ("银行", ["银行"]),
    ("证券", ["证券", "券商"]),
    ("券商", ["证券", "券商"]),
    ("军工", ["军工", "航天航空", "国防"]),
    ("国防", ["军工", "航天航空"]),
    ("房地产", ["房地产", "房地产开发"]),
    ("地产", ["房地产"]),
    ("煤炭", ["煤炭", "能源"]),
    ("钢铁", ["钢铁"]),
    ("有色", ["有色金属"]),
    ("黄金", ["黄金", "有色金属"]),
    ("石油", ["石油", "石化"]),
    ("电力", ["电力", "新能源"]),
    ("环保", ["环保", "节能环保"]),
    ("传媒", ["传媒", "文化传媒"]),
    ("游戏", ["游戏", "传媒"]),
    ("旅游", ["旅游", "酒店餐饮"]),
    ("食品", ["食品饮料"]),
    ("农业", ["农业", "农牧饲渔"]),
    ("建材", ["建材", "水泥建材"]),
    ("保险", ["保险"]),
    ("汽车", ["汽车", "汽车整车"]),
    ("机械", ["机械", "机械行业"]),
    ("通信", ["通信", "通讯行业"]),
    ("计算机", ["计算机", "软件", "电子信息"]),
    ("信息", ["电子信息", "软件"]),
    ("电子", ["电子信息", "电子元件"]),
    ("化工", ["化工", "化学原料"]),
    ("装备", ["专用设备", "机械"]),
    ("智能", ["电子信息", "软件"]),
    ("高端", ["专用设备", "电子信息"]),
    ("制造", ["机械", "专用设备"]),
    ("材料", ["材料", "化工"]),
    ("物流", ["物流", "交运物流"]),
    ("交通", ["交通", "交运"]),
    ("航空", ["航天航空", "民航"]),
    ("航天", ["航天航空"]),
]


def _infer_sectors_from_name(fund_name: str) -> list[str]:
    """根据基金名称关键词推断关联板块。"""
    sectors: list[str] = []
    for keyword, sector_names in _FUND_NAME_SECTOR_MAP:
        if keyword in fund_name:
            for s in sector_names:
                if s not in sectors:
                    sectors.append(s)
    return sectors


def _find_sector_change_pct(sector_name: str, sector_ranking: list[dict]) -> float | None:
    """从板块排名数据中查找指定板块的涨跌幅。"""
    for s in sector_ranking:
        name = str(s.get("name", ""))
        # 模糊匹配:板块名包含或被包含
        if sector_name in name or name in sector_name:
            return float(s.get("change_pct", 0))
    return None


def _build_market_signal(index_data: list[dict]) -> dict[str, Any]:
    """根据大盘指数涨跌幅生成市场信号。"""
    # 找最大涨跌幅(绝对值)的指数作为参考
    max_abs_change = 0.0
    max_index: dict = {}
    for idx in index_data:
        try:
            change_pct = float(idx.get("change_pct", 0))
            if abs(change_pct) > abs(max_abs_change):
                max_abs_change = change_pct
                max_index = idx
        except (ValueError, TypeError):
            continue

    if not max_index:
        return {"signal": "none", "action": "暂无", "reason": "大盘数据不可用", "change_pct": 0.0}

    change_pct = max_abs_change
    idx_name = str(max_index.get("name", ""))

    if change_pct <= -2.0:
        return {
            "signal": "dca",
            "action": "定投",
            "reason": f"{idx_name}跌 {abs(change_pct):.2f}%,市场超跌,适合分批定投逢低加仓",
            "change_pct": round(change_pct, 2),
            "index_name": idx_name,
        }
    if change_pct <= -1.0:
        return {
            "signal": "watch",
            "action": "持有",
            "reason": f"{idx_name}跌 {abs(change_pct):.2f}%,市场偏弱,持有观望,可小额定投",
            "change_pct": round(change_pct, 2),
            "index_name": idx_name,
        }
    if change_pct >= 2.0:
        return {
            "signal": "take_profit",
            "action": "止盈",
            "reason": f"{idx_name}涨 {change_pct:.2f}%,市场过热,建议分批止盈减仓",
            "change_pct": round(change_pct, 2),
            "index_name": idx_name,
        }
    if change_pct >= 1.0:
        return {
            "signal": "hold",
            "action": "持有",
            "reason": f"{idx_name}涨 {change_pct:.2f}%,市场偏强,持有为主",
            "change_pct": round(change_pct, 2),
            "index_name": idx_name,
        }
    return {
        "signal": "hold",
        "action": "持有",
        "reason": f"{idx_name}涨跌幅 {change_pct:.2f}%,市场平稳,持有观望",
        "change_pct": round(change_pct, 2),
        "index_name": idx_name,
    }


def _build_sector_signal(fund_name: str, sector_ranking: list[dict]) -> dict[str, Any]:
    """根据基金名称推断关联板块,并基于板块涨跌幅生成信号。"""
    sectors = _infer_sectors_from_name(fund_name)
    if not sectors:
        return {"signal": "none", "action": "暂无", "reason": "未匹配到关联板块", "sectors": []}

    matched: list[dict] = []
    for s in sectors:
        change_pct = _find_sector_change_pct(s, sector_ranking)
        if change_pct is not None:
            matched.append({"sector": s, "change_pct": round(change_pct, 2)})

    if not matched:
        return {"signal": "none", "action": "暂无", "reason": f"关联板块 {sectors} 数据不可用", "sectors": sectors}

    # 取跌幅最大或涨幅最大的板块作为主导信号
    worst = min(matched, key=lambda x: x["change_pct"])
    best = max(matched, key=lambda x: x["change_pct"])

    if worst["change_pct"] <= -2.0:
        return {
            "signal": "add",
            "action": "加仓",
            "reason": f"关联板块[{worst['sector']}]跌 {abs(worst['change_pct']):.2f}%,板块超跌,可逢低加仓",
            "sectors": matched,
        }
    if best["change_pct"] >= 3.0:
        return {
            "signal": "take_profit",
            "action": "止盈",
            "reason": f"关联板块[{best['sector']}]涨 {best['change_pct']:.2f}%,板块过热,建议分批止盈",
            "sectors": matched,
        }
    return {
        "signal": "hold",
        "action": "持有",
        "reason": f"关联板块{[m['sector'] for m in matched]}涨跌幅在正常区间,持有观望",
        "sectors": matched,
    }


def _build_pnl_signal(cost_nav: float, current_nav: float) -> dict[str, Any]:
    """根据持仓盈亏生成信号。"""
    if cost_nav <= 0 or current_nav <= 0:
        return {"signal": "none", "action": "暂无", "reason": "成本净值或当前净值无效"}
    pnl_pct = (current_nav - cost_nav) / cost_nav * 100
    if pnl_pct >= 20.0:
        return {
            "signal": "take_profit",
            "action": "止盈",
            "reason": f"浮盈 {pnl_pct:.2f}%,已达止盈线(20%),建议分批减仓锁定收益",
            "pnl_pct": round(pnl_pct, 2),
        }
    if pnl_pct <= -15.0:
        return {
            "signal": "watch",
            "action": "持有",
            "reason": f"浮亏 {abs(pnl_pct):.2f}%,已达关注线(-15%),持有观望,可考虑补仓摊低成本",
            "pnl_pct": round(pnl_pct, 2),
        }
    return {
        "signal": "hold",
        "action": "持有",
        "reason": f"浮亏盈 {pnl_pct:+.2f}%,在正常区间,持有",
        "pnl_pct": round(pnl_pct, 2),
    }


def _merge_signals(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """合并多个信号,优先级:止盈 > 加仓/定投 > 持有 > 关注。"""
    valid = [s for s in signals if s.get("signal") and s["signal"] != "none"]
    if not valid:
        return {
            "signal": "none",
            "action": "暂无建议",
            "reason": "数据不足,暂无明确建议",
        }

    # 优先级映射
    priority = {"take_profit": 4, "add": 3, "dca": 3, "hold": 2, "watch": 1, "none": 0}
    top = max(valid, key=lambda s: priority.get(s["signal"], 0))
    reasons = [s["reason"] for s in valid if s.get("reason")]
    return {
        "signal": top["signal"],
        "action": top["action"],
        "reason": " | ".join(reasons),
        "details": valid,
    }


def generate_fund_advice(positions: list[dict]) -> dict[str, Any]:
    """为用户基金持仓生成建议。

    Args:
        positions: 基金持仓列表,每项含 fund_code / fund_name / shares / cost_nav / buy_date

    Returns:
        {
            "market_signal": {...},       # 大盘信号
            "positions_advice": [          # 每只基金的建议
                {
                    "fund_code": "110022",
                    "fund_name": "易方达消费行业股票",
                    "current_nav": 2.724,
                    "cost_nav": 2.5,
                    "pnl_pct": 8.96,
                    "advice": {"signal": "hold", "action": "持有", "reason": "...", "details": [...]}
                }
            ],
            "summary": "..."               # 整体建议摘要
        }
    """
    if not positions:
        return {
            "market_signal": {"signal": "none", "reason": "无持仓"},
            "positions_advice": [],
            "summary": "暂无基金持仓,请先添加持仓",
        }

    # 并行拉取大盘指数 + 板块排名
    fund_codes = [p.get("fund_code", "") for p in positions if p.get("fund_code")]
    funcs = [
        ("index", get_index_realtime),
        ("sector", get_sector_ranking),
        ("fund_quotes", lambda: get_fund_realtime_tencent(fund_codes) if fund_codes else []),
    ]
    fetched = _parallel_fetch(funcs)

    index_data = fetched.get("index") or []
    sector_ranking = fetched.get("sector") or []
    fund_quotes = fetched.get("fund_quotes") or []
    quote_map = {q.get("code", ""): q for q in fund_quotes}

    market_signal = _build_market_signal(index_data)

    positions_advice: list[dict] = []
    for pos in positions:
        code = str(pos.get("fund_code", ""))
        name = str(pos.get("fund_name", ""))
        cost_nav = float(pos.get("cost_nav", 0) or 0)
        shares = float(pos.get("shares", 0) or 0)

        quote = quote_map.get(code, {})
        current_nav = float(quote.get("nav", 0) or 0)

        signals: list[dict] = [market_signal]

        # 板块信号
        if name:
            signals.append(_build_sector_signal(name, sector_ranking))

        # 盈亏信号
        if cost_nav > 0 and current_nav > 0:
            signals.append(_build_pnl_signal(cost_nav, current_nav))
        elif not current_nav:
            signals.append({"signal": "none", "reason": f"基金 {code} 实时净值不可用"})

        advice = _merge_signals(signals)
        pnl_pct = ((current_nav - cost_nav) / cost_nav * 100) if cost_nav > 0 and current_nav > 0 else 0.0
        pnl_amount = (current_nav - cost_nav) * shares if current_nav > 0 and cost_nav > 0 else 0.0

        positions_advice.append({
            "fund_code": code,
            "fund_name": name,
            "current_nav": current_nav,
            "cost_nav": cost_nav,
            "shares": shares,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_amount": round(pnl_amount, 2),
            "advice": advice,
        })

    # 整体摘要
    actions = [p["advice"]["action"] for p in positions_advice]
    take_profit_count = sum(1 for a in actions if "止盈" in a)
    add_count = sum(1 for a in actions if "加仓" in a or "定投" in a)
    hold_count = sum(1 for a in actions if "持有" in a)
    summary_parts = [f"大盘: {market_signal['action']}"]
    if take_profit_count:
        summary_parts.append(f"{take_profit_count} 只建议止盈")
    if add_count:
        summary_parts.append(f"{add_count} 只建议加仓/定投")
    if hold_count:
        summary_parts.append(f"{hold_count} 只建议持有")
    summary = " | ".join(summary_parts)

    return {
        "market_signal": market_signal,
        "positions_advice": positions_advice,
        "summary": summary,
    }

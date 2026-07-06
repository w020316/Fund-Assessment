"""股市/基金话术库

预置话术模板 + 变量填充,根据当前持仓/行情数据生成个性化话术。

设计原则:
- 场景驱动:每条话术标注适用场景(买入/卖出/持有/观望/止盈/止损/定投/加仓)
- 变量填充:模板含 {stock_name}/{cost_price}/{current_price}/{pnl_pct} 等占位符
- 分类清晰:股市话术(技术面/基本面/资金面/情绪面)+ 基金话术(定投/止盈/加仓/择时)
- 安全兜底:变量缺失时用「—」占位,不报错
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger


# ──────────────────────────────────────────────
# 股市话术模板
# ──────────────────────────────────────────────
_STOCK_SCRIPTS: list[dict[str, Any]] = [
    # === 买入场景 ===
    {
        "id": "stock_buy_breakthrough",
        "category": "stock",
        "scene": "buy",
        "dimension": "技术面",
        "title": "突破买入",
        "template": "{stock_name}突破关键阻力位 {resistance_price},成交量放大至 {volume_ratio} 倍,MACD 金叉,短期动能强劲,可考虑分批建仓,止损位 {stop_loss_price}。",
        "variables": ["stock_name", "resistance_price", "volume_ratio", "stop_loss_price"],
    },
    {
        "id": "stock_buy_dip",
        "category": "stock",
        "scene": "buy",
        "dimension": "技术面",
        "title": "回调买入",
        "template": "{stock_name}回调至 {current_price} 元,接近支撑位 {support_price},RSI 降至 {rsi} 超卖区间,可考虑逢低吸纳,目标价 {target_price}。",
        "variables": ["stock_name", "current_price", "support_price", "rsi", "target_price"],
    },
    {
        "id": "stock_buy_value",
        "category": "stock",
        "scene": "buy",
        "dimension": "基本面",
        "title": "价值低估",
        "template": "{stock_name}当前 PE {pe} 倍,低于行业均值 {industry_pe} 倍,ROE {roe}%,毛利率 {gross_margin}%,基本面优质但估值偏低,具备中长期配置价值。",
        "variables": ["stock_name", "pe", "industry_pe", "roe", "gross_margin"],
    },
    {
        "id": "stock_buy_capital_inflow",
        "category": "stock",
        "scene": "buy",
        "dimension": "资金面",
        "title": "主力流入",
        "template": "{stock_name}近 3 日主力净流入 {main_inflow} 万元,大单买入占比 {large_order_ratio}%,资金明显抢筹,关注后续放量突破机会。",
        "variables": ["stock_name", "main_inflow", "large_order_ratio"],
    },
    # === 卖出场景 ===
    {
        "id": "stock_sell_overbought",
        "category": "stock",
        "scene": "sell",
        "dimension": "技术面",
        "title": "超买卖出",
        "template": "{stock_name}RSI 达 {rsi},进入超买区间,KDJ 高位死叉,短期回调风险加大,可考虑分批减仓锁定利润。",
        "variables": ["stock_name", "rsi"],
    },
    {
        "id": "stock_sell_resistance",
        "category": "stock",
        "scene": "sell",
        "dimension": "技术面",
        "title": "遇阻回落",
        "template": "{stock_name}冲击 {resistance_price} 阻力位未果,成交量萎缩,出现长上影线,短期遇阻明显,可考虑减仓观望。",
        "variables": ["stock_name", "resistance_price"],
    },
    {
        "id": "stock_sell_take_profit",
        "category": "stock",
        "scene": "sell",
        "dimension": "基本面",
        "title": "止盈兑现",
        "template": "{stock_name}已达目标价 {target_price},浮盈 {pnl_pct}%,基本面预期兑现,可分批止盈落袋为安。",
        "variables": ["stock_name", "target_price", "pnl_pct"],
    },
    # === 持有场景 ===
    {
        "id": "stock_hold_trend",
        "category": "stock",
        "scene": "hold",
        "dimension": "技术面",
        "title": "趋势持有",
        "template": "{stock_name}处于上升通道,均线多头排列,MACD 红柱放大,趋势完好,持有为主,跌破 {ma20} 考虑减仓。",
        "variables": ["stock_name", "ma20"],
    },
    {
        "id": "stock_hold_fundamental",
        "category": "stock",
        "scene": "hold",
        "dimension": "基本面",
        "title": "基本面持有",
        "template": "{stock_name}业绩稳健,营收同比 {revenue_yoy}%,净利同比 {profit_yoy}%,行业地位稳固,中长期持有。",
        "variables": ["stock_name", "revenue_yoy", "profit_yoy"],
    },
    # === 观望场景 ===
    {
        "id": "stock_wait_unclear",
        "category": "stock",
        "scene": "wait",
        "dimension": "技术面",
        "title": "方向不明",
        "template": "{stock_name}近期缩量横盘,方向不明,建议观望,等待放量选择方向后再跟进。",
        "variables": ["stock_name"],
    },
    {
        "id": "stock_wait_market_weak",
        "category": "stock",
        "scene": "wait",
        "dimension": "情绪面",
        "title": "市场偏弱",
        "template": "大盘 {index_name}跌 {index_change_pct}%,市场情绪偏弱,跌停 {limit_down_count} 家,{stock_name}建议观望,等待市场企稳。",
        "variables": ["index_name", "index_change_pct", "limit_down_count", "stock_name"],
    },
    # === 止损场景 ===
    {
        "id": "stock_stop_loss",
        "category": "stock",
        "scene": "stop_loss",
        "dimension": "技术面",
        "title": "破位止损",
        "template": "{stock_name}跌破支撑位 {support_price},浮亏 {pnl_pct}%,技术形态破坏,严格执行止损,等待企稳后再考虑回补。",
        "variables": ["stock_name", "support_price", "pnl_pct"],
    },
]


# ──────────────────────────────────────────────
# 基金话术模板
# ──────────────────────────────────────────────
_FUND_SCRIPTS: list[dict[str, Any]] = [
    # === 定投场景 ===
    {
        "id": "fund_dca_market_drop",
        "category": "fund",
        "scene": "dca",
        "dimension": "择时",
        "title": "大跌定投",
        "template": "大盘 {index_name}跌 {index_change_pct}%,市场超跌,{fund_name}({fund_code})逢低分批定投,摊低成本,等待反弹。",
        "variables": ["index_name", "index_change_pct", "fund_name", "fund_code"],
    },
    {
        "id": "fund_dca_regular",
        "category": "fund",
        "scene": "dca",
        "dimension": "择时",
        "title": "定期定投",
        "template": "{fund_name}({fund_code})当前净值 {current_nav} 元,坚持每周/每月定投,平摊成本,长期积累筹码,适合工薪族稳健理财。",
        "variables": ["fund_name", "fund_code", "current_nav"],
    },
    {
        "id": "fund_dca_sector_oversold",
        "category": "fund",
        "scene": "dca",
        "dimension": "择时",
        "title": "板块超跌定投",
        "template": "{fund_name}关联板块[{sector_name}]跌 {sector_change_pct}%,板块超跌,可加大定投额度,逢低积累筹码。",
        "variables": ["fund_name", "sector_name", "sector_change_pct"],
    },
    # === 止盈场景 ===
    {
        "id": "fund_take_profit_target",
        "category": "fund",
        "scene": "take_profit",
        "dimension": "择时",
        "title": "达标止盈",
        "template": "{fund_name}({fund_code})浮盈 {pnl_pct}%,已达止盈线,建议分批止盈,锁定收益,留 30% 底仓博取后续上涨。",
        "variables": ["fund_name", "fund_code", "pnl_pct"],
    },
    {
        "id": "fund_take_profit_sector_overbought",
        "category": "fund",
        "scene": "take_profit",
        "dimension": "择时",
        "title": "板块过热止盈",
        "template": "{fund_name}关联板块[{sector_name}]涨 {sector_change_pct}%,板块过热,估值偏高,建议分批止盈减仓。",
        "variables": ["fund_name", "sector_name", "sector_change_pct"],
    },
    {
        "id": "fund_take_profit_market_hot",
        "category": "fund",
        "scene": "take_profit",
        "dimension": "择时",
        "title": "市场过热止盈",
        "template": "大盘 {index_name}涨 {index_change_pct}%,市场过热,{fund_name}建议分批止盈,落袋为安。",
        "variables": ["index_name", "index_change_pct", "fund_name"],
    },
    # === 加仓场景 ===
    {
        "id": "fund_add_position_sector_dip",
        "category": "fund",
        "scene": "add",
        "dimension": "择时",
        "title": "板块回调加仓",
        "template": "{fund_name}关联板块[{sector_name}]跌 {sector_change_pct}%,板块超跌,可逢低加仓,降低平均成本。",
        "variables": ["fund_name", "sector_name", "sector_change_pct"],
    },
    {
        "id": "fund_add_position_loss",
        "category": "fund",
        "scene": "add",
        "dimension": "择时",
        "title": "浮亏补仓",
        "template": "{fund_name}({fund_code})浮亏 {pnl_pct}%,接近关注线,可考虑补仓摊低成本,但需控制仓位,避免重仓单一基金。",
        "variables": ["fund_name", "fund_code", "pnl_pct"],
    },
    # === 持有场景 ===
    {
        "id": "fund_hold_normal",
        "category": "fund",
        "scene": "hold",
        "dimension": "择时",
        "title": "正常持有",
        "template": "{fund_name}({fund_code})当前净值 {current_nav} 元,浮盈 {pnl_pct}%,在正常区间,持有观望为主。",
        "variables": ["fund_name", "fund_code", "current_nav", "pnl_pct"],
    },
    {
        "id": "fund_hold_market_stable",
        "category": "fund",
        "scene": "hold",
        "dimension": "择时",
        "title": "市场平稳持有",
        "template": "大盘 {index_name}涨跌幅 {index_change_pct}%,市场平稳,{fund_name}持有观望,按既定计划执行。",
        "variables": ["index_name", "index_change_pct", "fund_name"],
    },
    # === 观望场景 ===
    {
        "id": "fund_wait_data_missing",
        "category": "fund",
        "scene": "wait",
        "dimension": "择时",
        "title": "数据不足观望",
        "template": "{fund_name}({fund_code})实时净值或板块数据暂不可用,建议观望,等待数据恢复后再做决策。",
        "variables": ["fund_name", "fund_code"],
    },
    {
        "id": "fund_wait_market_weak",
        "category": "fund",
        "scene": "wait",
        "dimension": "择时",
        "title": "市场偏弱观望",
        "template": "大盘 {index_name}跌 {index_change_pct}%,市场偏弱,{fund_name}持有观望,可小额定投但不建议大额加仓。",
        "variables": ["index_name", "index_change_pct", "fund_name"],
    },
]


_ALL_SCRIPTS = _STOCK_SCRIPTS + _FUND_SCRIPTS


def list_scripts(category: str = "", scene: str = "") -> list[dict[str, Any]]:
    """列出话术模板(可按 category/scene 过滤)。"""
    result = _ALL_SCRIPTS
    if category:
        result = [s for s in result if s["category"] == category]
    if scene:
        result = [s for s in result if s["scene"] == scene]
    return result


def get_script(script_id: str) -> dict[str, Any] | None:
    """按 ID 获取单个话术模板。"""
    for s in _ALL_SCRIPTS:
        if s["id"] == script_id:
            return s
    return None


def _fill_template(template: str, variables: dict[str, Any]) -> str:
    """填充模板变量,缺失变量用「—」占位。"""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        val = variables.get(key)
        if val is None or val == "":
            return "—"
        # 数值类做合理格式化
        if isinstance(val, float):
            if val == int(val):
                return str(int(val))
            return f"{val:.2f}"
        return str(val)

    return re.sub(r"\{(\w+)\}", replace, template)


def generate_script(script_id: str, variables: dict[str, Any]) -> dict[str, Any] | None:
    """根据模板 ID + 变量生成话术。

    Args:
        script_id: 话术模板 ID
        variables: 变量字典,如 {"stock_name": "贵州茅台", "pe": 28.5}

    Returns:
        {"id", "category", "scene", "dimension", "title", "content", "variables"}
    """
    script = get_script(script_id)
    if not script:
        logger.warning(f"script not found: {script_id}")
        return None
    content = _fill_template(script["template"], variables)
    return {
        "id": script["id"],
        "category": script["category"],
        "scene": script["scene"],
        "dimension": script["dimension"],
        "title": script["title"],
        "content": content,
        "variables": variables,
    }


def match_fund_scripts(fund_advice: dict[str, Any]) -> list[dict[str, Any]]:
    """根据基金建议自动匹配话术。

    Args:
        fund_advice: generate_fund_advice 返回的单只基金 advice 项(含 advice.signal/advice.action)

    Returns:
        匹配的话术列表(已填充变量)
    """
    signal = fund_advice.get("advice", {}).get("signal", "none")
    variables = {
        "fund_name": fund_advice.get("fund_name", ""),
        "fund_code": fund_advice.get("fund_code", ""),
        "current_nav": fund_advice.get("current_nav", 0),
        "cost_nav": fund_advice.get("cost_nav", 0),
        "pnl_pct": fund_advice.get("pnl_pct", 0),
    }
    # 从 market_signal 补充大盘变量
    market = fund_advice.get("market_signal", {})
    variables["index_name"] = market.get("index_name", "")
    variables["index_change_pct"] = market.get("change_pct", 0)

    scene_map = {
        "take_profit": "take_profit",
        "add": "add",
        "dca": "dca",
        "hold": "hold",
        "watch": "wait",
        "none": "wait",
    }
    scene = scene_map.get(signal, "hold")
    scripts = list_scripts(category="fund", scene=scene)
    return [generate_script(s["id"], variables) for s in scripts]  # type: ignore[list-item]


def match_stock_scripts(stock_data: dict[str, Any]) -> list[dict[str, Any]]:
    """根据个股数据自动匹配话术。

    Args:
        stock_data: 含 code/name/price/change_pct/pnl_pct 等字段的个股数据

    Returns:
        匹配的话术列表(已填充变量)
    """
    variables = {
        "stock_name": stock_data.get("name", ""),
        "current_price": stock_data.get("price", 0),
        "pnl_pct": stock_data.get("pnl_pct", 0),
    }
    # 根据涨跌幅决定场景
    change_pct = float(stock_data.get("change_pct", 0) or 0)
    pnl_pct = float(stock_data.get("pnl_pct", 0) or 0)
    if pnl_pct >= 20:
        scene = "sell"
    elif change_pct >= 5:
        scene = "sell"
    elif change_pct <= -5:
        scene = "wait"
    else:
        scene = "hold"
    scripts = list_scripts(category="stock", scene=scene)
    return [generate_script(s["id"], variables) for s in scripts]  # type: ignore[list-item]


def get_script_categories() -> dict[str, Any]:
    """获取话术库分类结构(用于前端浏览)。"""
    stock_scenes = sorted({s["scene"] for s in _STOCK_SCRIPTS})
    fund_scenes = sorted({s["scene"] for s in _FUND_SCRIPTS})
    stock_dimensions = sorted({s["dimension"] for s in _STOCK_SCRIPTS})
    fund_dimensions = sorted({s["dimension"] for s in _FUND_SCRIPTS})
    return {
        "stock": {
            "count": len(_STOCK_SCRIPTS),
            "scenes": stock_scenes,
            "dimensions": stock_dimensions,
        },
        "fund": {
            "count": len(_FUND_SCRIPTS),
            "scenes": fund_scenes,
            "dimensions": fund_dimensions,
        },
        "total": len(_ALL_SCRIPTS),
    }

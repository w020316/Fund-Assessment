"""基金建议五信号融合引擎(P1-4)

基于五信号加权融合,生成基金买卖建议:
1. 技术面信号(20%): 基金净值趋势(MA5/MA20)
2. 基本面信号(20%): 重仓股估值(PE/PB)+净值分位数
3. 消息面信号(25%): 消息面情绪指数(P1-1)
4. 重仓股板块信号(20%): 重仓股板块轮动+净值影响(P1-2)
5. 大盘环境信号(15%): 大盘温度计(P1-3)

设计原则:
- 数据可降级:某信号数据不可用时,自动降级到其他信号
- 可解释:每个信号附带评分理由
- 与v1兼容:不修改原有 fund_advisor.py,新增 v2 接口
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

from src.core import data_source_v2 as ds2


# ===== 信号权重 =====
SIGNAL_WEIGHTS = {
    "technical": 0.20,      # 技术面
    "fundamental": 0.20,    # 基本面
    "news": 0.25,           # 消息面
    "holdings": 0.20,       # 重仓股板块
    "market": 0.15,         # 大盘环境
}


def _calc_technical_signal(nav_history: list[dict]) -> dict[str, Any]:
    """技术面信号(20%): 基金净值MA5/MA20趋势

    Args:
        nav_history: 基金历史净值 [{date, nav, ...}, ...]

    Returns:
        {"score", "signal", "reason", "ma5", "ma20", "trend"}
    """
    if not nav_history or len(nav_history) < 5:
        return {
            "score": 50,
            "signal": "neutral",
            "reason": "净值数据不足,无法计算趋势",
            "weight": SIGNAL_WEIGHTS["technical"],
        }

    navs = [float(n.get("nav", 0)) for n in nav_history if n.get("nav")]
    if len(navs) < 5:
        return {
            "score": 50,
            "signal": "neutral",
            "reason": "有效净值数据不足",
            "weight": SIGNAL_WEIGHTS["technical"],
        }

    ma5 = sum(navs[-5:]) / 5
    ma20 = sum(navs[-20:]) / 20 if len(navs) >= 20 else sum(navs) / len(navs)
    current_nav = navs[-1]

    # 趋势判断
    if ma5 > ma20:
        trend = "多头排列"
        if current_nav > ma5:
            signal = "bullish"
            score = 75
            reason = f"净值{current_nav:.4f} > MA5({ma5:.4f}) > MA20({ma20:.4f}),多头排列,趋势向上"
        else:
            signal = "neutral_bullish"
            score = 60
            reason = f"MA5({ma5:.4f}) > MA20({ma20:.4f}),多头趋势但净值回调,持有"
    elif ma5 < ma20:
        trend = "空头排列"
        if current_nav < ma5:
            signal = "bearish"
            score = 25
            reason = f"净值{current_nav:.4f} < MA5({ma5:.4f}) < MA20({ma20:.4f}),空头排列,趋势向下"
        else:
            signal = "neutral_bearish"
            score = 40
            reason = f"MA5({ma5:.4f}) < MA20({ma20:.4f}),空头趋势但净值反弹,观望"
    else:
        trend = "震荡"
        signal = "neutral"
        score = 50
        reason = f"MA5({ma5:.4f}) ≈ MA20({ma20:.4f}),趋势震荡"

    return {
        "score": score,
        "signal": signal,
        "reason": reason,
        "ma5": round(ma5, 4),
        "ma20": round(ma20, 4),
        "current_nav": current_nav,
        "trend": trend,
        "weight": SIGNAL_WEIGHTS["technical"],
    }


def _calc_fundamental_signal(
    holdings: list[dict],
    quotes: list[dict],
    nav_history: list[dict],
) -> dict[str, Any]:
    """基本面信号(20%): 重仓股估值(PE/PB)+净值分位数

    Args:
        holdings: 重仓股列表
        quotes: 重仓股实时行情(含pe_ttm/pb)
        nav_history: 基金历史净值

    Returns:
        {"score", "signal", "reason", "avg_pe", "avg_pb", "nav_percentile"}
    """
    # 1. 重仓股估值
    quote_map = {q.get("code", ""): q for q in quotes}
    pes: list[float] = []
    pbs: list[float] = []
    total_weight = 0.0
    for h in holdings:
        code = h.get("code", "")
        weight = h.get("weight", 0)
        q = quote_map.get(code, {})
        pe = float(q.get("pe_ttm", 0) or 0)
        pb = float(q.get("pb", 0) or 0)
        if pe > 0:
            pes.append(pe * weight)
            total_weight += weight
        if pb > 0:
            pbs.append(pb * weight)

    avg_pe = sum(pes) / total_weight if total_weight > 0 else 0
    avg_pb = sum(pbs) / total_weight if total_weight > 0 else 0

    # PE评分: PE<15 低估, 15-30 合理, 30-50 偏高, >50 高估
    if avg_pe > 0:
        if avg_pe < 15:
            pe_score = 80
            pe_label = "低估"
        elif avg_pe < 30:
            pe_score = 60
            pe_label = "合理"
        elif avg_pe < 50:
            pe_score = 40
            pe_label = "偏高"
        else:
            pe_score = 20
            pe_label = "高估"
    else:
        pe_score = 50
        pe_label = "无数据"

    # 2. 净值分位数(当前净值在过去N日的位置)
    nav_percentile = 50
    if nav_history and len(nav_history) >= 10:
        navs = sorted([float(n.get("nav", 0)) for n in nav_history if n.get("nav")])
        current_nav = float(nav_history[-1].get("nav", 0))
        if navs and current_nav > 0:
            below_count = sum(1 for n in navs if n < current_nav)
            nav_percentile = below_count / len(navs) * 100

    # 净值分位数评分: 低分位(20%以下) → 逢低布局(80分), 高分位(80%以上) → 高位减仓(20分)
    if nav_percentile < 20:
        nav_score = 80
        nav_label = "低位"
    elif nav_percentile < 40:
        nav_score = 65
        nav_label = "偏低"
    elif nav_percentile < 60:
        nav_score = 50
        nav_label = "中位"
    elif nav_percentile < 80:
        nav_score = 35
        nav_label = "偏高"
    else:
        nav_score = 20
        nav_label = "高位"

    # 综合: PE 50% + 净值分位 50%
    score = (pe_score + nav_score) / 2

    if score >= 70:
        signal = "bullish"
    elif score >= 55:
        signal = "neutral_bullish"
    elif score >= 45:
        signal = "neutral"
    elif score >= 30:
        signal = "neutral_bearish"
    else:
        signal = "bearish"

    return {
        "score": round(score, 1),
        "signal": signal,
        "reason": f"重仓股PE={avg_pe:.1f}({pe_label}),净值分位{nav_percentile:.0f}%({nav_label})",
        "avg_pe": round(avg_pe, 2),
        "avg_pb": round(avg_pb, 2),
        "pe_label": pe_label,
        "nav_percentile": round(nav_percentile, 1),
        "nav_label": nav_label,
        "weight": SIGNAL_WEIGHTS["fundamental"],
    }


def _calc_news_signal(news_data: dict) -> dict[str, Any]:
    """消息面信号(25%): 消息面情绪指数

    Args:
        news_data: 消息面聚合结果(P1-1)

    Returns:
        {"score", "signal", "reason", "sentiment_index"}
    """
    sentiment_index = float(news_data.get("sentiment_index", 50) or 50) if news_data else 50
    total_count = int(news_data.get("total_count", 0)) if news_data else 0

    if total_count == 0:
        return {
            "score": 50,
            "signal": "neutral",
            "reason": "消息面数据不足",
            "weight": SIGNAL_WEIGHTS["news"],
        }

    # sentiment_index: 0-100, >60 偏多, <40 偏空
    if sentiment_index >= 70:
        signal = "bullish"
        label = "强烈利好"
    elif sentiment_index >= 55:
        signal = "neutral_bullish"
        label = "偏多"
    elif sentiment_index >= 45:
        signal = "neutral"
        label = "中性"
    elif sentiment_index >= 30:
        signal = "neutral_bearish"
        label = "偏空"
    else:
        signal = "bearish"
        label = "强烈利空"

    return {
        "score": round(sentiment_index, 1),
        "signal": signal,
        "reason": f"消息面情绪指数{sentiment_index:.0f}({label}),共{total_count}条新闻",
        "sentiment_index": round(sentiment_index, 1),
        "news_count": total_count,
        "label": label,
        "weight": SIGNAL_WEIGHTS["news"],
    }


def _calc_holdings_signal(holdings_data: dict) -> dict[str, Any]:
    """重仓股板块信号(20%): 重仓股板块轮动+净值影响

    Args:
        holdings_data: 重仓股分析结果(P1-2)

    Returns:
        {"score", "signal", "reason", "nav_impact", "sector_signal"}
    """
    if not holdings_data or not holdings_data.get("holdings"):
        return {
            "score": 50,
            "signal": "neutral",
            "reason": "无重仓股数据",
            "weight": SIGNAL_WEIGHTS["holdings"],
        }

    # 1. 净值影响预估
    nav_impact = holdings_data.get("nav_impact", {})
    est_change = float(nav_impact.get("estimated_change_pct", 0) or 0)
    # est_change 通常很小(0.1~-0.1),映射: 0.5% → 80, 0 → 50, -0.5% → 20
    impact_score = max(0, min(100, 50 + est_change * 60))

    # 2. 板块轮动信号(基于sector_rotation)
    sector_rotation = holdings_data.get("sector_rotation", [])
    if sector_rotation:
        # 加权平均板块信号
        signal_map = {"强势": 80, "抗跌": 65, "中性": 50, "弱势": 30, "滞涨": 35, "无数据": 50}
        total_weight = 0.0
        weighted_score = 0.0
        for sr in sector_rotation:
            w = float(sr.get("weight", 0))
            sig = sr.get("signal", "无数据")
            weighted_score += signal_map.get(sig, 50) * w
            total_weight += w
        sector_score = weighted_score / total_weight if total_weight > 0 else 50
    else:
        sector_score = 50

    # 综合: 净值影响 60% + 板块轮动 40%
    score = impact_score * 0.6 + sector_score * 0.4

    if score >= 70:
        signal = "bullish"
    elif score >= 55:
        signal = "neutral_bullish"
    elif score >= 45:
        signal = "neutral"
    elif score >= 30:
        signal = "neutral_bearish"
    else:
        signal = "bearish"

    return {
        "score": round(score, 1),
        "signal": signal,
        "reason": f"重仓股预估净值影响{est_change:+.2f}%,板块轮动评分{sector_score:.0f}",
        "nav_impact_pct": round(est_change, 4),
        "sector_score": round(sector_score, 1),
        "weight": SIGNAL_WEIGHTS["holdings"],
    }


def _calc_market_signal(thermometer: dict) -> dict[str, Any]:
    """大盘环境信号(15%): 大盘温度计

    Args:
        thermometer: 大盘温度计(P1-3)

    Returns:
        {"score", "signal", "reason", "thermometer_score", "level"}
    """
    if not thermometer:
        return {
            "score": 50,
            "signal": "neutral",
            "reason": "大盘温度计数据不可用",
            "weight": SIGNAL_WEIGHTS["market"],
        }

    score = float(thermometer.get("score", 50))
    level = thermometer.get("level", "中性")

    if score >= 80:
        signal = "bearish"  # 极热,反向信号(风险)
    elif score >= 60:
        signal = "neutral_bullish"
    elif score >= 40:
        signal = "neutral"
    elif score >= 20:
        signal = "neutral_bearish"  # 偏冷,逢低布局(正面信号)
    else:
        signal = "bullish"  # 极冷,底部机会

    return {
        "score": round(score, 1),
        "signal": signal,
        "reason": f"大盘温度计{score}分({level})",
        "thermometer_score": round(score, 1),
        "level": level,
        "action": thermometer.get("action", ""),
        "weight": SIGNAL_WEIGHTS["market"],
    }


def _signal_to_action(signal: str, score: float) -> tuple[str, str]:
    """信号 + 评分 → 操作建议"""
    if signal == "bullish" and score >= 70:
        return ("add", "加仓")
    if signal == "bearish" and score >= 75:
        return ("take_profit", "止盈")  # 高位减仓
    if signal == "bearish" and score <= 25:
        return ("dca", "定投")  # 低位定投
    if signal == "neutral_bullish":
        return ("hold", "持有")
    if signal == "neutral_bearish":
        return ("watch", "关注")
    return ("hold", "持有")


def _merge_five_signals(
    technical: dict,
    fundamental: dict,
    news: dict,
    holdings: dict,
    market: dict,
) -> dict[str, Any]:
    """五信号加权融合"""
    signals = [technical, fundamental, news, holdings, market]
    total_weight = sum(s.get("weight", 0) for s in signals)
    weighted_score = sum(s.get("score", 50) * s.get("weight", 0) for s in signals)
    final_score = weighted_score / total_weight if total_weight > 0 else 50

    # 信号方向投票(权重)
    bull_weight = sum(s.get("weight", 0) for s in signals if "bullish" in s.get("signal", ""))
    bear_weight = sum(s.get("weight", 0) for s in signals if "bearish" in s.get("signal", ""))

    if bull_weight > bear_weight * 1.5:
        direction = "bullish"
    elif bear_weight > bull_weight * 1.5:
        direction = "bearish"
    else:
        direction = "neutral"

    signal, action = _signal_to_action(direction, final_score)

    # 综合理由
    reasons = []
    for s in signals:
        if s.get("reason"):
            weight_pct = s.get("weight", 0) * 100
            reasons.append(f"[{weight_pct:.0f}%]{s.get('reason', '')}")

    return {
        "final_score": round(final_score, 1),
        "direction": direction,
        "signal": signal,
        "action": action,
        "reason": " | ".join(reasons[:3]),  # 只显示前3条避免过长
        "signals": {
            "technical": technical,
            "fundamental": fundamental,
            "news": news,
            "holdings": holdings,
            "market": market,
        },
    }


async def analyze_fund_five_signals(
    fund_code: str,
    fund_name: str = "",
    cost_nav: float = 0.0,
    shares: float = 0.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """单只基金五信号分析

    Args:
        fund_code: 基金代码
        fund_name: 基金名称
        cost_nav: 持仓成本净值
        shares: 持有份额
        context: 调用方已抓取的数据(可选,P2 优化避免重复抓取)
            支持键:nav_history/quotes/holdings_data/news_data/thermometer/stock_quotes
            任一缺失则本函数自行抓取该键,存在则直接复用

    Returns:
        {
            "fund_code", "fund_name",
            "current_nav", "cost_nav", "pnl_pct",
            "five_signals": {...},
            "advice": {...}
        }
    """
    # P2 修复(2026-07-29):允许调用方传入已抓取数据,避免重复网络请求
    # multi_agent_fund 已抓取 5 源数据,本函数原再次抓取相同数据导致延迟翻倍
    ctx = context or {}

    # 延迟导入避免循环依赖
    from src.analysis.fund_holdings import analyze_fund_holdings
    from src.analysis.market_assessment import get_market_thermometer
    from src.analysis.news_aggregator import get_news_feed

    # 并行抓取 5 源数据(若 context 已提供则跳过对应抓取)
    async def _fetch_nav_history():
        return await asyncio.to_thread(ds2.get_fund_history_tencent, fund_code, "1y")

    async def _fetch_realtime():
        return await asyncio.to_thread(ds2.get_fund_realtime_tencent, [fund_code])

    async def _fetch_holdings():
        return await analyze_fund_holdings(fund_code)

    async def _fetch_news():
        return await get_news_feed(fund_code=fund_code)

    async def _fetch_market():
        return await get_market_thermometer()

    # 仅抓取 context 中缺失的数据
    pending_fetches = []
    pending_keys = []
    if "nav_history" not in ctx:
        pending_fetches.append(_fetch_nav_history())
        pending_keys.append("nav_history")
    if "quotes" not in ctx:
        pending_fetches.append(_fetch_realtime())
        pending_keys.append("quotes")
    if "holdings_data" not in ctx:
        pending_fetches.append(_fetch_holdings())
        pending_keys.append("holdings_data")
    if "news_data" not in ctx:
        pending_fetches.append(_fetch_news())
        pending_keys.append("news_data")
    if "thermometer" not in ctx:
        pending_fetches.append(_fetch_market())
        pending_keys.append("thermometer")

    fetched: list = []
    if pending_fetches:
        fetched = await asyncio.gather(*pending_fetches, return_exceptions=True)
    fetched_map = dict(zip(pending_keys, fetched))

    nav_history = ctx.get("nav_history", fetched_map.get("nav_history"))
    quotes = ctx.get("quotes", fetched_map.get("quotes"))
    holdings_data = ctx.get("holdings_data", fetched_map.get("holdings_data"))
    news_data = ctx.get("news_data", fetched_map.get("news_data"))
    thermometer = ctx.get("thermometer", fetched_map.get("thermometer"))

    # 处理异常
    if isinstance(nav_history, Exception):
        nav_history = []
        logger.warning(f"five_signals: nav_history failed: {nav_history}")
    if isinstance(quotes, Exception):
        quotes = []
        logger.warning(f"five_signals: quotes failed: {quotes}")
    if isinstance(holdings_data, Exception):
        holdings_data = {}
        logger.warning(f"five_signals: holdings failed: {holdings_data}")
    if isinstance(news_data, Exception):
        news_data = {}
        logger.warning(f"five_signals: news failed: {news_data}")
    if isinstance(thermometer, Exception):
        thermometer = {}
        logger.warning(f"five_signals: thermometer failed: {thermometer}")

    # 当前净值
    current_nav = 0.0
    if quotes and isinstance(quotes, list) and len(quotes) > 0:
        current_nav = float(quotes[0].get("nav", 0) or 0)

    # 重仓股实时行情(用于基本面信号)
    # P2:若 context 已提供 stock_quotes,直接复用避免重复抓取
    stock_quotes: list[dict] = ctx.get("stock_quotes", [])
    if not stock_quotes and holdings_data and holdings_data.get("holdings"):
        stock_codes = [h["code"] for h in holdings_data["holdings"] if h.get("code")]
        if stock_codes:
            try:
                stock_quotes = await asyncio.to_thread(ds2.get_realtime_quote_tencent, stock_codes)
            except Exception as e:
                logger.warning(f"five_signals: stock_quotes failed: {e}")

    # 计算5信号
    technical = _calc_technical_signal(nav_history)
    fundamental = _calc_fundamental_signal(
        holdings_data.get("holdings", []) if holdings_data else [],
        stock_quotes,
        nav_history,
    )
    news = _calc_news_signal(news_data)
    holdings = _calc_holdings_signal(holdings_data)
    market = _calc_market_signal(thermometer)

    five_signals = _merge_five_signals(technical, fundamental, news, holdings, market)

    # 盈亏
    pnl_pct = ((current_nav - cost_nav) / cost_nav * 100) if cost_nav > 0 and current_nav > 0 else 0.0
    pnl_amount = (current_nav - cost_nav) * shares if current_nav > 0 and cost_nav > 0 else 0.0

    # 基于五信号+盈亏的综合建议
    final_action = five_signals["action"]
    final_signal = five_signals["signal"]
    # 止盈线判断
    if cost_nav > 0 and current_nav > 0:
        if pnl_pct >= 20:
            final_signal = "take_profit"
            final_action = "止盈"
        elif pnl_pct <= -15 and final_signal in ("dca", "add"):
            final_action = "加仓"

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "current_nav": current_nav,
        "cost_nav": cost_nav,
        "shares": shares,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_amount": round(pnl_amount, 2),
        "five_signals": five_signals,
        "advice": {
            "signal": final_signal,
            "action": final_action,
            "score": five_signals["final_score"],
            "reason": five_signals["reason"],
        },
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def generate_fund_advice_v2(positions: list[dict]) -> dict[str, Any]:
    """五信号融合建议(主入口)

    Args:
        positions: 持仓列表 [{fund_code, fund_name, shares, cost_nav, ...}, ...]

    Returns:
        {
            "positions_advice": [...],  # 每只基金的五信号分析
            "summary": "...",            # 整体建议摘要
        }
    """
    if not positions:
        return {
            "positions_advice": [],
            "summary": "暂无基金持仓,请先添加持仓",
        }

    # 并行分析所有持仓(限制并发数避免过载)
    semaphore = asyncio.Semaphore(3)  # 同时最多3只

    async def _analyze_with_limit(pos: dict) -> dict:
        async with semaphore:
            return await analyze_fund_five_signals(
                fund_code=pos.get("fund_code", ""),
                fund_name=pos.get("fund_name", ""),
                cost_nav=float(pos.get("cost_nav", 0) or 0),
                shares=float(pos.get("shares", 0) or 0),
            )

    tasks = [_analyze_with_limit(p) for p in positions]
    positions_advice = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常
    clean_advice: list[dict] = []
    for i, result in enumerate(positions_advice):
        if isinstance(result, Exception):
            logger.warning(f"five_signals: position {positions[i].get('fund_code', '')} failed: {result}")
            # P1 修复(2026-08-01):不返回异常详情到客户端,避免泄露内部信息
            clean_advice.append({
                "fund_code": positions[i].get("fund_code", ""),
                "fund_name": positions[i].get("fund_name", ""),
                "error": "分析失败,请稍后重试",
                "advice": {"signal": "none", "action": "暂无建议", "reason": "分析失败,请稍后重试"},
            })
        else:
            clean_advice.append(result)

    # 整体摘要
    actions = [p.get("advice", {}).get("action", "") for p in clean_advice]
    take_profit_count = sum(1 for a in actions if "止盈" in a)
    add_count = sum(1 for a in actions if "加仓" in a or "定投" in a)
    hold_count = sum(1 for a in actions if "持有" in a)
    watch_count = sum(1 for a in actions if "关注" in a)
    summary_parts = []
    if take_profit_count:
        summary_parts.append(f"{take_profit_count} 只建议止盈")
    if add_count:
        summary_parts.append(f"{add_count} 只建议加仓/定投")
    if hold_count:
        summary_parts.append(f"{hold_count} 只建议持有")
    if watch_count:
        summary_parts.append(f"{watch_count} 只建议关注")
    summary = " | ".join(summary_parts) if summary_parts else "无明确建议"

    return {
        "positions_advice": clean_advice,
        "summary": summary,
        "method": "five_signals_v2",
        "weights": SIGNAL_WEIGHTS,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

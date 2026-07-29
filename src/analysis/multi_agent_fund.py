"""多智能体基金分析(P1-5)

7位专业分析师角色 + 多空辩论 + 风险辩论 + A股特有约束建模

7位分析师角色(针对基金投资决策辅助):
1. news(消息面分析师): 消息面情绪+热点事件+公告研报
2. fund(基金分析师): 基金净值趋势+盈亏+规模
3. sector(板块分析师): 板块轮动+重仓股板块暴露+集中度
4. technical(技术分析师): K线形态+均线+量价
5. fundamental(基本面分析师): 重仓股PE/PB/ROE+营收利润
6. risk(风险分析师): 限售解禁+股东减持+股权质押+融资融券
7. macro(宏观分析师): 大盘温度计+北向资金+政策面

输出:
- 7位分析师意见(opinions)
- 多空辩论(bull_bear_debate)
- 风险辩论(risk_debate)
- 组合经理决策(portfolio_manager_decision)
- 最终建议(action/confidence/target_price/stop_loss)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from loguru import logger

from src.core import data_source_v2 as ds2


# ===== 7角色定义 =====
ANALYST_ROLES = [
    {
        "role": "news",
        "name": "消息面分析师",
        "responsibility": "分析消息面情绪指数、热点事件、公告研报对基金的影响",
        "data_needed": ["news_data"],
    },
    {
        "role": "fund",
        "name": "基金分析师",
        "responsibility": "分析基金净值趋势、盈亏情况、规模变化、基金经理能力",
        "data_needed": ["fund_quote", "nav_history"],
    },
    {
        "role": "sector",
        "name": "板块分析师",
        "responsibility": "分析重仓股板块轮动、板块暴露度、集中度、净值影响预估",
        "data_needed": ["holdings_data"],
    },
    {
        "role": "technical",
        "name": "技术分析师",
        "responsibility": "分析基金净值K线形态、MA5/MA20均线、量价关系、技术指标",
        "data_needed": ["nav_history"],
    },
    {
        "role": "fundamental",
        "name": "基本面分析师",
        "responsibility": "分析重仓股PE/PB/ROE、营收利润增长、估值水平",
        "data_needed": ["holdings_data", "stock_quotes"],
    },
    {
        "role": "risk",
        "name": "风险分析师",
        "responsibility": "分析重仓股限售解禁、股东减持、股权质押、融资融券风险",
        "data_needed": ["holdings_data"],
    },
    {
        "role": "macro",
        "name": "宏观分析师",
        "responsibility": "分析大盘温度计、北向资金流向、宏观政策、市场情绪",
        "data_needed": ["thermometer"],
    },
]


def _build_fund_analysis_prompt(
    fund_code: str,
    fund_name: str,
    context: dict[str, Any],
    cost_nav: float = 0.0,
    shares: float = 0.0,
    mode: str = "deep",
) -> str:
    """构建基金多智能体分析prompt

    Args:
        fund_code: 基金代码
        fund_name: 基金名称
        context: 包含 news_data/fund_quote/nav_history/holdings_data/stock_quotes/thermometer/five_signals
        cost_nav: 持仓成本净值
        shares: 持有份额
        mode: deep/quick
    """
    fund_quote = context.get("fund_quote", {})
    nav_history = context.get("nav_history", [])
    news_data = context.get("news_data", {})
    holdings_data = context.get("holdings_data", {})
    stock_quotes = context.get("stock_quotes", [])
    thermometer = context.get("thermometer", {})
    five_signals = context.get("five_signals", {})

    # 基金基本信息
    current_nav = float(fund_quote.get("nav", 0) or 0)
    change_pct = float(fund_quote.get("change_pct", 0) or 0)
    pnl_pct = ((current_nav - cost_nav) / cost_nav * 100) if cost_nav > 0 and current_nav > 0 else 0.0

    # 净值趋势
    nav_summary = ""
    if nav_history:
        recent = nav_history[-10:]
        nav_summary = "\n".join(
            f"  {n.get('date', '')}: 净值{n.get('nav', 0)} 涨跌{n.get('change_pct', 0)}%"
            for n in recent
        )

    # 消息面
    news_summary = ""
    if news_data:
        sentiment_index = news_data.get("sentiment_index", 50)
        total_count = news_data.get("total_count", 0)
        hot_events = news_data.get("hot_events", [])[:5]
        news_summary = f"情绪指数: {sentiment_index}/100\n新闻总数: {total_count}\n热点事件:\n"
        news_summary += "\n".join(
            f"  [{e.get('sentiment', '')}] {e.get('title', '')}"
            for e in hot_events
        )

    # 重仓股
    holdings_summary = ""
    if holdings_data and holdings_data.get("holdings"):
        holdings = holdings_data.get("holdings", [])[:10]
        concentration = holdings_data.get("concentration", {})
        nav_impact = holdings_data.get("nav_impact", {})
        sector_rotation = holdings_data.get("sector_rotation", [])
        holdings_summary = f"前十大重仓股:\n"
        holdings_summary += "\n".join(
            f"  {h.get('name', '')}({h.get('code', '')}) 权重{h.get('weight', 0)}% 变化{h.get('change', '')}"
            for h in holdings
        )
        holdings_summary += f"\n集中度: Top5={concentration.get('top5_weight', 0)}% HHI={concentration.get('hhi', 0)}({concentration.get('level', '')})"
        holdings_summary += f"\n净值影响预估: {nav_impact.get('estimated_change_pct', 0)}%"
        holdings_summary += "\n板块轮动:"
        holdings_summary += "\n".join(
            f"  {s.get('sector', '')} 权重{s.get('weight', 0)}% 涨跌{s.get('change_pct', 0)}% 信号{s.get('signal', '')}"
            for s in sector_rotation[:5]
        )

    # 重仓股估值
    valuation_summary = ""
    if stock_quotes:
        pes = [float(q.get("pe_ttm", 0) or 0) for q in stock_quotes if q.get("pe_ttm")]
        pbs = [float(q.get("pb", 0) or 0) for q in stock_quotes if q.get("pb")]
        if pes:
            valuation_summary = f"重仓股PE平均: {sum(pes)/len(pes):.1f}\n重仓股PB平均: {sum(pbs)/len(pbs):.2f}" if pbs else f"重仓股PE平均: {sum(pes)/len(pes):.1f}"

    # 大盘温度计
    market_summary = ""
    if thermometer:
        market_summary = f"大盘温度计: {thermometer.get('score', 50)}/100 ({thermometer.get('level', '')})\n"
        market_summary += f"建议: {thermometer.get('action', '')}"
        components = thermometer.get("components", {})
        if components:
            market_summary += f"\n指数趋势: {components.get('index_trend', {}).get('score', 50)}"
            market_summary += f"\n板块轮动: {components.get('sector_rotation', {}).get('score', 50)}"
            market_summary += f"\n资金流向: {components.get('capital_flow', {}).get('score', 50)}"
            market_summary += f"\n市场情绪: {components.get('sentiment', {}).get('score', 50)}"

    # 五信号融合结果
    signals_summary = ""
    if five_signals:
        signals_summary = f"五信号综合评分: {five_signals.get('final_score', 50)}/100\n"
        signals_summary += f"方向: {five_signals.get('direction', '')}\n"
        signals_summary += f"信号: {five_signals.get('signal', '')}\n"
        signals_summary += f"操作: {five_signals.get('action', '')}"

    depth_instruction = ""
    if mode == "deep":
        depth_instruction = """
请进行深度分析,每个分析师需要给出详细论证过程,辩论环节需要多轮交锋。
"""
    else:
        depth_instruction = """
请进行快速分析,每个分析师给出核心结论即可,辩论环节简明扼要。
"""

    prompt = f"""你是一位专业的基金投资研究总监,现在需要对基金 {fund_code}({fund_name})进行全面的多智能体分析。

## 基金基本信息
- 基金代码: {fund_code}
- 基金名称: {fund_name}
- 当前净值: {current_nav}
- 当日涨跌: {change_pct}%
- 持仓成本: {cost_nav}
- 持有份额: {shares}
- 浮动盈亏: {pnl_pct:.2f}%

## 基金净值趋势(近10日)
{nav_summary}

## 消息面
{news_summary}

## 重仓股分析
{holdings_summary}

## 重仓股估值
{valuation_summary}

## 大盘环境
{market_summary}

## 五信号融合结果(参考)
{signals_summary}

{depth_instruction}

请模拟7位专业分析师分别从各自维度进行分析,然后进行多空辩论、风险辩论,最后由组合经理做出A股特有约束下的决策。

7位分析师角色:
1. news(消息面分析师): 分析消息面情绪指数、热点事件、公告研报对基金重仓股的影响
2. fund(基金分析师): 分析基金净值趋势、盈亏情况、规模变化、基金经理能力
3. sector(板块分析师): 分析重仓股板块轮动、板块暴露度、集中度、净值影响预估
4. technical(技术分析师): 分析基金净值K线形态、MA5/MA20均线、量价关系、技术指标
5. fundamental(基本面分析师): 分析重仓股PE/PB/ROE、营收利润增长、估值水平
6. risk(风险分析师): 分析重仓股限售解禁、股东减持、股权质押、融资融券风险
7. macro(宏观分析师): 分析大盘温度计、北向资金流向、宏观政策、市场情绪

A股特有约束:
- 涨跌停限制(±10%,科创板/创业板±20%)
- T+1交易制度
- 融资融券标的限制
- 北向资金每日额度限制
- 限售股解禁周期

请严格按照以下JSON格式返回分析结果(不要包含任何其他文字,只返回JSON):

{{
  "opinions": [
    {{
      "role": "news",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "消息面分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "fund",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "基金分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "sector",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "板块分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "technical",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "技术面分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "fundamental",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "基本面分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "risk",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "风险分析推理过程,结合限售解禁、股东减持、股权质押、融资融券等",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "macro",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "宏观分析推理过程,结合大盘温度计、北向资金、政策面等",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }}
  ],
  "bull_bear_debate": {{
    "topic": "{fund_name}多空辩论",
    "bull_arguments": ["看多论点1", "看多论点2", "看多论点3"],
    "bear_arguments": ["看空论点1", "看空论点2", "看空论点3"],
    "bull_score": 0-100,
    "bear_score": 0-100,
    "consensus": "BULLISH/BEARISH/NEUTRAL",
    "confidence": 0.0-1.0
  }},
  "risk_debate": {{
    "top_risks": ["风险1", "风险2", "风险3"],
    "mitigations": ["缓解措施1", "缓解措施2", "缓解措施3"],
    "risk_level": "LOW/MEDIUM/HIGH",
    "risk_score": 0-100
  }},
  "portfolio_manager_decision": {{
    "action": "BUY/SELL/HOLD/WAIT",
    "confidence": 0.0-1.0,
    "target_price": 0.0,
    "stop_loss_price": 0.0,
    "position_sizing": "建议仓位比例0-100%",
    "reasoning": "组合经理决策推理过程",
    "key_factors": ["关键因素1", "关键因素2", "关键因素3"]
  }},
  "final_recommendation": {{
    "action": "加仓/减仓/持有/定投/止盈",
    "confidence": 0.0-1.0,
    "time_horizon": "短期/中期/长期",
    "expected_return": "预期收益率",
    "key_risks": ["主要风险1", "主要风险2"]
  }}
}}
"""
    return prompt


def _parse_fund_analysis_response(response_text: str, fund_code: str) -> dict[str, Any]:
    """解析LLM返回的基金分析结果"""
    # 尝试提取JSON
    text = response_text.strip()
    # 去除markdown代码块
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个JSON对象
        import re
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                logger.error(f"解析基金分析结果失败: {e}")
                return _fallback_fund_result(fund_code, "LLM返回格式异常")
        else:
            return _fallback_fund_result(fund_code, "LLM返回无JSON")

    # 标准化字段
    opinions = result.get("opinions", [])
    bull_bear = result.get("bull_bear_debate", {})
    risk_debate = result.get("risk_debate", {})
    pm_decision = result.get("portfolio_manager_decision", {})
    final_rec = result.get("final_recommendation", {})

    return {
        "fund_code": fund_code,
        "agent_opinions": opinions,
        "bull_bear_debate": bull_bear,
        "risk_debate": risk_debate,
        "portfolio_manager_decision": pm_decision,
        "final_recommendation": final_rec,
        "action": pm_decision.get("action", "HOLD"),
        "confidence": float(pm_decision.get("confidence", 0.5) or 0.5),
        "analysis_mode": "multi_agent_fund",
        "analyst_count": len(opinions),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _fallback_fund_result(fund_code: str, reason: str) -> dict[str, Any]:
    """分析失败时的兜底结果"""
    return {
        "fund_code": fund_code,
        "agent_opinions": [],
        "bull_bear_debate": {},
        "risk_debate": {},
        "portfolio_manager_decision": {},
        "final_recommendation": {
            "action": "持有",
            "confidence": 0.0,
            "reason": reason,
        },
        "action": "HOLD",
        "confidence": 0.0,
        "analysis_mode": "multi_agent_fund",
        "analyst_count": 0,
        "error": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def analyze_fund_with_agents(
    fund_code: str,
    fund_name: str = "",
    cost_nav: float = 0.0,
    shares: float = 0.0,
    mode: str = "deep",
) -> dict[str, Any]:
    """基金多智能体分析(主入口)

    Args:
        fund_code: 基金代码
        fund_name: 基金名称
        cost_nav: 持仓成本净值
        shares: 持有份额
        mode: deep/quick

    Returns:
        多智能体分析结果
    """
    # 延迟导入避免循环依赖
    from src.analysis.fund_advisor_v2 import analyze_fund_five_signals
    from src.analysis.fund_holdings import analyze_fund_holdings
    from src.analysis.market_assessment import get_market_thermometer
    from src.analysis.news_aggregator import get_news_feed
    from src.core.llm_router import get_llm_router

    logger.info(f"starting multi-agent fund analysis for {fund_code}")

    # 1. 并行抓取所有数据
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

    nav_history, quotes, holdings_data, news_data, thermometer = await asyncio.gather(
        _fetch_nav_history(),
        _fetch_realtime(),
        _fetch_holdings(),
        _fetch_news(),
        _fetch_market(),
        return_exceptions=True,
    )

    # 处理异常
    if isinstance(nav_history, Exception):
        nav_history = []
    if isinstance(quotes, Exception):
        quotes = []
    if isinstance(holdings_data, Exception):
        holdings_data = {}
    if isinstance(news_data, Exception):
        news_data = {}
    if isinstance(thermometer, Exception):
        thermometer = {}

    fund_quote = quotes[0] if quotes and isinstance(quotes, list) and len(quotes) > 0 else {}

    # 2. 抓取重仓股实时行情
    stock_quotes: list[dict] = []
    if holdings_data and holdings_data.get("holdings"):
        stock_codes = [h["code"] for h in holdings_data["holdings"] if h.get("code")]
        if stock_codes:
            try:
                stock_quotes = await asyncio.to_thread(ds2.get_realtime_quote_tencent, stock_codes)
            except Exception as e:
                logger.warning(f"multi_agent_fund: stock_quotes failed: {e}")

    # 3. 计算五信号(用于参考)
    # P2 修复(2026-07-29):传入已抓取数据,避免 analyze_fund_five_signals 重复抓取 5 源数据
    # 原代码导致同一次请求发起 2 倍网络请求,延迟翻倍
    try:
        five_signals_result = await analyze_fund_five_signals(
            fund_code=fund_code,
            fund_name=fund_name,
            cost_nav=cost_nav,
            shares=shares,
            context={
                "nav_history": nav_history if isinstance(nav_history, list) else [],
                "quotes": quotes if isinstance(quotes, list) else [],
                "holdings_data": holdings_data if isinstance(holdings_data, dict) else {},
                "news_data": news_data if isinstance(news_data, dict) else {},
                "thermometer": thermometer if isinstance(thermometer, dict) else {},
                "stock_quotes": stock_quotes,
            },
        )
        five_signals = five_signals_result.get("five_signals", {})
    except Exception as e:
        logger.warning(f"multi_agent_fund: five_signals failed: {e}")
        five_signals = {}

    # 4. 构建 context
    context = {
        "fund_quote": fund_quote,
        "nav_history": nav_history if isinstance(nav_history, list) else [],
        "news_data": news_data if isinstance(news_data, dict) else {},
        "holdings_data": holdings_data if isinstance(holdings_data, dict) else {},
        "stock_quotes": stock_quotes,
        "thermometer": thermometer if isinstance(thermometer, dict) else {},
        "five_signals": five_signals,
    }

    # 5. 构建 prompt
    prompt = _build_fund_analysis_prompt(
        fund_code=fund_code,
        fund_name=fund_name,
        context=context,
        cost_nav=cost_nav,
        shares=shares,
        mode=mode,
    )

    messages = [
        {
            "role": "system",
            "content": "你是一位资深的基金投资研究总监,管理7位专业分析师团队(消息面/基金/板块/技术/基本面/风险/宏观)。你需要协调多空辩论和风险辩论,最终由组合经理做出A股特有约束下的基金决策。请始终以JSON格式返回分析结果。",
        },
        {"role": "user", "content": prompt},
    ]

    # 6. 调用 LLM
    try:
        router = get_llm_router()
        response = router.chat(
            messages,
            temperature=0.5 if mode == "deep" else 0.3,
            json_mode=True,
            timeout=120,
        )
        result = _parse_fund_analysis_response(response.content, fund_code)
        # 附带原始数据快照(供前端展示)
        result["data_snapshot"] = {
            "current_nav": float(fund_quote.get("nav", 0) or 0),
            "change_pct": float(fund_quote.get("change_pct", 0) or 0),
            "holdings_count": len(holdings_data.get("holdings", [])) if isinstance(holdings_data, dict) else 0,
            "news_count": news_data.get("total_count", 0) if isinstance(news_data, dict) else 0,
            "thermometer_score": thermometer.get("score", 0) if isinstance(thermometer, dict) else 0,
            "five_signals_score": five_signals.get("final_score", 0) if isinstance(five_signals, dict) else 0,
        }
        logger.info(f"multi-agent fund analysis completed for {fund_code}: action={result['action']}, analysts={result['analyst_count']}")
        return result
    except Exception as e:
        logger.error(f"multi-agent fund analysis failed for {fund_code}: {e}")
        return _fallback_fund_result(fund_code, f"多智能体分析失败: {e}")

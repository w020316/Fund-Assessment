from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv
from loguru import logger

from src.core.data_source_v2 import (
    get_capital_flow_detail,
    get_company_info,
    get_dragon_tiger,
    get_financial_snapshot,
    get_global_news,
    get_index_realtime,
    get_kline_mootdx,
    get_margin_trading,
    get_northbound_flow_realtime,
    get_realtime_quote_tencent,
    get_research_reports,
    get_sector_ranking,
    get_shareholder_count,
    get_stock_news,
)

load_dotenv()

_TTAPI_API_KEY = os.getenv("TTAPI_API_KEY", "6a1d8d71-83e1-a29a-73bd-054f629404a0")
_TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "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")
_TINYFISH_API_KEY = os.getenv("TINYFISH_API_KEY", "sk-tinyfish-uc4UsT0fms_HCoYfw7q-vZXkKF5w_usf")

_TTAPI_BASE_URL = "https://ttapi.io/v1"
_TINYFISH_BASE_URL = "https://api.tinyfish.io/v1"

_DEFAULT_MODEL = "gpt-4o"
_TIMEOUT = 60
_SEARCH_TIMEOUT = 15


def _gather_stock_data(stock_code: str) -> dict[str, Any]:
    data: dict[str, Any] = {}

    try:
        quotes = get_realtime_quote_tencent([stock_code])
        if quotes:
            data["quote"] = quotes[0]
    except Exception as e:
        logger.warning(f"gather quote failed: {e}")

    try:
        kline = get_kline_mootdx(stock_code, period="daily", count=60)
        if kline:
            data["kline_daily"] = kline[-30:]
    except Exception as e:
        logger.warning(f"gather kline failed: {e}")

    try:
        flow = get_capital_flow_detail(stock_code)
        if flow:
            data["capital_flow"] = flow
    except Exception as e:
        logger.warning(f"gather capital flow failed: {e}")

    try:
        financial = get_financial_snapshot(stock_code)
        if financial:
            data["financial"] = financial
    except Exception as e:
        logger.warning(f"gather financial failed: {e}")

    try:
        company = get_company_info(stock_code)
        if company:
            data["company"] = company
    except Exception as e:
        logger.warning(f"gather company info failed: {e}")

    try:
        margin = get_margin_trading(stock_code)
        if margin:
            data["margin"] = margin
    except Exception as e:
        logger.warning(f"gather margin failed: {e}")

    try:
        holder = get_shareholder_count(stock_code)
        if holder:
            data["shareholder"] = holder
    except Exception as e:
        logger.warning(f"gather shareholder failed: {e}")

    try:
        reports = get_research_reports(stock_code, page_size=5)
        if reports:
            data["research_reports"] = reports
    except Exception as e:
        logger.warning(f"gather research reports failed: {e}")

    try:
        news = get_stock_news(stock_code, page_size=8)
        if news:
            data["news"] = news
    except Exception as e:
        logger.warning(f"gather stock news failed: {e}")

    try:
        dragon_tiger = get_dragon_tiger()
        if dragon_tiger:
            stock_dt = [d for d in dragon_tiger if d.get("code") == stock_code]
            data["dragon_tiger"] = stock_dt if stock_dt else dragon_tiger[:10]
    except Exception as e:
        logger.warning(f"gather dragon tiger failed: {e}")

    try:
        northbound = get_northbound_flow_realtime()
        if northbound:
            data["northbound_flow"] = northbound
    except Exception as e:
        logger.warning(f"gather northbound flow failed: {e}")

    try:
        global_news = get_global_news()
        if global_news:
            data["global_news"] = global_news[:10]
    except Exception as e:
        logger.warning(f"gather global news failed: {e}")

    return data


def _build_analysis_prompt(stock_code: str, stock_data: dict[str, Any], search_news: list[dict], mode: str) -> str:
    quote = stock_data.get("quote", {})
    financial = stock_data.get("financial", {})
    capital_flow = stock_data.get("capital_flow", {})
    company = stock_data.get("company", {})
    margin = stock_data.get("margin", {})
    shareholder = stock_data.get("shareholder", {})
    kline = stock_data.get("kline_daily", [])
    reports = stock_data.get("research_reports", [])
    stock_news = stock_data.get("news", [])
    dragon_tiger = stock_data.get("dragon_tiger", [])
    northbound_flow = stock_data.get("northbound_flow", {})
    global_news = stock_data.get("global_news", [])

    kline_summary = ""
    if kline:
        recent = kline[-5:]
        kline_summary = "\n".join(
            f"  {d['date']}: 开{d['open']:.2f} 收{d['close']:.2f} 高{d['high']:.2f} 低{d['low']:.2f} 量{d['volume']:.0f}"
            for d in recent
        )

    reports_summary = ""
    if reports:
        reports_summary = "\n".join(
            f"  [{r.get('publish_date', '')}] {r.get('org_name', '')} - {r.get('title', '')} 评级:{r.get('rating', '')}"
            for r in reports[:5]
        )

    news_summary = ""
    all_news = list(stock_news) + list(search_news)
    if all_news:
        news_summary = "\n".join(
            f"  [{n.get('publish_time', n.get('published_date', ''))}] {n.get('title', '')} - {n.get('source', '')}"
            for n in all_news[:10]
        )

    dragon_tiger_summary = ""
    if dragon_tiger:
        dragon_tiger_summary = "\n".join(
            f"  {d.get('name', '')}({d.get('code', '')}) 涨跌:{d.get('change_pct', 0)}% 买入:{d.get('buy_amount', 0)} 卖出:{d.get('sell_amount', 0)} 净额:{d.get('net_amount', 0)} 原因:{d.get('reason', '')}"
            for d in dragon_tiger[:10]
        )

    northbound_summary = ""
    if northbound_flow:
        northbound_summary = f"  沪股通净流入: {northbound_flow.get('sh_net_inflow', 0)}  深股通净流入: {northbound_flow.get('sz_net_inflow', 0)}  北向合计净流入: {northbound_flow.get('total_net_inflow', 0)}"

    global_news_summary = ""
    if global_news:
        global_news_summary = "\n".join(
            f"  [{n.get('publish_time', '')}] {n.get('title', '')} - {n.get('source', '')}"
            for n in global_news[:8]
        )

    depth_instruction = ""
    if mode == "deep":
        depth_instruction = """
请进行深度分析，每个分析师需要给出详细论证过程，辩论环节需要多轮交锋。
"""
    else:
        depth_instruction = """
请进行快速分析，每个分析师给出核心结论即可，辩论环节简明扼要。
"""

    prompt = f"""你是一位专业的A股投资研究总监，现在需要对股票 {stock_code} 进行全面的多智能体分析。

## 实时行情
- 股票代码: {quote.get('code', stock_code)}
- 股票名称: {quote.get('name', '')}
- 当前价格: {quote.get('price', 0)}
- 涨跌幅: {quote.get('change_pct', 0)}%
- 成交量: {quote.get('volume', 0)}
- 成交额: {quote.get('amount', 0)}
- 换手率: {quote.get('turnover', 0)}
- PE(TTM): {quote.get('pe_ttm', 0)}
- PB: {quote.get('pb', 0)}
- 总市值: {quote.get('total_market_value', 0)}
- 流通市值: {quote.get('circ_market_value', 0)}
- 涨停价: {quote.get('high_limit', 0)}
- 跌停价: {quote.get('low_limit', 0)}

## 基本面数据
- PE(TTM): {financial.get('pe_ttm', 0)}
- PB: {financial.get('pb', 0)}
- ROE: {financial.get('roe', 0)}%
- 毛利率: {financial.get('gross_margin', 0)}%
- 净利率: {financial.get('net_margin', 0)}%
- 营收同比增长: {financial.get('revenue_yoy', 0)}%
- 净利润同比增长: {financial.get('profit_yoy', 0)}%

## 资金流向
- 主力净流入: {capital_flow.get('main_net_inflow', 0)}
- 主力流入占比: {capital_flow.get('main_inflow_pct', 0)}%
- 超大单净流入: {capital_flow.get('super_large_net_inflow', 0)}
- 大单净流入: {capital_flow.get('large_net_inflow', 0)}
- 中单净流入: {capital_flow.get('medium_net_inflow', 0)}
- 小单净流入: {capital_flow.get('small_net_inflow', 0)}

## 融资融券
- 融资余额: {margin.get('margin_balance', 0)}
- 融券余额: {margin.get('short_balance', 0)}

## 股东户数
- 户数: {shareholder.get('holder_num', 0)}
- 变化: {shareholder.get('change_pct', 0)}%

## 近5日K线
{kline_summary}

## 研报摘要
{reports_summary}

## 新闻动态
{news_summary}

## 龙虎榜数据
{dragon_tiger_summary}

## 北向资金
{northbound_summary}

## 全球宏观新闻
{global_news_summary}

{depth_instruction}

请模拟7位专业分析师分别从各自维度进行分析，然后进行多空辩论、风险辩论，最后由组合经理做出决策。

7位分析师角色：
1. fundamental（基本面分析师）：分析PE/PB/ROE/营收利润增长等财务指标
2. technical（技术面分析师）：分析K线形态、均线系统、量价关系、技术指标
3. sentiment（情绪面分析师）：分析市场情绪、换手率、融资融券、股东户数变化
4. news（新闻面分析师）：分析公司新闻、行业动态、研报评级
5. policy（政策分析师）：分析监管政策、行业政策、窗口指导、宏观政策对标的的影响
6. hot_money（游资追踪师）：分析龙虎榜、大单流向、主力资金动向、北向资金
7. lockup（解禁监控师）：分析限售股解禁、大股东减持、股权质押、融资融券风险

请严格按照以下JSON格式返回分析结果（不要包含任何其他文字，只返回JSON）：

{{
  "opinions": [
    {{
      "role": "fundamental",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "基本面分析推理过程",
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
      "role": "sentiment",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "情绪面分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "news",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "新闻面分析推理过程",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "policy",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "政策面分析推理过程，结合监管政策、行业政策、窗口指导等",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "hot_money",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "游资追踪分析推理过程，结合龙虎榜、大单流向、北向资金等",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }},
    {{
      "role": "lockup",
      "signal": "BULLISH/BEARISH/NEUTRAL",
      "confidence": 0.0-1.0,
      "reasoning": "解禁监控分析推理过程，结合限售解禁、股东减持、股权质押、融资融券等",
      "key_points": ["要点1", "要点2", "要点3"],
      "score": 0-100
    }}
  ],
  "bull_bear_debate": {{
    "topic": "{stock_code}多空辩论",
    "bull_arguments": ["看多论点1", "看多论点2", "看多论点3"],
    "bear_arguments": ["看空论点1", "看空论点2", "看空论点3"],
    "bull_score": 0-100,
    "bear_score": 0-100,
    "consensus": "BULLISH/BEARISH/NEUTRAL",
    "confidence": 0.0-1.0
  }},
  "risk_debate": {{
    "aggressive_position": {{
      "view": "激进方观点：看好后市，建议积极布局",
      "arguments": ["激进论点1", "激进论点2"],
      "suggested_position": 0.5-0.8,
      "risk_tolerance": "HIGH"
    }},
    "conservative_position": {{
      "view": "保守方观点：风险较大，建议谨慎观望",
      "arguments": ["保守论点1", "保守论点2"],
      "suggested_position": 0.0-0.2,
      "risk_tolerance": "LOW"
    }},
    "neutral_position": {{
      "view": "中性方观点：机会与风险并存，建议适度参与",
      "arguments": ["中性论点1", "中性论点2"],
      "suggested_position": 0.2-0.5,
      "risk_tolerance": "MEDIUM"
    }},
    "final_risk_level": "LOW/MEDIUM/HIGH",
    "final_suggested_position": 0.0-0.8
  }},
  "portfolio_decision": {{
    "action": "BUY/HOLD/SELL",
    "position_size": 0.0-0.8,
    "target_price": 目标价格,
    "stop_loss_price": 止损价格,
    "confidence": 0.0-1.0,
    "reasoning": "组合经理决策推理",
    "astock_constraints": {{
      "t_plus_1": "T+1交易约束说明",
      "price_limit": "涨跌停限制说明（主板10%/创业板20%/ST5%）",
      "min_lot": "最小交易单位100股",
      "warnings": ["A股特有风险提示1", "A股特有风险提示2"]
    }}
  }}
}}"""

    return prompt


def _call_ttapi(messages: list[dict[str, str]], model: str = _DEFAULT_MODEL, temperature: float = 0.7, json_mode: bool = True) -> str:
    url = f"{_TTAPI_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_TTAPI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            logger.error("TTAPI returned empty content")
            raise ValueError("TTAPI returned empty content")
        return content
    except requests.exceptions.Timeout:
        logger.error("TTAPI request timeout")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"TTAPI request failed: {e}")
        raise
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"TTAPI response parse failed: {e}")
        raise


def _parse_analysis_response(response_text: str, stock_code: str) -> dict[str, Any]:
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning("failed to parse AI response as JSON, attempting extraction")
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                result = json.loads(response_text[start:end])
            except json.JSONDecodeError as e:
                logger.error(f"JSON extraction failed: {e}")
                return _fallback_result(stock_code, "AI响应解析失败")
        else:
            return _fallback_result(stock_code, "AI响应解析失败")

    now = datetime.now().isoformat()

    opinions = []
    for op in result.get("opinions", []):
        opinions.append({
            "role": op.get("role", "unknown"),
            "stock_code": stock_code,
            "signal": op.get("signal", "NEUTRAL"),
            "confidence": float(op.get("confidence", 0.3)),
            "reasoning": op.get("reasoning", ""),
            "key_points": op.get("key_points", []),
            "score": float(op.get("score", 50)),
            "timestamp": now,
        })

    debate_raw = result.get("bull_bear_debate", result.get("debate", {}))
    debate = {
        "topic": debate_raw.get("topic", f"{stock_code}多空辩论"),
        "bull_arguments": debate_raw.get("bull_arguments", []),
        "bear_arguments": debate_raw.get("bear_arguments", []),
        "bull_score": float(debate_raw.get("bull_score", 50)),
        "bear_score": float(debate_raw.get("bear_score", 50)),
        "consensus": debate_raw.get("consensus", "NEUTRAL"),
        "confidence": float(debate_raw.get("confidence", 0.3)),
    }

    risk_debate_raw = result.get("risk_debate", {})
    risk_debate = {
        "aggressive_position": risk_debate_raw.get("aggressive_position", {
            "view": "激进方观点",
            "arguments": [],
            "suggested_position": 0.6,
            "risk_tolerance": "HIGH",
        }),
        "conservative_position": risk_debate_raw.get("conservative_position", {
            "view": "保守方观点",
            "arguments": [],
            "suggested_position": 0.1,
            "risk_tolerance": "LOW",
        }),
        "neutral_position": risk_debate_raw.get("neutral_position", {
            "view": "中性方观点",
            "arguments": [],
            "suggested_position": 0.3,
            "risk_tolerance": "MEDIUM",
        }),
        "final_risk_level": risk_debate_raw.get("final_risk_level", "MEDIUM"),
        "final_suggested_position": float(risk_debate_raw.get("final_suggested_position", 0.3)),
    }

    decision_raw = result.get("portfolio_decision", result.get("decision", {}))
    action = decision_raw.get("action", "HOLD")
    position_size = float(decision_raw.get("position_size", 0.0))
    confidence = float(decision_raw.get("confidence", 0.3))
    target_price = float(decision_raw.get("target_price", 0))
    stop_loss_price = float(decision_raw.get("stop_loss_price", 0))

    astock_constraints_raw = decision_raw.get("astock_constraints", {})
    astock_constraints = {
        "t_plus_1": astock_constraints_raw.get("t_plus_1", "A股实行T+1交易制度，当日买入次日方可卖出"),
        "price_limit": astock_constraints_raw.get("price_limit", "主板涨跌幅10%，创业板/科创板20%，ST股5%"),
        "min_lot": astock_constraints_raw.get("min_lot", "最小交易单位100股"),
        "warnings": astock_constraints_raw.get("warnings", []),
    }

    risk_level = "LOW"
    warnings: list[str] = []
    adjusted_position = position_size

    avg_confidence = 0.0
    if opinions:
        avg_confidence = sum(op["confidence"] for op in opinions) / len(opinions)

    if avg_confidence < 0.3:
        risk_level = "HIGH"
        warnings.append("分析师置信度普遍较低，建议谨慎")
        adjusted_position *= 0.5
    elif avg_confidence < 0.5:
        risk_level = "MEDIUM"
        warnings.append("分析师置信度中等，建议适度控制仓位")

    if debate["confidence"] < 0.3:
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        warnings.append("多空辩论分歧较大，方向不明确")

    bullish_count = sum(1 for op in opinions if op["signal"] == "BULLISH")
    bearish_count = sum(1 for op in opinions if op["signal"] == "BEARISH")
    if abs(bullish_count - bearish_count) <= 1 and len(opinions) >= 3:
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        warnings.append("分析师意见分歧较大，建议降低仓位")
        adjusted_position *= 0.7

    if risk_debate["final_risk_level"] == "HIGH":
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        adjusted_position = min(adjusted_position, risk_debate["final_suggested_position"])
        warnings.append("风险辩论结论为高风险，建议降低仓位")

    if action == "BUY" and adjusted_position > 0.5:
        adjusted_position = min(adjusted_position, 0.5)
        warnings.append("单只股票买入仓位上限50%")

    if action == "SELL":
        adjusted_position = min(adjusted_position, 0.5)
        warnings.append("卖出仓位上限50%")

    for w in astock_constraints.get("warnings", []):
        if w and w not in warnings:
            warnings.append(w)

    risk_assessment = {
        "risk_level": risk_level,
        "warnings": warnings,
        "adjusted_position": round(adjusted_position, 2),
        "avg_confidence": round(avg_confidence, 2),
    }

    reasoning = decision_raw.get("reasoning", "")
    if warnings:
        reasoning += "；风控提示：" + "；".join(warnings)

    return {
        "stock_code": stock_code,
        "action": action,
        "position_size": round(adjusted_position, 2),
        "confidence": round(confidence, 2),
        "reasoning": reasoning,
        "agent_opinions": opinions,
        "debate_result": debate,
        "risk_debate": risk_debate,
        "risk_assessment": risk_assessment,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
        "astock_constraints": astock_constraints,
        "timestamp": now,
    }


def _fallback_result(stock_code: str, reason: str) -> dict[str, Any]:
    now = datetime.now().isoformat()
    fallback_roles = ["fundamental", "technical", "sentiment", "news", "policy", "hot_money", "lockup"]
    fallback_opinions = []
    for role in fallback_roles:
        fallback_opinions.append({
            "role": role,
            "stock_code": stock_code,
            "signal": "NEUTRAL",
            "confidence": 0.1,
            "reasoning": f"[降级] {reason}",
            "key_points": ["分析降级"],
            "score": 50.0,
            "timestamp": now,
        })
    return {
        "stock_code": stock_code,
        "action": "HOLD",
        "position_size": 0.0,
        "confidence": 0.0,
        "reasoning": f"[降级] {reason}，默认持有观望",
        "agent_opinions": fallback_opinions,
        "debate_result": {
            "topic": f"{stock_code}多空辩论",
            "bull_arguments": [],
            "bear_arguments": [],
            "bull_score": 50.0,
            "bear_score": 50.0,
            "consensus": "NEUTRAL",
            "confidence": 0.1,
        },
        "risk_debate": {
            "aggressive_position": {
                "view": "激进方观点",
                "arguments": [],
                "suggested_position": 0.6,
                "risk_tolerance": "HIGH",
            },
            "conservative_position": {
                "view": "保守方观点",
                "arguments": [],
                "suggested_position": 0.1,
                "risk_tolerance": "LOW",
            },
            "neutral_position": {
                "view": "中性方观点",
                "arguments": [],
                "suggested_position": 0.3,
                "risk_tolerance": "MEDIUM",
            },
            "final_risk_level": "HIGH",
            "final_suggested_position": 0.0,
        },
        "risk_assessment": {
            "risk_level": "HIGH",
            "warnings": [f"分析降级: {reason}"],
            "adjusted_position": 0.0,
            "avg_confidence": 0.1,
        },
        "target_price": 0,
        "stop_loss_price": 0,
        "astock_constraints": {
            "t_plus_1": "A股实行T+1交易制度，当日买入次日方可卖出",
            "price_limit": "主板涨跌幅10%，创业板/科创板20%，ST股5%",
            "min_lot": "最小交易单位100股",
            "warnings": [f"分析降级: {reason}"],
        },
        "timestamp": now,
    }


def _search_tavily(query: str) -> list[dict]:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": _TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": 8,
    }
    try:
        resp = requests.post(url, json=payload, timeout=_SEARCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "content": r.get("content", "")[:300],
                "url": r.get("url", ""),
                "source": "tavily",
                "publish_time": r.get("published_date", ""),
            }
            for r in results
        ]
    except Exception as e:
        logger.warning(f"tavily search failed: {e}")
        return []


def _search_tinyfish(query: str) -> list[dict]:
    url = f"{_TINYFISH_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_TINYFISH_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "system",
                "content": "你是一个新闻搜索助手。根据用户查询，返回相关的财经新闻。请以JSON数组格式返回，每个元素包含title、content、url、source字段。",
            },
            {"role": "user", "content": f"搜索关于以下主题的最新财经新闻：{query}"},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=_SEARCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return []
        parsed = json.loads(content)
        items = parsed.get("results", parsed.get("news", []))
        if isinstance(items, list):
            return [
                {
                    "title": item.get("title", ""),
                    "content": item.get("content", "")[:300],
                    "url": item.get("url", ""),
                    "source": f"tinyfish:{item.get('source', '')}",
                    "publish_time": item.get("published_date", ""),
                }
                for item in items
            ]
        return []
    except Exception as e:
        logger.warning(f"tinyfish search failed: {e}")
        return []


def search_news(query: str) -> list[dict]:
    results = _search_tavily(query)
    if results:
        return results

    logger.info("tavily search returned empty, falling back to tinyfish")
    results = _search_tinyfish(query)
    if results:
        return results

    logger.warning("all news search sources failed")
    return []


def analyze_stock(stock_code: str, mode: str = "deep") -> dict[str, Any]:
    logger.info(f"starting {mode} analysis for {stock_code}")

    stock_data = _gather_stock_data(stock_code)
    if not stock_data:
        logger.warning(f"no data gathered for {stock_code}")
        return _fallback_result(stock_code, "无法获取股票数据")

    search_results = search_news(f"{stock_code} 股票 最新消息")

    prompt = _build_analysis_prompt(stock_code, stock_data, search_results, mode)

    messages = [
        {
            "role": "system",
            "content": "你是一位资深的A股投资研究总监，擅长基本面、技术面、情绪面、新闻面、政策面、游资追踪和解禁监控分析。你的分析客观严谨，所有结论都有数据支撑。请始终以JSON格式返回分析结果。",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response_text = _call_ttapi(messages, temperature=0.5 if mode == "deep" else 0.3)
        result = _parse_analysis_response(response_text, stock_code)
        logger.info(f"analysis completed for {stock_code}: action={result['action']}, confidence={result['confidence']}")
        return result
    except Exception as e:
        logger.error(f"AI analysis failed for {stock_code}: {e}")
        return _fallback_result(stock_code, f"AI分析失败: {e}")


def quick_analysis(stock_code: str) -> dict[str, Any]:
    logger.info(f"starting quick analysis for {stock_code}")

    stock_data = _gather_stock_data(stock_code)
    if not stock_data:
        return _fallback_result(stock_code, "无法获取股票数据")

    search_results = search_news(f"{stock_code} 最新")

    prompt = _build_analysis_prompt(stock_code, stock_data, search_results, "quick")

    messages = [
        {
            "role": "system",
            "content": "你是一位A股投资分析师，请快速给出核心结论。以JSON格式返回，格式与深度分析相同但推理更简洁。",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response_text = _call_ttapi(messages, temperature=0.3)
        result = _parse_analysis_response(response_text, stock_code)
        logger.info(f"quick analysis completed for {stock_code}: action={result['action']}")
        return result
    except Exception as e:
        logger.error(f"quick analysis failed for {stock_code}: {e}")
        return _fallback_result(stock_code, f"快速分析失败: {e}")


def multi_analyze(stock_code: str, mode: str = "deep") -> dict[str, Any]:
    logger.info(f"starting multi-agent {mode} analysis for {stock_code}")

    stock_data = _gather_stock_data(stock_code)
    if not stock_data:
        logger.warning(f"no data gathered for {stock_code}")
        return _fallback_result(stock_code, "无法获取股票数据")

    search_results = search_news(f"{stock_code} 股票 最新消息 政策")

    prompt = _build_analysis_prompt(stock_code, stock_data, search_results, mode)

    messages = [
        {
            "role": "system",
            "content": "你是一位资深的A股投资研究总监，管理7位专业分析师团队（基本面、技术面、情绪面、新闻面、政策面、游资追踪、解禁监控）。你需要协调多空辩论和风险辩论，最终由组合经理做出A股特有约束下的决策。请始终以JSON格式返回分析结果。",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response_text = _call_ttapi(messages, temperature=0.5 if mode == "deep" else 0.3)
        result = _parse_analysis_response(response_text, stock_code)
        result["analysis_mode"] = "multi_agent"
        result["analyst_count"] = len(result.get("agent_opinions", []))
        logger.info(f"multi-agent analysis completed for {stock_code}: action={result['action']}, analysts={result['analyst_count']}")
        return result
    except Exception as e:
        logger.error(f"multi-agent analysis failed for {stock_code}: {e}")
        return _fallback_result(stock_code, f"多智能体分析失败: {e}")


def analyze_portfolio(positions: list[dict]) -> dict[str, Any]:
    logger.info(f"starting portfolio analysis for {len(positions)} positions")

    position_analyses: list[dict[str, Any]] = []
    for pos in positions:
        symbol = pos.get("symbol", pos.get("code", ""))
        if not symbol:
            continue
        try:
            quotes = get_realtime_quote_tencent([symbol])
            if quotes:
                pos["current_price"] = quotes[0].get("price", pos.get("current_price", 0))
                pos["name"] = quotes[0].get("name", pos.get("name", ""))
            analysis = quick_analysis(symbol)
            position_analyses.append({
                "symbol": symbol,
                "name": pos.get("name", ""),
                "quantity": pos.get("quantity", 0),
                "cost_price": pos.get("cost_price", 0),
                "current_price": pos.get("current_price", 0),
                "profit": pos.get("profit", 0),
                "profit_pct": pos.get("profit_pct", 0),
                "action": analysis.get("action", "HOLD"),
                "confidence": analysis.get("confidence", 0),
                "target_price": analysis.get("target_price", 0),
                "stop_loss_price": analysis.get("stop_loss_price", 0),
                "risk_level": analysis.get("risk_assessment", {}).get("risk_level", "MEDIUM"),
            })
        except Exception as e:
            logger.warning(f"portfolio analysis failed for {symbol}: {e}")
            position_analyses.append({
                "symbol": symbol,
                "name": pos.get("name", ""),
                "quantity": pos.get("quantity", 0),
                "cost_price": pos.get("cost_price", 0),
                "current_price": pos.get("current_price", 0),
                "profit": pos.get("profit", 0),
                "profit_pct": pos.get("profit_pct", 0),
                "action": "HOLD",
                "confidence": 0,
                "target_price": 0,
                "stop_loss_price": 0,
                "risk_level": "HIGH",
            })

    total_value = sum(
        p.get("current_price", 0) * p.get("quantity", 0)
        for p in positions
    )
    position_weights: dict[str, float] = {}
    for p in positions:
        symbol = p.get("symbol", p.get("code", ""))
        value = p.get("current_price", 0) * p.get("quantity", 0)
        position_weights[symbol] = round(value / total_value, 4) if total_value > 0 else 0

    concentration_risk = "LOW"
    max_weight = max(position_weights.values()) if position_weights else 0
    if max_weight > 0.4:
        concentration_risk = "HIGH"
    elif max_weight > 0.25:
        concentration_risk = "MEDIUM"

    portfolio_prompt = f"""你是一位专业的A股投资组合经理，现在需要分析用户的持仓组合并给出建议。

## 持仓明细
{json.dumps(position_analyses, ensure_ascii=False, indent=2)}

## 仓位权重
{json.dumps(position_weights, ensure_ascii=False, indent=2)}

## 组合统计
- 总市值: {total_value:.2f}
- 持仓数量: {len(positions)}
- 最大持仓权重: {max_weight:.2%}
- 集中度风险: {concentration_risk}

请分析以下内容并以JSON格式返回：
1. 持仓集中度风险评估
2. 板块暴露分析
3. 再平衡建议
4. 止损/止盈建议
5. 组合整体风险评分

{{
  "concentration_risk": {{
    "level": "LOW/MEDIUM/HIGH",
    "max_weight": {max_weight},
    "overweight_positions": ["权重过高的股票代码"],
    "suggestion": "集中度风险建议"
  }},
  "sector_exposure": {{
    "sectors": [
      {{"name": "板块名", "weight": 0.0-1.0, "risk": "LOW/MEDIUM/HIGH"}}
    ],
    "concentrated_sectors": ["过度集中的板块"],
    "suggestion": "板块暴露建议"
  }},
  "rebalancing": [
    {{"symbol": "股票代码", "action": "REDUCE/INCREASE/EXIT", "reason": "原因", "target_weight": 0.0-1.0}}
  ],
  "stop_loss_take_profit": [
    {{"symbol": "股票代码", "current_price": 当前价, "stop_loss": 止损价, "take_profit": 止盈价, "reason": "原因"}}
  ],
  "overall_risk_score": {{
    "score": 0-100,
    "level": "LOW/MEDIUM/HIGH",
    "summary": "组合整体风险评估",
    "key_risks": ["风险1", "风险2"],
    "suggestions": ["建议1", "建议2"]
  }}
}}"""

    messages = [
        {
            "role": "system",
            "content": "你是一位专业的A股投资组合经理，擅长组合风险管理和资产配置。请以JSON格式返回分析结果。",
        },
        {"role": "user", "content": portfolio_prompt},
    ]

    try:
        response_text = _call_ttapi(messages, temperature=0.3)
        try:
            portfolio_result = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                portfolio_result = json.loads(response_text[start:end])
            else:
                portfolio_result = {}
    except Exception as e:
        logger.error(f"portfolio AI analysis failed: {e}")
        portfolio_result = {}

    return {
        "positions": position_analyses,
        "position_weights": position_weights,
        "total_value": total_value,
        "concentration_risk_level": concentration_risk,
        "portfolio_analysis": portfolio_result,
        "timestamp": datetime.now().isoformat(),
    }


def get_market_outlook() -> dict[str, Any]:
    logger.info("starting market outlook analysis")

    market_data: dict[str, Any] = {}

    try:
        indices = get_index_realtime()
        if indices:
            market_data["indices"] = indices
    except Exception as e:
        logger.warning(f"gather index data failed: {e}")

    try:
        northbound = get_northbound_flow_realtime()
        if northbound:
            market_data["northbound_flow"] = northbound
    except Exception as e:
        logger.warning(f"gather northbound flow failed: {e}")

    try:
        sectors = get_sector_ranking()
        if sectors:
            market_data["hot_sectors"] = sectors[:10]
            market_data["cold_sectors"] = sectors[-5:] if len(sectors) > 10 else []
    except Exception as e:
        logger.warning(f"gather sector ranking failed: {e}")

    try:
        global_news = get_global_news()
        if global_news:
            market_data["global_news"] = global_news[:8]
    except Exception as e:
        logger.warning(f"gather global news failed: {e}")

    if not market_data:
        return {
            "outlook": "UNKNOWN",
            "confidence": 0.0,
            "summary": "无法获取市场数据",
            "timestamp": datetime.now().isoformat(),
        }

    outlook_prompt = f"""你是一位专业的A股市场策略分析师，请根据以下市场数据给出今日市场展望。

## 市场数据
{json.dumps(market_data, ensure_ascii=False, indent=2)}

请以JSON格式返回市场展望：

{{
  "outlook": "BULLISH/BEARISH/NEUTRAL",
  "confidence": 0.0-1.0,
  "summary": "市场整体展望概述",
  "index_analysis": [
    {{"name": "指数名称", "change_pct": 涨跌幅, "trend": "UP/DOWN/FLAT", "comment": "点评"}}
  ],
  "sector_rotation": {{
    "hot_sectors": ["热门板块1", "热门板块2", "热门板块3"],
    "cold_sectors": ["冷门板块1", "冷门板块2"],
    "rotation_signal": "板块轮动信号描述"
  }},
  "capital_flow": {{
    "northbound": "北向资金流向描述",
    "signal": "INFLOW/OUTFLOW/NEUTRAL"
  }},
  "risk_alerts": ["风险提示1", "风险提示2"],
  "opportunities": ["机会1", "机会2"],
  "strategy": "操作策略建议"
}}"""

    messages = [
        {
            "role": "system",
            "content": "你是一位专业的A股市场策略分析师，擅长大盘走势判断和板块轮动分析。请以JSON格式返回分析结果。",
        },
        {"role": "user", "content": outlook_prompt},
    ]

    try:
        response_text = _call_ttapi(messages, temperature=0.3, json_mode=True)
        try:
            outlook_result = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                outlook_result = json.loads(response_text[start:end])
            else:
                outlook_result = {}
    except Exception as e:
        logger.warning(f"market outlook json_mode failed: {e}, retrying without json_mode")
        try:
            response_text = _call_ttapi(messages, temperature=0.3, json_mode=False)
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                outlook_result = json.loads(response_text[start:end])
            else:
                outlook_result = {}
        except Exception as e2:
            logger.error(f"market outlook AI analysis failed: {e2}")
            outlook_result = {}

    return {
        "market_data": market_data,
        "outlook_analysis": outlook_result,
        "timestamp": datetime.now().isoformat(),
    }

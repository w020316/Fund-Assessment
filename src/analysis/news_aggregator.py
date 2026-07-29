"""消息面聚合引擎 - 4源并行抓取+去重+分类+LLM总结

数据源:
1. 财经新闻(data_source_v2.get_global_news / get_stock_news)
2. 公告研报(data_source_v2.get_research_reports)
3. 舆情热度(akshare stock_hot_follow_em / stock_hot_tweet_em)
4. AI检索(ai_service._search_tavily + LLM总结)

输出:结构化消息面摘要(热点事件/情绪指数/关键新闻)
"""
from __future__ import annotations

import asyncio
import difflib
from datetime import datetime
from typing import Any

from loguru import logger

from src.core import data_source_v2 as ds2

try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False

# SnowNLP 中文情感分析(借鉴 isnowany/snownlp 开源项目)
# 与关键词法并行使用,SnowNLP 失败时降级到纯关键词法
try:
    from snownlp import SnowNLP
    _HAS_SNOWNLP = True
except ImportError:
    SnowNLP = None  # 占位符,便于测试 mock(用 patch.object 时模块属性必须存在)
    _HAS_SNOWNLP = False


# ===== 情绪分类关键词 =====
_POSITIVE_KEYWORDS = [
    "利好", "上涨", "增长", "突破", "创新高", "超预期", "获批", "中标",
    "回购", "增持", "分红", "盈利", "景气", "回暖", "复苏", "刺激",
    "支持", "扶持", "补贴", "减税", "降准", "降息", "改革", "开放",
]

_NEGATIVE_KEYWORDS = [
    "利空", "下跌", "下滑", "亏损", "暴跌", "预警", "减持", "质押",
    "违规", "处罚", "退市", "停牌", "风险", "危机", "衰退", "紧缩",
    "加息", "通胀", "制裁", "贸易战", "疫情", "灾害", "事故", "爆雷",
]


def _classify_sentiment(title: str, content: str = "") -> str:
    """基于关键词 + SnowNLP 语义融合分类新闻情绪:利好/利空/中性

    决策逻辑(借鉴 isnowany/snownlp 朴素贝叶斯语义分析):
    1. 关键词法主决策:pos_count vs neg_count(保持原行为,向后兼容)
    2. SnowNLP 反转检测:仅在关键词法判定方向与 SnowNLP 强烈相反时覆盖
       - 关键词法判利好,但 SnowNLP 分值<0.3 → 覆盖为利空(识别"超预期下滑"等语义反转)
       - 关键词法判利空,但 SnowNLP 分值>0.7 → 覆盖为利好
    3. 关键词法判中性(pos==neg)时,SnowNLP 进一步细分
    4. SnowNLP 不可用/异常 → 退化为纯关键词法(完全向后兼容)
    """
    text = f"{title} {content}"
    pos_count = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    neg_count = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)

    # SnowNLP 语义分值(None 表示不可用/失败,此时退化为纯关键词法)
    snownlp_score = _snownlp_sentiment_score(text) if _HAS_SNOWNLP else None

    # 关键词法主决策
    if pos_count > neg_count:
        # 关键词倾向利好,SnowNLP 强烈看空则反转(语义反转检测)
        if snownlp_score is not None and snownlp_score < 0.3:
            return "利空"
        return "利好"

    if neg_count > pos_count:
        # 关键词倾向利空,SnowNLP 强烈看多则反转
        if snownlp_score is not None and snownlp_score > 0.7:
            return "利好"
        return "利空"

    # pos == neg(中性情况),用 SnowNLP 进一步细分
    if snownlp_score is not None:
        if snownlp_score > 0.6:
            return "利好"
        if snownlp_score < 0.4:
            return "利空"
    return "中性"


def _snownlp_sentiment_score(text: str) -> float | None:
    """SnowNLP 情感分值(0~1,>0.5 偏积极),供外部调用计算板块情绪均值。

    借鉴 isnowany/snownlp 项目,用于将离散三分类升级为连续分值,
    便于"板块情绪指数"等聚合计算(连续值可求均值,离散值不可)。
    SnowNLP 不可用/计算失败时返回 None(调用方负责降级)。
    """
    if not _HAS_SNOWNLP or not text.strip():
        return None
    try:
        return float(SnowNLP(text[:500]).sentiments)
    except Exception:
        return None


def _deduplicate(news_list: list[dict], similarity_threshold: float = 0.8) -> list[dict]:
    """标题相似度去重,保留最新的一条"""
    if not news_list:
        return []
    result: list[dict] = []
    for item in news_list:
        title = item.get("title", "")
        is_dup = False
        for existing in result:
            ratio = difflib.SequenceMatcher(None, title, existing.get("title", "")).ratio()
            if ratio >= similarity_threshold:
                is_dup = True
                break
        if not is_dup:
            result.append(item)
    return result


def _fetch_finance_news(limit: int = 20) -> list[dict]:
    """财经新闻(东方财富+新浪降级)"""
    try:
        data = ds2.get_global_news()
        items = data[:limit] if data else []
        for item in items:
            item["category"] = "财经新闻"
        return items
    except Exception as e:
        logger.warning(f"消息面:财经新闻抓取失败: {e}")
        return []


def _fetch_research_reports(limit: int = 10) -> list[dict]:
    """券商研报"""
    try:
        data = ds2.get_research_reports(page=1, page_size=limit)
        for item in data:
            item["category"] = "研报"
        return data
    except Exception as e:
        logger.warning(f"消息面:研报抓取失败: {e}")
        return []


def _fetch_sentiment_hot(limit: int = 10) -> list[dict]:
    """舆情热度(东方财富人气榜+讨论榜)"""
    if not _HAS_AKSHARE:
        return []
    result: list[dict] = []
    try:
        df = ak.stock_hot_follow_em()
        for _, row in df.head(limit).iterrows():
            result.append({
                "title": f"{row.get('股票名称', '')} 人气上升",
                "content": f"关注度:{row.get('关注度', '—')},排名上升",
                "source": "东方财富人气榜",
                "publish_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "category": "舆情热度",
                "stock_code": row.get("股票代码", ""),
                "stock_name": row.get("股票名称", ""),
            })
    except Exception as e:
        logger.warning(f"消息面:人气榜抓取失败: {e}")
    return result


def _fetch_stock_news(stock_code: str, limit: int = 15) -> list[dict]:
    """指定股票的相关新闻"""
    try:
        data = ds2.get_stock_news(stock_code, page=1, page_size=limit)
        for item in data:
            item["category"] = "个股新闻"
            item["stock_code"] = stock_code
        return data
    except Exception as e:
        logger.warning(f"消息面:个股新闻抓取失败: {e}")
        return []


def _ai_search(query: str) -> list[dict]:
    """AI检索(Tavily)"""
    try:
        from src.core.ai_service import _search_tavily
        results = _search_tavily(query)
        for item in results:
            item["category"] = "AI检索"
        return results
    except Exception as e:
        logger.warning(f"消息面:AI检索失败: {e}")
        return []


def _build_news_item(raw: dict) -> dict:
    """统一新闻项结构"""
    title = raw.get("title", "")
    content = raw.get("content", "")
    return {
        "title": title,
        "content": content[:200] if content else "",
        "source": raw.get("source", ""),
        "publish_time": raw.get("publish_time", ""),
        "category": raw.get("category", ""),
        "sentiment": _classify_sentiment(title, content),
        "stock_code": raw.get("stock_code", ""),
        "stock_name": raw.get("stock_name", ""),
        "url": raw.get("url", ""),
    }


def _calc_sentiment_index(news_items: list[dict]) -> int:
    """计算情绪指数 0-100(50为中性,>50偏多,<50偏空)"""
    if not news_items:
        return 50
    pos = sum(1 for n in news_items if n.get("sentiment") == "利好")
    neg = sum(1 for n in news_items if n.get("sentiment") == "利空")
    total = len(news_items)
    # 情绪指数 = 50 + (利好占比 - 利空占比) * 50
    index = 50 + (pos - neg) / total * 50
    return max(0, min(100, int(index)))


async def get_news_feed(sector: str = "", fund_code: str = "") -> dict:
    """消息面流(支持板块/基金过滤)

    Args:
        sector: 板块名称过滤(可选)
        fund_code: 基金代码(可选,用于查询其重仓股新闻)

    Returns:
        {hot_events, sentiment_index, key_news}
    """
    # 并行抓取4源
    finance_news, reports, sentiment_hot = await asyncio.gather(
        asyncio.to_thread(_fetch_finance_news, 20),
        asyncio.to_thread(_fetch_research_reports, 10),
        asyncio.to_thread(_fetch_sentiment_hot, 10),
    )

    all_news = finance_news + reports + sentiment_hot

    # 如果指定了基金代码,查询重仓股新闻
    if fund_code:
        try:
            from src.analysis.fund_holdings import get_fund_holdings
            holdings = await asyncio.to_thread(get_fund_holdings, fund_code)
            top_stocks = holdings.get("top_holdings", [])[:5]
            stock_news_tasks = [
                asyncio.to_thread(_fetch_stock_news, s["code"], 5)
                for s in top_stocks
                if s.get("code")
            ]
            if stock_news_tasks:
                # P2 修复(2026-07-29):原未传 return_exceptions=True
                # 任一股票新闻抓取抛异常会导致整个 gather 失败,丢失其他成功结果
                # 改为:隔离异常,仅记录失败股票,保留成功的新闻数据
                stock_news_results = await asyncio.gather(*stock_news_tasks, return_exceptions=True)
                for idx, news_list in enumerate(stock_news_results):
                    if isinstance(news_list, Exception):
                        stock_code = top_stocks[idx]["code"] if idx < len(top_stocks) else "?"
                        logger.warning(f"消息面:重仓股 {stock_code} 新闻抓取失败: {news_list}")
                        continue
                    all_news.extend(news_list)
        except Exception as e:
            logger.warning(f"消息面:基金重仓股新闻抓取失败: {e}")

    # 统一结构
    unified = [_build_news_item(n) for n in all_news]

    # 板块过滤
    if sector:
        unified = [n for n in unified if sector in n.get("content", "") or sector in n.get("title", "")]

    # 去重
    unified = _deduplicate(unified)

    # 排序(利好利空优先)
    unified.sort(key=lambda x: {"利好": 0, "利空": 1, "中性": 2}.get(x.get("sentiment", "中性"), 2))

    return {
        "hot_events": unified[:20],
        "sentiment_index": _calc_sentiment_index(unified),
        "key_news": unified[:10],
        "total_count": len(unified),
        "query": {"sector": sector, "fund_code": fund_code},
    }


async def get_hot_events(limit: int = 10) -> list[dict]:
    """热点事件 Top N"""
    feed = await get_news_feed()
    return feed["hot_events"][:limit]


async def get_sentiment_index() -> dict:
    """情绪指数"""
    feed = await get_news_feed()
    index = feed["sentiment_index"]
    if index > 60:
        level = "偏多"
    elif index < 40:
        level = "偏空"
    else:
        level = "中性"
    return {
        "index": index,
        "level": level,
        "positive_count": sum(1 for n in feed["hot_events"] if n.get("sentiment") == "利好"),
        "negative_count": sum(1 for n in feed["hot_events"] if n.get("sentiment") == "利空"),
        "neutral_count": sum(1 for n in feed["hot_events"] if n.get("sentiment") == "中性"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def ai_search(query: str) -> dict:
    """AI检索(Tavily+LLM总结)

    Args:
        query: 检索关键词

    Returns:
        {query, results, summary, sentiment}
    """
    # Tavily检索
    results = await asyncio.to_thread(_ai_search, query)

    if not results:
        return {
            "query": query,
            "results": [],
            "summary": "未检索到相关消息",
            "sentiment": "中性",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # 统一结构+分类
    unified = [_build_news_item(n) for n in results]

    # LLM总结
    summary = ""
    try:
        from src.core.llm_router import get_llm_router
        router = get_llm_router()
        news_text = "\n".join([f"- {n['title']}: {n['content']}" for n in unified[:8]])
        prompt = f"""请根据以下财经新闻,生成一段简短的消息面总结(100字以内),并判断整体情绪倾向:

{news_text}

要求:
1. 提炼核心信息,不要罗列新闻
2. 指出对市场/板块的潜在影响
3. 100字以内"""

        resp = router.chat(
            messages=[
                {"role": "system", "content": "你是专业的财经分析师,擅长从新闻中提炼核心信息。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            timeout=30,
        )
        summary = resp.content
    except Exception as e:
        logger.warning(f"消息面:LLM总结失败,使用简单聚合: {e}")
        summary = f"共检索到{len(unified)}条相关消息," + ";".join([n["title"][:20] for n in unified[:3]])

    sentiment = _classify_sentiment(" ".join([n["title"] for n in unified]))

    return {
        "query": query,
        "results": unified,
        "summary": summary,
        "sentiment": sentiment,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

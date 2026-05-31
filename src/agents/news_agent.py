from __future__ import annotations

import random

from src.agents.base import AgentRole, BaseAgent, AgentOpinion

_HAS_NEWS = False
try:
    from src.analysis.news import fetch_news, analyze_news_sentiment, score_news
    _HAS_NEWS = True
except ImportError:
    pass


class NewsAgent(BaseAgent):
    role = AgentRole.NEWS

    def analyze(self, stock_code: str, **kwargs) -> AgentOpinion:
        if _HAS_NEWS:
            try:
                return self._real_analysis(stock_code)
            except Exception:
                pass
        return self._mock_analysis(stock_code)

    def _real_analysis(self, stock_code: str) -> AgentOpinion:
        news_list = fetch_news(stock_code)
        sentiment = analyze_news_sentiment(news_list)
        score = score_news(stock_code)

        key_points: list[str] = []
        reasoning_parts: list[str] = []

        if not news_list:
            key_points.append("暂无近期新闻")
            reasoning_parts.append("未获取到相关新闻数据")
        else:
            key_points.append(f"获取{len(news_list)}条相关新闻")
            reasoning_parts.append(f"共获取{len(news_list)}条新闻资讯")

            pos_count = 0
            neg_count = 0
            for news in news_list:
                title = news.get("title", "")
                content = news.get("content", "")
                text = f"{title} {content}"
                from src.analysis.news import _POSITIVE_KEYWORDS, _NEGATIVE_KEYWORDS
                p = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
                n = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
                if p > n:
                    pos_count += 1
                elif n > p:
                    neg_count += 1

            key_points.append(f"正面新闻{pos_count}条，负面新闻{neg_count}条")
            reasoning_parts.append(f"正面新闻{pos_count}条，负面{neg_count}条")

        if sentiment > 0.3:
            key_points.append("新闻情绪偏正面")
            reasoning_parts.append(f"新闻情绪值{sentiment:.2f}，偏正面")
        elif sentiment > -0.3:
            key_points.append("新闻情绪中性")
            reasoning_parts.append(f"新闻情绪值{sentiment:.2f}，中性")
        else:
            key_points.append("新闻情绪偏负面")
            reasoning_parts.append(f"新闻情绪值{sentiment:.2f}，偏负面")

        if score >= 70:
            signal = "BULLISH"
        elif score >= 40:
            signal = "NEUTRAL"
        else:
            signal = "BEARISH"

        confidence = round(min(abs(score - 50) / 50, 1.0), 2)

        return self._create_opinion(
            stock_code=stock_code,
            signal=signal,
            confidence=confidence,
            reasoning="；".join(reasoning_parts),
            key_points=key_points,
            score=score,
        )

    def _mock_analysis(self, stock_code: str) -> AgentOpinion:
        score = round(random.uniform(10, 90), 2)
        sentiment = round(random.uniform(-1.0, 1.0), 2)
        news_count = random.randint(0, 20)

        key_points = [
            f"获取{news_count}条相关新闻",
            f"新闻情绪值{sentiment}，{'正面' if sentiment > 0.3 else '中性' if sentiment > -0.3 else '负面'}",
            random.choice(["热点题材活跃", "无明显热点", "题材退潮"]),
        ]

        if score >= 70:
            signal = "BULLISH"
        elif score >= 40:
            signal = "NEUTRAL"
        else:
            signal = "BEARISH"

        confidence = round(min(abs(score - 50) / 50, 1.0), 2)

        return self._create_opinion(
            stock_code=stock_code,
            signal=signal,
            confidence=confidence,
            reasoning=f"[模拟] 新闻面分析：新闻情绪{sentiment}，评分{score}",
            key_points=key_points,
            score=score,
        )

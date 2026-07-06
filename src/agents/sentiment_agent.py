from __future__ import annotations

from src.agents.base import AgentRole, BaseAgent, AgentOpinion

_HAS_SENTIMENT = False
try:
    from src.analysis.sentiment import compute_market_sentiment, score_sentiment
    _HAS_SENTIMENT = True
except ImportError:
    pass


class SentimentAgent(BaseAgent):
    role = AgentRole.SENTIMENT

    def analyze(self, stock_code: str, **kwargs) -> AgentOpinion:
        if _HAS_SENTIMENT:
            try:
                return self._real_analysis(stock_code)
            except Exception:
                pass
        return self._mock_analysis(stock_code)

    def _real_analysis(self, stock_code: str) -> AgentOpinion:
        market_sentiment = compute_market_sentiment()
        score = score_sentiment(stock_code)

        key_points: list[str] = []
        reasoning_parts: list[str] = []

        if market_sentiment >= 70:
            key_points.append("市场整体情绪乐观")
            reasoning_parts.append(f"市场情绪指数{market_sentiment:.1f}，偏乐观")
        elif market_sentiment >= 40:
            key_points.append("市场情绪中性")
            reasoning_parts.append(f"市场情绪指数{market_sentiment:.1f}，中性偏稳")
        else:
            key_points.append("市场情绪悲观")
            reasoning_parts.append(f"市场情绪指数{market_sentiment:.1f}，偏悲观")

        if score >= 70:
            key_points.append("个股情绪偏强")
            reasoning_parts.append("个股表现强于市场，资金关注度较高")
        elif score >= 40:
            key_points.append("个股情绪中性")
            reasoning_parts.append("个股表现与市场基本同步")
        else:
            key_points.append("个股情绪偏弱")
            reasoning_parts.append("个股表现弱于市场，资金关注度较低")

        reasoning_parts.append(f"综合情绪评分{score:.1f}")

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
        return self._create_opinion(
            stock_code=stock_code,
            signal="NEUTRAL",
            confidence=0.0,
            reasoning="[降级] 情绪面分析模块不可用",
            key_points=["情绪面分析模块不可用，已降级"],
            score=50.0,
        )

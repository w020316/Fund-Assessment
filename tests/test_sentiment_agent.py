"""情绪面智能体单元测试

验证 src/agents/sentiment_agent.py 的核心逻辑:
- SentimentAgent.role 角色定义
- _real_analysis 路径(mock compute_market_sentiment/score_sentiment)
- 市场情绪/个股情绪→信号映射
- _mock_analysis 降级路径
- analyze 异常降级
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.base import AgentOpinion, AgentRole
from src.agents import sentiment_agent as sa_module
from src.agents.sentiment_agent import SentimentAgent


@pytest.fixture
def agent() -> SentimentAgent:
    return SentimentAgent()


def _opinion_via_real(agent: SentimentAgent, market_sentiment: float, score: float) -> AgentOpinion:
    """辅助:走 _real_analysis 路径"""
    with patch.object(sa_module, "_HAS_SENTIMENT", True), \
         patch.object(sa_module, "compute_market_sentiment", return_value=market_sentiment), \
         patch.object(sa_module, "score_sentiment", return_value=score):
        return agent.analyze("600519")


class TestRole:
    def test_role_is_sentiment(self, agent):
        assert agent.role == AgentRole.SENTIMENT


class TestRealAnalysis:
    """_real_analysis 路径"""

    def test_bullish_signal_when_score_high(self, agent):
        """score>=70 → BULLISH,市场情绪乐观"""
        op = _opinion_via_real(agent, market_sentiment=75.0, score=80.0)
        assert op.signal == "BULLISH"
        assert op.score == 80.0
        assert any("乐观" in p for p in op.key_points)
        assert any("偏强" in p for p in op.key_points)

    def test_bearish_signal_when_score_low(self, agent):
        """score<40 → BEARISH,市场情绪悲观"""
        op = _opinion_via_real(agent, market_sentiment=30.0, score=20.0)
        assert op.signal == "BEARISH"
        assert any("悲观" in p for p in op.key_points)
        assert any("偏弱" in p for p in op.key_points)

    def test_neutral_signal_when_score_middle(self, agent):
        """40<=score<70 → NEUTRAL"""
        op = _opinion_via_real(agent, market_sentiment=55.0, score=50.0)
        assert op.signal == "NEUTRAL"
        assert any("中性" in p for p in op.key_points)

    def test_confidence_bounded(self, agent):
        """confidence 应在 [0,1]"""
        op = _opinion_via_real(agent, market_sentiment=80.0, score=100.0)
        assert 0.0 <= op.confidence <= 1.0

    def test_score_extreme_low_market_sentiment(self, agent):
        """市场情绪指数极低 → '悲观'"""
        op = _opinion_via_real(agent, market_sentiment=20.0, score=30.0)
        assert any("悲观" in p for p in op.key_points)


class TestMockAnalysis:
    """_mock_analysis 降级路径"""

    def test_mock_analysis_returns_neutral(self, agent):
        with patch.object(sa_module, "_HAS_SENTIMENT", False):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.0
        assert op.score == 50.0
        assert "降级" in op.reasoning

    def test_real_analysis_exception_falls_back(self, agent):
        """_real_analysis 抛异常 → 应回退到 _mock_analysis"""
        with patch.object(sa_module, "_HAS_SENTIMENT", True), \
             patch.object(sa_module, "compute_market_sentiment", side_effect=Exception("网络错误")):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.score == 50.0


class TestOpinionShape:
    def test_returns_agent_opinion_instance(self, agent):
        with patch.object(sa_module, "_HAS_SENTIMENT", False):
            op = agent.analyze("000001")
        assert isinstance(op, AgentOpinion)
        assert op.role == AgentRole.SENTIMENT

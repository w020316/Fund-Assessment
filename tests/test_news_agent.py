"""新闻面智能体单元测试

验证 src/agents/news_agent.py 的核心逻辑:
- NewsAgent.role 角色定义
- _real_analysis 路径(mock fetch_news/analyze_news_sentiment/score_news)
- 新闻数量与情绪值→信号映射
- 空新闻列表降级
- _mock_analysis 降级路径
- analyze 异常降级
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.base import AgentOpinion, AgentRole
from src.agents import news_agent as na_module
from src.agents.news_agent import NewsAgent


@pytest.fixture
def agent() -> NewsAgent:
    return NewsAgent()


def _opinion_via_real(
    agent: NewsAgent,
    news_list: list[dict],
    sentiment: float,
    score: float,
) -> AgentOpinion:
    """辅助:走 _real_analysis 路径"""
    with patch.object(na_module, "_HAS_NEWS", True), \
         patch.object(na_module, "fetch_news", return_value=news_list), \
         patch.object(na_module, "analyze_news_sentiment", return_value=sentiment), \
         patch.object(na_module, "score_news", return_value=score):
        return agent.analyze("600519")


class TestRole:
    def test_role_is_news(self, agent):
        assert agent.role == AgentRole.NEWS


class TestRealAnalysis:
    """_real_analysis 路径"""

    def test_bullish_signal_when_score_high(self, agent):
        """score>=70 → BULLISH,新闻情绪偏正面"""
        news = [
            {"title": "公司业绩利好", "content": "净利润增长30%"},
            {"title": "中标大订单", "content": "签署合作协议"},
        ]
        op = _opinion_via_real(agent, news_list=news, sentiment=0.5, score=80.0)
        assert op.signal == "BULLISH"
        assert op.score == 80.0
        assert any("正面" in p for p in op.key_points)
        assert any("2条" in p for p in op.key_points)

    def test_bearish_signal_when_score_low(self, agent):
        """score<40 → BEARISH,新闻情绪偏负面"""
        news = [
            {"title": "公司遭处罚", "content": "违规被调查"},
            {"title": "业绩亏损", "content": "净利润下降50%"},
        ]
        op = _opinion_via_real(agent, news_list=news, sentiment=-0.5, score=20.0)
        assert op.signal == "BEARISH"
        assert any("负面" in p for p in op.key_points)

    def test_neutral_signal_when_score_middle(self, agent):
        """40<=score<70 → NEUTRAL"""
        news = [{"title": "公司公告", "content": "例行公告"}]
        op = _opinion_via_real(agent, news_list=news, sentiment=0.0, score=55.0)
        assert op.signal == "NEUTRAL"
        assert any("中性" in p for p in op.key_points)

    def test_empty_news_list(self, agent):
        """空新闻列表 → '暂无近期新闻'"""
        op = _opinion_via_real(agent, news_list=[], sentiment=0.0, score=50.0)
        assert any("暂无" in p for p in op.key_points)

    def test_confidence_bounded(self, agent):
        op = _opinion_via_real(
            agent, news_list=[{"title": "利好", "content": "增长"}],
            sentiment=0.5, score=100.0,
        )
        assert 0.0 <= op.confidence <= 1.0


class TestMockAnalysis:
    """_mock_analysis 降级路径"""

    def test_mock_analysis_returns_neutral(self, agent):
        with patch.object(na_module, "_HAS_NEWS", False):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.0
        assert op.score == 50.0
        assert "降级" in op.reasoning

    def test_real_analysis_exception_falls_back(self, agent):
        """_real_analysis 抛异常 → 应回退"""
        with patch.object(na_module, "_HAS_NEWS", True), \
             patch.object(na_module, "fetch_news", side_effect=Exception("网络错误")):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.score == 50.0


class TestOpinionShape:
    def test_returns_agent_opinion_instance(self, agent):
        with patch.object(na_module, "_HAS_NEWS", False):
            op = agent.analyze("000001")
        assert isinstance(op, AgentOpinion)
        assert op.role == AgentRole.NEWS

"""基本面智能体单元测试

验证 src/agents/fundamental_agent.py 的核心逻辑:
- FundamentalAgent.role 角色定义
- _real_analysis 路径(mock analyze_fundamental/score_fundamental)
- 评分→信号映射(BULLISH/NEUTRAL/BEARISH)
- PE/PB/ROE/增长 等关键点拼接
- _mock_analysis 降级路径(模块不可用)
- analyze 异常降级
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.base import AgentOpinion, AgentRole
from src.agents import fundamental_agent as fa_module
from src.agents.fundamental_agent import FundamentalAgent


@pytest.fixture
def agent() -> FundamentalAgent:
    return FundamentalAgent()


def _opinion_via_real(agent: FundamentalAgent, fundamental_data: dict, score: float) -> AgentOpinion:
    """辅助:通过 mock 调用 _real_analysis 路径"""
    with patch.object(fa_module, "_HAS_FUNDAMENTAL", True), \
         patch.object(fa_module, "analyze_fundamental", return_value=fundamental_data), \
         patch.object(fa_module, "score_fundamental", return_value=score):
        return agent.analyze("600519")


class TestRole:
    """角色定义"""

    def test_role_is_fundamental(self, agent):
        assert agent.role == AgentRole.FUNDAMENTAL


class TestRealAnalysis:
    """_real_analysis 路径(模块可用)"""

    def test_bullish_signal_when_score_high(self, agent):
        """score>=70 → BULLISH,关键点含 PE"""
        op = _opinion_via_real(
            agent,
            {"PE": 12.0, "PB": 1.5, "ROE": 20.0, "revenue_growth": 25.0, "profit_growth": 30.0},
            score=80.0,
        )
        assert op.signal == "BULLISH"
        assert op.score == 80.0
        assert op.stock_code == "600519"
        assert any("PE=" in p for p in op.key_points)
        assert "估值偏低" in " ".join(op.key_points)

    def test_bearish_signal_when_score_low(self, agent):
        """score<40 → BEARISH"""
        op = _opinion_via_real(
            agent,
            {"PE": 80.0, "PB": 12.0, "ROE": 3.0, "revenue_growth": -10.0, "profit_growth": -15.0},
            score=20.0,
        )
        assert op.signal == "BEARISH"
        assert any("估值过高" in p for p in op.key_points)
        assert any("负增长" in p for p in op.key_points)

    def test_neutral_signal_when_score_middle(self, agent):
        """40<=score<70 → NEUTRAL"""
        op = _opinion_via_real(
            agent,
            {"PE": 20.0, "PB": 2.5, "ROE": 10.0, "revenue_growth": 8.0, "profit_growth": 5.0},
            score=55.0,
        )
        assert op.signal == "NEUTRAL"

    def test_loss_company_pe_zero(self, agent):
        """PE<=0 → 亏损状态"""
        op = _opinion_via_real(
            agent,
            {"PE": 0.0, "PB": 0.0, "ROE": -5.0, "revenue_growth": -20.0, "profit_growth": -30.0},
            score=10.0,
        )
        assert any("亏损" in p for p in op.key_points)

    def test_confidence_bounded_to_1(self, agent):
        """confidence 不超过 1.0"""
        op = _opinion_via_real(
            agent,
            {"PE": 10.0, "PB": 1.0, "ROE": 30.0, "revenue_growth": 30.0, "profit_growth": 40.0},
            score=100.0,
        )
        assert 0.0 <= op.confidence <= 1.0


class TestMockAnalysis:
    """_mock_analysis 降级路径"""

    def test_mock_analysis_returns_neutral(self, agent):
        """模块不可用 → 返回中性降级意见"""
        with patch.object(fa_module, "_HAS_FUNDAMENTAL", False):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.0
        assert op.score == 50.0
        assert "降级" in op.reasoning

    def test_real_analysis_exception_falls_back_to_mock(self, agent):
        """_real_analysis 抛异常 → analyze 应回退到 _mock_analysis"""
        with patch.object(fa_module, "_HAS_FUNDAMENTAL", True), \
             patch.object(fa_module, "analyze_fundamental", side_effect=Exception("网络错误")):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.score == 50.0


class TestOpinionShape:
    """返回值结构契约"""

    def test_returns_agent_opinion_instance(self, agent):
        with patch.object(fa_module, "_HAS_FUNDAMENTAL", False):
            op = agent.analyze("000001")
        assert isinstance(op, AgentOpinion)
        assert op.role == AgentRole.FUNDAMENTAL
        assert isinstance(op.key_points, list)
        assert isinstance(op.reasoning, str)

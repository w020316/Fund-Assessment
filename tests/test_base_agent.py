"""智能体基类单元测试

验证 src/agents/base.py 的核心逻辑:
- AgentRole 枚举完整性
- AgentOpinion / DebateResult / TradingDecision 数据类
- BaseAgent 抽象基类(子类化 + _create_opinion)
- 时间戳与字段默认值
"""
from __future__ import annotations

from dataclasses import is_dataclass
from datetime import datetime

import pytest

from src.agents.base import (
    AgentOpinion,
    AgentRole,
    BaseAgent,
    DebateResult,
    TradingDecision,
)


class _ConcreteAgent(BaseAgent):
    """用于测试 BaseAgent 的具体子类"""
    role = AgentRole.FUNDAMENTAL

    def analyze(self, stock_code: str, **kwargs) -> AgentOpinion:
        return self._create_opinion(
            stock_code=stock_code,
            signal="NEUTRAL",
            confidence=0.5,
            reasoning="测试",
            key_points=["点1"],
            score=50.0,
        )


class TestAgentRole:
    """AgentRole 枚举"""

    def test_all_roles_defined(self):
        expected = {
            "FUNDAMENTAL", "TECHNICAL", "SENTIMENT", "NEWS",
            "BULL_RESEARCHER", "BEAR_RESEARCHER", "TRADER",
            "RISK_MANAGER", "PORTFOLIO_MANAGER",
        }
        actual = {r.name for r in AgentRole}
        assert actual == expected

    def test_role_values_are_strings(self):
        for role in AgentRole:
            assert isinstance(role.value, str)
            assert role.value

    def test_role_uniqueness(self):
        values = [r.value for r in AgentRole]
        assert len(values) == len(set(values))


class TestAgentOpinion:
    """AgentOpinion 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(AgentOpinion)

    def test_creation_with_required_fields(self):
        op = AgentOpinion(
            role=AgentRole.FUNDAMENTAL,
            stock_code="600519",
            signal="BULLISH",
            confidence=0.8,
            reasoning="基本面优秀",
            key_points=["ROE高", "PE合理"],
            score=75.0,
            timestamp="2026-07-29T10:00:00",
        )
        assert op.role == AgentRole.FUNDAMENTAL
        assert op.stock_code == "600519"
        assert op.signal == "BULLISH"
        assert op.confidence == 0.8
        assert op.score == 75.0
        assert len(op.key_points) == 2

    def test_signal_string_arbitrary(self):
        """signal 为字符串,允许任意值(无枚举约束)"""
        op = AgentOpinion(
            role=AgentRole.TECHNICAL, stock_code="000001", signal="CUSTOM",
            confidence=0.0, reasoning="", key_points=[], score=0.0,
            timestamp="t",
        )
        assert op.signal == "CUSTOM"


class TestDebateResult:
    """DebateResult 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(DebateResult)

    def test_creation(self):
        dr = DebateResult(
            topic="股票600519多空辩论",
            bull_arguments=["看多1", "看多2"],
            bear_arguments=["看空1"],
            bull_score=80.0,
            bear_score=40.0,
            consensus="BULLISH",
            confidence=0.5,
        )
        assert dr.topic.startswith("股票")
        assert len(dr.bull_arguments) == 2
        assert dr.consensus == "BULLISH"


class TestTradingDecision:
    """TradingDecision 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(TradingDecision)

    def test_creation_with_debate_none(self):
        decision = TradingDecision(
            stock_code="600519",
            action="BUY",
            position_size=0.5,
            confidence=0.7,
            reasoning="综合看多",
            agent_opinions=[],
            debate_result=None,
            risk_assessment={"risk_level": "LOW"},
            timestamp="2026-07-29T10:00:00",
        )
        assert decision.action == "BUY"
        assert decision.debate_result is None
        assert decision.risk_assessment["risk_level"] == "LOW"

    def test_creation_with_full_payload(self):
        op = AgentOpinion(
            role=AgentRole.FUNDAMENTAL, stock_code="600519", signal="BULLISH",
            confidence=0.8, reasoning="r", key_points=[], score=80.0, timestamp="t",
        )
        dr = DebateResult(
            topic="t", bull_arguments=[], bear_arguments=[],
            bull_score=0.0, bear_score=0.0, consensus="NEUTRAL", confidence=0.0,
        )
        decision = TradingDecision(
            stock_code="600519", action="HOLD", position_size=0.0,
            confidence=0.3, reasoning="r", agent_opinions=[op],
            debate_result=dr, risk_assessment={}, timestamp="ts",
        )
        assert len(decision.agent_opinions) == 1
        assert decision.debate_result is not None


class TestBaseAgent:
    """BaseAgent 抽象基类"""

    def test_base_agent_is_abstract(self):
        """BaseAgent 不能直接实例化"""
        with pytest.raises(TypeError):
            BaseAgent()  # type: ignore[abstract]

    def test_concrete_subclass_can_instantiate(self):
        agent = _ConcreteAgent()
        assert agent.role == AgentRole.FUNDAMENTAL

    def test_create_opinion_uses_role(self):
        """_create_opinion 应使用 self.role"""
        agent = _ConcreteAgent()
        op = agent._create_opinion(
            stock_code="000001", signal="BULLISH", confidence=0.9,
            reasoning="强", key_points=["k"], score=85.0,
        )
        assert op.role == AgentRole.FUNDAMENTAL
        assert op.stock_code == "000001"
        assert op.signal == "BULLISH"
        assert op.confidence == 0.9
        assert op.score == 85.0

    def test_create_opinion_generates_iso_timestamp(self):
        """_create_opinion 应生成 ISO 时间戳(可被 fromisoformat 解析)"""
        agent = _ConcreteAgent()
        op = agent._create_opinion(
            stock_code="000001", signal="NEUTRAL", confidence=0.0,
            reasoning="", key_points=[], score=50.0,
        )
        # 应能解析为合法 datetime
        parsed = datetime.fromisoformat(op.timestamp)
        assert isinstance(parsed, datetime)

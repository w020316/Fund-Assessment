"""交易管理器单元测试

验证 src/agents/trading_manager.py 的核心逻辑:
- TraderAgent.decide(综合评分→操作建议)
- RiskManagerAgent.assess(风险评级/仓位调整/告警)
- PortfolioManagerAgent.finalize(组合最终决策)
- TradingManager.run_analysis(端到端,mock 子智能体)
- TradingManager.quick_analysis / get_decision_history
- 异常降级(_parallel_analyze 超时兜底)
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from src.agents.base import AgentOpinion, AgentRole, DebateResult, TradingDecision
from src.agents.trading_manager import (
    PortfolioManagerAgent,
    RiskManagerAgent,
    TradingManager,
    TraderAgent,
)


def _make_opinion(
    role: AgentRole = AgentRole.FUNDAMENTAL,
    signal: str = "NEUTRAL",
    confidence: float = 0.5,
    score: float = 50.0,
    reasoning: str = "测试",
    key_points: list[str] | None = None,
) -> AgentOpinion:
    return AgentOpinion(
        role=role,
        stock_code="600519",
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
        key_points=key_points or [],
        score=score,
        timestamp=datetime.now().isoformat(),
    )


def _make_debate(consensus: str = "NEUTRAL", confidence: float = 0.3) -> DebateResult:
    return DebateResult(
        topic="t",
        bull_arguments=[],
        bear_arguments=[],
        bull_score=0.0,
        bear_score=0.0,
        consensus=consensus,
        confidence=confidence,
    )


# ----------------- TraderAgent -----------------


class TestTraderAgent:
    """TraderAgent.decide"""

    def test_buy_action_when_score_high(self):
        """综合评分>=65 且看多票>看空票 → BUY"""
        trader = TraderAgent()
        opinions = [
            _make_opinion(signal="BULLISH", score=80.0, confidence=0.8),
            _make_opinion(signal="BULLISH", score=75.0, confidence=0.7),
            _make_opinion(signal="BEARISH", score=20.0, confidence=0.6),
        ]
        action, size, reasoning = trader.decide(opinions, _make_debate("BULLISH", 0.5))
        assert action == "BUY"
        assert 0.0 < size <= 0.8
        assert "看多" in reasoning

    def test_sell_action_when_score_low(self):
        """综合评分<=35 且看空票>看多票 → SELL"""
        trader = TraderAgent()
        opinions = [
            _make_opinion(signal="BEARISH", score=20.0, confidence=0.8),
            _make_opinion(signal="BEARISH", score=15.0, confidence=0.7),
            _make_opinion(signal="BULLISH", score=80.0, confidence=0.6),
        ]
        action, size, reasoning = trader.decide(opinions, _make_debate("BEARISH", 0.5))
        assert action == "SELL"
        assert 0.0 < size <= 0.8

    def test_hold_action_when_neutral(self):
        """中性场景 → HOLD,size 0"""
        trader = TraderAgent()
        opinions = [
            _make_opinion(signal="NEUTRAL", score=50.0, confidence=0.5),
            _make_opinion(signal="NEUTRAL", score=50.0, confidence=0.5),
        ]
        action, size, _ = trader.decide(opinions, None)
        assert action == "HOLD"
        assert size == 0.0

    def test_empty_opinions_hold(self):
        """空 opinions → HOLD"""
        trader = TraderAgent()
        action, size, _ = trader.decide([], None)
        assert action == "HOLD"
        assert size == 0.0


# ----------------- RiskManagerAgent -----------------


class TestRiskManagerAgent:
    """RiskManagerAgent.assess"""

    def test_low_risk_when_high_confidence(self):
        """置信度>=0.5 → LOW risk"""
        rm = RiskManagerAgent()
        opinions = [_make_opinion(confidence=0.8), _make_opinion(confidence=0.7)]
        result = rm.assess("BUY", 0.3, opinions, _make_debate("BULLISH", 0.5))
        assert result["risk_level"] == "LOW"
        assert result["avg_confidence"] == pytest.approx(0.75, rel=1e-2)

    def test_high_risk_when_low_confidence(self):
        """置信度<0.3 → HIGH risk,仓位减半"""
        rm = RiskManagerAgent()
        opinions = [_make_opinion(confidence=0.2), _make_opinion(confidence=0.2)]
        result = rm.assess("BUY", 0.4, opinions, _make_debate("BULLISH", 0.5))
        assert result["risk_level"] == "HIGH"
        assert result["adjusted_position"] == pytest.approx(0.2, rel=1e-2)
        assert any("置信度" in w for w in result["warnings"])

    def test_buy_position_capped_at_half(self):
        """BUY 仓位 > 0.5 → 截断至 0.5"""
        rm = RiskManagerAgent()
        opinions = [_make_opinion(confidence=0.9)]
        result = rm.assess("BUY", 0.8, opinions, _make_debate("BULLISH", 0.5))
        assert result["adjusted_position"] <= 0.5

    def test_low_debate_confidence_raises_risk(self):
        """辩论置信度<0.2 → 至少 MEDIUM"""
        rm = RiskManagerAgent()
        opinions = [_make_opinion(confidence=0.9)]
        result = rm.assess("BUY", 0.2, opinions, _make_debate("NEUTRAL", 0.1))
        assert result["risk_level"] in {"MEDIUM", "HIGH"}


# ----------------- PortfolioManagerAgent -----------------


class TestPortfolioManagerAgent:
    """PortfolioManagerAgent.finalize"""

    def test_finalize_uses_adjusted_position(self):
        """finalize 应使用风控调整后的仓位"""
        pm = PortfolioManagerAgent()
        decision = pm.finalize(
            stock_code="600519",
            action="BUY",
            position_size=0.8,
            confidence=0.7,
            reasoning="买入",
            opinions=[_make_opinion()],
            debate_result=_make_debate(),
            risk_assessment={"adjusted_position": 0.5, "warnings": ["风控提示"]},
        )
        assert isinstance(decision, TradingDecision)
        assert decision.action == "BUY"
        assert decision.position_size == 0.5
        assert "风控提示" in decision.reasoning

    def test_finalize_no_warnings(self):
        """无风控告警 → reasoning 不追加"""
        pm = PortfolioManagerAgent()
        decision = pm.finalize(
            stock_code="600519", action="HOLD", position_size=0.0,
            confidence=0.3, reasoning="持有",
            opinions=[], debate_result=None,
            risk_assessment={"adjusted_position": 0.0, "warnings": []},
        )
        assert decision.reasoning == "持有"


# ----------------- TradingManager -----------------


def _stub_opinion(role: AgentRole, stock_code: str = "600519") -> AgentOpinion:
    return AgentOpinion(
        role=role, stock_code=stock_code, signal="NEUTRAL",
        confidence=0.5, reasoning="桩意见", key_points=["桩"],
        score=50.0, timestamp=datetime.now().isoformat(),
    )


@pytest.fixture
def manager(monkeypatch) -> TradingManager:
    """所有子 agent.analyze 替换为桩函数,完全隔离网络"""
    mgr = TradingManager()
    monkeypatch.setattr(mgr.fundamental_agent, "analyze",
                        lambda stock_code, **kw: _stub_opinion(AgentRole.FUNDAMENTAL, stock_code))
    monkeypatch.setattr(mgr.technical_agent, "analyze",
                        lambda stock_code, **kw: _stub_opinion(AgentRole.TECHNICAL, stock_code))
    monkeypatch.setattr(mgr.sentiment_agent, "analyze",
                        lambda stock_code, **kw: _stub_opinion(AgentRole.SENTIMENT, stock_code))
    monkeypatch.setattr(mgr.news_agent, "analyze",
                        lambda stock_code, **kw: _stub_opinion(AgentRole.NEWS, stock_code))
    return mgr


class TestTradingManager:
    """TradingManager 端到端"""

    def test_run_analysis_returns_decision(self, manager):
        """run_analysis 应返回 TradingDecision(mock 子智能体)"""
        decision = manager.run_analysis("600519")
        assert isinstance(decision, TradingDecision)
        assert decision.stock_code == "600519"
        assert decision.action in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= decision.confidence <= 1.0

    def test_quick_analysis_returns_opinions(self, manager):
        """quick_analysis 应返回 4 个意见(并行)"""
        opinions = manager.quick_analysis("600519")
        assert len(opinions) == 4
        roles = {op.role for op in opinions}
        assert roles == {
            AgentRole.FUNDAMENTAL, AgentRole.TECHNICAL,
            AgentRole.SENTIMENT, AgentRole.NEWS,
        }

    def test_history_accumulates(self, manager):
        """run_analysis 多次调用 → 历史累积"""
        assert len(manager.get_decision_history()) == 0
        manager.run_analysis("600519")
        manager.run_analysis("000001")
        history = manager.get_decision_history()
        assert len(history) == 2
        assert history[0].stock_code == "600519"
        assert history[1].stock_code == "000001"

    def test_history_returns_copy(self, manager):
        """get_decision_history 应返回副本(外部修改不影响内部)"""
        manager.run_analysis("600519")
        history = manager.get_decision_history()
        history.clear()
        assert len(manager.get_decision_history()) == 1


class TestParallelAnalyzeFallback:
    """_parallel_analyze 异常降级(超时/异常 → 默认中性意见)"""

    def test_agent_exception_returns_neutral_opinion(self):
        """子 agent 抛异常 → 返回默认中性意见"""
        mgr = TradingManager()
        with patch.object(mgr.fundamental_agent, "analyze", side_effect=Exception("超时")), \
             patch.object(mgr.technical_agent, "analyze", side_effect=Exception("超时")), \
             patch.object(mgr.sentiment_agent, "analyze", side_effect=Exception("超时")), \
             patch.object(mgr.news_agent, "analyze", side_effect=Exception("超时")):
            opinions = mgr._parallel_analyze("600519")
        assert len(opinions) == 4
        for op in opinions:
            assert op.signal == "NEUTRAL"
            assert op.confidence == 0.1
            assert "超时" in op.reasoning or "降级" in op.reasoning

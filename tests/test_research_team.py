"""研究团队单元测试

验证 src/agents/research_team.py 的核心逻辑:
- BullResearcher.build_arguments 看多论据构建
- BearResearcher.build_arguments 看空论据构建
- ResearchTeam.debate 多空辩论
- _rebuttal_adjust 反驳调整
- 边界场景(空 opinions / 全中性)
- 共识判定(BULLISH/BEARISH/NEUTRAL)
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.base import AgentOpinion, AgentRole, DebateResult
from src.agents.research_team import BearResearcher, BullResearcher, ResearchTeam


def _make_opinion(
    role: AgentRole = AgentRole.FUNDAMENTAL,
    stock_code: str = "600519",
    signal: str = "NEUTRAL",
    confidence: float = 0.5,
    score: float = 50.0,
    reasoning: str = "测试理由",
    key_points: list[str] | None = None,
) -> AgentOpinion:
    return AgentOpinion(
        role=role,
        stock_code=stock_code,
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
        key_points=key_points if key_points is not None else [],
        score=score,
        timestamp=datetime.now().isoformat(),
    )


@pytest.fixture
def team() -> ResearchTeam:
    return ResearchTeam()


class TestBullResearcher:
    """BullResearcher 看多论据"""

    def test_arguments_from_bullish_opinions(self):
        """BULLISH 意见 → 看多论据"""
        researcher = BullResearcher()
        opinions = [
            _make_opinion(signal="BULLISH", reasoning="ROE高", score=80.0),
            _make_opinion(signal="BULLISH", reasoning="增长强劲", score=75.0),
        ]
        args = researcher.build_arguments(opinions)
        assert len(args) == 2
        assert all("看多" in a for a in args)

    def test_fallback_when_no_bullish(self):
        """无看多意见但 score>=40 → 兜底论据"""
        researcher = BullResearcher()
        opinions = [_make_opinion(signal="NEUTRAL", score=55.0, key_points=["无"])]
        args = researcher.build_arguments(opinions)
        assert len(args) >= 1
        assert "支撑" in args[0]

    def test_final_fallback_no_score(self):
        """完全无支撑 → 默认论据"""
        researcher = BullResearcher()
        opinions = [_make_opinion(signal="NEUTRAL", score=10.0, key_points=["无"])]
        args = researcher.build_arguments(opinions)
        assert len(args) == 1
        assert "反弹" in args[0]


class TestBearResearcher:
    """BearResearcher 看空论据"""

    def test_arguments_from_bearish_opinions(self):
        researcher = BearResearcher()
        opinions = [
            _make_opinion(signal="BEARISH", reasoning="业绩下滑", score=20.0),
        ]
        args = researcher.build_arguments(opinions)
        assert len(args) == 1
        assert "看空" in args[0]

    def test_fallback_when_no_bearish(self):
        """无看空意见但 score<60 → 下行风险论据"""
        researcher = BearResearcher()
        opinions = [_make_opinion(signal="NEUTRAL", score=50.0, key_points=["无"])]
        args = researcher.build_arguments(opinions)
        assert len(args) >= 1
        assert "下行风险" in args[0]

    def test_final_fallback_high_score(self):
        """score>=60 → 默认论据"""
        researcher = BearResearcher()
        opinions = [_make_opinion(signal="BULLISH", score=90.0, key_points=["无"])]
        args = researcher.build_arguments(opinions)
        assert len(args) == 1
        assert "回调" in args[0]


class TestDebate:
    """ResearchTeam.debate"""

    def test_debate_returns_debate_result(self, team):
        opinions = [
            _make_opinion(signal="BULLISH", score=80.0, confidence=0.8),
            _make_opinion(signal="BEARISH", score=20.0, confidence=0.7),
        ]
        result = team.debate(opinions, rounds=2)
        assert isinstance(result, DebateResult)
        assert "600519" in result.topic
        assert len(result.bull_arguments) > 0
        assert len(result.bear_arguments) > 0
        assert result.consensus in {"BULLISH", "BEARISH", "NEUTRAL"}
        assert 0.0 <= result.confidence <= 1.0

    def test_debate_strong_bullish_consensus(self, team):
        """强看多意见 → BULLISH 共识"""
        opinions = [
            _make_opinion(signal="BULLISH", score=90.0, confidence=0.9),
            _make_opinion(signal="BULLISH", score=85.0, confidence=0.8),
            _make_opinion(signal="BULLISH", score=80.0, confidence=0.7),
        ]
        result = team.debate(opinions, rounds=1)
        assert result.consensus == "BULLISH"
        assert result.bull_score > result.bear_score

    def test_debate_strong_bearish_consensus(self, team):
        """强看空意见 → BEARISH 共识"""
        opinions = [
            _make_opinion(signal="BEARISH", score=10.0, confidence=0.9),
            _make_opinion(signal="BEARISH", score=15.0, confidence=0.8),
            _make_opinion(signal="BEARISH", score=20.0, confidence=0.7),
        ]
        result = team.debate(opinions, rounds=1)
        assert result.consensus == "BEARISH"
        assert result.bear_score > result.bull_score

    def test_debate_empty_opinions(self, team):
        """空 opinions → 默认均衡"""
        result = team.debate([], rounds=1)
        assert isinstance(result, DebateResult)
        # 默认均衡 → NEUTRAL
        assert result.consensus == "NEUTRAL"
        assert result.confidence == 0.0

    def test_debate_rounds_aggregates_arguments(self, team):
        """多轮辩论应累积论据"""
        opinions = [
            _make_opinion(signal="BULLISH", score=80.0, confidence=0.8),
            _make_opinion(signal="BEARISH", score=20.0, confidence=0.8),
        ]
        result_1 = team.debate(opinions, rounds=1)
        result_2 = team.debate(opinions, rounds=3)
        assert len(result_2.bull_arguments) > len(result_1.bull_arguments)


class TestRebuttalAdjust:
    """_rebuttal_adjust 反驳调整"""

    def test_bull_score_reduced_when_bear_args_more(self, team):
        """看空论据多于看多 → 看多评分被下调"""
        opinions = [_make_opinion(signal="BULLISH", score=80.0, confidence=0.8)]
        bull_args = ["看多1"]
        bear_args = ["看空1", "看空2", "看空3"]
        adjusted = team._rebuttal_adjust(opinions, bull_args, bear_args)
        assert adjusted[0].score < 80.0

    def test_bear_score_increased_when_bull_args_more(self, team):
        """看多论据多于看空 → 看空评分被上调"""
        opinions = [_make_opinion(signal="BEARISH", score=20.0, confidence=0.8)]
        bull_args = ["看多1", "看多2", "看多3"]
        bear_args = ["看空1"]
        adjusted = team._rebuttal_adjust(opinions, bull_args, bear_args)
        assert adjusted[0].score > 20.0

    def test_neutral_opinion_unchanged_score(self, team):
        """中性意见评分不变"""
        opinions = [_make_opinion(signal="NEUTRAL", score=50.0, confidence=0.5)]
        adjusted = team._rebuttal_adjust(opinions, ["b1"], ["s1"])
        assert adjusted[0].score == 50.0

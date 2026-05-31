from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AgentRole(Enum):
    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    NEWS = "news"
    BULL_RESEARCHER = "bull_researcher"
    BEAR_RESEARCHER = "bear_researcher"
    TRADER = "trader"
    RISK_MANAGER = "risk_manager"
    PORTFOLIO_MANAGER = "portfolio_manager"


@dataclass
class AgentOpinion:
    role: AgentRole
    stock_code: str
    signal: str
    confidence: float
    reasoning: str
    key_points: list[str]
    score: float
    timestamp: str


@dataclass
class DebateResult:
    topic: str
    bull_arguments: list[str]
    bear_arguments: list[str]
    bull_score: float
    bear_score: float
    consensus: str
    confidence: float


@dataclass
class TradingDecision:
    stock_code: str
    action: str
    position_size: float
    confidence: float
    reasoning: str
    agent_opinions: list[AgentOpinion]
    debate_result: DebateResult | None
    risk_assessment: dict[str, Any]
    timestamp: str


class BaseAgent(ABC):
    role: AgentRole

    @abstractmethod
    def analyze(self, stock_code: str, **kwargs) -> AgentOpinion:
        pass

    def _create_opinion(
        self,
        stock_code: str,
        signal: str,
        confidence: float,
        reasoning: str,
        key_points: list[str],
        score: float,
    ) -> AgentOpinion:
        return AgentOpinion(
            role=self.role,
            stock_code=stock_code,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            key_points=key_points,
            score=score,
            timestamp=datetime.now().isoformat(),
        )

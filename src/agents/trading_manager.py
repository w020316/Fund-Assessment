from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from src.agents.base import AgentOpinion, AgentRole, DebateResult, TradingDecision
from src.agents.fundamental_agent import FundamentalAgent
from src.agents.news_agent import NewsAgent
from src.agents.research_team import ResearchTeam
from src.agents.sentiment_agent import SentimentAgent
from src.agents.technical_agent import TechnicalAgent


class TraderAgent:
    role = AgentRole.TRADER

    def decide(
        self,
        opinions: list[AgentOpinion],
        debate_result: DebateResult | None,
    ) -> tuple[str, float, str]:
        avg_score = 0.0
        if opinions:
            avg_score = sum(op.score for op in opinions) / len(opinions)

        debate_bonus = 0.0
        if debate_result:
            if debate_result.consensus == "BULLISH":
                debate_bonus = 10.0
            elif debate_result.consensus == "BEARISH":
                debate_bonus = -10.0

        final_score = avg_score + debate_bonus

        bullish_count = sum(1 for op in opinions if op.signal == "BULLISH")
        bearish_count = sum(1 for op in opinions if op.signal == "BEARISH")
        neutral_count = sum(1 for op in opinions if op.signal == "NEUTRAL")

        if final_score >= 65 and bullish_count > bearish_count:
            action = "BUY"
            position_size = min(final_score / 100, 0.8)
        elif final_score <= 35 and bearish_count > bullish_count:
            action = "SELL"
            position_size = min((100 - final_score) / 100, 0.8)
        else:
            action = "HOLD"
            position_size = 0.0

        reasoning_parts: list[str] = []
        reasoning_parts.append(f"综合评分{avg_score:.1f}")
        reasoning_parts.append(f"看多{bullish_count}票，看空{bearish_count}票，中性{neutral_count}票")
        if debate_result:
            reasoning_parts.append(f"辩论共识：{debate_result.consensus}（置信度{debate_result.confidence:.0%}）")
        reasoning_parts.append(f"建议操作：{action}，仓位{position_size:.0%}")

        return action, round(position_size, 2), "；".join(reasoning_parts)


class RiskManagerAgent:
    role = AgentRole.RISK_MANAGER

    def assess(
        self,
        action: str,
        position_size: float,
        opinions: list[AgentOpinion],
        debate_result: DebateResult | None,
    ) -> dict[str, Any]:
        risk_level = "LOW"
        warnings: list[str] = []
        adjusted_position = position_size

        avg_confidence = 0.0
        if opinions:
            avg_confidence = sum(op.confidence for op in opinions) / len(opinions)

        if avg_confidence < 0.3:
            risk_level = "HIGH"
            warnings.append("分析师置信度普遍较低，建议谨慎")
            adjusted_position *= 0.5
        elif avg_confidence < 0.5:
            risk_level = "MEDIUM"
            warnings.append("分析师置信度中等，建议适度控制仓位")

        if debate_result and debate_result.confidence < 0.2:
            risk_level = max(risk_level, "MEDIUM") if risk_level in ("LOW", "MEDIUM") else risk_level
            warnings.append("多空辩论分歧较大，方向不明确")

        conflicting = sum(1 for op in opinions if op.signal == "BULLISH") - sum(
            1 for op in opinions if op.signal == "BEARISH"
        )
        if abs(conflicting) <= 1 and len(opinions) >= 3:
            if risk_level == "LOW":
                risk_level = "MEDIUM"
            warnings.append("分析师意见分歧较大，建议降低仓位")
            adjusted_position *= 0.7

        if action == "BUY" and adjusted_position > 0.5:
            adjusted_position = min(adjusted_position, 0.5)
            warnings.append("单只股票买入仓位上限50%")

        if action == "SELL":
            adjusted_position = min(adjusted_position, 0.5)
            warnings.append("卖出仓位上限50%")

        return {
            "risk_level": risk_level,
            "warnings": warnings,
            "adjusted_position": round(adjusted_position, 2),
            "avg_confidence": round(avg_confidence, 2),
        }


class PortfolioManagerAgent:
    role = AgentRole.PORTFOLIO_MANAGER

    def finalize(
        self,
        stock_code: str,
        action: str,
        position_size: float,
        confidence: float,
        reasoning: str,
        opinions: list[AgentOpinion],
        debate_result: DebateResult | None,
        risk_assessment: dict[str, Any],
    ) -> TradingDecision:
        adjusted_position = risk_assessment.get("adjusted_position", position_size)
        risk_warnings = risk_assessment.get("warnings", [])

        final_reasoning = reasoning
        if risk_warnings:
            final_reasoning += "；风控提示：" + "；".join(risk_warnings)

        return TradingDecision(
            stock_code=stock_code,
            action=action,
            position_size=adjusted_position,
            confidence=confidence,
            reasoning=final_reasoning,
            agent_opinions=opinions,
            debate_result=debate_result,
            risk_assessment=risk_assessment,
            timestamp=datetime.now().isoformat(),
        )


class TradingManager:
    def __init__(self) -> None:
        self.fundamental_agent = FundamentalAgent()
        self.technical_agent = TechnicalAgent()
        self.sentiment_agent = SentimentAgent()
        self.news_agent = NewsAgent()
        self.research_team = ResearchTeam()
        self.trader = TraderAgent()
        self.risk_manager = RiskManagerAgent()
        self.portfolio_manager = PortfolioManagerAgent()
        self._history: list[TradingDecision] = []

    def run_analysis(self, stock_code: str) -> TradingDecision:
        opinions = self._parallel_analyze(stock_code)
        debate_result = self.research_team.debate(opinions, rounds=2)
        action, position_size, reasoning = self.trader.decide(opinions, debate_result)

        confidence = 0.0
        if opinions:
            confidence = sum(op.confidence for op in opinions) / len(opinions)

        risk_assessment = self.risk_manager.assess(
            action, position_size, opinions, debate_result,
        )

        decision = self.portfolio_manager.finalize(
            stock_code=stock_code,
            action=action,
            position_size=position_size,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            opinions=opinions,
            debate_result=debate_result,
            risk_assessment=risk_assessment,
        )

        self._history.append(decision)
        return decision

    def quick_analysis(self, stock_code: str) -> list[AgentOpinion]:
        return self._parallel_analyze(stock_code)

    def get_decision_history(self) -> list[TradingDecision]:
        return list(self._history)

    def _parallel_analyze(self, stock_code: str) -> list[AgentOpinion]:
        agents = [
            self.fundamental_agent,
            self.technical_agent,
            self.sentiment_agent,
            self.news_agent,
        ]

        opinions: list[AgentOpinion] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(agent.analyze, stock_code): agent
                for agent in agents
            }
            for future in as_completed(futures):
                try:
                    opinion = future.result(timeout=30)
                    opinions.append(opinion)
                except Exception:
                    agent = futures[future]
                    mock_opinion = AgentOpinion(
                        role=agent.role,
                        stock_code=stock_code,
                        signal="NEUTRAL",
                        confidence=0.1,
                        reasoning="[降级] 分析超时，使用默认中性意见",
                        key_points=["分析超时"],
                        score=50.0,
                        timestamp=datetime.now().isoformat(),
                    )
                    opinions.append(mock_opinion)

        return opinions

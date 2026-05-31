from __future__ import annotations

from src.agents.base import AgentOpinion, AgentRole, DebateResult


class BullResearcher:
    role = AgentRole.BULL_RESEARCHER

    def build_arguments(self, opinions: list[AgentOpinion]) -> list[str]:
        arguments: list[str] = []

        for op in opinions:
            if op.signal == "BULLISH":
                arguments.append(f"[{op.role.value}] 看多：{op.reasoning}")
            elif op.signal == "NEUTRAL":
                positive_points = [p for p in op.key_points if any(
                    kw in p for kw in ["增长", "强", "低", "合理", "乐观", "正面", "金叉", "多头", "超卖"]
                )]
                if positive_points:
                    arguments.append(f"[{op.role.value}] 中性偏多：{'；'.join(positive_points)}")

        if not arguments:
            for op in opinions:
                if op.score >= 40:
                    arguments.append(f"[{op.role.value}] 评分{op.score}，存在一定支撑")

        if not arguments:
            arguments.append("当前缺乏明确看多信号，但市场存在潜在反弹可能")

        return arguments


class BearResearcher:
    role = AgentRole.BEAR_RESEARCHER

    def build_arguments(self, opinions: list[AgentOpinion]) -> list[str]:
        arguments: list[str] = []

        for op in opinions:
            if op.signal == "BEARISH":
                arguments.append(f"[{op.role.value}] 看空：{op.reasoning}")
            elif op.signal == "NEUTRAL":
                negative_points = [p for p in op.key_points if any(
                    kw in p for kw in ["下降", "弱", "高", "悲观", "负面", "死叉", "空头", "超买", "亏损", "负增长"]
                )]
                if negative_points:
                    arguments.append(f"[{op.role.value}] 中性偏空：{'；'.join(negative_points)}")

        if not arguments:
            for op in opinions:
                if op.score < 60:
                    arguments.append(f"[{op.role.value}] 评分{op.score}，存在下行风险")

        if not arguments:
            arguments.append("当前缺乏明确看空信号，但需警惕潜在回调风险")

        return arguments


class ResearchTeam:
    def __init__(self) -> None:
        self.bull = BullResearcher()
        self.bear = BearResearcher()

    def debate(self, opinions: list[AgentOpinion], rounds: int = 2) -> DebateResult:
        topic = f"股票{opinions[0].stock_code if opinions else 'unknown'}多空辩论"

        bull_arguments: list[str] = []
        bear_arguments: list[str] = []

        bull_score = 0.0
        bear_score = 0.0

        for op in opinions:
            if op.signal == "BULLISH":
                bull_score += op.score * op.confidence
            elif op.signal == "BEARISH":
                bear_score += (100 - op.score) * op.confidence
            else:
                bull_score += op.score * op.confidence * 0.5
                bear_score += (100 - op.score) * op.confidence * 0.5

        for round_num in range(rounds):
            round_bull = self.bull.build_arguments(opinions)
            round_bear = self.bear.build_arguments(opinions)

            bull_arguments.extend(round_bull)
            bear_arguments.extend(round_bear)

            for arg in round_bull:
                bull_score += 5.0
            for arg in round_bear:
                bear_score += 5.0

            if round_num < rounds - 1:
                opinions = self._rebuttal_adjust(opinions, round_bull, round_bear)

        total = bull_score + bear_score
        if total > 0:
            bull_ratio = bull_score / total
            bear_ratio = bear_score / total
        else:
            bull_ratio = 0.5
            bear_ratio = 0.5

        if bull_ratio > 0.6:
            consensus = "BULLISH"
        elif bear_ratio > 0.6:
            consensus = "BEARISH"
        else:
            consensus = "NEUTRAL"

        confidence = round(abs(bull_ratio - bear_ratio), 2)

        return DebateResult(
            topic=topic,
            bull_arguments=bull_arguments,
            bear_arguments=bear_arguments,
            bull_score=round(bull_score, 2),
            bear_score=round(bear_score, 2),
            consensus=consensus,
            confidence=confidence,
        )

    def _rebuttal_adjust(
        self,
        opinions: list[AgentOpinion],
        bull_args: list[str],
        bear_args: list[str],
    ) -> list[AgentOpinion]:
        adjusted: list[AgentOpinion] = []
        for op in opinions:
            score = op.score
            if op.signal == "BULLISH" and len(bear_args) > len(bull_args):
                score = max(score - 3, 0)
            elif op.signal == "BEARISH" and len(bull_args) > len(bear_args):
                score = min(score + 3, 100)
            adjusted.append(
                AgentOpinion(
                    role=op.role,
                    stock_code=op.stock_code,
                    signal=op.signal,
                    confidence=op.confidence,
                    reasoning=op.reasoning,
                    key_points=op.key_points,
                    score=round(score, 2),
                    timestamp=op.timestamp,
                )
            )
        return adjusted

from __future__ import annotations

import random

from src.agents.base import AgentRole, BaseAgent, AgentOpinion

_HAS_FUNDAMENTAL = False
try:
    from src.analysis.fundamental import analyze_fundamental, score_fundamental
    _HAS_FUNDAMENTAL = True
except ImportError:
    pass


class FundamentalAgent(BaseAgent):
    role = AgentRole.FUNDAMENTAL

    def analyze(self, stock_code: str, **kwargs) -> AgentOpinion:
        if _HAS_FUNDAMENTAL:
            try:
                return self._real_analysis(stock_code)
            except Exception:
                pass
        return self._mock_analysis(stock_code)

    def _real_analysis(self, stock_code: str) -> AgentOpinion:
        data = analyze_fundamental(stock_code)
        score = score_fundamental(stock_code)

        pe = data.get("PE", 0.0)
        pb = data.get("PB", 0.0)
        roe = data.get("ROE", 0.0)
        rev_growth = data.get("revenue_growth", 0.0)
        profit_growth = data.get("profit_growth", 0.0)

        key_points: list[str] = []
        reasoning_parts: list[str] = []

        if pe > 0:
            if pe < 15:
                key_points.append(f"PE={pe:.1f}，估值偏低")
                reasoning_parts.append(f"市盈率{pe:.1f}处于低位，估值有吸引力")
            elif pe < 25:
                key_points.append(f"PE={pe:.1f}，估值合理")
                reasoning_parts.append(f"市盈率{pe:.1f}处于合理区间")
            elif pe < 40:
                key_points.append(f"PE={pe:.1f}，估值偏高")
                reasoning_parts.append(f"市盈率{pe:.1f}偏高，需关注估值风险")
            else:
                key_points.append(f"PE={pe:.1f}，估值过高")
                reasoning_parts.append(f"市盈率{pe:.1f}过高，估值风险较大")
        else:
            key_points.append(f"PE={pe:.1f}，亏损状态")
            reasoning_parts.append("公司处于亏损状态")

        if pb > 0:
            if pb < 1:
                key_points.append(f"PB={pb:.1f}，破净")
            elif pb < 2:
                key_points.append(f"PB={pb:.1f}，合理")
            else:
                key_points.append(f"PB={pb:.1f}，偏高")
        reasoning_parts.append(f"市净率{pb:.1f}")

        if roe > 15:
            key_points.append(f"ROE={roe:.1f}%，盈利能力强")
            reasoning_parts.append(f"净资产收益率{roe:.1f}%表现优秀")
        elif roe > 8:
            key_points.append(f"ROE={roe:.1f}%，盈利能力一般")
        else:
            key_points.append(f"ROE={roe:.1f}%，盈利能力弱")
            reasoning_parts.append(f"净资产收益率{roe:.1f}%偏低")

        if rev_growth > 20:
            key_points.append(f"营收增长{rev_growth:.1f}%，高增长")
        elif rev_growth > 0:
            key_points.append(f"营收增长{rev_growth:.1f}%，正增长")
        else:
            key_points.append(f"营收增长{rev_growth:.1f}%，负增长")
        reasoning_parts.append(f"营收同比增长{rev_growth:.1f}%")

        if profit_growth > 20:
            key_points.append(f"净利润增长{profit_growth:.1f}%，高增长")
        elif profit_growth > 0:
            key_points.append(f"净利润增长{profit_growth:.1f}%，正增长")
        else:
            key_points.append(f"净利润增长{profit_growth:.1f}%，负增长")
        reasoning_parts.append(f"净利润同比增长{profit_growth:.1f}%")

        if score >= 70:
            signal = "BULLISH"
        elif score >= 40:
            signal = "NEUTRAL"
        else:
            signal = "BEARISH"

        confidence = min(abs(score - 50) / 50, 1.0)

        return self._create_opinion(
            stock_code=stock_code,
            signal=signal,
            confidence=round(confidence, 2),
            reasoning="；".join(reasoning_parts),
            key_points=key_points,
            score=score,
        )

    def _mock_analysis(self, stock_code: str) -> AgentOpinion:
        pe = round(random.uniform(5, 80), 1)
        pb = round(random.uniform(0.5, 8), 1)
        roe = round(random.uniform(-5, 30), 1)
        rev_growth = round(random.uniform(-20, 50), 1)
        profit_growth = round(random.uniform(-30, 60), 1)

        score = round(random.uniform(10, 90), 2)

        key_points = [
            f"PE={pe}，{'偏低' if pe < 15 else '合理' if pe < 25 else '偏高'}",
            f"PB={pb}，{'偏低' if pb < 1 else '合理' if pb < 2 else '偏高'}",
            f"ROE={roe}%，{'强' if roe > 15 else '一般' if roe > 8 else '弱'}",
            f"营收增长{rev_growth}%",
            f"净利润增长{profit_growth}%",
        ]

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
            reasoning=f"[模拟] 基本面分析：PE={pe}, PB={pb}, ROE={roe}%, 营收增长{rev_growth}%, 净利润增长{profit_growth}%",
            key_points=key_points,
            score=score,
        )

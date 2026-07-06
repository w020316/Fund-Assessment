from __future__ import annotations

from src.agents.base import AgentRole, BaseAgent, AgentOpinion

_HAS_TECHNICAL = False
try:
    from src.analysis.technical import compute_indicators, score_technical
    _HAS_TECHNICAL = True
except ImportError:
    pass

_HAS_AKSHARE = False
try:
    import akshare as ak
    import pandas as pd
    _HAS_AKSHARE = True
except ImportError:
    pass


class TechnicalAgent(BaseAgent):
    role = AgentRole.TECHNICAL

    def analyze(self, stock_code: str, **kwargs) -> AgentOpinion:
        if _HAS_TECHNICAL and _HAS_AKSHARE:
            try:
                return self._real_analysis(stock_code)
            except Exception:
                pass
        return self._mock_analysis(stock_code)

    def _real_analysis(self, stock_code: str) -> AgentOpinion:
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="qfq")
        if df is None or df.empty:
            return self._create_opinion(
                stock_code=stock_code,
                signal="NEUTRAL",
                confidence=0.1,
                reasoning="技术面数据获取为空，无法计算指标",
                key_points=["行情数据为空，技术分析降级"],
                score=50.0,
            )

        df.columns = [c.strip() for c in df.columns]
        col_map = {
            "开盘": "open", "最高": "high", "最低": "low",
            "收盘": "close", "成交量": "volume",
        }
        rename_map = {k: v for k, v in col_map.items() if k in df.columns}
        df = df.rename(columns=rename_map)

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            return self._create_opinion(
                stock_code=stock_code,
                signal="NEUTRAL",
                confidence=0.1,
                reasoning="技术面数据字段不完整，缺少必要列",
                key_points=["行情字段缺失，技术分析降级"],
                score=50.0,
            )

        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])

        if len(df) < 60:
            return self._create_opinion(
                stock_code=stock_code,
                signal="NEUTRAL",
                confidence=0.1,
                reasoning=f"技术面数据样本不足，仅{len(df)}条，需至少60条",
                key_points=[f"数据样本不足（{len(df)}条），技术分析降级"],
                score=50.0,
            )

        df = df.tail(120).reset_index(drop=True)
        df_with_indicators = compute_indicators(df)
        score = score_technical(df_with_indicators)

        latest = df_with_indicators.iloc[-1]
        key_points: list[str] = []
        reasoning_parts: list[str] = []

        close = float(latest.get("close", 0))
        ma5 = latest.get("MA5")
        ma10 = latest.get("MA10")
        ma20 = latest.get("MA20")
        ma60 = latest.get("MA60")

        if all(v is not None and not pd.isna(v) for v in [ma5, ma10, ma20, ma60]):
            ma5, ma10, ma20, ma60 = float(ma5), float(ma10), float(ma20), float(ma60)
            if close > ma5 > ma10 > ma20 > ma60:
                key_points.append("均线多头排列")
                reasoning_parts.append("MA5>MA10>MA20>MA60，多头排列")
            elif close < ma5 < ma10 < ma20 < ma60:
                key_points.append("均线空头排列")
                reasoning_parts.append("MA5<MA10<MA20<MA60，空头排列")
            else:
                key_points.append("均线交织")
                reasoning_parts.append("均线走势交织，方向不明")
        else:
            key_points.append("均线数据不完整")

        macd_val = latest.get("MACD")
        macd_signal = latest.get("MACD_signal")
        macd_hist = latest.get("MACD_hist")
        if all(v is not None and not pd.isna(v) for v in [macd_val, macd_signal, macd_hist]):
            macd_val, macd_signal, macd_hist = float(macd_val), float(macd_signal), float(macd_hist)
            if macd_val > macd_signal and macd_hist > 0:
                key_points.append("MACD金叉且红柱放大")
                reasoning_parts.append("MACD金叉，红柱放大，动能增强")
            elif macd_val > macd_signal:
                key_points.append("MACD金叉")
                reasoning_parts.append("MACD金叉，动能转强")
            elif macd_val < macd_signal and macd_hist < 0:
                key_points.append("MACD死叉且绿柱放大")
                reasoning_parts.append("MACD死叉，绿柱放大，动能减弱")
            else:
                key_points.append("MACD死叉")
                reasoning_parts.append("MACD死叉，动能转弱")
        else:
            key_points.append("MACD数据不可用")

        kdj_k = latest.get("KDJ_K")
        kdj_d = latest.get("KDJ_D")
        kdj_j = latest.get("KDJ_J")
        if all(v is not None and not pd.isna(v) for v in [kdj_k, kdj_d, kdj_j]):
            kdj_k, kdj_d, kdj_j = float(kdj_k), float(kdj_d), float(kdj_j)
            if kdj_j > 80:
                key_points.append(f"KDJ超买(J={kdj_j:.0f})")
            elif kdj_j < 20:
                key_points.append(f"KDJ超卖(J={kdj_j:.0f})")
            else:
                key_points.append(f"KDJ中性(J={kdj_j:.0f})")
            reasoning_parts.append(f"KDJ: K={kdj_k:.0f}, D={kdj_d:.0f}, J={kdj_j:.0f}")
        else:
            key_points.append("KDJ数据不可用")

        rsi = latest.get("RSI")
        if rsi is not None and not pd.isna(rsi):
            rsi = float(rsi)
            if rsi > 70:
                key_points.append(f"RSI超买({rsi:.0f})")
                reasoning_parts.append(f"RSI={rsi:.0f}，超买区域")
            elif rsi < 30:
                key_points.append(f"RSI超卖({rsi:.0f})")
                reasoning_parts.append(f"RSI={rsi:.0f}，超卖区域")
            else:
                key_points.append(f"RSI中性({rsi:.0f})")
                reasoning_parts.append(f"RSI={rsi:.0f}，中性区域")
        else:
            key_points.append("RSI数据不可用")

        boll_upper = latest.get("BOLL_upper")
        boll_lower = latest.get("BOLL_lower")
        if all(v is not None and not pd.isna(v) for v in [boll_upper, boll_lower, close]):
            boll_upper, boll_lower = float(boll_upper), float(boll_lower)
            if close > boll_upper:
                key_points.append("突破布林上轨")
            elif close < boll_lower:
                key_points.append("跌破布林下轨")
            else:
                key_points.append("布林带内运行")
            reasoning_parts.append(f"BOLL: 上轨{boll_upper:.2f}, 下轨{boll_lower:.2f}")

        atr = latest.get("ATR")
        if atr is not None and not pd.isna(atr) and close > 0:
            atr = float(atr)
            atr_ratio = atr / close
            key_points.append(f"ATR占比{atr_ratio:.2%}")
            reasoning_parts.append(f"ATR={atr:.2f}，波动率{'高' if atr_ratio > 0.03 else '中' if atr_ratio > 0.015 else '低'}")

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
            reasoning="；".join(reasoning_parts),
            key_points=key_points,
            score=score,
        )

    def _mock_analysis(self, stock_code: str) -> AgentOpinion:
        return self._create_opinion(
            stock_code=stock_code,
            signal="NEUTRAL",
            confidence=0.0,
            reasoning="[降级] 技术面分析模块不可用",
            key_points=["技术面分析模块不可用，已降级"],
            score=50.0,
        )

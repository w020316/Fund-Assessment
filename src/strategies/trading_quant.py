from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta
import akshare as ak
from enum import Enum
from dataclasses import dataclass, field


class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    WATCH = "WATCH"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


@dataclass
class ScoreResult:
    technical: float = 0.0
    capital: float = 0.0
    fundamental: float = 0.0
    news: float = 0.0
    sentiment: float = 0.0
    composite: float = 0.0
    signal: Signal = Signal.HOLD
    suggestion: str = ""


WEIGHTS = {
    "technical": 0.25,
    "capital": 0.30,
    "fundamental": 0.10,
    "news": 0.20,
    "sentiment": 0.15,
}


class TradingQuant:
    def __init__(self) -> None:
        self._cache: dict[str, pd.DataFrame] = {}

    def _get_kline(self, stock_code: str, period: str = "daily", days: int = 120) -> pd.DataFrame:
        key = f"{stock_code}_{period}_{days}"
        if key in self._cache:
            return self._cache[key]
        symbol = f"{stock_code}"
        df = ak.stock_zh_a_hist(symbol=symbol, period=period, adjust="qfq")
        if df is not None and not df.empty:
            df = df.tail(days).reset_index(drop=True)
            self._cache[key] = df
        return df

    def _score_technical(self, stock_code: str) -> float:
        df = self._get_kline(stock_code)
        if df.empty or len(df) < 30:
            return 50.0
        close = df["收盘"].astype(float)
        score = 50.0
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        if ma5.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]:
            score += 15.0
        elif ma5.iloc[-1] > ma20.iloc[-1]:
            score += 8.0
        elif ma5.iloc[-1] < ma20.iloc[-1]:
            score -= 8.0
        rsi = ta.rsi(close, length=14)
        if rsi is not None and not rsi.empty:
            rsi_val = float(rsi.iloc[-1])
            if 40 <= rsi_val <= 60:
                score += 10.0
            elif 30 <= rsi_val <= 70:
                score += 5.0
            elif rsi_val > 80:
                score -= 15.0
            elif rsi_val < 20:
                score += 10.0
        macd = ta.macd(close)
        if macd is not None and not macd.empty:
            macd_line = macd.iloc[-1, 0]
            signal_line = macd.iloc[-1, 1]
            if macd_line > signal_line:
                score += 10.0
            else:
                score -= 5.0
        if close.iloc[-1] > close.iloc[-5]:
            score += 5.0
        else:
            score -= 5.0
        return max(0.0, min(100.0, score))

    def _score_capital(self, stock_code: str) -> float:
        score = 50.0
        try:
            df = ak.stock_individual_fund_flow(stock=stock_code, market="sh" if stock_code.startswith("6") else "sz")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                net_amount = float(latest.get("主力净流入-净额", 0))
                net_pct = float(latest.get("主力净流入-净占比", 0))
                if net_amount > 0:
                    score += min(20.0, net_pct * 2)
                else:
                    score += max(-20.0, net_pct * 2)
                if net_pct > 5:
                    score += 10.0
                elif net_pct < -5:
                    score -= 10.0
        except Exception:
            pass
        return max(0.0, min(100.0, score))

    def _score_fundamental(self, stock_code: str) -> float:
        score = 50.0
        try:
            df = ak.stock_financial_analysis_indicator(symbol=stock_code)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                roe = float(latest.get("净资产收益率(%)", 0))
                gross_margin = float(latest.get("销售毛利率(%)", 0))
                if roe > 15:
                    score += 15.0
                elif roe > 8:
                    score += 8.0
                elif roe < 0:
                    score -= 10.0
                if gross_margin > 40:
                    score += 10.0
                elif gross_margin > 20:
                    score += 5.0
                elif gross_margin < 10:
                    score -= 5.0
        except Exception:
            pass
        try:
            df_val = ak.stock_a_indicator_lg(symbol=stock_code)
            if df_val is not None and not df_val.empty:
                latest_val = df_val.iloc[-1]
                pe = float(latest_val.get("pe", 0))
                pb = float(latest_val.get("pb", 0))
                if 0 < pe < 15:
                    score += 10.0
                elif 15 <= pe < 30:
                    score += 5.0
                elif pe > 60:
                    score -= 10.0
                if 0 < pb < 1.5:
                    score += 5.0
                elif pb > 5:
                    score -= 5.0
        except Exception:
            pass
        return max(0.0, min(100.0, score))

    def _score_news(self, stock_code: str) -> float:
        score = 50.0
        try:
            df = ak.stock_news_em(symbol=stock_code)
            if df is not None and not df.empty:
                positive_keywords = ["利好", "增长", "突破", "创新高", "签约", "中标", "回购"]
                negative_keywords = ["利空", "下降", "亏损", "违规", "处罚", "减持", "风险"]
                pos_count = 0
                neg_count = 0
                for title in df["新闻标题"].head(20):
                    title_str = str(title)
                    if any(kw in title_str for kw in positive_keywords):
                        pos_count += 1
                    if any(kw in title_str for kw in negative_keywords):
                        neg_count += 1
                score += (pos_count - neg_count) * 5.0
        except Exception:
            pass
        return max(0.0, min(100.0, score))

    def _score_sentiment(self, stock_code: str) -> float:
        score = 50.0
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                row = df[df["代码"] == stock_code]
                if not row.empty:
                    turnover = float(row.iloc[0].get("换手率", 0))
                    if turnover > 10:
                        score += 15.0
                    elif turnover > 5:
                        score += 8.0
                    elif turnover < 1:
                        score -= 5.0
                    amt = float(row.iloc[0].get("成交额", 0))
                    if amt > 1e9:
                        score += 10.0
                    elif amt > 5e8:
                        score += 5.0
        except Exception:
            pass
        return max(0.0, min(100.0, score))

    @staticmethod
    def _signal_from_score(score: float) -> Signal:
        if score >= 80:
            return Signal.STRONG_BUY
        if score >= 65:
            return Signal.BUY
        if score >= 50:
            return Signal.WATCH
        if score >= 35:
            return Signal.HOLD
        if score >= 20:
            return Signal.SELL
        return Signal.STRONG_SELL

    @staticmethod
    def _suggestion_from_signal(signal: Signal) -> str:
        mapping: dict[Signal, str] = {
            Signal.STRONG_BUY: "强烈买入，多维度共振，可重仓参与",
            Signal.BUY: "买入信号，基本面与技术面配合较好",
            Signal.WATCH: "观望为主，等待更明确信号",
            Signal.HOLD: "持有不动，当前无明确方向",
            Signal.SELL: "卖出信号，风险加大，建议减仓",
            Signal.STRONG_SELL: "强烈卖出，多维度看空，尽快离场",
        }
        return mapping[signal]

    def stock_analysis(self, stock_code: str) -> dict:
        technical = self._score_technical(stock_code)
        capital = self._score_capital(stock_code)
        fundamental = self._score_fundamental(stock_code)
        news = self._score_news(stock_code)
        sentiment = self._score_sentiment(stock_code)
        composite = (
            technical * WEIGHTS["technical"]
            + capital * WEIGHTS["capital"]
            + fundamental * WEIGHTS["fundamental"]
            + news * WEIGHTS["news"]
            + sentiment * WEIGHTS["sentiment"]
        )
        signal = self._signal_from_score(composite)
        suggestion = self._suggestion_from_signal(signal)
        return {
            "stock_code": stock_code,
            "scores": {
                "technical": round(technical, 2),
                "capital": round(capital, 2),
                "fundamental": round(fundamental, 2),
                "news": round(news, 2),
                "sentiment": round(sentiment, 2),
            },
            "composite": round(composite, 2),
            "signal": signal.value,
            "suggestion": suggestion,
        }

    def capital_flow(self, stock_code: str) -> dict:
        result: dict = {
            "stock_code": stock_code,
            "main_net_inflow": 0.0,
            "main_net_pct": 0.0,
            "retail_net_inflow": 0.0,
            "retail_net_pct": 0.0,
            "super_large_net": 0.0,
            "large_net": 0.0,
            "medium_net": 0.0,
            "small_net": 0.0,
        }
        try:
            df = ak.stock_individual_fund_flow(
                stock=stock_code, market="sh" if stock_code.startswith("6") else "sz"
            )
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                result["main_net_inflow"] = float(latest.get("主力净流入-净额", 0))
                result["main_net_pct"] = float(latest.get("主力净流入-净占比", 0))
                result["retail_net_inflow"] = float(latest.get("散户净流入-净额", 0))
                result["retail_net_pct"] = float(latest.get("散户净流入-净占比", 0))
        except Exception:
            pass
        try:
            df_detail = ak.stock_individual_fund_flow_rank(indicator="今日")
            if df_detail is not None and not df_detail.empty:
                row = df_detail[df_detail["代码"] == stock_code]
                if not row.empty:
                    r = row.iloc[0]
                    result["super_large_net"] = float(r.get("超大单净流入-净额", 0))
                    result["large_net"] = float(r.get("大单净流入-净额", 0))
                    result["medium_net"] = float(r.get("中单净流入-净额", 0))
                    result["small_net"] = float(r.get("小单净流入-净额", 0))
        except Exception:
            pass
        return result

    def northbound_flow(self) -> dict:
        result: dict = {
            "total_net_inflow": 0.0,
            "sh_net_inflow": 0.0,
            "sz_net_inflow": 0.0,
            "top_stocks": [],
        }
        try:
            df = ak.stock_hsgt_north_net_flow_in_em()
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                result["total_net_inflow"] = float(latest.get("当日净流入", 0))
        except Exception:
            pass
        try:
            df_sh = ak.stock_hsgt_north_net_flow_in_em(symbol="沪股通")
            if df_sh is not None and not df_sh.empty:
                result["sh_net_inflow"] = float(df_sh.iloc[-1].get("当日净流入", 0))
        except Exception:
            pass
        try:
            df_sz = ak.stock_hsgt_north_net_flow_in_em(symbol="深股通")
            if df_sz is not None and not df_sz.empty:
                result["sz_net_inflow"] = float(df_sz.iloc[-1].get("当日净流入", 0))
        except Exception:
            pass
        try:
            df_top = ak.stock_hsgt_hold_stock_em(market="北向")
            if df_top is not None and not df_top.empty:
                top = df_top.head(10)
                result["top_stocks"] = [
                    {
                        "code": str(row.get("股票代码", "")),
                        "name": str(row.get("股票简称", "")),
                        "hold_amount": float(row.get("持股数量", 0)),
                        "hold_pct": float(row.get("持股占流通股比", 0)),
                    }
                    for _, row in top.iterrows()
                ]
        except Exception:
            pass
        return result

    def market_anomaly(self) -> list[dict]:
        anomalies: list[dict] = []
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return anomalies
            df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
            df["换手率"] = pd.to_numeric(df["换手率"], errors="coerce")
            df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce")
            surge = df[df["涨跌幅"] > 7].sort_values("涨跌幅", ascending=False).head(20)
            for _, row in surge.iterrows():
                anomalies.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "type": "涨幅异动",
                    "change_pct": round(float(row.get("涨跌幅", 0)), 2),
                    "turnover": round(float(row.get("换手率", 0)), 2),
                    "amount": float(row.get("成交额", 0)),
                })
            high_turnover = df[(df["换手率"] > 15) & (df["涨跌幅"].between(-3, 3))].sort_values(
                "换手率", ascending=False
            ).head(10)
            for _, row in high_turnover.iterrows():
                anomalies.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "type": "换手异动",
                    "change_pct": round(float(row.get("涨跌幅", 0)), 2),
                    "turnover": round(float(row.get("换手率", 0)), 2),
                    "amount": float(row.get("成交额", 0)),
                })
        except Exception:
            pass
        return anomalies

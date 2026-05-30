from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta
import akshare as ak


@dataclass
class FundamentalData:
    pe: float = 0.0
    pb: float = 0.0
    ps: float = 0.0
    roe: float = 0.0
    gross_margin: float = 0.0
    net_margin: float = 0.0
    debt_ratio: float = 0.0
    revenue_growth: float = 0.0
    profit_growth: float = 0.0
    eps: float = 0.0


@dataclass
class TechnicalData:
    trend: str = "neutral"
    support: float = 0.0
    resistance: float = 0.0
    rsi: float = 50.0
    macd_signal: str = "neutral"
    pattern: str = "none"
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0


@dataclass
class IndustryData:
    industry: str = ""
    rank: int = 0
    total_in_industry: int = 0
    policy_bias: str = "neutral"
    competition_level: str = "medium"


class AStockAnalyst:
    def __init__(self) -> None:
        self._kline_cache: dict[str, pd.DataFrame] = {}

    def _get_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        if stock_code in self._kline_cache:
            return self._kline_cache[stock_code]
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="qfq")
            if df is not None and not df.empty:
                df = df.tail(days).reset_index(drop=True)
                self._kline_cache[stock_code] = df
                return df
        except Exception:
            pass
        return pd.DataFrame()

    def _analyze_fundamental(self, stock_code: str) -> FundamentalData:
        data = FundamentalData()
        try:
            df_val = ak.stock_a_indicator_lg(symbol=stock_code)
            if df_val is not None and not df_val.empty:
                latest = df_val.iloc[-1]
                data.pe = float(latest.get("pe", 0))
                data.pb = float(latest.get("pb", 0))
                data.ps = float(latest.get("ps", 0))
        except Exception:
            pass
        try:
            df_fin = ak.stock_financial_analysis_indicator(symbol=stock_code)
            if df_fin is not None and not df_fin.empty:
                latest = df_fin.iloc[0]
                data.roe = float(latest.get("净资产收益率(%)", 0))
                data.gross_margin = float(latest.get("销售毛利率(%)", 0))
                data.net_margin = float(latest.get("销售净利率(%)", 0))
                data.debt_ratio = float(latest.get("资产负债率(%)", 0))
        except Exception:
            pass
        try:
            df_growth = ak.stock_financial_abstract_ths(symbol=stock_code)
            if df_growth is not None and not df_growth.empty:
                latest = df_growth.iloc[0]
                data.revenue_growth = float(latest.get("营业收入同比增长(%)", 0))
                data.profit_growth = float(latest.get("净利润同比增长(%)", 0))
                data.eps = float(latest.get("每股收益", 0))
        except Exception:
            pass
        return data

    def _analyze_technical(self, stock_code: str) -> TechnicalData:
        data = TechnicalData()
        df = self._get_kline(stock_code)
        if df.empty or len(df) < 30:
            return data
        close = df["收盘"].astype(float)
        high = df["最高"].astype(float)
        low = df["最低"].astype(float)
        data.ma5 = float(close.rolling(5).mean().iloc[-1])
        data.ma20 = float(close.rolling(20).mean().iloc[-1])
        data.ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else float(close.mean())
        if data.ma5 > data.ma20 > data.ma60:
            data.trend = "bullish"
        elif data.ma5 < data.ma20 < data.ma60:
            data.trend = "bearish"
        else:
            data.trend = "neutral"
        recent_low = float(low.tail(20).min())
        recent_high = float(high.tail(20).max())
        data.support = round(recent_low, 2)
        data.resistance = round(recent_high, 2)
        rsi = ta.rsi(close, length=14)
        if rsi is not None and not rsi.empty:
            data.rsi = round(float(rsi.iloc[-1]), 2)
        macd = ta.macd(close)
        if macd is not None and not macd.empty:
            macd_line = float(macd.iloc[-1, 0])
            signal_line = float(macd.iloc[-1, 1])
            hist = float(macd.iloc[-1, 2])
            if macd_line > signal_line and hist > 0:
                data.macd_signal = "bullish"
            elif macd_line < signal_line and hist < 0:
                data.macd_signal = "bearish"
            else:
                data.macd_signal = "neutral"
        if len(close) >= 5:
            if all(close.iloc[-i] > close.iloc[-i - 1] for i in range(1, 4)):
                data.pattern = "连续上涨"
            elif all(close.iloc[-i] < close.iloc[-i - 1] for i in range(1, 4)):
                data.pattern = "连续下跌"
            elif close.iloc[-1] > data.ma5 and close.iloc[-2] < float(close.rolling(5).mean().iloc[-2]):
                data.pattern = "金叉"
            elif close.iloc[-1] < data.ma5 and close.iloc[-2] > float(close.rolling(5).mean().iloc[-2]):
                data.pattern = "死叉"
        return data

    def _analyze_industry(self, stock_code: str) -> IndustryData:
        data = IndustryData()
        try:
            df_info = ak.stock_individual_info_em(symbol=stock_code)
            if df_info is not None and not df_info.empty:
                industry_row = df_info[df_info["item"] == "行业"]
                if not industry_row.empty:
                    data.industry = str(industry_row.iloc[0]["value"])
        except Exception:
            pass
        if not data.industry:
            return data
        try:
            df_sector = ak.stock_board_industry_name_em()
            if df_sector is not None and not df_sector.empty:
                sector_row = df_sector[df_sector["板块名称"].str.contains(data.industry, na=False)]
                if not sector_row.empty:
                    sector_code = str(sector_row.iloc[0].get("板块代码", ""))
                    try:
                        df_members = ak.stock_board_industry_cons_em(symbol=sector_code)
                        if df_members is not None and not df_members.empty:
                            data.total_in_industry = len(df_members)
                            member_row = df_members[df_members["代码"] == stock_code]
                            if not member_row.empty:
                                df_sorted = df_members.sort_values("总市值", ascending=False)
                                rank_list = df_sorted["代码"].tolist()
                                if stock_code in rank_list:
                                    data.rank = rank_list.index(stock_code) + 1
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            df_policy = ak.stock_news_em(symbol=stock_code)
            if df_policy is not None and not df_policy.empty:
                positive_kw = ["政策支持", "利好", "补贴", "扶持", "规划"]
                negative_kw = ["监管", "限制", "处罚", "收紧"]
                pos = sum(1 for t in df_policy["新闻标题"].head(10) if any(k in str(t) for k in positive_kw))
                neg = sum(1 for t in df_policy["新闻标题"].head(10) if any(k in str(t) for k in negative_kw))
                if pos > neg + 2:
                    data.policy_bias = "positive"
                elif neg > pos + 2:
                    data.policy_bias = "negative"
        except Exception:
            pass
        if data.total_in_industry > 0:
            if data.total_in_industry > 100:
                data.competition_level = "high"
            elif data.total_in_industry > 50:
                data.competition_level = "medium"
            else:
                data.competition_level = "low"
        return data

    def comprehensive_analysis(self, stock_code: str) -> dict:
        fundamental = self._analyze_fundamental(stock_code)
        technical = self._analyze_technical(stock_code)
        industry = self._analyze_industry(stock_code)
        fund_score = 50.0
        if 0 < fundamental.pe < 15:
            fund_score += 10.0
        elif fundamental.pe > 50:
            fund_score -= 10.0
        if fundamental.roe > 15:
            fund_score += 10.0
        elif fundamental.roe < 5:
            fund_score -= 5.0
        if fundamental.gross_margin > 40:
            fund_score += 5.0
        if fundamental.revenue_growth > 20:
            fund_score += 5.0
        tech_score = 50.0
        if technical.trend == "bullish":
            tech_score += 15.0
        elif technical.trend == "bearish":
            tech_score -= 15.0
        if technical.macd_signal == "bullish":
            tech_score += 10.0
        elif technical.macd_signal == "bearish":
            tech_score -= 10.0
        if 40 <= technical.rsi <= 60:
            tech_score += 5.0
        elif technical.rsi > 70:
            tech_score -= 10.0
        elif technical.rsi < 30:
            tech_score += 5.0
        industry_score = 50.0
        if industry.policy_bias == "positive":
            industry_score += 15.0
        elif industry.policy_bias == "negative":
            industry_score -= 10.0
        if industry.rank > 0 and industry.total_in_industry > 0:
            rank_pct = industry.rank / industry.total_in_industry
            if rank_pct < 0.2:
                industry_score += 10.0
            elif rank_pct > 0.8:
                industry_score -= 5.0
        composite = fund_score * 0.35 + tech_score * 0.40 + industry_score * 0.25
        return {
            "stock_code": stock_code,
            "fundamental": {
                "pe": fundamental.pe,
                "pb": fundamental.pb,
                "ps": fundamental.ps,
                "roe": fundamental.roe,
                "gross_margin": fundamental.gross_margin,
                "net_margin": fundamental.net_margin,
                "debt_ratio": fundamental.debt_ratio,
                "revenue_growth": fundamental.revenue_growth,
                "profit_growth": fundamental.profit_growth,
                "eps": fundamental.eps,
                "score": round(fund_score, 2),
            },
            "technical": {
                "trend": technical.trend,
                "support": technical.support,
                "resistance": technical.resistance,
                "rsi": technical.rsi,
                "macd_signal": technical.macd_signal,
                "pattern": technical.pattern,
                "ma5": round(technical.ma5, 2),
                "ma20": round(technical.ma20, 2),
                "ma60": round(technical.ma60, 2),
                "score": round(tech_score, 2),
            },
            "industry": {
                "industry": industry.industry,
                "rank": industry.rank,
                "total_in_industry": industry.total_in_industry,
                "policy_bias": industry.policy_bias,
                "competition_level": industry.competition_level,
                "score": round(industry_score, 2),
            },
            "composite_score": round(composite, 2),
        }

    def compare_stocks(self, codes: list[str]) -> dict:
        results: dict[str, dict] = {}
        for code in codes:
            results[code] = self.comprehensive_analysis(code)
        if len(results) < 2:
            return {"stocks": results, "ranking": []}
        ranking = sorted(
            results.items(),
            key=lambda x: x[1].get("composite_score", 0),
            reverse=True,
        )
        return {
            "stocks": results,
            "ranking": [
                {"stock_code": code, "composite_score": data.get("composite_score", 0)}
                for code, data in ranking
            ],
        }

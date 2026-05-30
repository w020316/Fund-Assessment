import numpy as np
import pandas as pd
import akshare as ak


def analyze_fundamental(stock_code: str) -> dict:
    result: dict = {
        "PE": 0.0,
        "PB": 0.0,
        "ROE": 0.0,
        "revenue_growth": 0.0,
        "profit_growth": 0.0,
    }

    try:
        indicator_df = ak.stock_a_indicator_lg(symbol=stock_code)
        if indicator_df is not None and not indicator_df.empty:
            latest = indicator_df.iloc[-1]
            indicator_df.columns = [c.strip() for c in indicator_df.columns]
            for col in ["pe", "pe_ttm", "市盈率"]:
                if col in indicator_df.columns:
                    val = latest[col]
                    if pd.notna(val):
                        result["PE"] = float(val)
                        break
            for col in ["pb", "市净率"]:
                if col in indicator_df.columns:
                    val = latest[col]
                    if pd.notna(val):
                        result["PB"] = float(val)
                        break
    except Exception:
        pass

    try:
        fin_df = ak.stock_financial_analysis_indicator(symbol=stock_code)
        if fin_df is not None and not fin_df.empty:
            fin_df.columns = [c.strip() for c in fin_df.columns]
            latest_fin = fin_df.iloc[-1]
            for col in ["净资产收益率", "roe", "加权净资产收益率"]:
                if col in fin_df.columns:
                    val = latest_fin[col]
                    if pd.notna(val):
                        result["ROE"] = float(val)
                        break
            for col in ["营业收入同比增长率", "营收同比增长率", "营业收入增长率"]:
                if col in fin_df.columns:
                    val = latest_fin[col]
                    if pd.notna(val):
                        result["revenue_growth"] = float(val)
                        break
            for col in ["净利润同比增长率", "净利润增长率", "归母净利润同比增长率"]:
                if col in fin_df.columns:
                    val = latest_fin[col]
                    if pd.notna(val):
                        result["profit_growth"] = float(val)
                        break
    except Exception:
        pass

    return result


def _score_valuation(pe: float, pb: float) -> float:
    score = 20.0

    if pe > 0:
        if pe < 15:
            score += 10.0
        elif pe < 25:
            score += 7.0
        elif pe < 40:
            score += 4.0
        elif pe < 60:
            score += 2.0
        else:
            score += 0.0
    elif pe < 0:
        score += 2.0

    if pb > 0:
        if pb < 1:
            score += 10.0
        elif pb < 2:
            score += 7.0
        elif pb < 3:
            score += 4.0
        elif pb < 5:
            score += 2.0
        else:
            score += 0.0
    elif pb < 0:
        score += 2.0

    return min(score, 40.0)


def _score_profitability(roe: float) -> float:
    if roe > 20:
        return 30.0
    elif roe > 15:
        return 24.0
    elif roe > 10:
        return 18.0
    elif roe > 5:
        return 12.0
    elif roe > 0:
        return 6.0
    else:
        return 2.0


def _score_growth(revenue_growth: float, profit_growth: float) -> float:
    score = 0.0

    if revenue_growth > 30:
        score += 15.0
    elif revenue_growth > 15:
        score += 12.0
    elif revenue_growth > 5:
        score += 9.0
    elif revenue_growth > 0:
        score += 6.0
    else:
        score += 2.0

    if profit_growth > 30:
        score += 15.0
    elif profit_growth > 15:
        score += 12.0
    elif profit_growth > 5:
        score += 9.0
    elif profit_growth > 0:
        score += 6.0
    else:
        score += 2.0

    return min(score, 30.0)


def score_fundamental(stock_code: str) -> float:
    data = analyze_fundamental(stock_code)

    val_score = _score_valuation(data["PE"], data["PB"])
    profit_score = _score_profitability(data["ROE"])
    growth_score = _score_growth(data["revenue_growth"], data["profit_growth"])

    total = val_score + profit_score + growth_score
    return round(total, 2)

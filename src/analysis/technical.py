import numpy as np
import pandas as pd
import pandas_ta as ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    for period in [5, 10, 20, 60]:
        result[f"MA{period}"] = ta.sma(result["close"], length=period)

    macd = ta.macd(result["close"])
    if macd is not None:
        result["MACD"] = macd.iloc[:, 0]
        result["MACD_signal"] = macd.iloc[:, 1]
        result["MACD_hist"] = macd.iloc[:, 2]

    stoch = ta.stoch(result["high"], result["low"], result["close"])
    if stoch is not None:
        result["KDJ_K"] = stoch.iloc[:, 0]
        result["KDJ_D"] = stoch.iloc[:, 1]
        result["KDJ_J"] = 3 * result["KDJ_K"] - 2 * result["KDJ_D"]

    result["RSI"] = ta.rsi(result["close"], length=14)

    boll = ta.bbands(result["close"], length=20)
    if boll is not None:
        result["BOLL_upper"] = boll.iloc[:, 0]
        result["BOLL_mid"] = boll.iloc[:, 1]
        result["BOLL_lower"] = boll.iloc[:, 2]

    result["ATR"] = ta.atr(result["high"], result["low"], result["close"], length=14)

    typical_price = (result["high"] + result["low"] + result["close"]) / 3
    cumulative_volume = result["volume"].cumsum()
    cumulative_tp_volume = (typical_price * result["volume"]).cumsum()
    result["VWAP"] = cumulative_tp_volume / cumulative_volume

    return result


def _score_ma_alignment(row: pd.Series) -> float:
    mas = []
    for period in [5, 10, 20, 60]:
        col = f"MA{period}"
        if col in row.index and not pd.isna(row[col]):
            mas.append(row[col])
        else:
            return 0.0

    if len(mas) < 4:
        return 0.0

    is_bullish = all(mas[i] >= mas[i + 1] for i in range(len(mas) - 1))
    is_bearish = all(mas[i] <= mas[i + 1] for i in range(len(mas) - 1))

    if is_bullish:
        return 30.0
    elif is_bearish:
        return 0.0

    score = 15.0
    close = row["close"]
    if close > mas[0]:
        score += 5.0
    if close > mas[1]:
        score += 3.0
    if close > mas[2]:
        score += 2.0
    return min(score, 30.0)


def _score_momentum(row: pd.Series) -> float:
    score = 0.0

    macd_val = row.get("MACD")
    macd_signal = row.get("MACD_signal")
    macd_hist = row.get("MACD_hist")

    if macd_val is not None and macd_signal is not None and macd_hist is not None:
        if not pd.isna(macd_val) and not pd.isna(macd_signal) and not pd.isna(macd_hist):
            if macd_val > macd_signal:
                score += 10.0
                if macd_hist > 0:
                    score += 5.0
            else:
                score += 2.0
            if macd_val > 0:
                score += 5.0

    kdj_k = row.get("KDJ_K")
    kdj_d = row.get("KDJ_D")
    kdj_j = row.get("KDJ_J")

    if kdj_k is not None and kdj_d is not None and kdj_j is not None:
        if not pd.isna(kdj_k) and not pd.isna(kdj_d) and not pd.isna(kdj_j):
            if kdj_k > kdj_d:
                score += 5.0
            if 20 < kdj_j < 80:
                score += 5.0
            elif kdj_j <= 20:
                score += 2.0

    return min(score, 30.0)


def _score_rsi(row: pd.Series) -> float:
    rsi = row.get("RSI")
    if rsi is None or pd.isna(rsi):
        return 10.0

    if 40 <= rsi <= 60:
        return 20.0
    elif 30 <= rsi < 40:
        return 16.0
    elif 60 < rsi <= 70:
        return 14.0
    elif 20 <= rsi < 30:
        return 12.0
    elif 70 < rsi <= 80:
        return 8.0
    elif rsi < 20:
        return 8.0
    else:
        return 4.0


def _score_volatility(row: pd.Series) -> float:
    score = 10.0

    close = row.get("close")
    boll_upper = row.get("BOLL_upper")
    boll_lower = row.get("BOLL_lower")
    boll_mid = row.get("BOLL_mid")

    if all(v is not None and not pd.isna(v) for v in [close, boll_upper, boll_lower, boll_mid]):
        boll_width = (boll_upper - boll_lower) / boll_mid if boll_mid > 0 else 0
        position = (close - boll_lower) / (boll_upper - boll_lower) if boll_upper > boll_lower else 0.5

        if boll_width < 0.05:
            score += 5.0
        elif boll_width < 0.10:
            score += 3.0
        else:
            score += 1.0

        if 0.2 <= position <= 0.8:
            score += 5.0
        elif 0.1 <= position <= 0.9:
            score += 3.0
        else:
            score += 1.0

    atr = row.get("ATR")
    if atr is not None and not pd.isna(atr) and close is not None and not pd.isna(close) and close > 0:
        atr_ratio = atr / close
        if atr_ratio < 0.02:
            score += 5.0
        elif atr_ratio < 0.04:
            score += 3.0
        else:
            score += 1.0

    return min(score, 20.0)


def score_technical(df: pd.DataFrame) -> float:
    df_with_indicators = compute_indicators(df)

    if df_with_indicators.empty:
        return 50.0

    latest = df_with_indicators.iloc[-1]

    trend_score = _score_ma_alignment(latest)
    momentum_score = _score_momentum(latest)
    rsi_score = _score_rsi(latest)
    volatility_score = _score_volatility(latest)

    total = trend_score + momentum_score + rsi_score + volatility_score
    return round(total, 2)

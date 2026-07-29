"""技术指标计算模块（纯 pandas/numpy 实现，无 pandas_ta 依赖）。

所有指标实现保持与 pandas_ta 输出结构一致：
- sma: 返回 Series
- macd: 返回 DataFrame(macd, macd_signal, macd_hist)
- stoch: 返回 DataFrame(stoch_k, stoch_d)
- rsi: 返回 Series
- bbands: 返回 DataFrame(upper, mid, lower)
- atr: 返回 Series
"""
import numpy as np
import pandas as pd


def sma(close: pd.Series, length: int = 30) -> pd.Series:
    """简单移动平均（兼容 pandas_ta.sma）。"""
    return close.rolling(window=length, min_periods=length).mean()


def ema(close: pd.Series, length: int = 12) -> pd.Series:
    """指数移动平均。"""
    return close.ewm(span=length, adjust=False).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """相对强弱指数（兼容 pandas_ta.rsi）。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD 指标（兼容 pandas_ta.macd）。返回 DataFrame(macd, macd_signal, macd_hist)。"""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    macd_signal = ema(macd_line, signal)
    macd_hist = macd_line - macd_signal
    return pd.DataFrame({
        "MACD": macd_line,
        "MACD_signal": macd_signal,
        "MACD_hist": macd_hist,
    })


def stoch(high: pd.Series, low: pd.Series, close: pd.Series,
          k: int = 14, d: int = 3) -> pd.DataFrame:
    """随机振荡指标 KDJ（兼容 pandas_ta.stoch）。返回 DataFrame(K, D)。"""
    low_min = low.rolling(window=k, min_periods=k).min()
    high_max = high.rolling(window=k, min_periods=k).max()
    denom = high_max - low_min
    denom = denom.replace(0, np.nan)
    rsv = (close - low_min) / denom * 100
    rsv = rsv.fillna(50.0)
    k_line = rsv.ewm(com=d - 1, adjust=False).mean()
    d_line = k_line.ewm(com=d - 1, adjust=False).mean()
    return pd.DataFrame({"K": k_line, "D": d_line})


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """布林带（兼容 pandas_ta.bbands）。返回 DataFrame(upper, mid, lower)。"""
    mid = close.rolling(window=length, min_periods=length).mean()
    rolling_std = close.rolling(window=length, min_periods=length).std()
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std
    return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower})


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        length: int = 14) -> pd.Series:
    """真实波幅均值（兼容 pandas_ta.atr）。"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    for period in [5, 10, 20, 60]:
        result[f"MA{period}"] = sma(result["close"], length=period)

    macd_df = macd(result["close"])
    if macd_df is not None:
        result["MACD"] = macd_df["MACD"]
        result["MACD_signal"] = macd_df["MACD_signal"]
        result["MACD_hist"] = macd_df["MACD_hist"]

    stoch_df = stoch(result["high"], result["low"], result["close"])
    if stoch_df is not None:
        result["KDJ_K"] = stoch_df["K"]
        result["KDJ_D"] = stoch_df["D"]
        result["KDJ_J"] = 3 * result["KDJ_K"] - 2 * result["KDJ_D"]

    result["RSI"] = rsi(result["close"], length=14)

    boll_df = bbands(result["close"], length=20)
    if boll_df is not None:
        result["BOLL_upper"] = boll_df["upper"]
        result["BOLL_mid"] = boll_df["mid"]
        result["BOLL_lower"] = boll_df["lower"]

    result["ATR"] = atr(result["high"], result["low"], result["close"], length=14)

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

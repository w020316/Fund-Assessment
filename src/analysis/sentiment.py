import numpy as np
import pandas as pd
import akshare as ak


def _get_market_overview() -> pd.DataFrame:
    try:
        df = ak.stock_zh_a_spot_em()
        return df
    except Exception:
        return pd.DataFrame()


def _score_advance_decline(market_df: pd.DataFrame) -> float:
    if market_df.empty:
        return 15.0

    change_col = None
    for col in ["涨跌幅", "changepercent", "涨跌幅%"]:
        if col in market_df.columns:
            change_col = col
            break

    if change_col is None:
        return 15.0

    changes = pd.to_numeric(market_df[change_col], errors="coerce").dropna()
    if changes.empty:
        return 15.0

    advance_count = (changes > 0).sum()
    decline_count = (changes < 0).sum()
    total = advance_count + decline_count

    if total == 0:
        return 15.0

    ratio = advance_count / total

    if ratio > 0.7:
        return 30.0
    elif ratio > 0.6:
        return 25.0
    elif ratio > 0.5:
        return 20.0
    elif ratio > 0.4:
        return 15.0
    elif ratio > 0.3:
        return 10.0
    else:
        return 5.0


def _score_limit_ratio(market_df: pd.DataFrame) -> float:
    if market_df.empty:
        return 15.0

    change_col = None
    for col in ["涨跌幅", "changepercent", "涨跌幅%"]:
        if col in market_df.columns:
            change_col = col
            break

    if change_col is None:
        return 15.0

    changes = pd.to_numeric(market_df[change_col], errors="coerce").dropna()
    if changes.empty:
        return 15.0

    limit_up = (changes >= 9.9).sum()
    limit_down = (changes <= -9.9).sum()

    if limit_up + limit_down == 0:
        return 15.0

    ratio = limit_up / (limit_up + limit_down)

    if ratio > 0.8:
        return 30.0
    elif ratio > 0.6:
        return 25.0
    elif ratio > 0.5:
        return 20.0
    elif ratio > 0.4:
        return 15.0
    elif ratio > 0.2:
        return 10.0
    else:
        return 5.0


def _score_volume_change(market_df: pd.DataFrame) -> float:
    if market_df.empty:
        return 10.0

    volume_col = None
    for col in ["成交额", "volume", "成交额(元)"]:
        if col in market_df.columns:
            volume_col = col
            break

    if volume_col is None:
        return 10.0

    volumes = pd.to_numeric(market_df[volume_col], errors="coerce").dropna()
    if volumes.empty:
        return 10.0

    total_volume = volumes.sum()
    avg_volume_threshold = 1e12

    if total_volume > avg_volume_threshold * 1.5:
        return 20.0
    elif total_volume > avg_volume_threshold * 1.2:
        return 16.0
    elif total_volume > avg_volume_threshold * 0.8:
        return 12.0
    elif total_volume > avg_volume_threshold * 0.5:
        return 8.0
    else:
        return 4.0


def _score_volatility_index(market_df: pd.DataFrame) -> float:
    if market_df.empty:
        return 10.0

    change_col = None
    for col in ["涨跌幅", "changepercent", "涨跌幅%"]:
        if col in market_df.columns:
            change_col = col
            break

    if change_col is None:
        return 10.0

    changes = pd.to_numeric(market_df[change_col], errors="coerce").dropna()
    if changes.empty:
        return 10.0

    market_vol = changes.std()

    if market_vol < 1.0:
        return 20.0
    elif market_vol < 1.5:
        return 16.0
    elif market_vol < 2.0:
        return 12.0
    elif market_vol < 3.0:
        return 8.0
    else:
        return 4.0


def compute_market_sentiment() -> float:
    market_df = _get_market_overview()

    ad_score = _score_advance_decline(market_df)
    limit_score = _score_limit_ratio(market_df)
    volume_score = _score_volume_change(market_df)
    vol_score = _score_volatility_index(market_df)

    total = ad_score + limit_score + volume_score + vol_score
    return round(total, 2)


def score_sentiment(stock_code: str) -> float:
    market_sentiment = compute_market_sentiment()

    stock_change = 0.0
    try:
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="qfq")
        if df is not None and len(df) >= 2:
            df.columns = [c.strip() for c in df.columns]
            close_col = "收盘" if "收盘" in df.columns else "close"
            if close_col in df.columns:
                latest_close = float(df.iloc[-1][close_col])
                prev_close = float(df.iloc[-2][close_col])
                if prev_close > 0:
                    stock_change = (latest_close - prev_close) / prev_close * 100
    except Exception:
        pass

    stock_bonus = 0.0
    if stock_change > 5:
        stock_bonus = 10.0
    elif stock_change > 2:
        stock_bonus = 7.0
    elif stock_change > 0:
        stock_bonus = 4.0
    elif stock_change > -2:
        stock_bonus = 2.0
    elif stock_change > -5:
        stock_bonus = 0.0
    else:
        stock_bonus = -5.0

    total = market_sentiment * 0.8 + (50 + stock_bonus) * 0.2
    return round(min(max(total, 0), 100), 2)

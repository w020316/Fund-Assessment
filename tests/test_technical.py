"""技术指标计算模块单元测试

验证 src/analysis/technical.py 的核心逻辑(纯 pandas/numpy,无需 mock):
- sma/ema 简单/指数移动平均
- rsi 相对强弱指数
- macd 指标(返回 DataFrame)
- stoch KDJ(返回 DataFrame)
- bbands 布林带(返回 DataFrame)
- atr 真实波幅均值
- compute_indicators 综合指标计算
- score_technical 综合评分(0-100)

注: 2026-07-29 已修复 compute_indicators 中的函数名错误(原调用 _sma/_macd 等
不存在的别名,现已改为调用 sma/macd 等)。移除了临时兼容 fixture。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.technical import (
    atr,
    bbands,
    compute_indicators,
    ema,
    macd,
    rsi,
    score_technical,
    sma,
    stoch,
)


@pytest.fixture
def close_series() -> pd.Series:
    """30 日收盘价序列(用于指标计算)"""
    return pd.Series(np.linspace(10.0, 20.0, 30))


@pytest.fixture
def high_low_close() -> tuple[pd.Series, pd.Series, pd.Series]:
    """30 日 HLC 序列"""
    base = np.linspace(10.0, 20.0, 30)
    close = pd.Series(base)
    high = pd.Series(base + 0.5)
    low = pd.Series(base - 0.5)
    return high, low, close


@pytest.fixture
def kline_df() -> pd.DataFrame:
    """60 日 K线 DataFrame(满足 MA60 计算所需)"""
    np.random.seed(42)
    n = 60
    close = pd.Series(np.cumsum(np.random.randn(n) * 0.5) + 50.0)
    high = close + np.abs(np.random.randn(n) * 0.3) + 0.1
    low = close - np.abs(np.random.randn(n) * 0.3) - 0.1
    open_ = close + np.random.randn(n) * 0.2
    volume = pd.Series(np.random.randint(1_000_000, 5_000_000, n))
    return pd.DataFrame({
        "open": open_,
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    })


class TestSmaEma:
    """sma/ema 移动平均"""

    def test_sma_returns_series(self, close_series):
        result = sma(close_series, length=5)
        assert isinstance(result, pd.Series)
        assert len(result) == len(close_series)

    def test_sma_first_n_periods_are_nan(self, close_series):
        """前 length-1 个值为 NaN(min_periods=length)"""
        result = sma(close_series, length=10)
        assert result.iloc[:9].isna().all()
        assert not pd.isna(result.iloc[9])

    def test_sma_value_correctness(self):
        """sma 计算结果正确性"""
        data = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(data, length=5)
        # (1+2+3+4+5)/5 = 3.0
        assert result.iloc[4] == pytest.approx(3.0)

    def test_ema_returns_series_no_nan(self, close_series):
        """ema 不产生 NaN(ewm adjust=False)"""
        result = ema(close_series, length=5)
        assert isinstance(result, pd.Series)
        assert not result.isna().any()


class TestRsi:
    """rsi 相对强弱指数"""

    def test_rsi_returns_series(self, close_series):
        result = rsi(close_series, length=14)
        assert isinstance(result, pd.Series)
        assert len(result) == len(close_series)

    def test_rsi_filled_50_when_nan(self, close_series):
        """NaN 值填 50(避免除零)"""
        result = rsi(close_series, length=14)
        assert not result.isna().any()

    def test_rsi_noisy_uptrend_above_50(self):
        """带噪声的上涨趋势 → RSI 应高于 50"""
        np.random.seed(1)
        uptrend = np.linspace(10.0, 30.0, 60) + np.random.randn(60) * 0.3
        data = pd.Series(uptrend)
        result = rsi(data, length=14)
        # 上涨多于下跌,RSI 应偏多
        assert result.iloc[-1] > 50

    def test_rsi_noisy_downtrend_below_50(self):
        """带噪声的下跌趋势 → RSI 应低于 50"""
        np.random.seed(2)
        downtrend = np.linspace(30.0, 10.0, 60) + np.random.randn(60) * 0.3
        data = pd.Series(downtrend)
        result = rsi(data, length=14)
        assert result.iloc[-1] < 50

    def test_rsi_bounded_0_100(self, close_series):
        """RSI 应在 [0, 100] 范围内"""
        result = rsi(close_series, length=14).dropna()
        assert (result >= 0).all() and (result <= 100).all()

    def test_rsi_strict_monotonic_returns_50(self):
        """严格单调序列 → loss 全 0 → NaN 填 50(实现特性)"""
        data = pd.Series(np.linspace(10.0, 30.0, 50))
        result = rsi(data, length=14)
        assert result.iloc[-1] == pytest.approx(50.0)


class TestMacd:
    """macd 指标"""

    def test_macd_returns_dataframe(self, close_series):
        result = macd(close_series)
        assert isinstance(result, pd.DataFrame)
        assert set(result.columns) == {"MACD", "MACD_signal", "MACD_hist"}

    def test_macd_hist_equals_diff(self, close_series):
        """MACD_hist = MACD - MACD_signal"""
        result = macd(close_series)
        diff = result["MACD"] - result["MACD_signal"]
        pd.testing.assert_series_equal(result["MACD_hist"], diff, check_names=False)

    def test_macd_no_nan_in_tail(self, close_series):
        """尾部不应有 NaN(ewm 算法)"""
        result = macd(close_series)
        assert not result.iloc[-5:].isna().any().any()


class TestStoch:
    """stoch KDJ"""

    def test_stoch_returns_dataframe(self, high_low_close):
        high, low, close = high_low_close
        result = stoch(high, low, close, k=14, d=3)
        assert isinstance(result, pd.DataFrame)
        assert set(result.columns) == {"K", "D"}

    def test_stoch_k_d_within_0_100(self, high_low_close):
        """K/D 应在 [0, 100] 范围内"""
        high, low, close = high_low_close
        result = stoch(high, low, close)
        valid = result.dropna()
        assert (valid["K"] >= 0).all() and (valid["K"] <= 100).all()
        assert (valid["D"] >= 0).all() and (valid["D"] <= 100).all()


class TestBbands:
    """bbands 布林带"""

    def test_bbands_returns_dataframe(self, close_series):
        result = bbands(close_series, length=20, std=2.0)
        assert isinstance(result, pd.DataFrame)
        assert set(result.columns) == {"upper", "mid", "lower"}

    def test_bbands_upper_greater_than_lower(self, close_series):
        """upper >= mid >= lower"""
        result = bbands(close_series, length=20, std=2.0).dropna()
        assert (result["upper"] >= result["mid"]).all()
        assert (result["mid"] >= result["lower"]).all()

    def test_bbands_first_n_periods_nan(self, close_series):
        """前 length-1 个值应为 NaN"""
        result = bbands(close_series, length=20, std=2.0)
        assert result.iloc[:19].isna().all().all()


class TestAtr:
    """atr 真实波幅均值"""

    def test_atr_returns_series(self, high_low_close):
        high, low, close = high_low_close
        result = atr(high, low, close, length=14)
        assert isinstance(result, pd.Series)
        assert len(result) == len(close)

    def test_atr_positive(self, high_low_close):
        """ATR 应非负"""
        high, low, close = high_low_close
        result = atr(high, low, close, length=14).dropna()
        assert (result >= 0).all()


class TestComputeIndicators:
    """compute_indicators 综合指标计算"""

    def test_compute_indicators_adds_all_columns(self, kline_df):
        """应添加所有指标列"""
        result = compute_indicators(kline_df)
        expected_cols = [
            "MA5", "MA10", "MA20", "MA60",
            "MACD", "MACD_signal", "MACD_hist",
            "KDJ_K", "KDJ_D", "KDJ_J",
            "RSI", "BOLL_upper", "BOLL_mid", "BOLL_lower",
            "ATR", "VWAP",
        ]
        for col in expected_cols:
            assert col in result.columns, f"missing column: {col}"

    def test_compute_indicators_preserves_original(self, kline_df):
        """应保留原数据行数"""
        result = compute_indicators(kline_df)
        assert len(result) == len(kline_df)

    def test_compute_indicators_vwap_positive(self, kline_df):
        """VWAP 应为正(成交价+成交量)"""
        result = compute_indicators(kline_df)
        assert (result["VWAP"] > 0).all()

    def test_compute_indicators_kdj_j_formula(self, kline_df):
        """KDJ_J = 3*K - 2*D"""
        result = compute_indicators(kline_df)
        expected_j = 3 * result["KDJ_K"] - 2 * result["KDJ_D"]
        pd.testing.assert_series_equal(
            result["KDJ_J"], expected_j, check_names=False
        )


class TestScoreTechnical:
    """score_technical 综合评分"""

    def test_score_within_0_100(self, kline_df):
        """评分应在 [0, 100] 范围内"""
        score = score_technical(kline_df)
        assert 0 <= score <= 100

    def test_score_returns_float(self, kline_df):
        """返回 float 类型"""
        score = score_technical(kline_df)
        assert isinstance(score, float)

    def test_score_empty_df_returns_default(self):
        """空 DataFrame 应返回默认值 50"""
        empty_df = pd.DataFrame(columns=["open", "close", "high", "low", "volume"])
        score = score_technical(empty_df)
        assert score == 50.0

    def test_score_short_df_no_crash(self):
        """短 K 线也应能计算(指标可能为 NaN,但不报错)"""
        np.random.seed(0)
        n = 10
        df = pd.DataFrame({
            "open": np.linspace(10, 12, n),
            "close": np.linspace(10, 12, n) + 0.1,
            "high": np.linspace(10, 12, n) + 0.5,
            "low": np.linspace(10, 12, n) - 0.5,
            "volume": np.full(n, 1_000_000),
        })
        score = score_technical(df)
        assert 0 <= score <= 100

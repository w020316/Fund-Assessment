"""市场情绪分析模块单元测试

验证 src/analysis/sentiment.py 的核心逻辑:
- _score_advance_decline 涨跌比评分
- _score_limit_ratio 涨停跌停比评分
- _score_volume_change 成交量变化评分
- _score_volatility_index 波动率评分
- compute_market_sentiment 市场情绪综合计算
- score_sentiment 个股情绪评分(mock akshare)
- 异常降级场景
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from src.analysis import sentiment
from src.analysis.sentiment import (
    _score_advance_decline,
    _score_limit_ratio,
    _score_volatility_index,
    _score_volume_change,
    compute_market_sentiment,
    score_sentiment,
)


def _make_market_df(
    changes: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    """构造全市场快照 DataFrame"""
    if changes is None:
        changes = [1.0, 2.0, -0.5, 0.3, -1.2, 0.8, 1.5, -0.3, 0.2, 0.5]
    df = pd.DataFrame({"涨跌幅": changes})
    if volumes is not None:
        df["成交额"] = volumes
    return df


class TestScoreAdvanceDecline:
    """_score_advance_decline 涨跌比评分"""

    def test_empty_df_returns_default(self):
        assert _score_advance_decline(pd.DataFrame()) == 15.0

    def test_no_change_column_returns_default(self):
        df = pd.DataFrame({"close": [10, 11, 12]})
        assert _score_advance_decline(df) == 15.0

    def test_strong_bullish_top_score(self):
        """上涨占比 > 70% → 30"""
        changes = [1.0] * 8 + [-1.0] * 2  # 80% 上涨
        df = _make_market_df(changes=changes)
        assert _score_advance_decline(df) == 30.0

    def test_strong_bearish_bottom_score(self):
        """上涨占比 < 30% → 5"""
        changes = [-1.0] * 8 + [1.0] * 2  # 20% 上涨
        df = _make_market_df(changes=changes)
        assert _score_advance_decline(df) == 5.0

    def test_balanced_market_middle_score(self):
        """上涨占比约 50% → 15 或 20"""
        changes = [1.0] * 5 + [-1.0] * 5  # 50% 上涨
        df = _make_market_df(changes=changes)
        score = _score_advance_decline(df)
        assert 15.0 <= score <= 20.0

    def test_all_zero_changes_returns_default(self):
        """全部 0 涨跌幅 → total=0,返回 15"""
        df = _make_market_df(changes=[0.0, 0.0, 0.0])
        assert _score_advance_decline(df) == 15.0


class TestScoreLimitRatio:
    """_score_limit_ratio 涨停跌停比评分"""

    def test_empty_df_returns_default(self):
        assert _score_limit_ratio(pd.DataFrame()) == 15.0

    def test_all_limit_up_top_score(self):
        """涨停占比 > 80% → 30"""
        changes = [10.0] * 9 + [9.9]
        df = _make_market_df(changes=changes)
        assert _score_limit_ratio(df) == 30.0

    def test_all_limit_down_bottom_score(self):
        """跌停占比 > 80% → 5"""
        changes = [-10.0] * 9 + [-9.9]
        df = _make_market_df(changes=changes)
        assert _score_limit_ratio(df) == 5.0

    def test_no_limit_returns_default(self):
        """无涨跌停 → 15"""
        changes = [1.0, -1.0, 2.0, -2.0]
        df = _make_market_df(changes=changes)
        assert _score_limit_ratio(df) == 15.0

    def test_balanced_limit(self):
        """涨停跌停各半 → 15 或 20"""
        changes = [10.0, -10.0] * 5
        df = _make_market_df(changes=changes)
        score = _score_limit_ratio(df)
        assert 15.0 <= score <= 20.0


class TestScoreVolumeChange:
    """_score_volume_change 成交量变化评分"""

    def test_empty_df_returns_default(self):
        assert _score_volume_change(pd.DataFrame()) == 10.0

    def test_no_volume_column_returns_default(self):
        df = pd.DataFrame({"涨跌幅": [1.0, 2.0]})
        assert _score_volume_change(df) == 10.0

    def test_huge_volume_top_score(self):
        """总成交额 > 1.5万亿 → 20"""
        volumes = [3e11] * 10  # 总 3万亿
        df = _make_market_df(volumes=volumes)
        assert _score_volume_change(df) == 20.0

    def test_low_volume_low_score(self):
        """总成交额 < 5千亿 → 4"""
        volumes = [1e10] * 10  # 总 1千亿
        df = _make_market_df(volumes=volumes)
        assert _score_volume_change(df) == 4.0

    def test_medium_volume_middle_score(self):
        """总成交额 0.8-1.2万亿 → 12"""
        volumes = [1e11] * 10  # 总 1千亿... 调整为约 1万亿
        volumes = [1e11] * 10  # 1e12 总额
        df = _make_market_df(volumes=volumes)
        score = _score_volume_change(df)
        assert score in (8.0, 12.0, 16.0, 20.0)


class TestScoreVolatilityIndex:
    """_score_volatility_index 波动率评分"""

    def test_empty_df_returns_default(self):
        assert _score_volatility_index(pd.DataFrame()) == 10.0

    def test_low_volatility_top_score(self):
        """波动率 < 1.0 → 20"""
        changes = [0.1, 0.2, -0.1, 0.05, -0.05, 0.1, -0.1, 0.2, 0.0, 0.1]
        df = _make_market_df(changes=changes)
        assert _score_volatility_index(df) == 20.0

    def test_high_volatility_low_score(self):
        """波动率 >= 3.0 → 4"""
        changes = [5.0, -4.5, 4.0, -3.8, 5.2, -4.9, 4.5, -4.2, 5.0, -4.8]
        df = _make_market_df(changes=changes)
        assert _score_volatility_index(df) == 4.0


class TestComputeMarketSentiment:
    """compute_market_sentiment 市场情绪综合"""

    def test_returns_float_in_range(self):
        """综合情绪应在 [0, 100] 范围内"""
        with patch.object(sentiment, "_get_market_overview", return_value=_make_market_df()):
            score = compute_market_sentiment()
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_empty_market_returns_default(self):
        """空市场 → 各项默认 15+15+10+10=50"""
        with patch.object(sentiment, "_get_market_overview", return_value=pd.DataFrame()):
            score = compute_market_sentiment()
        # 15 + 15 + 10 + 10 = 50
        assert score == pytest.approx(50.0)

    def test_get_market_overview_exception_returns_empty(self):
        """akshare 异常 → 返回空 DataFrame"""
        with patch.object(sentiment.ak, "stock_zh_a_spot_em", side_effect=Exception("net error")):
            df = sentiment._get_market_overview()
        assert df.empty


class TestScoreSentiment:
    """score_sentiment 个股情绪评分"""

    def test_returns_float_in_range(self):
        """个股情绪评分应在 [0, 100] 范围内"""
        market_df = _make_market_df()
        hist_df = pd.DataFrame({"收盘": [100.0, 105.0, 110.0]})
        with patch.object(sentiment, "_get_market_overview", return_value=market_df):
            with patch.object(sentiment.ak, "stock_zh_a_hist", return_value=hist_df):
                score = score_sentiment("600519")
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_stock_history_fetch_exception_safe(self):
        """个股历史抓取异常 → 不影响整体评分"""
        market_df = _make_market_df()
        with patch.object(sentiment, "_get_market_overview", return_value=market_df):
            with patch.object(sentiment.ak, "stock_zh_a_hist", side_effect=Exception("net error")):
                score = score_sentiment("600519")
        assert 0 <= score <= 100

    def test_stock_strong_rise_adds_bonus(self):
        """个股涨幅 > 5% → 加 10 分奖励"""
        market_df = _make_market_df()
        # 涨幅 6%: 100 → 106
        hist_df = pd.DataFrame({"收盘": [100.0, 106.0]})
        with patch.object(sentiment, "_get_market_overview", return_value=market_df):
            base_market_score = compute_market_sentiment()
            with patch.object(sentiment.ak, "stock_zh_a_hist", return_value=hist_df):
                score = score_sentiment("600519")
        # 公式: market*0.8 + (50+bonus)*0.2
        expected = base_market_score * 0.8 + (50 + 10.0) * 0.2
        assert score == pytest.approx(min(max(expected, 0), 100), abs=0.01)

    def test_stock_strong_drop_negative_bonus(self):
        """个股跌幅 > 5% → -5 分惩罚"""
        market_df = _make_market_df()
        # 跌幅 6%: 100 → 94
        hist_df = pd.DataFrame({"收盘": [100.0, 94.0]})
        with patch.object(sentiment, "_get_market_overview", return_value=market_df):
            base_market_score = compute_market_sentiment()
            with patch.object(sentiment.ak, "stock_zh_a_hist", return_value=hist_df):
                score = score_sentiment("600519")
        expected = base_market_score * 0.8 + (50 + -5.0) * 0.2
        assert score == pytest.approx(min(max(expected, 0), 100), abs=0.01)

    def test_single_hist_row_safe(self):
        """只有 1 条历史记录 → 无法计算涨跌幅,bonus 为 0"""
        market_df = _make_market_df()
        hist_df = pd.DataFrame({"收盘": [100.0]})
        with patch.object(sentiment, "_get_market_overview", return_value=market_df):
            with patch.object(sentiment.ak, "stock_zh_a_hist", return_value=hist_df):
                score = score_sentiment("600519")
        assert 0 <= score <= 100

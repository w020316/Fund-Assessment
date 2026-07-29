"""技术面智能体单元测试

验证 src/agents/technical_agent.py 的核心逻辑:
- TechnicalAgent.role 角色定义
- _real_analysis 路径(mock akshare + 计算指标)
- 数据降级场景(空df/缺字段/样本不足)
- _mock_analysis 降级路径
- analyze 分支选择
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.agents.base import AgentOpinion, AgentRole
from src.agents import technical_agent as ta_module
from src.agents.technical_agent import TechnicalAgent


@pytest.fixture
def agent() -> TechnicalAgent:
    return TechnicalAgent()


def _make_kline_df(rows: int = 120, start_price: float = 100.0) -> pd.DataFrame:
    """构造合法的日K DataFrame(中文列名,模拟 ak.stock_zh_a_hist)"""
    dates = pd.date_range("2026-01-01", periods=rows)
    # 价格缓慢单边上行,保证 close 非 NaN 且趋势确定
    closes = start_price + np.arange(rows) * 0.5
    opens = closes - 0.3
    highs = closes + 0.8
    lows = closes - 0.8
    volumes = np.full(rows, 1_000_000.0)
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": opens,
        "最高": highs,
        "最低": lows,
        "收盘": closes,
        "成交量": volumes,
        "成交额": closes * volumes,
    })


def _run_real(agent: TechnicalAgent, df: pd.DataFrame | None) -> AgentOpinion:
    """辅助:通过 mock 走 _real_analysis 路径"""
    with patch.object(ta_module, "_HAS_TECHNICAL", True), \
         patch.object(ta_module, "_HAS_AKSHARE", True), \
         patch.object(ta_module, "ak") as mock_ak, \
         patch.object(ta_module, "compute_indicators", side_effect=lambda x: x), \
         patch.object(ta_module, "score_technical", return_value=75.0):
        mock_ak.stock_zh_a_hist.return_value = df
        return agent.analyze("600519")


class TestRole:
    def test_role_is_technical(self, agent):
        assert agent.role == AgentRole.TECHNICAL


class TestRealAnalysis:
    """_real_analysis 路径"""

    def test_valid_data_returns_opinion(self, agent):
        """足够数据 → 返回完整意见"""
        df = _make_kline_df(rows=120)
        op = _run_real(agent, df)
        assert isinstance(op, AgentOpinion)
        assert op.stock_code == "600519"
        assert op.signal in {"BULLISH", "NEUTRAL", "BEARISH"}
        assert op.score == 75.0

    def test_empty_df_returns_neutral_low_confidence(self, agent):
        """空 DataFrame → 降级中性,confidence 0.1"""
        op = _run_real(agent, pd.DataFrame())
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.1
        assert op.score == 50.0

    def test_none_df_returns_neutral(self, agent):
        """akshare 返回 None → 降级"""
        op = _run_real(agent, None)
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.1

    def test_insufficient_rows_returns_neutral(self, agent):
        """数据 < 60 行 → 降级,reasoning 含 '样本不足'"""
        df = _make_kline_df(rows=30)
        op = _run_real(agent, df)
        assert op.signal == "NEUTRAL"
        assert "样本不足" in op.reasoning or "样本不足" in " ".join(op.key_points)


class TestMockAnalysis:
    """_mock_analysis 降级路径"""

    def test_mock_analysis_returns_neutral(self, agent):
        with patch.object(ta_module, "_HAS_TECHNICAL", False), \
             patch.object(ta_module, "_HAS_AKSHARE", True):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.0
        assert op.score == 50.0
        assert "降级" in op.reasoning

    def test_mock_analysis_when_no_akshare(self, agent):
        """无 akshare 依赖 → 降级"""
        with patch.object(ta_module, "_HAS_TECHNICAL", True), \
             patch.object(ta_module, "_HAS_AKSHARE", False):
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.score == 50.0

    def test_real_analysis_exception_falls_back(self, agent):
        """_real_analysis 抛异常 → analyze 应回退到 _mock_analysis"""
        with patch.object(ta_module, "_HAS_TECHNICAL", True), \
             patch.object(ta_module, "_HAS_AKSHARE", True), \
             patch.object(ta_module, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.side_effect = Exception("网络异常")
            op = agent.analyze("600519")
        assert op.signal == "NEUTRAL"
        assert op.score == 50.0


# ===== 指标分支覆盖(compute_indicators 被 mock 为透传,通过 df 列直接驱动分支) =====

# 基础 K 线末行 close = 100 + 119*0.5 = 159.5
_BASE_CLOSE = 100.0 + 119 * 0.5

_INDICATOR_COLS = [
    "MA5", "MA10", "MA20", "MA60",
    "MACD", "MACD_signal", "MACD_hist",
    "KDJ_K", "KDJ_D", "KDJ_J",
    "RSI", "BOLL_upper", "BOLL_lower", "ATR",
]


def _make_kline_with_indicators(overrides: dict[str, float | None], rows: int = 120) -> pd.DataFrame:
    """构造带指标列的 K 线 df。

    overrides 中值为 None 表示该列置为 NaN(模拟指标缺失)。
    未在 overrides 中出现的指标列默认为 NaN。
    """
    df = _make_kline_df(rows=rows)
    for col in _INDICATOR_COLS:
        if col in overrides:
            val = overrides[col]
            df[col] = np.nan if val is None else val
        else:
            df[col] = np.nan
    return df


def _run_real_with_score(agent: TechnicalAgent, df: pd.DataFrame, score: float = 75.0) -> AgentOpinion:
    """通过 mock 走 _real_analysis 路径,允许自定义 score_technical 返回值"""
    with patch.object(ta_module, "_HAS_TECHNICAL", True), \
         patch.object(ta_module, "_HAS_AKSHARE", True), \
         patch.object(ta_module, "ak") as mock_ak, \
         patch.object(ta_module, "compute_indicators", side_effect=lambda x: x), \
         patch.object(ta_module, "score_technical", return_value=score):
        mock_ak.stock_zh_a_hist.return_value = df
        return agent.analyze("600519")


def _kp(op: AgentOpinion) -> str:
    """把 key_points 合并为一个字符串便于断言"""
    return " ".join(op.key_points)


class TestMaAlignmentBranches:
    """均线排列分支"""

    def test_bullish_alignment(self, agent):
        df = _make_kline_with_indicators({
            "MA5": 158.0, "MA10": 157.0, "MA20": 156.0, "MA60": 155.0,
        })
        op = _run_real_with_score(agent, df)
        assert "均线多头排列" in _kp(op)
        assert "多头排列" in op.reasoning

    def test_bearish_alignment(self, agent):
        df = _make_kline_with_indicators({
            "MA5": 160.0, "MA10": 161.0, "MA20": 162.0, "MA60": 163.0,
        })
        op = _run_real_with_score(agent, df)
        assert "均线空头排列" in _kp(op)
        assert "空头排列" in op.reasoning

    def test_mixed_alignment(self, agent):
        df = _make_kline_with_indicators({
            "MA5": 150.0, "MA10": 160.0, "MA20": 140.0, "MA60": 170.0,
        })
        op = _run_real_with_score(agent, df)
        assert "均线交织" in _kp(op)

    def test_ma_incomplete_returns_unavailable(self, agent):
        """MA 列缺失(NaN) → '均线数据不完整'"""
        df = _make_kline_with_indicators({"MA5": None, "MA10": None, "MA20": None, "MA60": None})
        op = _run_real_with_score(agent, df)
        assert "均线数据不完整" in _kp(op)

    def test_partial_ma_incomplete(self, agent):
        """部分 MA 缺失(NaN) → 视为不完整"""
        df = _make_kline_with_indicators({
            "MA5": 158.0, "MA10": 157.0, "MA20": None, "MA60": 155.0,
        })
        op = _run_real_with_score(agent, df)
        assert "均线数据不完整" in _kp(op)


class TestMacdBranches:
    """MACD 分支"""

    def test_golden_cross_with_histogram(self, agent):
        df = _make_kline_with_indicators({
            "MACD": 2.0, "MACD_signal": 1.0, "MACD_hist": 0.5,
        })
        op = _run_real_with_score(agent, df)
        assert "MACD金叉且红柱放大" in _kp(op)

    def test_golden_cross_only(self, agent):
        """MACD > signal 但 hist <= 0 → 仅 'MACD金叉'"""
        df = _make_kline_with_indicators({
            "MACD": 2.0, "MACD_signal": 1.0, "MACD_hist": -0.3,
        })
        op = _run_real_with_score(agent, df)
        assert "MACD金叉" in _kp(op)
        assert "红柱放大" not in _kp(op)

    def test_death_cross_with_histogram(self, agent):
        df = _make_kline_with_indicators({
            "MACD": 1.0, "MACD_signal": 2.0, "MACD_hist": -0.5,
        })
        op = _run_real_with_score(agent, df)
        assert "MACD死叉且绿柱放大" in _kp(op)

    def test_death_cross_only(self, agent):
        """MACD < signal 但 hist >= 0 → 仅 'MACD死叉'"""
        df = _make_kline_with_indicators({
            "MACD": 1.0, "MACD_signal": 2.0, "MACD_hist": 0.4,
        })
        op = _run_real_with_score(agent, df)
        assert "MACD死叉" in _kp(op)
        assert "绿柱放大" not in _kp(op)

    def test_macd_incomplete(self, agent):
        df = _make_kline_with_indicators({"MACD": None, "MACD_signal": None, "MACD_hist": None})
        op = _run_real_with_score(agent, df)
        assert "MACD数据不可用" in _kp(op)


class TestKdjBranches:
    """KDJ 分支"""

    def test_overbought(self, agent):
        df = _make_kline_with_indicators({"KDJ_K": 70.0, "KDJ_D": 75.0, "KDJ_J": 85.0})
        op = _run_real_with_score(agent, df)
        assert "KDJ超买" in _kp(op)

    def test_oversold(self, agent):
        df = _make_kline_with_indicators({"KDJ_K": 15.0, "KDJ_D": 18.0, "KDJ_J": 12.0})
        op = _run_real_with_score(agent, df)
        assert "KDJ超卖" in _kp(op)

    def test_neutral(self, agent):
        df = _make_kline_with_indicators({"KDJ_K": 50.0, "KDJ_D": 50.0, "KDJ_J": 50.0})
        op = _run_real_with_score(agent, df)
        assert "KDJ中性" in _kp(op)

    def test_kdj_incomplete(self, agent):
        df = _make_kline_with_indicators({"KDJ_K": None, "KDJ_D": None, "KDJ_J": None})
        op = _run_real_with_score(agent, df)
        assert "KDJ数据不可用" in _kp(op)


class TestRsiBranches:
    """RSI 分支"""

    def test_overbought(self, agent):
        df = _make_kline_with_indicators({"RSI": 75.0})
        op = _run_real_with_score(agent, df)
        assert "RSI超买" in _kp(op)

    def test_oversold(self, agent):
        df = _make_kline_with_indicators({"RSI": 25.0})
        op = _run_real_with_score(agent, df)
        assert "RSI超卖" in _kp(op)

    def test_neutral(self, agent):
        df = _make_kline_with_indicators({"RSI": 50.0})
        op = _run_real_with_score(agent, df)
        assert "RSI中性" in _kp(op)

    def test_rsi_incomplete(self, agent):
        df = _make_kline_with_indicators({"RSI": None})
        op = _run_real_with_score(agent, df)
        assert "RSI数据不可用" in _kp(op)


class TestBollBranches:
    """布林带分支"""

    def test_breakout_upper(self, agent):
        """close > boll_upper → 突破布林上轨"""
        df = _make_kline_with_indicators({"BOLL_upper": 150.0, "BOLL_lower": 140.0})
        op = _run_real_with_score(agent, df)
        assert "突破布林上轨" in _kp(op)

    def test_breakdown_lower(self, agent):
        """close < boll_lower → 跌破布林下轨"""
        df = _make_kline_with_indicators({"BOLL_upper": 170.0, "BOLL_lower": 165.0})
        op = _run_real_with_score(agent, df)
        assert "跌破布林下轨" in _kp(op)

    def test_inside_band(self, agent):
        """boll_lower < close < boll_upper → 布林带内运行"""
        df = _make_kline_with_indicators({"BOLL_upper": 170.0, "BOLL_lower": 140.0})
        op = _run_real_with_score(agent, df)
        assert "布林带内运行" in _kp(op)

    def test_boll_incomplete_skipped(self, agent):
        """BOLL 列缺失 → 不附加 BOLL key_point(无 else 分支)"""
        df = _make_kline_with_indicators({"BOLL_upper": None, "BOLL_lower": None})
        op = _run_real_with_score(agent, df)
        assert "布林" not in _kp(op)


class TestAtrBranches:
    """ATR 波动率分支"""

    def test_atr_high_volatility(self, agent):
        """ATR/close > 3% → 高波动"""
        df = _make_kline_with_indicators({"ATR": 10.0})  # 10/159.5 ≈ 6.3%
        op = _run_real_with_score(agent, df)
        assert "ATR占比" in _kp(op)
        assert "高" in op.reasoning

    def test_atr_medium_volatility(self, agent):
        """1.5% < ATR/close <= 3% → 中波动"""
        df = _make_kline_with_indicators({"ATR": 3.0})  # 3/159.5 ≈ 1.88%
        op = _run_real_with_score(agent, df)
        assert "中" in op.reasoning

    def test_atr_low_volatility(self, agent):
        """ATR/close <= 1.5% → 低波动"""
        df = _make_kline_with_indicators({"ATR": 1.0})  # 1/159.5 ≈ 0.63%
        op = _run_real_with_score(agent, df)
        assert "低" in op.reasoning

    def test_atr_incomplete_skipped(self, agent):
        """ATR 缺失 → 不附加 ATR key_point"""
        df = _make_kline_with_indicators({"ATR": None})
        op = _run_real_with_score(agent, df)
        assert "ATR占比" not in _kp(op)


class TestSignalByScore:
    """signal 由 score 决定"""

    def test_score_high_returns_bullish(self, agent):
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=80.0)
        assert op.signal == "BULLISH"
        assert op.confidence == round(min(abs(80 - 50) / 50, 1.0), 2)

    def test_score_mid_returns_neutral(self, agent):
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=55.0)
        assert op.signal == "NEUTRAL"

    def test_score_threshold_70_is_bullish(self, agent):
        """score == 70 应为 BULLISH(>=70)"""
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=70.0)
        assert op.signal == "BULLISH"

    def test_score_threshold_40_is_neutral(self, agent):
        """score == 40 应为 NEUTRAL(>=40 且 <70)"""
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=40.0)
        assert op.signal == "NEUTRAL"

    def test_score_low_returns_bearish(self, agent):
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=20.0)
        assert op.signal == "BEARISH"

    def test_confidence_capped_at_one(self, agent):
        """score=100 → confidence 上限 1.0"""
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=100.0)
        assert op.confidence == 1.0

    def test_confidence_zero_at_midpoint(self, agent):
        """score=50 → confidence=0.0"""
        df = _make_kline_with_indicators({})
        op = _run_real_with_score(agent, df, score=50.0)
        assert op.confidence == 0.0


class TestRealAnalysisColumnRename:
    """_real_analysis 列名/字段相关分支"""

    def test_missing_required_columns_degrades(self, agent):
        """缺少必要列(open/high/low/close/volume) → 降级 NEUTRAL"""
        # 构造仅含日期+收盘的 df,缺少 open/high/low/volume
        df = pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=120).strftime("%Y-%m-%d"),
            "收盘": 100.0 + np.arange(120) * 0.5,
        })
        op = _run_real_with_score(agent, df)
        assert op.signal == "NEUTRAL"
        assert op.confidence == 0.1
        assert "字段缺失" in " ".join(op.key_points)

    def test_columns_with_whitespace_stripped(self, agent):
        """列名带空格应被 strip 后识别"""
        df = _make_kline_df(rows=120)
        # 给列名加空格,验证 _real_analysis 能 strip
        df.columns = [f" {c} " for c in df.columns]
        op = _run_real_with_score(agent, df)
        # 列被 strip 后应能正常计算(非降级)
        assert op.score == 75.0

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

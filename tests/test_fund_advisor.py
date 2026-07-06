"""基金建议规则引擎单元测试

验证 src/analysis/fund_advisor.py 的核心逻辑:
- 板块关键词推断
- 大盘信号生成(涨跌幅阈值)
- 板块信号生成
- 盈亏信号生成
- 信号合并(优先级)
- 端到端 generate_fund_advice(mock 数据)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.analysis import fund_advisor
from src.analysis.fund_advisor import (
    _build_market_signal,
    _build_pnl_signal,
    _build_sector_signal,
    _infer_sectors_from_name,
    _merge_signals,
    generate_fund_advice,
)


class TestInferSectors:
    """基金名称 → 板块关键词推断"""

    def test_baijiu_fund_matches_baijiu_sector(self):
        assert "白酒" in _infer_sectors_from_name("招商中证白酒指数A")

    def test_consumer_fund_matches_consumer_sectors(self):
        sectors = _infer_sectors_from_name("易方达消费行业股票")
        assert "食品饮料" in sectors or "商业百货" in sectors or "消费" in sectors

    def test_tech_fund_matches_tech_sectors(self):
        sectors = _infer_sectors_from_name("华夏科技成长混合")
        assert "电子信息" in sectors or "软件" in sectors or "半导体" in sectors

    def test_medical_fund_matches_medical_sectors(self):
        sectors = _infer_sectors_from_name("中欧医疗健康混合A")
        assert "医药" in sectors or "医疗" in sectors

    def test_no_match_returns_empty(self):
        assert _infer_sectors_from_name("未知基金") == []

    def test_multiple_keywords_match(self):
        # "新能源" 和 "汽车" 都在映射表
        sectors = _infer_sectors_from_name("新能源汽车主题基金")
        assert "新能源" in sectors
        assert "汽车" in sectors


class TestBuildMarketSignal:
    """大盘信号生成"""

    def test_big_drop_triggers_dca(self):
        """大盘跌 > 2% 触发定投信号"""
        index_data = [{"name": "上证指数", "change_pct": -2.5}]
        signal = _build_market_signal(index_data)
        assert signal["signal"] == "dca"
        assert signal["action"] == "定投"

    def test_moderate_drop_triggers_watch(self):
        """大盘跌 1-2% 触发关注信号"""
        index_data = [{"name": "深证成指", "change_pct": -1.5}]
        signal = _build_market_signal(index_data)
        assert signal["signal"] == "watch"

    def test_big_rise_triggers_take_profit(self):
        """大盘涨 > 2% 触发止盈信号"""
        index_data = [{"name": "创业板指", "change_pct": 2.8}]
        signal = _build_market_signal(index_data)
        assert signal["signal"] == "take_profit"
        assert signal["action"] == "止盈"

    def test_moderate_rise_triggers_hold(self):
        """大盘涨 1-2% 触发持有信号"""
        index_data = [{"name": "上证指数", "change_pct": 1.3}]
        signal = _build_market_signal(index_data)
        assert signal["signal"] == "hold"

    def test_flat_market_triggers_hold(self):
        """大盘涨跌幅 < 1% 触发持有信号"""
        index_data = [{"name": "上证指数", "change_pct": 0.5}]
        signal = _build_market_signal(index_data)
        assert signal["signal"] == "hold"

    def test_empty_index_data_returns_none(self):
        """空数据返回 none 信号"""
        signal = _build_market_signal([])
        assert signal["signal"] == "none"

    def test_uses_max_abs_change_index(self):
        """使用绝对值最大的指数作为参考"""
        index_data = [
            {"name": "上证指数", "change_pct": 0.5},
            {"name": "深证成指", "change_pct": -2.5},
            {"name": "创业板指", "change_pct": 1.0},
        ]
        signal = _build_market_signal(index_data)
        assert signal["signal"] == "dca"
        assert signal["index_name"] == "深证成指"


class TestBuildPnlSignal:
    """盈亏信号生成"""

    def test_big_profit_triggers_take_profit(self):
        """浮盈 > 20% 触发止盈"""
        signal = _build_pnl_signal(cost_nav=2.0, current_nav=2.5)
        assert signal["signal"] == "take_profit"
        assert signal["action"] == "止盈"

    def test_big_loss_triggers_watch(self):
        """浮亏 > 15% 触发关注"""
        signal = _build_pnl_signal(cost_nav=2.0, current_nav=1.6)
        assert signal["signal"] == "watch"
        assert signal["pnl_pct"] == -20.0

    def test_normal_range_triggers_hold(self):
        """正常盈亏区间触发持有"""
        signal = _build_pnl_signal(cost_nav=2.0, current_nav=2.1)
        assert signal["signal"] == "hold"

    def test_zero_cost_returns_none(self):
        """成本为 0 返回 none"""
        signal = _build_pnl_signal(cost_nav=0, current_nav=2.0)
        assert signal["signal"] == "none"

    def test_zero_current_returns_none(self):
        """当前净值为 0 返回 none"""
        signal = _build_pnl_signal(cost_nav=2.0, current_nav=0)
        assert signal["signal"] == "none"


class TestMergeSignals:
    """信号合并(优先级)"""

    def test_take_profit_wins_over_hold(self):
        """止盈优先于持有"""
        signals = [
            {"signal": "hold", "action": "持有", "reason": "市场平稳"},
            {"signal": "take_profit", "action": "止盈", "reason": "浮盈达标"},
        ]
        merged = _merge_signals(signals)
        assert merged["signal"] == "take_profit"
        assert "浮盈达标" in merged["reason"]

    def test_add_wins_over_hold(self):
        """加仓优先于持有"""
        signals = [
            {"signal": "hold", "action": "持有", "reason": "市场平稳"},
            {"signal": "add", "action": "加仓", "reason": "板块超跌"},
        ]
        merged = _merge_signals(signals)
        assert merged["signal"] == "add"

    def test_all_none_returns_none(self):
        """全部 none 返回无建议"""
        signals = [
            {"signal": "none", "reason": "无数据"},
            {"signal": "none", "reason": "无数据2"},
        ]
        merged = _merge_signals(signals)
        assert merged["signal"] == "none"
        assert merged["action"] == "暂无建议"

    def test_empty_signals_returns_none(self):
        merged = _merge_signals([])
        assert merged["signal"] == "none"

    def test_reasons_are_concatenated(self):
        """多个 reason 用 | 连接"""
        signals = [
            {"signal": "hold", "action": "持有", "reason": "市场平稳"},
            {"signal": "watch", "action": "持有", "reason": "浮亏关注"},
        ]
        merged = _merge_signals(signals)
        assert "市场平稳" in merged["reason"]
        assert "浮亏关注" in merged["reason"]
        assert "|" in merged["reason"]


class TestGenerateFundAdvice:
    """端到端建议生成(mock 数据)"""

    def test_empty_positions_returns_empty_advice(self):
        """空持仓返回空建议"""
        result = generate_fund_advice([])
        assert result["positions_advice"] == []
        assert "暂无基金持仓" in result["summary"]

    def test_advice_with_mock_data(self):
        """mock 数据验证完整流程"""
        positions = [
            {"fund_code": "110022", "fund_name": "易方达消费行业股票",
             "shares": 1000, "cost_nav": 2.5, "buy_date": "2026-01-15"},
        ]
        mock_index = [{"name": "上证指数", "change_pct": -2.5}]
        mock_sector = [{"name": "食品饮料", "change_pct": -3.0}]
        mock_quotes = [{"code": "110022", "nav": 2.724}]

        mock_fetch_result = {
            "index": mock_index,
            "sector": mock_sector,
            "fund_quotes": mock_quotes,
        }
        with patch("src.analysis.fund_advisor._parallel_fetch", return_value=mock_fetch_result):
            result = generate_fund_advice(positions)

        assert len(result["positions_advice"]) == 1
        advice = result["positions_advice"][0]
        assert advice["fund_code"] == "110022"
        assert advice["current_nav"] == 2.724
        assert advice["pnl_pct"] == pytest.approx(8.96, abs=0.1)
        # 大盘跌 > 2% + 板块跌 > 2% → 应触发定投或加仓
        assert advice["advice"]["signal"] in ("dca", "add", "take_profit")

    def test_advice_handles_missing_quote(self):
        """基金净值不可用时不崩溃"""
        positions = [
            {"fund_code": "999999", "fund_name": "未知基金",
             "shares": 100, "cost_nav": 1.0, "buy_date": "2026-01-01"},
        ]
        mock_fetch_result = {"index": [], "sector": [], "fund_quotes": []}
        with patch("src.analysis.fund_advisor._parallel_fetch", return_value=mock_fetch_result):
            result = generate_fund_advice(positions)

        advice = result["positions_advice"][0]
        assert advice["current_nav"] == 0.0
        assert advice["advice"]["signal"] == "none"

    def test_summary_contains_counts(self):
        """摘要包含止盈/加仓/持有计数"""
        positions = [
            {"fund_code": "001", "fund_name": "白酒基金A", "shares": 100, "cost_nav": 1.0, "buy_date": ""},
            {"fund_code": "002", "fund_name": "白酒基金B", "shares": 100, "cost_nav": 1.0, "buy_date": ""},
        ]
        # 构造 2 只都触发止盈(浮盈 > 20%)
        mock_quotes = [
            {"code": "001", "nav": 1.5},  # +50%
            {"code": "002", "nav": 1.4},  # +40%
        ]
        mock_fetch_result = {"index": [], "sector": [], "fund_quotes": mock_quotes}
        with patch("src.analysis.fund_advisor._parallel_fetch", return_value=mock_fetch_result):
            result = generate_fund_advice(positions)

        assert "2 只建议止盈" in result["summary"]

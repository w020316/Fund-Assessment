"""交易量化策略单元测试

验证 src/strategies/trading_quant.py 的核心逻辑:
- Signal 枚举 / ScoreResult 数据类
- WEIGHTS 权重和为 1
- _signal_from_score 评分→信号映射(静态)
- _suggestion_from_signal 信号→建议映射(静态)
- stock_analysis 个股分析(mock akshare)
- capital_flow 资金流(mock akshare)
- market_anomaly 市场异动(mock akshare)
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategies import trading_quant as mod
from src.strategies.trading_quant import (
    Signal,
    ScoreResult,
    TradingQuant,
    WEIGHTS,
)


@pytest.fixture
def quant() -> TradingQuant:
    return TradingQuant()


def _make_kline_df(rows: int = 120) -> pd.DataFrame:
    closes = 100.0 + np.arange(rows) * 0.5
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=rows).strftime("%Y-%m-%d"),
        "开盘": closes - 0.3,
        "最高": closes + 0.8,
        "最低": closes - 0.8,
        "收盘": closes,
        "成交量": np.full(rows, 1_000_000.0),
        "成交额": closes * 1_000_000,
    })


class TestSignal:
    """Signal 枚举"""

    def test_all_signals_defined(self):
        expected = {"STRONG_BUY", "BUY", "WATCH", "HOLD", "SELL", "STRONG_SELL"}
        actual = {s.name for s in Signal}
        assert actual == expected

    def test_is_str_enum(self):
        assert isinstance(Signal.BUY, str)


class TestScoreResult:
    """ScoreResult 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(ScoreResult)

    def test_defaults(self):
        r = ScoreResult()
        assert r.composite == 0.0
        assert r.signal == Signal.HOLD
        assert r.suggestion == ""


class TestWeights:
    """WEIGHTS 权重"""

    def test_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_all_dimensions_present(self):
        assert set(WEIGHTS.keys()) == {"technical", "capital", "fundamental", "news", "sentiment"}


class TestSignalFromScore:
    """_signal_from_score 静态映射"""

    def test_strong_buy_threshold(self):
        assert TradingQuant._signal_from_score(80.0) == Signal.STRONG_BUY

    def test_buy_threshold(self):
        assert TradingQuant._signal_from_score(65.0) == Signal.BUY

    def test_watch_threshold(self):
        assert TradingQuant._signal_from_score(50.0) == Signal.WATCH

    def test_hold_threshold(self):
        assert TradingQuant._signal_from_score(35.0) == Signal.HOLD

    def test_sell_threshold(self):
        assert TradingQuant._signal_from_score(20.0) == Signal.SELL

    def test_strong_sell_threshold(self):
        assert TradingQuant._signal_from_score(10.0) == Signal.STRONG_SELL


class TestSuggestionFromSignal:
    """_suggestion_from_signal 静态映射"""

    def test_each_signal_has_suggestion(self):
        for signal in Signal:
            suggestion = TradingQuant._suggestion_from_signal(signal)
            assert isinstance(suggestion, str)
            assert len(suggestion) > 0


class TestStockAnalysis:
    """stock_analysis 个股分析"""

    def test_returns_full_structure(self, quant):
        """应返回完整结构(scores/composite/signal/suggestion)"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120)
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            result = quant.stock_analysis("600519")
        assert result["stock_code"] == "600519"
        assert "scores" in result
        assert "composite" in result
        assert "signal" in result
        assert "suggestion" in result
        assert set(result["scores"].keys()) == {"technical", "capital", "fundamental", "news", "sentiment"}
        assert 0.0 <= result["composite"] <= 100.0

    def test_insufficient_kline_data_technical_score_50(self, quant):
        """K线 < 30 行 → technical 默认 50"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(20)
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame()
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame()
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            result = quant.stock_analysis("600519")
        assert result["scores"]["technical"] == 50.0


class TestCapitalFlow:
    """capital_flow 资金流"""

    def test_empty_akshare_returns_defaults(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_individual_fund_flow_rank.return_value = pd.DataFrame()
            result = quant.capital_flow("600519")
        assert result["stock_code"] == "600519"
        assert result["main_net_inflow"] == 0.0
        assert result["main_net_pct"] == 0.0

    def test_exception_returns_defaults(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.side_effect = Exception("网络错误")
            mock_ak.stock_individual_fund_flow_rank.side_effect = Exception("网络错误")
            result = quant.capital_flow("600519")
        assert result["main_net_inflow"] == 0.0


class TestMarketAnomaly:
    """market_anomaly 市场异动"""

    def test_empty_spot_returns_empty(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            anomalies = quant.market_anomaly()
        assert anomalies == []

    def test_exception_returns_empty(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = Exception("网络错误")
            anomalies = quant.market_anomaly()
        assert anomalies == []


# ── r7 阶段二 Q3 覆盖率补强 ────────────────────────────────────────────

FULL_YAML = """
trading_quant:
  weights:
    technical: 0.5
    capital: 0.1
    fundamental: 0.1
    news: 0.2
    sentiment: 0.1
  signals:
    strong_buy: 90.0
    buy: 70.0
    watch: 55.0
    hold: 40.0
    sell: 25.0
"""


class TestReadActiveParams:
    """_read_active_params 策略自进化参数读取与回退"""

    def test_full_section_read(self, tmp_path, monkeypatch):
        p = tmp_path / "strategies.yaml"
        p.write_text(FULL_YAML, encoding="utf-8")
        monkeypatch.setattr(mod, "STRATEGIES_YAML_PATH", p)
        weights, signals = mod._read_active_params()
        assert weights["technical"] == 0.5
        assert signals["strong_buy"] == 90.0

    def test_incomplete_section_falls_back(self, tmp_path, monkeypatch):
        """字段不全 → 整体回退默认常量(不半套生效)"""
        p = tmp_path / "strategies.yaml"
        p.write_text("trading_quant:\n  weights:\n    technical: 0.9\n", encoding="utf-8")
        monkeypatch.setattr(mod, "STRATEGIES_YAML_PATH", p)
        weights, signals = mod._read_active_params()
        assert weights == dict(mod.WEIGHTS)
        assert signals == dict(mod.SIGNAL_THRESHOLDS)

    def test_missing_file_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "STRATEGIES_YAML_PATH", tmp_path / "nope.yaml")
        weights, signals = mod._read_active_params()
        assert weights == dict(mod.WEIGHTS)

    def test_corrupt_yaml_falls_back(self, tmp_path, monkeypatch):
        p = tmp_path / "strategies.yaml"
        p.write_text("{{{not yaml", encoding="utf-8")
        monkeypatch.setattr(mod, "STRATEGIES_YAML_PATH", p)
        weights, _ = mod._read_active_params()
        assert weights == dict(mod.WEIGHTS)


class TestKlineCache:
    """_get_kline 缓存行为"""

    def test_second_call_uses_cache(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120)
            quant._get_kline("600519")
            quant._get_kline("600519")
        assert mock_ak.stock_zh_a_hist.call_count == 1

    def test_empty_df_not_cached(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            df1 = quant._get_kline("600519")
            df2 = quant._get_kline("600519")
        assert mock_ak.stock_zh_a_hist.call_count == 2
        assert df1.empty and df2.empty

    def test_different_period_not_shared(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120)
            quant._get_kline("600519", period="daily")
            quant._get_kline("600519", period="weekly")
        assert mock_ak.stock_zh_a_hist.call_count == 2


class TestScoreTechnicalBands:
    """_score_technical 趋势/RSI/MACD 分支"""

    def test_full_uptrend_scores_90(self, quant):
        """多头排列 + RSI 中性 + MACD 金叉 + 短期上行 → 90"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(120)
            score = quant._score_technical("600519")
        assert score == 90.0

    def test_full_downtrend_scores_42(self, quant):
        """空头排列 + RSI 超卖(<20 加分) + MACD 死叉 + 短期下行 → 42"""
        closes = 200.0 - np.arange(120) * 0.5
        df = _make_kline_df(120)
        df["收盘"] = closes
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            score = quant._score_technical("600519")
        assert score == 42.0

    def test_short_kline_returns_50(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(10)
            assert quant._score_technical("600519") == 50.0

    def test_empty_kline_returns_50(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            assert quant._score_technical("600519") == 50.0


class TestScoreCapital:
    """_score_capital 资金面评分"""

    def _flow_df(self, net_amount: float, net_pct: float) -> pd.DataFrame:
        return pd.DataFrame([{"主力净流入-净额": net_amount, "主力净流入-净占比": net_pct}])

    def test_positive_inflow_over_5(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = self._flow_df(1e8, 8.0)
            score = quant._score_capital("600519")
        assert score == 50.0 + min(20.0, 16.0) + 10.0  # 76

    def test_negative_outflow_under_minus5(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = self._flow_df(-1e8, -8.0)
            score = quant._score_capital("600519")
        assert score == 50.0 + max(-20.0, -16.0) - 10.0  # 24

    def test_small_pct_no_extra_bonus(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = self._flow_df(1e6, 2.0)
            score = quant._score_capital("600519")
        assert score == 54.0

    def test_empty_df_returns_50(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            assert quant._score_capital("600519") == 50.0

    def test_exception_returns_50(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.side_effect = Exception("flow down")
            assert quant._score_capital("600519") == 50.0

    def test_sz_market_routing(self, quant):
        """000 开头走 sz 市场: 通过 mock 参数断言"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            quant._score_capital("000001")
        assert mock_ak.stock_individual_fund_flow.call_args.kwargs.get("market") == "sz"


class TestScoreFundamental:
    """_score_fundamental 基本面评分"""

    def test_strong_fundamentals(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame(
                [{"净资产收益率(%)": 20.0, "销售毛利率(%)": 50.0}])
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame(
                [{"pe": 10.0, "pb": 1.0}])
            score = quant._score_fundamental("600519")
        assert score == 50.0 + 15.0 + 10.0 + 10.0 + 5.0  # 90

    def test_weak_fundamentals(self, quant):
        """ROE<0 + 毛利率<10 + 高 PE/PB → 扣分"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame(
                [{"净资产收益率(%)": -5.0, "销售毛利率(%)": 5.0}])
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame(
                [{"pe": 80.0, "pb": 8.0}])
            score = quant._score_fundamental("600519")
        assert score == 50.0 - 10.0 - 5.0 - 10.0 - 5.0  # 20

    def test_mid_bands(self, quant):
        """ROE 8~15 +8, 毛利率 20~40 +5, PE 15~30 +5"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame(
                [{"净资产收益率(%)": 10.0, "销售毛利率(%)": 30.0}])
            mock_ak.stock_a_indicator_lg.return_value = pd.DataFrame(
                [{"pe": 20.0, "pb": 3.0}])
            score = quant._score_fundamental("600519")
        assert score == 50.0 + 8.0 + 5.0 + 5.0  # 68

    def test_exceptions_return_50(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_analysis_indicator.side_effect = Exception("fin down")
            mock_ak.stock_a_indicator_lg.side_effect = Exception("val down")
            assert quant._score_fundamental("600519") == 50.0


class TestScoreNews:
    """_score_news 新闻舆情评分"""

    def test_positive_and_negative_titles(self, quant):
        titles = ["公司签约大单", "业绩超预期实现增长", "回购股份", "高管违规被处罚"]
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_news_em.return_value = pd.DataFrame({"新闻标题": titles})
            score = quant._score_news("600519")
        assert score == 50.0 + (3 - 1) * 5.0  # 60

    def test_clamped_at_100(self, quant):
        titles = ["利好"] * 10 + ["增长", "突破", "创新高", "签约", "中标"]
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_news_em.return_value = pd.DataFrame({"新闻标题": titles})
            score = quant._score_news("600519")
        assert score == 100.0

    def test_empty_and_exception(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_news_em.return_value = pd.DataFrame()
            assert quant._score_news("600519") == 50.0
            mock_ak.stock_news_em.side_effect = Exception("news down")
            assert quant._score_news("600519") == 50.0


class TestScoreSentiment:
    """_score_sentiment 市场情绪评分"""

    def _spot_df(self, turnover: float, amount: float) -> pd.DataFrame:
        return pd.DataFrame([{
            "代码": "600519", "换手率": turnover, "成交额": amount,
        }])

    def test_hot_stock(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = self._spot_df(12.0, 2e9)
            assert quant._score_sentiment("600519") == 75.0

    def test_cold_stock(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = self._spot_df(0.5, 1e8)
            assert quant._score_sentiment("600519") == 45.0

    def test_mid_bands(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = self._spot_df(6.0, 6e8)
            assert quant._score_sentiment("600519") == 63.0

    def test_no_matching_row_returns_50(self, quant):
        df = pd.DataFrame([{"代码": "000001", "换手率": 12.0, "成交额": 2e9}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = df
            assert quant._score_sentiment("600519") == 50.0

    def test_exception_returns_50(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = Exception("spot down")
            assert quant._score_sentiment("600519") == 50.0


class TestCapitalFlowWithData:
    """capital_flow 有数据行"""

    def test_flow_and_rank_filled(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame([{
                "主力净流入-净额": 1.5e8, "主力净流入-净占比": 7.5,
                "散户净流入-净额": -3e7, "散户净流入-净占比": -1.5,
            }])
            mock_ak.stock_individual_fund_flow_rank.return_value = pd.DataFrame([{
                "代码": "600519",
                "超大单净流入-净额": 1.0e8, "大单净流入-净额": 5e7,
                "中单净流入-净额": -2e7, "小单净流入-净额": -3e7,
            }])
            result = quant.capital_flow("600519")
        assert result["main_net_inflow"] == 1.5e8
        assert result["main_net_pct"] == 7.5
        assert result["retail_net_inflow"] == -3e7
        assert result["super_large_net"] == 1.0e8
        assert result["large_net"] == 5e7
        assert result["medium_net"] == -2e7
        assert result["small_net"] == -3e7

    def test_rank_row_missing_keeps_defaults(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_individual_fund_flow.return_value = pd.DataFrame()
            mock_ak.stock_individual_fund_flow_rank.return_value = pd.DataFrame(
                [{"代码": "000001", "超大单净流入-净额": 1.0}])
            result = quant.capital_flow("600519")
        assert result["super_large_net"] == 0.0


class TestNorthboundFlow:
    """northbound_flow 北向资金"""

    def test_all_sources_filled(self, quant):
        total = pd.DataFrame([{"当日净流入": 56.8}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_north_net_flow_in_em.side_effect = [total, total, total]
            mock_ak.stock_hsgt_hold_stock_em.return_value = pd.DataFrame([
                {"股票代码": "600519", "股票简称": "贵州茅台",
                 "持股数量": 1e7, "持股占流通股比": 3.2},
            ])
            result = quant.northbound_flow()
        assert result["total_net_inflow"] == 56.8
        assert result["sh_net_inflow"] == 56.8
        assert result["sz_net_inflow"] == 56.8
        assert len(result["top_stocks"]) == 1
        assert result["top_stocks"][0]["code"] == "600519"
        assert result["top_stocks"][0]["hold_pct"] == 3.2

    def test_all_sources_fail_returns_defaults(self, quant):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_north_net_flow_in_em.side_effect = Exception("down")
            mock_ak.stock_hsgt_hold_stock_em.side_effect = Exception("down")
            result = quant.northbound_flow()
        assert result["total_net_inflow"] == 0.0
        assert result["top_stocks"] == []


class TestMarketAnomalyRows:
    """market_anomaly 有数据行"""

    def test_surge_and_high_turnover_detected(self, quant):
        df = pd.DataFrame([
            {"代码": "600519", "名称": "贵州茅台", "涨跌幅": 9.8, "换手率": 5.0, "成交额": 2e9},
            {"代码": "000001", "名称": "平安银行", "涨跌幅": 1.0, "换手率": 18.0, "成交额": 3e9},
        ])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = df
            anomalies = quant.market_anomaly()
        types = {a["type"] for a in anomalies}
        assert "涨幅异动" in types
        assert "换手异动" in types
        surge = next(a for a in anomalies if a["type"] == "涨幅异动")
        assert surge["code"] == "600519"
        assert surge["change_pct"] == 9.8

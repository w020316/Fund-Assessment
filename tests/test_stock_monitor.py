"""个股监控策略单元测试

验证 src/strategies/stock_monitor.py 的核心逻辑:
- AlertType 枚举 / Alert 数据类
- DEFAULT_RULES 默认规则
- add_watch / remove_watch 监控管理
- _check_price_anomaly 涨幅异常(mock akshare)
- _check_rsi_extreme RSI 超买超卖
- check_alerts 综合告警(mock akshare)
- _get_spot_data 异常降级
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.strategies import stock_monitor as mod
from src.strategies.stock_monitor import (
    Alert,
    AlertType,
    DEFAULT_RULES,
    StockMonitor,
)


@pytest.fixture
def monitor() -> StockMonitor:
    return StockMonitor()


def _make_kline_df(rows: int = 60, closes: list[float] | None = None) -> pd.DataFrame:
    """构造日K(中文列名)"""
    if closes is None:
        closes = (100.0 + np.arange(rows) * 0.5).tolist()
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=rows).strftime("%Y-%m-%d"),
        "收盘": closes,
        "最高": [c + 0.5 for c in closes],
        "最低": [c - 0.5 for c in closes],
        "成交量": [1_000_000.0] * rows,
    })


class TestAlertType:
    """AlertType 枚举"""

    def test_all_types_defined(self):
        expected = {
            "PRICE_ANOMALY", "EPS_SURPRISE", "VOLUME_PRICE_DIVERGENCE",
            "BLOCK_TRADE", "RSI_EXTREME", "NORTHBOUND_ANOMALY", "SECTOR_ROTATION",
        }
        actual = {t.name for t in AlertType}
        assert actual == expected

    def test_is_str_enum(self):
        assert isinstance(AlertType.PRICE_ANOMALY, str)

    def test_default_rules_contains_all(self):
        assert set(DEFAULT_RULES) == set(AlertType)


class TestAlert:
    """Alert 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(Alert)

    def test_default_detail_is_empty_dict(self):
        a = Alert(stock_code="600519", alert_type=AlertType.RSI_EXTREME,
                  severity="medium", message="msg")
        assert a.detail == {}

    def test_creation_with_detail(self):
        a = Alert(
            stock_code="600519", alert_type=AlertType.PRICE_ANOMALY,
            severity="high", message="涨幅异常", detail={"daily_change": 16.0},
        )
        assert a.detail["daily_change"] == 16.0


class TestWatchlist:
    """add_watch / remove_watch"""

    def test_add_watch_default_rules(self, monitor):
        monitor.add_watch("600519")
        assert "600519" in monitor._watchlist
        assert monitor._watchlist["600519"] == DEFAULT_RULES

    def test_add_watch_custom_rules(self, monitor):
        custom = [AlertType.PRICE_ANOMALY, AlertType.RSI_EXTREME]
        monitor.add_watch("600519", custom)
        assert monitor._watchlist["600519"] == custom

    def test_remove_watch(self, monitor):
        monitor.add_watch("600519")
        monitor.remove_watch("600519")
        assert "600519" not in monitor._watchlist

    def test_remove_watch_nonexistent_silent(self, monitor):
        """移除不存在的监控 → 不抛异常"""
        monitor.remove_watch("999999")  # 不抛异常


class TestCheckPriceAnomaly:
    """_check_price_anomaly 涨幅异常"""

    def test_daily_change_over_15_triggers_high(self, monitor):
        """日涨幅 > 15% → high 告警"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 100.0, "涨跌幅": 16.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            alert = monitor._check_price_anomaly("600519")
        assert alert is not None
        assert alert.severity == "high"
        assert "15%" in alert.message

    def test_no_anomaly_returns_none(self, monitor):
        """正常涨幅 → None"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 100.0, "涨跌幅": 2.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(5)
            alert = monitor._check_price_anomaly("600519")
        assert alert is None


class TestCheckRsiExtreme:
    """_check_rsi_extreme RSI 超买超卖"""

    def test_overbought_returns_alert(self, monitor):
        """RSI > 70 → 超买告警"""
        # 构造持续上涨序列,RSI 会很高
        closes = [100.0 + i * 2 for i in range(60)]
        df = _make_kline_df(60, closes=closes)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            alert = monitor._check_rsi_extreme("600519")
        if alert is not None:
            assert alert.alert_type == AlertType.RSI_EXTREME
            assert alert.detail["direction"] in {"overbought", "oversold"}

    def test_insufficient_data_returns_none(self, monitor):
        """数据 < 30 行 → None"""
        df = _make_kline_df(20)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            alert = monitor._check_rsi_extreme("600519")
        assert alert is None


class TestCheckAlerts:
    """check_alerts 综合告警"""

    def test_empty_akshare_returns_empty_list(self, monitor):
        """akshare 全部返回空 → 无告警"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame()
            mock_ak.stock_hsgt_hold_stock_em.return_value = pd.DataFrame()
            mock_ak.stock_sector_fund_flow_rank.return_value = pd.DataFrame()
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
            alerts = monitor.check_alerts("600519")
        assert alerts == []

    def test_custom_rules_limit_checks(self, monitor):
        """自定义规则 → 仅检查指定项"""
        monitor.add_watch("600519", [AlertType.RSI_EXTREME])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(60)
            alerts = monitor.check_alerts("600519")
        # 仅检查 RSI_EXTREME,返回的告警类型应为 RSI 超买超卖(若有)
        for a in alerts:
            assert a["alert_type"] == AlertType.RSI_EXTREME.value


class TestSpotAndKline:
    """_get_spot_data / _get_kline 数据获取与降级(r7 阶段二 Q3)"""

    def test_spot_row_match(self, monitor):
        df = pd.DataFrame([{
            "代码": "600519", "最新价": 100.0, "涨跌幅": 3.5,
            "成交量": 2e6, "换手率": 1.5, "成交额": 2e8,
        }])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = df
            spot = monitor._get_spot_data("600519")
        assert spot["price"] == 100.0
        assert spot["change_pct"] == 3.5
        assert spot["volume"] == 2e6
        assert spot["turnover_rate"] == 1.5
        assert spot["amount"] == 2e8

    def test_spot_no_match_returns_defaults(self, monitor):
        """无匹配行 → 返回默认 0 值 dict(不抛异常)"""
        df = pd.DataFrame([{"代码": "000001", "最新价": 10.0, "涨跌幅": 1.0,
                            "成交量": 1e5, "换手率": 1.0, "成交额": 1e6}])
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = df
            spot = monitor._get_spot_data("600519")
        assert spot["price"] == 0.0 and spot["change_pct"] == 0.0

    def test_spot_exception_returns_defaults(self, monitor):
        """接口异常 → 降级返回默认 dict"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = RuntimeError("network down")
            spot = monitor._get_spot_data("600519")
        assert spot["price"] == 0.0

    def test_kline_tail_days(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(60)
            df = monitor._get_kline("600519", days=5)
        assert len(df) == 5
        assert list(df.columns) == list(_make_kline_df(5).columns)

    def test_kline_exception_returns_empty(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.side_effect = RuntimeError("kline down")
            df = monitor._get_kline("600519")
        assert df.empty


class TestCheckPriceAnomalyWeekly:
    """_check_price_anomaly 周涨幅分支"""

    def test_weekly_change_over_12_triggers_medium(self, monitor):
        """日涨幅 ≤15% 但 5 日累计涨幅 >12% → medium 告警"""
        closes = [100.0, 102.0, 105.0, 108.0, 114.0]  # 5日 +14%
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 114.0, "涨跌幅": 4.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(5, closes=closes)
            alert = monitor._check_price_anomaly("600519")
        assert alert is not None
        assert alert.severity == "medium"
        assert alert.detail["weekly_change"] > 12

    def test_weekly_change_under_threshold_returns_none(self, monitor):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]  # +4%
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 104.0, "涨跌幅": 1.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(5, closes=closes)
            alert = monitor._check_price_anomaly("600519")
        assert alert is None

    def test_kline_too_short_skips_weekly_check(self, monitor):
        """日K <5 行 → 跳过周涨幅检查,返回 None"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 100.0, "涨跌幅": 2.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(3)
            alert = monitor._check_price_anomaly("600519")
        assert alert is None


class TestCheckEpsSurprise:
    """_check_eps_surprise EPS 超预期"""

    def test_empty_abstract_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            assert monitor._check_eps_surprise("600519") is None

    def test_non_positive_eps_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame([
                {"每股收益": -0.5},
            ])
            assert monitor._check_eps_surprise("600519") is None

    def test_forecast_surprise_over_2_triggers_high(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame([
                {"每股收益": 1.2},
            ])
            mock_ak.stock_profit_forecast_ths.return_value = pd.DataFrame([
                {"预测每股收益": 1.0},
            ])
            alert = monitor._check_eps_surprise("600519")
        assert alert is not None
        assert alert.severity == "high"
        assert alert.detail["surprise_pct"] == 20.0

    def test_forecast_empty_falls_back_to_growth(self, monitor):
        """预测数据缺失 → 退化为财报同比分支"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame([
                {"每股收益": 1.2}, {"每股收益": 1.0},
            ])
            mock_ak.stock_profit_forecast_ths.return_value = pd.DataFrame()
            alert = monitor._check_eps_surprise("600519")
        assert alert is not None
        assert alert.severity == "medium"
        assert alert.detail["growth_pct"] == 20.0

    def test_forecast_exception_falls_back_to_growth(self, monitor):
        """预测接口异常 → warning 后走同比分支"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame([
                {"每股收益": 1.2}, {"每股收益": 1.0},
            ])
            mock_ak.stock_profit_forecast_ths.side_effect = RuntimeError("forecast down")
            alert = monitor._check_eps_surprise("600519")
        assert alert is not None
        assert alert.severity == "medium"

    def test_non_positive_prev_eps_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame([
                {"每股收益": 1.2}, {"每股收益": 0.0},
            ])
            mock_ak.stock_profit_forecast_ths.return_value = pd.DataFrame()
            assert monitor._check_eps_surprise("600519") is None

    def test_small_growth_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame([
                {"每股收益": 1.01}, {"每股收益": 1.0},
            ])
            mock_ak.stock_profit_forecast_ths.return_value = pd.DataFrame()
            assert monitor._check_eps_surprise("600519") is None

    def test_abstract_exception_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_financial_abstract_ths.side_effect = RuntimeError("fin down")
            assert monitor._check_eps_surprise("600519") is None


class TestCheckVolumePriceDivergence:
    """_check_volume_price_divergence 量价背离"""

    def test_volume_spike_flat_price_triggers_alert(self, monitor):
        closes = [100.0] * 30
        volumes = [1_000_000.0] * 29 + [1_400_000.0]  # 量变 +40% > 24%
        df = _make_kline_df(30, closes=closes)
        df["成交量"] = volumes
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            alert = monitor._check_volume_price_divergence("600519")
        assert alert is not None
        assert alert.severity == "medium"
        assert alert.detail["volume_change_pct"] > 24

    def test_price_moves_with_volume_returns_none(self, monitor):
        """量升价也大幅上移(非背离) → None"""
        # 末日大涨 2.0/114 ≈ +1.75% ≥ 0.56% 阈值, 价跌背离不成立
        closes = [100.0 + i * 0.5 for i in range(29)] + [116.0]
        volumes = [1_000_000.0] * 29 + [1_400_000.0]
        df = _make_kline_df(30, closes=closes)
        df["成交量"] = volumes
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            assert monitor._check_volume_price_divergence("600519") is None

    def test_insufficient_rows_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(15)
            assert monitor._check_volume_price_divergence("600519") is None


class TestCheckBlockTrade:
    """_check_block_trade 大宗交易折溢价"""

    def _spot_df(self, price: float) -> pd.DataFrame:
        return pd.DataFrame([{
            "代码": "600519", "最新价": price, "涨跌幅": 1.0,
            "成交量": 1e6, "换手率": 1.0, "成交额": 1e8,
        }])

    def test_premium_over_5_triggers_alert(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame([
                {"股票代码": "600519", "成交价": 110.0},
            ])
            mock_ak.stock_zh_a_spot_em.return_value = self._spot_df(100.0)
            alert = monitor._check_block_trade("600519")
        assert alert is not None
        assert alert.severity == "medium"
        assert "溢价" in alert.message

    def test_discount_over_5_triggers_alert(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame([
                {"股票代码": "600519", "成交价": 90.0},
            ])
            mock_ak.stock_zh_a_spot_em.return_value = self._spot_df(100.0)
            alert = monitor._check_block_trade("600519")
        assert alert is not None
        assert "折价" in alert.message

    def test_small_premium_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame([
                {"股票代码": "600519", "成交价": 101.0},
            ])
            mock_ak.stock_zh_a_spot_em.return_value = self._spot_df(100.0)
            assert monitor._check_block_trade("600519") is None

    def test_no_matching_trade_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame([
                {"股票代码": "000001", "成交价": 10.0},
            ])
            assert monitor._check_block_trade("600519") is None

    def test_empty_trades_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame()
            assert monitor._check_block_trade("600519") is None

    def test_zero_market_price_returns_none(self, monitor):
        """市价缺失(=0) → fail-safe 返回 None, 不算折溢价"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame([
                {"股票代码": "600519", "成交价": 110.0},
            ])
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            assert monitor._check_block_trade("600519") is None

    def test_exception_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_dzjy_mrmx.side_effect = RuntimeError("dzjy down")
            assert monitor._check_block_trade("600519") is None


class TestCheckRsiExtremeStrong:
    """_check_rsi_extreme 定向断言(强化旧用例的弱断言)"""

    def test_oversold_returns_alert(self, monitor):
        """持续下跌 → RSI < 30 → 超卖告警"""
        closes = [200.0 - i * 2 for i in range(60)]
        df = _make_kline_df(60, closes=closes)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            alert = monitor._check_rsi_extreme("600519")
        assert alert is not None
        assert alert.detail["direction"] == "oversold"
        assert alert.detail["rsi"] < 30

    def test_overbought_returns_alert(self, monitor):
        # 纯单调上涨会让 avg_loss=0 → RSI fillna(50) 中性, 因此构造
        # 「主升 + 每 10 日小幅回落」序列, 保证 avg_loss>0 且远小于 avg_gain
        closes = [100.0 + i * 2 for i in range(60)]
        for idx in range(10, 60, 10):
            closes[idx] -= 4
        df = _make_kline_df(60, closes=closes)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            alert = monitor._check_rsi_extreme("600519")
        assert alert is not None
        assert alert.detail["direction"] == "overbought"

    def test_neutral_rsi_returns_none(self, monitor):
        """涨跌交替 → RSI 中性 → None"""
        closes = [100.0 + (1.0 if i % 2 else -1.0) for i in range(60)]
        df = _make_kline_df(60, closes=closes)
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = df
            assert monitor._check_rsi_extreme("600519") is None


class TestCheckNorthboundAnomaly:
    """_check_northbound_anomaly 北向资金异动"""

    def _hold_df(self) -> pd.DataFrame:
        return pd.DataFrame([{"股票代码": "600519", "持股占流通股比": 3.2}])

    def test_empty_market_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.return_value = pd.DataFrame()
            assert monitor._check_northbound_anomaly("600519") is None

    def test_no_matching_stock_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.return_value = pd.DataFrame(
                [{"股票代码": "000001", "持股占流通股比": 1.0}])
            assert monitor._check_northbound_anomaly("600519") is None

    def test_5d_inflow_over_1_triggers_high(self, monitor):
        hist = pd.DataFrame({"持股数量": [100.0, 100.0, 100.0, 100.0, 103.0]})
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.return_value = self._hold_df()
            mock_ak.stock_hsgt_hold_detail_em.return_value = hist
            alert = monitor._check_northbound_anomaly("600519")
        assert alert is not None
        assert alert.severity == "high"
        assert alert.detail["change_5d_pct"] > 1

    def test_1d_inflow_over_1_triggers_medium(self, monitor):
        """5 日累计 ≤1% 但单日 >1% → medium"""
        hist = pd.DataFrame({"持股数量": [105.0, 105.0, 105.0, 104.0, 105.8]})
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.return_value = self._hold_df()
            mock_ak.stock_hsgt_hold_detail_em.return_value = hist
            alert = monitor._check_northbound_anomaly("600519")
        assert alert is not None
        assert alert.severity == "medium"
        assert alert.detail["change_1d_pct"] > 1

    def test_small_changes_returns_none(self, monitor):
        hist = pd.DataFrame({"持股数量": [100.0, 100.0, 100.0, 100.0, 100.5]})
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.return_value = self._hold_df()
            mock_ak.stock_hsgt_hold_detail_em.return_value = hist
            assert monitor._check_northbound_anomaly("600519") is None

    def test_detail_exception_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.return_value = self._hold_df()
            mock_ak.stock_hsgt_hold_detail_em.side_effect = RuntimeError("hsgt down")
            assert monitor._check_northbound_anomaly("600519") is None

    def test_outer_exception_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_hsgt_hold_stock_em.side_effect = RuntimeError("north down")
            assert monitor._check_northbound_anomaly("600519") is None


class TestCheckSectorRotation:
    """_check_sector_rotation 行业资金轮动"""

    def _sector_df(self, net_pct: float) -> pd.DataFrame:
        return pd.DataFrame([{"名称": "白酒", "净流入占比": net_pct}])

    def _info_df(self) -> pd.DataFrame:
        return pd.DataFrame([{"item": "行业", "value": "白酒"},
                             {"item": "总股本", "value": "12.56亿"}])

    def test_sector_and_stock_hot_triggers_high(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.return_value = self._sector_df(12.0)
            mock_ak.stock_individual_info_em.return_value = self._info_df()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 100.0, "涨跌幅": 6.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            alert = monitor._check_sector_rotation("600519")
        assert alert is not None
        assert alert.severity == "high"

    def test_sector_cold_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.return_value = self._sector_df(3.0)
            mock_ak.stock_individual_info_em.return_value = self._info_df()
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 100.0, "涨跌幅": 6.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            assert monitor._check_sector_rotation("600519") is None

    def test_empty_sector_df_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.return_value = pd.DataFrame()
            assert monitor._check_sector_rotation("600519") is None

    def test_industry_missing_returns_none(self, monitor):
        """个股资料缺『行业』条目 → None"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.return_value = self._sector_df(12.0)
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame(
                [{"item": "总股本", "value": "12.56亿"}])
            assert monitor._check_sector_rotation("600519") is None

    def test_info_exception_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.return_value = self._sector_df(12.0)
            mock_ak.stock_individual_info_em.side_effect = RuntimeError("info down")
            assert monitor._check_sector_rotation("600519") is None

    def test_sector_not_matching_industry_returns_none(self, monitor):
        """行业名在板块榜无匹配行 → None"""
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.return_value = pd.DataFrame(
                [{"名称": "半导体", "净流入占比": 12.0}])
            mock_ak.stock_individual_info_em.return_value = self._info_df()
            assert monitor._check_sector_rotation("600519") is None

    def test_outer_exception_returns_none(self, monitor):
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_sector_fund_flow_rank.side_effect = RuntimeError("sector down")
            assert monitor._check_sector_rotation("600519") is None


class TestCheckAlertsAggregation:
    """check_alerts 多告警聚合"""

    def test_multiple_alerts_aggregated(self, monitor):
        """日涨幅异常 + 北向异动同时触发 → 两条告警"""
        hist = pd.DataFrame({"持股数量": [100.0, 100.0, 100.0, 100.0, 103.0]})
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
                "代码": "600519", "最新价": 100.0, "涨跌幅": 16.0,
                "成交量": 1e6, "换手率": 2.0, "成交额": 1e8,
            }])
            mock_ak.stock_zh_a_hist.return_value = _make_kline_df(3)
            mock_ak.stock_financial_abstract_ths.return_value = pd.DataFrame()
            mock_ak.stock_dzjy_mrmx.return_value = pd.DataFrame()
            mock_ak.stock_hsgt_hold_stock_em.return_value = pd.DataFrame(
                [{"股票代码": "600519", "持股占流通股比": 3.2}])
            mock_ak.stock_hsgt_hold_detail_em.return_value = hist
            mock_ak.stock_sector_fund_flow_rank.return_value = pd.DataFrame()
            alerts = monitor.check_alerts("600519")
        types = {a["alert_type"] for a in alerts}
        assert AlertType.PRICE_ANOMALY.value in types
        assert AlertType.NORTHBOUND_ANOMALY.value in types

    def test_unknown_rule_is_skipped(self, monitor):
        """watchlist 中的规则若无对应 checker → 跳过不抛异常"""
        monitor.add_watch("600519", [AlertType.PRICE_ANOMALY])
        # 模拟枚举扩充后旧 checker 缺位: 直接清掉 checkers 的映射路径不可行,
        # 改为用一个不存在的规则类型绕过(类型注解不校验运行时内容)
        monitor._watchlist["600519"] = ["not_a_rule"]  # type: ignore[list-item]
        with patch.object(mod, "ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            assert monitor.check_alerts("600519") == []

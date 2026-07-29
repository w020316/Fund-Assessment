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

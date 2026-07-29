"""可转债T0狙击策略单元测试

验证 src/strategies/cb_t0_sniper.py 的核心逻辑:
- CBOpportunity 数据类
- 常量(STOP_LOSS_PCT/MAX_PREMIUM_RATE/POSITION_PCT)
- _build_cb_map 构建可转债映射(mock akshare)
- scan_cb_opportunities 扫描机会(mock akshare)
- monitor_cb 监控生成器(mock akshare + time.sleep)
- 异常降级
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.strategies import cb_t0_sniper as mod
from src.strategies.cb_t0_sniper import (
    CBOpportunity,
    CBT0Sniper,
    MAX_PREMIUM_RATE,
    POSITION_PCT,
    STOP_LOSS_PCT,
)


@pytest.fixture
def sniper(monkeypatch) -> CBT0Sniper:
    """构造已 mock akshare 的 CBT0Sniper(空映射)"""
    mock_ak = MagicMock()
    monkeypatch.setattr(mod, "ak", mock_ak)
    mock_ak.bond_zh_cov_info.return_value = pd.DataFrame()
    return CBT0Sniper()


class TestConstants:
    """常量定义"""

    def test_stop_loss_pct(self):
        assert STOP_LOSS_PCT == -0.03

    def test_max_premium_rate(self):
        assert MAX_PREMIUM_RATE == 0.30

    def test_position_pct(self):
        assert POSITION_PCT == 0.10


class TestCBOpportunity:
    """CBOpportunity 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(CBOpportunity)

    def test_creation(self):
        opp = CBOpportunity(
            cb_code="113001", cb_name="中行转债", stock_code="601988",
            stock_name="中国银行", cb_price=120.0, stock_price=10.0,
            conversion_price=8.0, conversion_value=125.0, premium_rate=-0.04,
            is_limit_up=True, volume_ratio=2.5, turnover_rate=15.0,
        )
        assert opp.cb_code == "113001"
        assert opp.is_limit_up is True
        assert opp.premium_rate == -0.04


class TestBuildCbMap:
    """_build_cb_map 构建映射"""

    def test_empty_akshare_returns_empty_map(self, sniper):
        """空 akshare → 空映射"""
        assert sniper._cb_map == {}

    def test_builds_map_from_cov_info(self, monkeypatch):
        """正常数据 → 构建 stock_code → cb_code 映射"""
        mock_ak = MagicMock()
        monkeypatch.setattr(mod, "ak", mock_ak)
        mock_ak.bond_zh_cov_info.return_value = pd.DataFrame([
            {"正股代码": "601988", "债券代码": "113001", "债券简称": "中行转债", "正股简称": "中国银行", "转股价": 8.0},
            {"正股代码": "600519", "债券代码": "113050", "债券简称": "茅王转债", "正股简称": "贵州茅台", "转股价": 1500.0},
        ])
        sniper = CBT0Sniper()
        assert sniper._cb_map["601988"] == "113001"
        assert sniper._cb_map["600519"] == "113050"

    def test_exception_returns_empty_map(self, monkeypatch):
        """akshare 异常 → 空映射,不抛异常"""
        mock_ak = MagicMock()
        monkeypatch.setattr(mod, "ak", mock_ak)
        mock_ak.bond_zh_cov_info.side_effect = Exception("网络错误")
        sniper = CBT0Sniper()
        assert sniper._cb_map == {}


class TestScanCbOpportunities:
    """scan_cb_opportunities 扫描机会"""

    def test_no_limit_ups_returns_empty(self, sniper):
        """无涨停股 → 空列表"""
        # sniper fixture 已 mock mod.ak,空 spot → 无涨停
        mod.ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
        opportunities = sniper.scan_cb_opportunities()
        assert opportunities == []

    def test_stock_not_in_cb_map_skipped(self, monkeypatch):
        """涨停股不在可转债映射中 → 跳过"""
        mock_ak = MagicMock()
        monkeypatch.setattr(mod, "ak", mock_ak)
        mock_ak.bond_zh_cov_info.return_value = pd.DataFrame()
        sniper = CBT0Sniper()
        # 涨停股 600519 不在 _cb_map 中
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{
            "代码": "600519", "名称": "贵州茅台", "涨跌幅": 10.0,
        }])
        opportunities = sniper.scan_cb_opportunities()
        assert opportunities == []


class TestMonitorCb:
    """monitor_cb 监控生成器"""

    def test_stock_not_in_map_yields_nothing(self, sniper):
        """监控不在映射中的股票 → 生成器立即结束(无 yield)"""
        # sniper._cb_map 为空,stock_code 不在其中
        gen = sniper.monitor_cb("999999", interval=0)
        with pytest.raises(StopIteration):
            next(gen)

    def test_stop_loss_signal_breaks_loop(self, monkeypatch):
        """跌破止损 → 生成 STOP_LOSS 信号并终止"""
        mock_ak = MagicMock()
        monkeypatch.setattr(mod, "ak", mock_ak)
        mock_ak.bond_zh_cov_info.return_value = pd.DataFrame([{
            "正股代码": "601988", "债券代码": "113001", "债券简称": "中行转债",
            "正股简称": "中国银行", "转股价": 10.0,
        }])
        sniper = CBT0Sniper()
        # 第一次:入场价=100;第二次:转债价=96 → 跌 4% < -3% 止损
        mock_ak.bond_zh_cov_spot.side_effect = [
            pd.DataFrame([{"代码": "113001", "最新价": 100.0, "换手率": 5.0}]),
            pd.DataFrame([{"代码": "113001", "最新价": 96.0, "换手率": 5.0}]),
        ]
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{"代码": "601988", "最新价": 10.0}])
        with patch.object(mod, "time") as mock_time:
            mock_time.sleep.return_value = None
            gen = sniper.monitor_cb("601988", interval=0)
            first = next(gen)   # 入场,HOLD
            second = next(gen)  # 跌破止损,STOP_LOSS
        assert first["signal"] == "HOLD"
        assert second["signal"] == "STOP_LOSS"
        assert second["pnl_pct"] == pytest.approx(-0.04, rel=1e-2)
        # STOP_LOSS 应终止生成器
        with pytest.raises(StopIteration):
            next(gen)

    def test_exit_premium_signal_breaks_loop(self, monkeypatch):
        """溢价率过高 → 生成 EXIT_PREMIUM 信号并终止"""
        mock_ak = MagicMock()
        monkeypatch.setattr(mod, "ak", mock_ak)
        mock_ak.bond_zh_cov_info.return_value = pd.DataFrame([{
            "正股代码": "601988", "债券代码": "113001", "债券简称": "中行转债",
            "正股简称": "中国银行", "转股价": 10.0,
        }])
        sniper = CBT0Sniper()
        # 转债价 200,正股价 10,转股价值=100,溢价率=100% > 30%
        mock_ak.bond_zh_cov_spot.return_value = pd.DataFrame([{"代码": "113001", "最新价": 200.0, "换手率": 5.0}])
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame([{"代码": "601988", "最新价": 10.0}])
        with patch.object(mod, "time") as mock_time:
            mock_time.sleep.return_value = None
            gen = sniper.monitor_cb("601988", interval=0)
            first = next(gen)
        assert first["signal"] == "EXIT_PREMIUM"
        assert first["position_pct"] == POSITION_PCT
        with pytest.raises(StopIteration):
            next(gen)

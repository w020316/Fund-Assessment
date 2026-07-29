"""风控管理器单元测试

验证 src/core/risk_manager.py 的核心逻辑:
- RiskManager 初始化(数据库/状态加载)
- checkOrder 订单风控检查(回撤/单日亏损/连损)
- update_position 持仓更新
- record_trade 交易记录(连损计数)
- get_risk_status 风险状态查询
- emergency_stop / resume 紧急停止/恢复
- 暂停期到期/每日重置
- TradeRecord 数据类
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.risk_manager import (
    RiskLevel,
    RiskManager,
    TradeRecord,
)


@pytest.fixture
def risk_manager(tmp_path: Path) -> RiskManager:
    """每个测试独立的 RiskManager(使用 tmp_path 隔离 SQLite)"""
    db_path = tmp_path / "risk.db"
    return RiskManager(db_path=db_path, initial_assets=1_000_000.0)


class TestInitialization:
    """初始化与状态加载"""

    def test_init_creates_db_file(self, tmp_path: Path):
        """初始化应创建数据库文件"""
        db_path = tmp_path / "risk.db"
        assert not db_path.exists()
        rm = RiskManager(db_path=db_path, initial_assets=1_000_000.0)
        assert db_path.exists()
        assert rm._total_assets == 1_000_000.0
        assert rm._peak_assets == 1_000_000.0

    def test_init_creates_parent_dir(self, tmp_path: Path):
        """父目录不存在时应自动创建"""
        db_path = tmp_path / "sub" / "nested" / "risk.db"
        rm = RiskManager(db_path=db_path)
        assert db_path.exists()

    def test_state_persists_across_instances(self, tmp_path: Path):
        """状态应在实例间持久化"""
        db_path = tmp_path / "risk.db"
        rm1 = RiskManager(db_path=db_path, initial_assets=1_000_000.0)
        rm1._total_assets = 1_100_000.0
        rm1._peak_assets = 1_100_000.0
        rm1._save_state()

        rm2 = RiskManager(db_path=db_path)
        assert rm2._total_assets == 1_100_000.0
        assert rm2._peak_assets == 1_100_000.0

    def test_load_state_exception_safe(self, tmp_path: Path):
        """_load_state 查询异常时应降级到 _save_state,不抛出"""
        db_path = tmp_path / "risk.db"
        rm = RiskManager(db_path=db_path, initial_assets=500_000.0)
        # 模拟 _load_state 内部查询失败,但不应抛出
        with patch.object(rm, "_get_conn") as mock_conn:
            mock_conn.side_effect = Exception("simulated DB error")
            rm._load_state()
        # 应保留 initial_assets(降级处理)
        assert rm._total_assets == 500_000.0


class TestCheckOrderNormal:
    """check_order 正常场景"""

    def test_normal_buy_order_passes(self, risk_manager):
        """正常买入订单应通过"""
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is True
        assert "通过" in reason

    def test_normal_sell_order_passes(self, risk_manager):
        """正常卖出订单应通过"""
        passed, reason = risk_manager.check_order({"side": "sell"})
        assert passed is True

    def test_check_order_returns_tuple(self, risk_manager):
        """应返回 (bool, str) 元组"""
        result = risk_manager.check_order({"side": "buy"})
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


class TestCheckOrderDrawdown:
    """check_order 资产回撤"""

    def test_drawdown_over_threshold_triggers_pause(self, risk_manager):
        """回撤超 15% → 暂停 5 天"""
        # 总资产 80万,峰值 100万 → 回撤 20%
        risk_manager._total_assets = 800_000.0
        risk_manager._peak_assets = 1_000_000.0
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is False
        assert "回撤" in reason
        assert risk_manager._is_paused is True
        assert risk_manager._pause_until is not None

    def test_paused_state_blocks_all_orders(self, risk_manager):
        """暂停期所有订单被拒"""
        risk_manager._is_paused = True
        risk_manager._pause_until = date.today() + timedelta(days=3)
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is False
        assert "暂停" in reason

    def test_pause_expires_after_period(self, risk_manager):
        """暂停期到期后自动恢复"""
        risk_manager._is_paused = True
        risk_manager._pause_until = date.today() - timedelta(days=1)  # 已过期
        risk_manager._save_state()
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is True
        assert risk_manager._is_paused is False


class TestCheckOrderDailyLoss:
    """check_order 单日亏损"""

    def test_daily_loss_over_threshold_blocks_buy(self, risk_manager):
        """单日亏损超 5% → 禁止开新仓"""
        risk_manager._daily_start_assets = 1_000_000.0
        risk_manager._daily_pnl = -60_000.0  # -6%
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is False
        assert "单日亏损" in reason
        assert risk_manager._no_new_positions is True

    def test_daily_loss_does_not_block_sell(self, risk_manager):
        """单日亏损不阻止卖出"""
        risk_manager._daily_start_assets = 1_000_000.0
        risk_manager._daily_pnl = -60_000.0
        passed, reason = risk_manager.check_order({"side": "sell"})
        assert passed is True

    def test_no_new_positions_blocks_buy_only(self, risk_manager):
        """no_new_positions 仅阻止买入,不阻止卖出"""
        risk_manager._no_new_positions = True
        buy_passed, _ = risk_manager.check_order({"side": "buy"})
        sell_passed, _ = risk_manager.check_order({"side": "sell"})
        assert buy_passed is False
        assert sell_passed is True


class TestCheckOrderConsecutiveStopLoss:
    """check_order 连续止损"""

    def test_consecutive_stop_loss_reduces_position(self, risk_manager):
        """连续 3 次止损 → 买入仓位缩减至 50%"""
        risk_manager._consecutive_stop_losses = 3
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is True
        assert "仓位缩减" in reason
        assert risk_manager._position_reduction == 0.5

    def test_below_limit_no_reduction(self, risk_manager):
        """连损 < 3 → 不缩减仓位"""
        risk_manager._consecutive_stop_losses = 2
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is True
        assert "通过" in reason
        assert risk_manager._position_reduction == 1.0


class TestCheckOrderEmergencyStop:
    """check_order 紧急停止"""

    def test_emergency_stop_blocks_all(self, risk_manager):
        """紧急停止 → 所有订单被拒"""
        risk_manager._is_emergency_stopped = True
        passed, reason = risk_manager.check_order({"side": "buy"})
        assert passed is False
        assert "紧急停止" in reason

    def test_emergency_stop_blocks_sell_too(self, risk_manager):
        """紧急停止连卖出也阻止"""
        risk_manager._is_emergency_stopped = True
        passed, _ = risk_manager.check_order({"side": "sell"})
        assert passed is False


class TestUpdatePosition:
    """update_position 持仓更新"""

    def test_updates_total_assets(self, risk_manager):
        risk_manager.update_position({"total_assets": 1_100_000.0})
        assert risk_manager._total_assets == 1_100_000.0

    def test_updates_peak_when_new_high(self, risk_manager):
        """创新高时更新 peak"""
        risk_manager.update_position({"total_assets": 1_200_000.0})
        assert risk_manager._peak_assets == 1_200_000.0

    def test_does_not_lower_peak_on_drawdown(self, risk_manager):
        """回撤时不降低 peak"""
        risk_manager.update_position({"total_assets": 1_200_000.0})
        risk_manager.update_position({"total_assets": 900_000.0})
        assert risk_manager._peak_assets == 1_200_000.0

    def test_updates_daily_pnl(self, risk_manager):
        risk_manager._daily_start_assets = 1_000_000.0
        risk_manager.update_position({"total_assets": 1_050_000.0})
        assert risk_manager._daily_pnl == 50_000.0


class TestRecordTrade:
    """record_trade 交易记录"""

    def test_records_profit(self, risk_manager):
        """盈利交易应增加总资产"""
        trade = TradeRecord(
            symbol="600519", side="sell", price=100.0, quantity=100,
            amount=10_000.0, profit=500.0, is_stop_loss=False,
        )
        risk_manager.record_trade(trade)
        assert risk_manager._total_assets == 1_000_500.0
        assert risk_manager._daily_pnl == 500.0

    def test_stop_loss_increases_consecutive_count(self, risk_manager):
        """止损交易 → 连损计数+1"""
        trade = TradeRecord(
            symbol="600519", side="sell", price=100.0, quantity=100,
            amount=10_000.0, profit=-200.0, is_stop_loss=True,
        )
        risk_manager.record_trade(trade)
        assert risk_manager._consecutive_stop_losses == 1

    def test_profitable_sell_resets_consecutive_count(self, risk_manager):
        """止盈卖出 → 重置连损计数"""
        risk_manager._consecutive_stop_losses = 2
        trade = TradeRecord(
            symbol="600519", side="sell", price=100.0, quantity=100,
            amount=10_000.0, profit=500.0, is_stop_loss=False,
        )
        risk_manager.record_trade(trade)
        assert risk_manager._consecutive_stop_losses == 0

    def test_record_trade_persists_to_db(self, risk_manager, tmp_path):
        """交易记录应持久化到 SQLite"""
        trade = TradeRecord(
            symbol="600519", side="buy", price=100.0, quantity=100,
            amount=10_000.0, profit=0.0, is_stop_loss=False,
        )
        risk_manager.record_trade(trade)
        # 重新创建实例,验证状态
        rm2 = RiskManager(db_path=risk_manager._db_path)
        assert rm2._total_assets == risk_manager._total_assets


class TestGetRiskStatus:
    """get_risk_status 风险状态查询"""

    def test_normal_status(self, risk_manager):
        status = risk_manager.get_risk_status()
        assert status.level == RiskLevel.NORMAL
        assert status.total_assets == 1_000_000.0
        assert status.is_paused is False
        assert status.is_emergency_stopped is False

    def test_emergency_status(self, risk_manager):
        risk_manager.emergency_stop()
        status = risk_manager.get_risk_status()
        assert status.level == RiskLevel.EMERGENCY
        assert status.is_emergency_stopped is True

    def test_paused_status(self, risk_manager):
        risk_manager._is_paused = True
        risk_manager._pause_until = date.today() + timedelta(days=3)
        status = risk_manager.get_risk_status()
        assert status.level == RiskLevel.DANGER
        assert "暂停" in status.message

    def test_drawdown_warning(self, risk_manager):
        """回撤接近阈值(>7.5%) → WARNING"""
        risk_manager._total_assets = 920_000.0  # 回撤 8%
        risk_manager._peak_assets = 1_000_000.0
        status = risk_manager.get_risk_status()
        assert status.level == RiskLevel.WARNING
        assert "回撤" in status.message

    def test_consecutive_stop_loss_warning(self, risk_manager):
        """连损 >= 3 → WARNING"""
        risk_manager._consecutive_stop_losses = 3
        status = risk_manager.get_risk_status()
        assert status.level == RiskLevel.WARNING


class TestEmergencyAndResume:
    """emergency_stop / resume"""

    def test_emergency_stop_sets_flag(self, risk_manager):
        risk_manager.emergency_stop()
        assert risk_manager._is_emergency_stopped is True

    def test_resume_resets_all_flags(self, risk_manager):
        """resume 应重置所有风控状态"""
        risk_manager._is_emergency_stopped = True
        risk_manager._is_paused = True
        risk_manager._pause_until = date.today() + timedelta(days=3)
        risk_manager._no_new_positions = True
        risk_manager._consecutive_stop_losses = 5
        risk_manager._position_reduction = 0.5

        risk_manager.resume()

        assert risk_manager._is_emergency_stopped is False
        assert risk_manager._is_paused is False
        assert risk_manager._pause_until is None
        assert risk_manager._no_new_positions is False
        assert risk_manager._consecutive_stop_losses == 0
        assert risk_manager._position_reduction == 1.0

    def test_resume_persists(self, risk_manager, tmp_path):
        """resume 后状态应持久化"""
        risk_manager.emergency_stop()
        risk_manager.resume()
        rm2 = RiskManager(db_path=risk_manager._db_path)
        assert rm2._is_emergency_stopped is False


class TestTradeRecord:
    """TradeRecord 数据类"""

    def test_creation_with_required_fields(self):
        trade = TradeRecord(
            symbol="600519", side="buy", price=100.0, quantity=100,
            amount=10_000.0, profit=0.0, is_stop_loss=False,
        )
        assert trade.symbol == "600519"
        assert trade.is_stop_loss is False
        assert isinstance(trade.timestamp, datetime)

    def test_default_timestamp(self):
        """timestamp 应有默认值"""
        trade = TradeRecord(
            symbol="600519", side="buy", price=100.0, quantity=100,
            amount=10_000.0, profit=0.0, is_stop_loss=False,
        )
        assert trade.timestamp is not None

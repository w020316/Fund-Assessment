"""每日资金流管理单元测试

验证 src/monitor/daily_flow.py:
- TradingPhase 枚举完整性与 lookup
- PhaseResult 数据类与 details 默认值
- DailyFlowManager 各阶段成功路径(返回 success=True)
- 各阶段内部异常降级(返回 success=False, 携带异常信息)
- run_phase 字符串调度(合法/非法 phase)
- _start_monitor 设置 _monitor_active
"""
from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import patch

import pytest

from src.monitor.daily_flow import DailyFlowManager, PhaseResult, TradingPhase


class TestTradingPhase:
    """TradingPhase 枚举"""

    def test_all_phases_defined(self):
        names = {p.name for p in TradingPhase}
        assert names == {
            "PRE_MARKET", "MORNING_SESSION", "AFTERNOON_SESSION",
            "LATE_TRADING", "POST_MARKET",
        }

    def test_phase_values_lowercase(self):
        assert TradingPhase.PRE_MARKET.value == "pre_market"
        assert TradingPhase.MORNING_SESSION.value == "morning_session"
        assert TradingPhase.AFTERNOON_SESSION.value == "afternoon_session"
        assert TradingPhase.LATE_TRADING.value == "late_trading"
        assert TradingPhase.POST_MARKET.value == "post_market"

    def test_lookup_by_value(self):
        assert TradingPhase("late_trading") == TradingPhase.LATE_TRADING

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TradingPhase("unknown")


class TestPhaseResult:
    """PhaseResult 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(PhaseResult)

    def test_required_fields(self):
        r = PhaseResult(phase=TradingPhase.PRE_MARKET, success=True, message="ok")
        assert r.phase == TradingPhase.PRE_MARKET
        assert r.success is True
        assert r.message == "ok"

    def test_details_defaults_to_empty_dict(self):
        r = PhaseResult(phase=TradingPhase.PRE_MARKET, success=True, message="ok")
        assert r.details == {}

    def test_explicit_details_kept(self):
        r = PhaseResult(phase=TradingPhase.PRE_MARKET, success=True, message="ok", details={"k": 1})
        assert r.details == {"k": 1}

    def test_post_init_converts_none_details(self):
        """显式传 details=None 应被 __post_init__ 转为 {}"""
        r = PhaseResult(phase=TradingPhase.PRE_MARKET, success=True, message="ok", details=None)
        assert r.details == {}

    def test_default_factory_independent_dicts(self):
        a = PhaseResult(phase=TradingPhase.PRE_MARKET, success=True, message="ok")
        b = PhaseResult(phase=TradingPhase.PRE_MARKET, success=True, message="ok")
        a.details["x"] = 1
        assert "x" not in b.details


class TestPreMarket:
    """pre_market 盘前阶段"""

    def test_success_returns_phase_result(self):
        mgr = DailyFlowManager()
        r = mgr.pre_market()
        assert isinstance(r, PhaseResult)
        assert r.phase == TradingPhase.PRE_MARKET
        assert r.success is True
        assert "盘前" in r.message

    def test_exception_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_update_watchlist_quotes", side_effect=RuntimeError("db down")):
            r = mgr.pre_market()
        assert r.success is False
        assert r.phase == TradingPhase.PRE_MARKET
        assert "db down" in r.message

    def test_exception_in_scan_limit_up_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_scan_limit_up_alerts", side_effect=ValueError("parse err")):
            r = mgr.pre_market()
        assert r.success is False
        assert "parse err" in r.message


class TestMorningSession:
    """morning_session 上午盘中"""

    def test_success_returns_phase_result(self):
        mgr = DailyFlowManager()
        r = mgr.morning_session()
        assert r.phase == TradingPhase.MORNING_SESSION
        assert r.success is True
        assert "上午" in r.message

    def test_start_monitor_sets_active(self):
        """_start_monitor 应将 _monitor_active 置为 True"""
        mgr = DailyFlowManager()
        assert mgr._monitor_active is False
        mgr.morning_session()
        assert mgr._monitor_active is True

    def test_exception_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_scan_cb_t0", side_effect=RuntimeError("cb err")):
            r = mgr.morning_session()
        assert r.success is False
        assert "cb err" in r.message

    def test_exception_in_capital_flow_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_monitor_capital_flow", side_effect=Exception("net")):
            r = mgr.morning_session()
        assert r.success is False
        assert "net" in r.message


class TestAfternoonSession:
    """afternoon_session 下午盘中"""

    def test_success_returns_phase_result(self):
        mgr = DailyFlowManager()
        r = mgr.afternoon_session()
        assert r.phase == TradingPhase.AFTERNOON_SESSION
        assert r.success is True
        assert "下午" in r.message

    def test_exception_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_continue_intraday_monitor", side_effect=IOError("io")):
            r = mgr.afternoon_session()
        assert r.success is False
        assert "io" in r.message


class TestLateTrading:
    """late_trading 尾盘"""

    def test_success_returns_phase_result(self):
        mgr = DailyFlowManager()
        r = mgr.late_trading()
        assert r.phase == TradingPhase.LATE_TRADING
        assert r.success is True
        assert "尾盘" in r.message

    def test_exception_in_new_high_scan_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_scan_new_high_strategy", side_effect=RuntimeError("scan")):
            r = mgr.late_trading()
        assert r.success is False
        assert "scan" in r.message

    def test_exception_in_execute_orders_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_execute_orders", side_effect=Exception("order fail")):
            r = mgr.late_trading()
        assert r.success is False
        assert "order fail" in r.message


class TestPostMarket:
    """post_market 盘后"""

    def test_success_returns_phase_result(self):
        mgr = DailyFlowManager()
        r = mgr.post_market()
        assert r.phase == TradingPhase.POST_MARKET
        assert r.success is True
        assert "盘后" in r.message

    def test_exception_in_daily_report_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_generate_daily_report", side_effect=Exception("report")):
            r = mgr.post_market()
        assert r.success is False
        assert "report" in r.message

    def test_exception_in_position_update_returns_failure(self):
        mgr = DailyFlowManager()
        with patch.object(mgr, "_update_positions", side_effect=ValueError("pos")):
            r = mgr.post_market()
        assert r.success is False
        assert "pos" in r.message


class TestRunPhase:
    """run_phase 字符串调度"""

    @pytest.mark.parametrize("phase_str,expected", [
        ("pre_market", TradingPhase.PRE_MARKET),
        ("morning_session", TradingPhase.MORNING_SESSION),
        ("afternoon_session", TradingPhase.AFTERNOON_SESSION),
        ("late_trading", TradingPhase.LATE_TRADING),
        ("post_market", TradingPhase.POST_MARKET),
    ])
    def test_valid_phase_string_routes_to_handler(self, phase_str, expected):
        mgr = DailyFlowManager()
        r = mgr.run_phase(phase_str)
        assert r.phase == expected
        assert r.success is True

    def test_unknown_phase_returns_failure_with_valid_list(self):
        mgr = DailyFlowManager()
        r = mgr.run_phase("invalid_phase")
        assert r.success is False
        # 错误响应回退到 PRE_MARKET
        assert r.phase == TradingPhase.PRE_MARKET
        assert "invalid_phase" in r.message
        # 消息中应列出所有合法值
        for v in ["pre_market", "morning_session", "afternoon_session", "late_trading", "post_market"]:
            assert v in r.message

    def test_unknown_phase_message_contains_all_valid_values(self):
        mgr = DailyFlowManager()
        r = mgr.run_phase("noon")
        valid = [p.value for p in TradingPhase]
        assert str(valid) in r.message


class TestInternalHelpers:
    """内部辅助方法直接调用(覆盖纯 log 路径)"""

    def test_all_internal_helpers_callable(self):
        mgr = DailyFlowManager()
        # 这些方法仅记录日志,调用不抛异常即覆盖
        mgr._update_watchlist_quotes()
        mgr._scan_limit_up_alerts()
        mgr._scan_cb_t0()
        mgr._monitor_limit_up()
        mgr._monitor_capital_flow()
        mgr._continue_intraday_monitor()
        mgr._scan_new_high_strategy()
        mgr._predict_limit_up_type()
        mgr._execute_orders()
        mgr._generate_daily_report()
        mgr._update_positions()
        mgr._analyze_daily_trades()
        mgr._prepare_tomorrow_watchlist()

    def test_start_monitor_sets_active_flag(self):
        mgr = DailyFlowManager()
        assert mgr._monitor_active is False
        mgr._start_monitor()
        assert mgr._monitor_active is True


class TestPhaseHandlersMapping:
    """_phase_handlers 映射完整性"""

    def test_all_phases_have_handlers(self):
        mgr = DailyFlowManager()
        for phase in TradingPhase:
            assert phase in mgr._phase_handlers
            assert callable(mgr._phase_handlers[phase])

    def test_handlers_bound_to_methods(self):
        mgr = DailyFlowManager()
        assert mgr._phase_handlers[TradingPhase.PRE_MARKET] == mgr.pre_market
        assert mgr._phase_handlers[TradingPhase.MORNING_SESSION] == mgr.morning_session

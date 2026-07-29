"""告警管理单元测试

验证 src/monitor/alert.py:
- AlertLevel 枚举值
- AlertInfo 数据类字段与默认值
- AlertManager 注册/检查/触发/去重
- 回调异常与条件异常的容错
"""
from __future__ import annotations

from dataclasses import is_dataclass
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.monitor.alert import AlertInfo, AlertLevel, AlertManager


class TestAlertLevel:
    """AlertLevel 枚举"""

    def test_all_levels_defined(self):
        names = {lv.name for lv in AlertLevel}
        assert names == {"INFO", "WARNING", "CRITICAL"}

    def test_level_values(self):
        assert AlertLevel.INFO.value == "INFO"
        assert AlertLevel.WARNING.value == "WARNING"
        assert AlertLevel.CRITICAL.value == "CRITICAL"

    def test_lookup_by_value(self):
        assert AlertLevel("CRITICAL") == AlertLevel.CRITICAL


class TestAlertInfo:
    """AlertInfo 数据类"""

    def test_is_dataclass(self):
        assert is_dataclass(AlertInfo)

    def test_required_fields(self):
        info = AlertInfo(
            rule_name="r1",
            stock_code="600519",
            level=AlertLevel.WARNING,
            message="m",
        )
        assert info.rule_name == "r1"
        assert info.stock_code == "600519"
        assert info.level == AlertLevel.WARNING
        assert info.message == "m"

    def test_timestamp_default_now(self):
        before = datetime.now()
        info = AlertInfo("r", "c", AlertLevel.INFO, "m")
        after = datetime.now()
        assert before <= info.timestamp <= after

    def test_details_default_empty_dict(self):
        info = AlertInfo("r", "c", AlertLevel.INFO, "m")
        assert info.details == {}

    def test_details_custom_kept(self):
        info = AlertInfo("r", "c", AlertLevel.INFO, "m", details={"k": 1})
        assert info.details == {"k": 1}

    def test_default_factory_independent_dicts(self):
        """details 默认工厂应使每个实例独立字典"""
        a = AlertInfo("r", "c", AlertLevel.INFO, "m")
        b = AlertInfo("r", "c", AlertLevel.INFO, "m")
        a.details["x"] = 1
        assert "x" not in b.details


class TestAlertManagerRegister:
    """AlertManager 注册"""

    def test_register_stores_rule(self):
        mgr = AlertManager()
        cond = lambda d: True  # noqa: E731
        cb = lambda info: None  # noqa: E731
        mgr.register_alert("rule1", cond, cb)
        assert "rule1" in mgr._rules
        assert mgr._rules["rule1"]["condition"] is cond
        assert mgr._rules["rule1"]["callback"] is cb

    def test_register_overwrites_existing(self):
        mgr = AlertManager()
        mgr.register_alert("r", lambda d: False, lambda i: None)
        new_cb = lambda i: None  # noqa: E731
        mgr.register_alert("r", lambda d: True, new_cb)
        assert mgr._rules["r"]["callback"] is new_cb

    def test_default_dedup_minutes(self):
        assert AlertManager()._dedup_minutes == 5

    def test_custom_dedup_minutes(self):
        assert AlertManager(dedup_minutes=10)._dedup_minutes == 10


class TestAlertManagerCheckAll:
    """check_all 检查与触发"""

    def test_no_rules_returns_empty(self):
        mgr = AlertManager()
        assert mgr.check_all("600519", {"price": 10}) == []

    def test_condition_true_triggers_and_callback(self):
        triggered = []
        mgr = AlertManager()
        mgr.register_alert(
            "breakout",
            lambda d: d.get("price", 0) > 100,
            lambda info: triggered.append(info),
        )
        result = mgr.check_all("600519", {"price": 110})
        assert len(result) == 1
        assert result[0].rule_name == "breakout"
        assert result[0].stock_code == "600519"
        assert result[0].level == AlertLevel.WARNING
        assert triggered and triggered[0] is result[0]
        # details 应携带 market_data
        assert result[0].details == {"price": 110}

    def test_condition_false_not_triggered(self):
        called = []
        mgr = AlertManager()
        mgr.register_alert("r", lambda d: d.get("price", 0) > 100, lambda i: called.append(i))
        result = mgr.check_all("000001", {"price": 50})
        assert result == []
        assert called == []

    def test_none_market_data_treated_as_empty(self):
        """market_data=None 时 condition 收到空 dict"""
        received = []
        mgr = AlertManager()
        mgr.register_alert("r", lambda d: received.append(d) or True, lambda i: None)
        mgr.check_all("c", None)
        assert received == [{}]

    def test_condition_exception_skipped(self):
        """condition 抛异常时该规则被跳过,不影响其他规则"""
        called = []
        mgr = AlertManager()
        mgr.register_alert("bad", lambda d: (_ for _ in ()).throw(ValueError("x")), lambda i: called.append("bad"))
        mgr.register_alert("good", lambda d: True, lambda i: called.append("good"))
        result = mgr.check_all("c", {})
        # bad 被跳过,good 触发
        assert len(result) == 1
        assert result[0].rule_name == "good"
        assert called == ["good"]

    def test_callback_exception_does_not_propagate(self):
        """回调抛异常应被捕获,不影响 check_all 返回"""
        mgr = AlertManager()
        mgr.register_alert("r", lambda d: True, lambda i: (_ for _ in ()).throw(RuntimeError("cb fail")))
        result = mgr.check_all("c", {})
        # 仍应被加入 triggered 列表(trigger_alert 在 callback 之前 append)
        assert len(result) == 1

    def test_multiple_rules_all_triggered(self):
        mgr = AlertManager()
        mgr.register_alert("r1", lambda d: True, lambda i: None)
        mgr.register_alert("r2", lambda d: True, lambda i: None)
        result = mgr.check_all("c", {})
        assert {r.rule_name for r in result} == {"r1", "r2"}


class TestAlertManagerDedup:
    """去重逻辑"""

    def test_first_trigger_not_deduplicated(self):
        mgr = AlertManager(dedup_minutes=5)
        assert mgr._is_deduplicated("c", "r") is False

    def test_within_window_is_deduplicated(self):
        mgr = AlertManager(dedup_minutes=5)
        key = "c:r"
        mgr._last_triggered[key] = datetime.now() - timedelta(minutes=1)
        assert mgr._is_deduplicated("c", "r") is True

    def test_outside_window_not_deduplicated(self):
        mgr = AlertManager(dedup_minutes=5)
        key = "c:r"
        mgr._last_triggered[key] = datetime.now() - timedelta(minutes=10)
        assert mgr._is_deduplicated("c", "r") is False

    def test_check_all_dedup_skips_recently_triggered(self):
        """同一规则在去重窗口内应被跳过(不加入返回列表,不回调)"""
        call_count = 0

        def cb(_info):
            nonlocal call_count
            call_count += 1

        mgr = AlertManager(dedup_minutes=5)
        mgr.register_alert("r", lambda d: True, cb)
        first = mgr.check_all("c", {})
        second = mgr.check_all("c", {})
        assert len(first) == 1
        assert second == []  # 第二次被去重
        assert call_count == 1  # 回调只触发一次

    def test_dedup_per_stock_per_rule(self):
        """去重按 stock_code:rule_name 维度独立"""
        called = []
        mgr = AlertManager(dedup_minutes=5)
        mgr.register_alert("r", lambda d: True, lambda i: called.append(i.stock_code))
        mgr.check_all("c1", {})  # 触发 c1/r
        mgr.check_all("c2", {})  # c2/r 不同 key,也应触发
        assert len(called) == 2
        assert sorted(called) == ["c1", "c2"]


class TestTriggerAlert:
    """trigger_alert 直接调用"""

    def test_unknown_rule_logs_warning_no_crash(self):
        """对未注册的 rule_name 调用 trigger_alert 不应崩溃"""
        mgr = AlertManager()
        info = AlertInfo("ghost", "c", AlertLevel.CRITICAL, "m")
        # 不抛异常即通过
        mgr.trigger_alert(info)

    def test_updates_last_triggered_timestamp(self):
        mgr = AlertManager()
        mgr.register_alert("r", lambda d: True, lambda i: None)
        info = AlertInfo("r", "c", AlertLevel.WARNING, "m")
        mgr.trigger_alert(info)
        assert "c:r" in mgr._last_triggered

    def test_callback_receives_alert_info(self):
        received = []
        mgr = AlertManager()
        mgr.register_alert("r", lambda d: True, lambda i: received.append(i))
        info = AlertInfo("r", "c", AlertLevel.CRITICAL, "msg")
        mgr.trigger_alert(info)
        assert received == [info]

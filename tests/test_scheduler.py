"""调度器单元测试

验证 src/core/scheduler.py:
- Scheduler 构造与默认任务注册(_register_default_jobs)
- _safe_run 安全执行(func 异常不外泄)
- set_callback 回调注册(合法/未知任务名)
- add_job / remove_job 调度任务管理
- get_jobs 任务查询(需 start 后 next_run_time 才可用)
- start / stop / is_running 生命周期(通过 mock 避免真实线程)
- _noop 静态方法

约束:不启动真实 BackgroundScheduler 长驻线程,所有 start/stop 用 mock;
get_jobs 包装方法因 next_run_time 在 start 前不可用,使用 start→stop 短驻夹具。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.core.scheduler import Scheduler, _DEFAULT_JOB_TIMEOUT


_DEFAULT_CALLBACK_KEYS = [
    "pre_market_watchlist", "pre_market_limit_up_scan",
    "intraday_bond_monitor", "intraday_limit_up_monitor", "intraday_capital_flow",
    "late_gem_new_high_scan", "late_limit_up_predict",
    "post_daily_report", "post_position_update", "post_trade_analysis",
    "post_watchlist_update",
]


@pytest.fixture
def scheduler() -> Scheduler:
    """构造调度器(不启动真实线程)"""
    return Scheduler()


@pytest.fixture
def started_scheduler():
    """启动调度器并立即在用例后停止,用于需要 next_run_time 的 get_jobs 测试"""
    s = Scheduler()
    s.start()
    yield s
    if s.is_running:
        s.stop()


def _raw_job_ids(scheduler: Scheduler) -> set[str]:
    """直接访问 apscheduler Job 对象的 id(不经过 get_jobs 包装,避开 next_run_time 缺失)"""
    return {j.id for j in scheduler._scheduler.get_jobs()}


def _raw_jobs(scheduler: Scheduler) -> dict[str, object]:
    """返回 {id: Job} 映射,Job 拥有 id/name/trigger 属性"""
    return {j.id: j for j in scheduler._scheduler.get_jobs()}


class TestSchedulerInit:
    """Scheduler 构造与默认配置"""

    def test_default_job_timeout(self, scheduler):
        assert scheduler._job_timeout == _DEFAULT_JOB_TIMEOUT

    def test_custom_job_timeout(self):
        s = Scheduler(job_timeout=120)
        assert s._job_timeout == 120

    def test_default_callbacks_registered(self, scheduler):
        """__init__ 应注册全部 11 个默认回调"""
        for key in _DEFAULT_CALLBACK_KEYS:
            assert key in scheduler._callbacks
            assert callable(scheduler._callbacks[key])

    def test_default_callbacks_are_noop(self, scheduler):
        """默认回调初始指向 _noop"""
        for key in _DEFAULT_CALLBACK_KEYS:
            assert scheduler._callbacks[key] == Scheduler._noop

    def test_default_jobs_added_to_underlying_scheduler(self, scheduler):
        """默认任务应已加入 apscheduler(无需 start 即可查询 Job.id)"""
        ids = _raw_job_ids(scheduler)
        for key in _DEFAULT_CALLBACK_KEYS:
            assert key in ids


class TestNoop:
    """_noop 静态方法"""

    def test_noop_returns_none(self):
        assert Scheduler._noop() is None

    def test_noop_does_not_raise(self):
        Scheduler._noop()  # 不抛异常即通过

    def test_noop_is_staticmethod(self):
        # 静态方法可通过类直接调用
        assert Scheduler._noop() is None


class TestSafeRun:
    """_safe_run 安全执行包装"""

    def test_runs_func_successfully(self, scheduler):
        called = []
        scheduler._safe_run("job1", lambda: called.append("ok"))
        assert called == ["ok"]

    def test_swallows_exception(self, scheduler):
        """func 抛异常应被捕获,不外泄"""

        def boom():
            raise RuntimeError("kaboom")

        scheduler._safe_run("job1", boom)  # 不抛异常即通过

    def test_exception_in_func_does_not_stop_caller(self, scheduler):
        log = []
        scheduler._safe_run("bad", lambda: (_ for _ in ()).throw(ValueError("x")))
        scheduler._safe_run("good", lambda: log.append("good"))
        assert log == ["good"]

    def test_safe_run_invokes_real_callback_after_set(self, scheduler):
        """set_callback 后 _safe_run 应调用新回调"""
        called = []

        def cb():
            called.append("hit")

        scheduler.set_callback("post_daily_report", cb)
        scheduler._safe_run("盘后-日报", scheduler._callbacks["post_daily_report"])
        assert called == ["hit"]


class TestSetCallback:
    """set_callback 回调注册"""

    def test_valid_job_name_updates_callback(self, scheduler):
        def my_cb():
            pass
        scheduler.set_callback("post_daily_report", my_cb)
        assert scheduler._callbacks["post_daily_report"] is my_cb

    def test_unknown_job_name_does_not_crash(self, scheduler):
        """未知任务名应仅 warning,不抛异常,不污染 _callbacks"""
        scheduler.set_callback("nonexistent_job", lambda: None)
        assert "nonexistent_job" not in scheduler._callbacks

    def test_callback_replaces_noop(self, scheduler):
        original = scheduler._callbacks["pre_market_watchlist"]
        assert original == Scheduler._noop
        new_cb = lambda: None  # noqa: E731
        scheduler.set_callback("pre_market_watchlist", new_cb)
        assert scheduler._callbacks["pre_market_watchlist"] is new_cb
        assert scheduler._callbacks["pre_market_watchlist"] != Scheduler._noop

    def test_each_callback_key_independent(self, scheduler):
        a = lambda: None  # noqa: E731
        b = lambda: None  # noqa: E731
        scheduler.set_callback("post_trade_analysis", a)
        scheduler.set_callback("post_watchlist_update", b)
        assert scheduler._callbacks["post_trade_analysis"] is a
        assert scheduler._callbacks["post_watchlist_update"] is b


class TestAddJob:
    """add_job 自定义任务"""

    def test_adds_job_to_scheduler(self, scheduler):
        scheduler.add_job(lambda: None, "custom_1", name="自定义任务", hour=9, minute=0)
        assert "custom_1" in _raw_job_ids(scheduler)

    def test_added_job_has_provided_name(self, scheduler):
        scheduler.add_job(lambda: None, "custom_2", name="我的任务", hour=10)
        jobs = _raw_jobs(scheduler)
        assert jobs["custom_2"].name == "我的任务"

    def test_default_name_falls_back_to_job_id(self, scheduler):
        """未传 name 时使用 job_id 作为 name"""
        scheduler.add_job(lambda: None, "custom_3", hour=10)
        jobs = _raw_jobs(scheduler)
        assert jobs["custom_3"].name == "custom_3"

    def test_interval_trigger_supported(self, scheduler):
        scheduler.add_job(
            lambda: None, "custom_interval", name="间隔任务",
            trigger="interval", seconds=60,
        )
        assert "custom_interval" in _raw_job_ids(scheduler)

    def test_invalid_trigger_does_not_crash(self, scheduler):
        """非法 trigger 应被捕获,不抛异常"""
        scheduler.add_job(
            lambda: None, "bad_trigger", name="坏触发器",
            trigger="not_a_real_trigger", invalid_kwarg=1,
        )
        # 不抛异常即通过,任务不会被添加
        assert "bad_trigger" not in _raw_job_ids(scheduler)


class TestRemoveJob:
    """remove_job 移除任务"""

    def test_remove_existing_job_returns_true(self, scheduler):
        scheduler.add_job(lambda: None, "to_remove", name="待移除", hour=10)
        assert scheduler.remove_job("to_remove") is True

    def test_remove_nonexistent_returns_false(self, scheduler):
        assert scheduler.remove_job("does_not_exist") is False

    def test_remove_default_job_returns_true(self, scheduler):
        """默认注册的任务也可移除"""
        assert scheduler.remove_job("post_daily_report") is True
        assert "post_daily_report" not in _raw_job_ids(scheduler)

    def test_remove_twice_second_returns_false(self, scheduler):
        scheduler.add_job(lambda: None, "once", name="x", hour=10)
        assert scheduler.remove_job("once") is True
        assert scheduler.remove_job("once") is False


class TestGetJobsWrapper:
    """get_jobs() 包装方法(start 后 next_run_time 才可用)"""

    def test_returns_list_of_dicts(self, started_scheduler):
        jobs = started_scheduler.get_jobs()
        assert isinstance(jobs, list)
        assert all(isinstance(j, dict) for j in jobs)

    def test_each_job_has_required_keys(self, started_scheduler):
        for j in started_scheduler.get_jobs():
            assert "id" in j
            assert "name" in j
            assert "next_run_time" in j
            assert "trigger" in j

    def test_default_jobs_count(self, started_scheduler):
        assert len(started_scheduler.get_jobs()) == 11

    def test_next_run_time_is_string_or_none(self, started_scheduler):
        for j in started_scheduler.get_jobs():
            assert j["next_run_time"] is None or isinstance(j["next_run_time"], str)

    def test_trigger_is_string(self, started_scheduler):
        for j in started_scheduler.get_jobs():
            assert isinstance(j["trigger"], str)

    def test_next_run_time_str_when_present(self, started_scheduler):
        """有 next_run_time 时应转为字符串"""
        jobs = {j["id"]: j for j in started_scheduler.get_jobs()}
        bond = jobs["intraday_bond_monitor"]
        # interval 任务 start 后必有 next_run_time
        assert bond["next_run_time"] is not None
        assert isinstance(bond["next_run_time"], str)

    def test_get_jobs_with_mock_none_next_run_time(self, scheduler):
        """通过 mock 验证 next_run_time 为 None 时返回 None(覆盖三元 False 分支)"""
        fake_job = MagicMock()
        fake_job.id = "x"
        fake_job.name = "n"
        fake_job.next_run_time = None
        fake_job.trigger = "cron"
        scheduler._scheduler.get_jobs = MagicMock(return_value=[fake_job])
        result = scheduler.get_jobs()
        assert result == [{"id": "x", "name": "n", "next_run_time": None, "trigger": "cron"}]

    def test_get_jobs_str_converts_truthy_next_run_time(self, scheduler):
        """next_run_time 真值时调用 str()"""
        ts = datetime(2026, 7, 29, 15, 0, 0)
        fake_job = MagicMock()
        fake_job.id = "y"
        fake_job.name = "nn"
        fake_job.next_run_time = ts
        fake_job.trigger = "interval"
        scheduler._scheduler.get_jobs = MagicMock(return_value=[fake_job])
        result = scheduler.get_jobs()
        assert result[0]["next_run_time"] == str(ts)


class TestStartStopLifecycle:
    """start/stop/is_running 生命周期(用 MagicMock 替换 _scheduler 避免真实线程)"""

    @pytest.fixture
    def mocked_scheduler(self) -> Scheduler:
        """构造后用 MagicMock 替换 _scheduler,允许控制 running 属性"""
        s = Scheduler()
        s._scheduler = MagicMock()
        return s

    def test_is_running_reflects_underlying_false(self, mocked_scheduler):
        mocked_scheduler._scheduler.running = False
        assert mocked_scheduler.is_running is False

    def test_is_running_reflects_underlying_true(self, mocked_scheduler):
        mocked_scheduler._scheduler.running = True
        assert mocked_scheduler.is_running is True

    def test_start_when_not_running_calls_start(self, mocked_scheduler):
        mocked_scheduler._scheduler.running = False
        mocked_scheduler.start()
        mocked_scheduler._scheduler.start.assert_called_once()

    def test_start_when_running_does_not_call_start(self, mocked_scheduler):
        mocked_scheduler._scheduler.running = True
        mocked_scheduler.start()
        mocked_scheduler._scheduler.start.assert_not_called()

    def test_stop_when_running_calls_shutdown(self, mocked_scheduler):
        mocked_scheduler._scheduler.running = True
        mocked_scheduler.stop()
        mocked_scheduler._scheduler.shutdown.assert_called_once_with(wait=False)

    def test_stop_when_not_running_does_not_call_shutdown(self, mocked_scheduler):
        mocked_scheduler._scheduler.running = False
        mocked_scheduler.stop()
        mocked_scheduler._scheduler.shutdown.assert_not_called()

    def test_idempotent_start_stop_cycle(self, mocked_scheduler):
        """start→stop→stop 第二次 stop 不再调用 shutdown"""
        mocked_scheduler._scheduler.running = False
        mocked_scheduler.start()
        mocked_scheduler._scheduler.running = True
        mocked_scheduler.stop()
        mocked_scheduler._scheduler.running = False
        mocked_scheduler.stop()
        assert mocked_scheduler._scheduler.shutdown.call_count == 1

    def test_real_start_stop_roundtrip_safe(self):
        """真实 start→stop 一次应能正常往返(覆盖真实 start/shutdown 路径)"""
        s = Scheduler()
        assert s.is_running is False
        s.start()
        assert s.is_running is True
        s.stop()
        assert s.is_running is False


class TestDefaultJobsTrigger:
    """默认任务触发器配置(验证关键时点,直接读 Job.trigger)"""

    def test_pre_market_jobs_scheduled_at_0830(self, scheduler):
        jobs = _raw_jobs(scheduler)
        pre_watchlist = jobs["pre_market_watchlist"]
        trig_str = str(pre_watchlist.trigger)
        assert "8" in trig_str
        assert "30" in trig_str

    def test_post_market_report_at_1500(self, scheduler):
        jobs = _raw_jobs(scheduler)
        post_report = jobs["post_daily_report"]
        trig_str = str(post_report.trigger)
        assert "15" in trig_str
        assert "0" in trig_str

    def test_intraday_bond_monitor_is_interval(self, scheduler):
        jobs = _raw_jobs(scheduler)
        bond_monitor = jobs["intraday_bond_monitor"]
        assert "interval" in str(bond_monitor.trigger).lower()

    def test_all_default_jobs_have_non_empty_names(self, scheduler):
        for j in scheduler._scheduler.get_jobs():
            assert j.name, f"job {j.id} 缺少 name"

    def test_all_default_jobs_have_triggers(self, scheduler):
        for j in scheduler._scheduler.get_jobs():
            assert j.trigger is not None

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

_TZ = ZoneInfo("Asia/Shanghai")

_DEFAULT_JOB_TIMEOUT = 300


def _is_trading_hours(now: datetime | None = None) -> bool:
    """判断当前是否为 A 股交易时段。

    P3-16 修复(2026-07-30):原盘中任务无交易时段守卫,
    - intraday_bond_monitor 用 IntervalTrigger(30s) 全天 24h 运行,非交易时段浪费资源
    - intraday_limit_up_monitor/capital_flow 虽用 CronTrigger(hour="9-11,13-15")
      但未排除周末,周六日仍会触发

    交易时段:周一至周五 9:25-11:30, 13:00-15:05(含集合竞价与收盘前)
    """
    if now is None:
        now = datetime.now(_TZ)
    # 周末不运行(周一=0, 周日=6)
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    # 上午 9:25-11:30 或 下午 13:00-15:05
    return (925 <= t <= 1130) or (1300 <= t <= 1505)


class Scheduler:
    def __init__(self, job_timeout: int = _DEFAULT_JOB_TIMEOUT):
        self._scheduler = BackgroundScheduler(timezone=_TZ, job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 60,
        })
        self._job_timeout = job_timeout
        self._callbacks: dict[str, Callable[..., None]] = {}
        self._register_default_jobs()

    def _safe_run(self, job_name: str, func: Callable[..., None]) -> None:
        try:
            logger.info(f"调度任务开始: {job_name}")
            func()
            logger.info(f"调度任务完成: {job_name}")
        except Exception as e:
            logger.error(f"调度任务异常: {job_name} - {e}")

    def _safe_run_intraday(self, job_name: str, func: Callable[..., None]) -> None:
        """盘中任务专用 wrapper:非交易时段直接跳过,避免无效执行。

        P3-16 修复(2026-07-30):intraday_bond_monitor(30s 间隔)全天运行,
        intraday_limit_up_monitor/capital_flow 未排除周末。
        用此 wrapper 包裹后,非交易时段直接 return,节省资源。
        """
        if not _is_trading_hours():
            return
        self._safe_run(job_name, func)

    def _register_default_jobs(self) -> None:
        self._callbacks = {
            "pre_market_watchlist": self._noop,
            "pre_market_limit_up_scan": self._noop,
            "intraday_bond_monitor": self._noop,
            "intraday_limit_up_monitor": self._noop,
            "intraday_capital_flow": self._noop,
            "late_gem_new_high_scan": self._noop,
            "late_limit_up_predict": self._noop,
            "post_daily_report": self._noop,
            "post_position_update": self._noop,
            "post_trade_analysis": self._noop,
            "post_watchlist_update": self._noop,
        }

        self._scheduler.add_job(
            lambda: self._safe_run("盘前-更新自选", self._callbacks["pre_market_watchlist"]),
            CronTrigger(hour=8, minute=30, timezone=_TZ),
            id="pre_market_watchlist",
            name="盘前-更新自选",
        )
        self._scheduler.add_job(
            lambda: self._safe_run("盘前-涨停预警", self._callbacks["pre_market_limit_up_scan"]),
            CronTrigger(hour=8, minute=30, timezone=_TZ),
            id="pre_market_limit_up_scan",
            name="盘前-涨停预警",
        )

        self._scheduler.add_job(
            lambda: self._safe_run_intraday("盘中-可转债监控", self._callbacks["intraday_bond_monitor"]),
            IntervalTrigger(seconds=30, timezone=_TZ),
            id="intraday_bond_monitor",
            name="盘中-可转债监控(30秒)",
            start_date="2024-01-01 09:30:00",
        )
        self._scheduler.add_job(
            lambda: self._safe_run_intraday("盘中-涨停监控", self._callbacks["intraday_limit_up_monitor"]),
            CronTrigger(hour="9-11,13-15", minute="*/1", timezone=_TZ),
            id="intraday_limit_up_monitor",
            name="盘中-涨停监控",
        )
        self._scheduler.add_job(
            lambda: self._safe_run_intraday("盘中-资金流向", self._callbacks["intraday_capital_flow"]),
            CronTrigger(hour="9-11,13-15", minute="*/5", timezone=_TZ),
            id="intraday_capital_flow",
            name="盘中-资金流向(5分钟)",
        )

        self._scheduler.add_job(
            lambda: self._safe_run("尾盘-创业板新高扫描", self._callbacks["late_gem_new_high_scan"]),
            CronTrigger(hour=14, minute=50, timezone=_TZ),
            id="late_gem_new_high_scan",
            name="尾盘-创业板新高扫描",
        )
        self._scheduler.add_job(
            lambda: self._safe_run("尾盘-涨停预测", self._callbacks["late_limit_up_predict"]),
            CronTrigger(hour=14, minute=50, timezone=_TZ),
            id="late_limit_up_predict",
            name="尾盘-涨停预测",
        )

        self._scheduler.add_job(
            lambda: self._safe_run("盘后-日报", self._callbacks["post_daily_report"]),
            CronTrigger(hour=15, minute=0, timezone=_TZ),
            id="post_daily_report",
            name="盘后-日报",
        )
        self._scheduler.add_job(
            lambda: self._safe_run("盘后-持仓更新", self._callbacks["post_position_update"]),
            CronTrigger(hour=15, minute=5, timezone=_TZ),
            id="post_position_update",
            name="盘后-持仓更新",
        )
        self._scheduler.add_job(
            lambda: self._safe_run("盘后-交易分析", self._callbacks["post_trade_analysis"]),
            CronTrigger(hour=15, minute=10, timezone=_TZ),
            id="post_trade_analysis",
            name="盘后-交易分析",
        )
        self._scheduler.add_job(
            lambda: self._safe_run("盘后-观察池更新", self._callbacks["post_watchlist_update"]),
            CronTrigger(hour=15, minute=15, timezone=_TZ),
            id="post_watchlist_update",
            name="盘后-观察池更新",
        )

    @staticmethod
    def _noop() -> None:
        pass

    def set_callback(self, job_name: str, callback: Callable[..., None]) -> None:
        if job_name in self._callbacks:
            self._callbacks[job_name] = callback
        else:
            logger.warning(f"未知任务名: {job_name}")

    def add_job(
        self,
        func: Callable[..., None],
        job_id: str,
        name: str = "",
        trigger: str = "cron",
        **trigger_kwargs: Any,
    ) -> None:
        try:
            self._scheduler.add_job(
                lambda: self._safe_run(name or job_id, func),
                trigger=trigger,
                id=job_id,
                name=name or job_id,
                **trigger_kwargs,
            )
            logger.info(f"添加调度任务: {name or job_id} ({job_id})")
        except Exception as e:
            logger.error(f"添加调度任务失败: {name or job_id} - {e}")

    def remove_job(self, job_id: str) -> bool:
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"移除调度任务: {job_id}")
            return True
        except Exception as e:
            logger.warning(f"移除调度任务失败: {job_id} - {e}")
            return False

    def get_jobs(self) -> list[dict[str, Any]]:
        jobs = self._scheduler.get_jobs()
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in jobs
        ]

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("调度器已启动")
        else:
            logger.warning("调度器已在运行中")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("调度器已停止")
        else:
            logger.warning("调度器未在运行")

    @property
    def is_running(self) -> bool:
        return self._scheduler.running

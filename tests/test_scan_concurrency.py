"""E-01 回归:扫描端点的并发规模与半开熔断(2026-11-17)。

背景(线上实测):
- akshare 的东财接口不设 socket 超时,`scan_limit_up` 曾超过 60s 未返回;
- 旧实现 `asyncio.wait_for(asyncio.to_thread(fn), timeout=15)` 只取消 await,
  线程继续跑到 TCP 放弃;默认 executor 线程被打满后,**全站** `asyncio.to_thread`
  一起排队 —— 加超时反而制造雪崩。

本文件锁定三条契约:
1. 扫描跑在专用 2 线程池(`_scan_pool`),峰值并发 ≤ 2,不污染默认 executor;
2. 超时后 60s 内直接 503(detail.cooldown=True),且**不再起新的上游任务**;
3. 冷却到期后自动放行,正常路径仍返回 200。
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from web.api import app
from web.routes import strategy

client = TestClient(app)


def _reset_cooldown() -> None:
    strategy._scan_last_timeout = 0.0


@pytest.fixture(autouse=True)
def _fresh_scan_pool(monkeypatch):
    """每个用例用独立的 2 线程池。

    超时用例里的慢任务在 wait_for 之后仍会跑完(wait_for 不 kill 线程);
    若共享模块级池, 上一个用例的残留线程会占住 worker, 让下一个用例的
    任务先排队再超时 —— 表现为"上游一次都没被调用"的随机失败。
    这里换新池只为隔离这个副作用, 池容量仍用被测常量。
    """
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(
        max_workers=strategy._SCAN_POOL_SIZE, thread_name_prefix="scan"
    )
    monkeypatch.setattr(strategy, "_scan_pool", pool)
    yield
    pool.shutdown(wait=False)


class TestRunScanMechanism:
    @pytest.mark.asyncio
    async def test_scan_runs_on_dedicated_pool_thread(self):
        """扫描必须在 scan 前缀的专用线程里跑,不能占用默认 executor。"""
        _reset_cooldown()
        names: list[str] = []

        def fn():
            names.append(threading.current_thread().name)
            return ["ok"]

        result = await strategy._run_scan("测试扫描", fn)

        assert result == ["ok"]
        assert names and names[0].startswith("scan"), f"未走专用池: {names}"

    @pytest.mark.asyncio
    async def test_peak_concurrency_is_capped_at_pool_size(self):
        """4 个并发扫描(不同单飞键)同时在飞,实际并行度不得超过池容量 2。"""
        _reset_cooldown()
        lock = threading.Lock()
        active = 0
        peak = 0

        def fn():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with lock:
                active -= 1
            return []

        with patch.object(strategy, "_SCAN_TIMEOUT_S", 5.0):
            results = await asyncio.gather(
                *[
                    strategy._run_scan("并发扫描", fn, key=f"k{i}")
                    for i in range(4)
                ]
            )

        assert results == [[], [], [], []]
        assert peak <= strategy._SCAN_POOL_SIZE, f"并发峰值 {peak} 超过池容量"
        assert peak >= 1

    @pytest.mark.asyncio
    async def test_single_flight_rejects_second_same_key_request(self):
        """同参数请求在飞时,第二个请求必须被快速拒绝(503 busy), 不再起上游抓取。"""
        _reset_cooldown()
        started = threading.Event()
        calls: list[int] = []

        def slow():
            calls.append(1)
            started.set()
            time.sleep(0.3)
            return ["first"]

        with patch.object(strategy, "_SCAN_TIMEOUT_S", 5.0):
            first = asyncio.create_task(strategy._run_scan("涨停扫描", slow))
            await asyncio.get_running_loop().run_in_executor(None, started.wait)

            with pytest.raises(HTTPException) as busy:
                await strategy._run_scan("涨停扫描", slow)

            assert busy.value.status_code == 503
            assert busy.value.detail["code"] == "SCAN_IN_PROGRESS"
            assert busy.value.detail["busy"] is True

            assert await first == ["first"]

        # 只起了一次上游抓取
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_single_flight_releases_after_completion(self):
        """第一个请求结束后单飞键必须释放,否则该端点永久 busy。"""
        _reset_cooldown()

        def quick():
            return ["one"]

        with patch.object(strategy, "_SCAN_TIMEOUT_S", 5.0):
            assert await strategy._run_scan("可转债扫描", quick) == ["one"]
            assert await strategy._run_scan("可转债扫描", quick) == ["one"]

    @pytest.mark.asyncio
    async def test_timeout_opens_cooldown_and_skips_next_upstream_call(self):
        """超时 → 开冷却;冷却期内的第二个请求直接 503, 且不再调用上游函数。"""
        _reset_cooldown()
        calls: list[int] = []

        def slow():
            calls.append(1)
            time.sleep(0.3)
            return []

        with patch.object(strategy, "_SCAN_TIMEOUT_S", 0.05):
            with pytest.raises(HTTPException) as first:
                await strategy._run_scan("涨停扫描", slow)
            assert first.value.status_code == 503
            assert first.value.detail["code"] == "SCAN_DATA_UNAVAILABLE"

            assert strategy._scan_cooldown_remaining() > 0

            with pytest.raises(HTTPException) as second:
                await strategy._run_scan("涨停扫描", slow)

        assert second.value.status_code == 503
        assert second.value.detail["cooldown"] is True
        assert second.value.detail["code"] == "SCAN_DATA_UNAVAILABLE"
        assert second.value.detail["retry_after_s"] > 0
        assert len(calls) == 1, "冷却期内仍然起了新的上游任务"

    @pytest.mark.asyncio
    async def test_cooldown_expiry_allows_retry(self):
        """半开:冷却到期后必须重新放行一次,否则该端点永远恢复不了。"""
        _reset_cooldown()
        strategy._scan_last_timeout = time.monotonic() - (strategy._SCAN_COOLDOWN_S + 1)

        assert strategy._scan_cooldown_remaining() == 0.0
        assert await strategy._run_scan("涨停扫描", lambda: ["back"]) == ["back"]


class TestScanEndpointsCooldown:
    """三个扫描端点都要走同一套超时/冷却语义(HTTP 层)。"""

    def test_limit_up_timeout_then_cooldown(self, monkeypatch):
        _reset_cooldown()
        calls: list[int] = []

        class SlowAnalyzer:
            def scan_limit_up(self):
                calls.append(1)
                time.sleep(0.3)
                return []

        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", True)
        monkeypatch.setattr(strategy, "LimitUpAnalyzer", SlowAnalyzer)
        monkeypatch.setattr(strategy, "_SCAN_TIMEOUT_S", 0.05)

        r1 = client.get("/api/strategy/scan/limit_up")
        assert r1.status_code == 503
        assert r1.json()["detail"]["code"] == "SCAN_DATA_UNAVAILABLE"

        r2 = client.get("/api/strategy/scan/limit_up")
        assert r2.status_code == 503
        assert r2.json()["detail"]["cooldown"] is True
        assert calls == [1], "冷却期内仍调用了上游"

    def test_cb_timeout_then_cooldown(self, monkeypatch):
        _reset_cooldown()
        calls: list[int] = []

        class SlowSniper:
            def scan_cb_opportunities(self):
                calls.append(1)
                time.sleep(0.3)
                return []

        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", True)
        monkeypatch.setattr(strategy, "CBT0Sniper", SlowSniper)
        monkeypatch.setattr(strategy, "_SCAN_TIMEOUT_S", 0.05)

        r1 = client.get("/api/strategy/scan/cb")
        assert r1.status_code == 503
        r2 = client.get("/api/strategy/scan/cb")
        assert r2.status_code == 503
        assert r2.json()["detail"]["cooldown"] is True
        assert calls == [1]

    def test_new_high_timeout_then_cooldown(self, monkeypatch):
        """新高扫描走 akshare 路径时同样受池与冷却约束。"""
        _reset_cooldown()
        import akshare as ak

        calls: list[int] = []

        def slow_spot():
            calls.append(1)
            time.sleep(0.3)
            return None

        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", True)
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", slow_spot)
        monkeypatch.setattr(strategy, "_SCAN_TIMEOUT_S", 0.05)

        r1 = client.get("/api/strategy/scan/new_high")
        assert r1.status_code == 503
        r2 = client.get("/api/strategy/scan/new_high")
        assert r2.status_code == 503
        assert r2.json()["detail"]["cooldown"] is True
        assert calls == [1]

    def test_normal_path_still_returns_200(self, monkeypatch):
        """冷却未开启时,正常路径必须照旧 200(不能把熔断做成常闭)。"""
        _reset_cooldown()

        class EmptyAnalyzer:
            def scan_limit_up(self):
                return []

        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", True)
        monkeypatch.setattr(strategy, "LimitUpAnalyzer", EmptyAnalyzer)

        resp = client.get("/api/strategy/scan/limit_up")
        assert resp.status_code == 200
        assert resp.json() == []


class TestScanRateLimit:
    """A-05(2026-11-17):扫描是匿名面核心功能,不加鉴权,但必须有成本边界。"""

    def test_scan_endpoint_rate_limited_after_six(self, monkeypatch):
        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", False)
        _reset_cooldown()

        codes = [client.get("/api/strategy/scan/limit_up").status_code for _ in range(7)]

        assert codes[:6] == [200] * 6
        assert codes[6] == 429, "第 7 次必须被限流, 否则可无限消耗上游"

    def test_scan_endpoints_are_not_admin_only(self, monkeypatch):
        """回归保护:扫描是匿名可用功能, 不能因为限流顺手挂上 require_admin。"""
        monkeypatch.setattr(strategy, "_HAS_STRATEGIES", False)
        _reset_cooldown()

        assert client.get("/api/strategy/scan/new_high").status_code == 200
        assert client.get("/api/strategy/scan/cb").status_code == 200

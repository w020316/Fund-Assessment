"""请求监控指标单元测试

验证 web/middleware/metrics.py 的 RequestMetrics:
- record(): 累计请求计数/延迟/状态码分布
- snapshot(): 返回监控快照(uptime/avg_latency/error_rate/top_slow)
- reset(): 清空指标
- 线程安全:并发写入不丢失数据
- 5xx 计入 error_count,4xx 不计入

借鉴 prometheus/client_python(4.2K Star)指标设计
"""
from __future__ import annotations

import threading
import time

import pytest

from web.middleware.metrics import RequestMetrics


class TestRecord:
    """record 方法"""

    def test_record_increments_request_count(self):
        """每次记录应使 request_count +1"""
        m = RequestMetrics()
        m.record("/api/test", 0.1, 200)
        assert m.snapshot()["request_count"] == 1
        m.record("/api/test", 0.2, 200)
        assert m.snapshot()["request_count"] == 2

    def test_record_5xx_increments_error_count(self):
        """5xx 状态码应计入 error_count"""
        m = RequestMetrics()
        m.record("/api/test", 0.1, 500)
        m.record("/api/test", 0.1, 503)
        snap = m.snapshot()
        assert snap["error_count"] == 2

    def test_record_4xx_not_error(self):
        """4xx 状态码不应计入 error_count"""
        m = RequestMetrics()
        m.record("/api/test", 0.1, 404)
        m.record("/api/test", 0.1, 401)
        snap = m.snapshot()
        assert snap["error_count"] == 0
        assert snap["request_count"] == 2

    def test_record_accumulates_latency_by_path(self):
        """相同路径的延迟应累计"""
        m = RequestMetrics()
        m.record("/api/a", 0.1, 200)
        m.record("/api/a", 0.3, 200)
        m.record("/api/b", 0.5, 200)
        snap = m.snapshot()
        # top_slow_paths 按累计延迟排序
        top = {item["path"]: item["total_latency_ms"] for item in snap["top_slow_paths"]}
        assert top["/api/a"] == 400.0  # 0.1 + 0.3 = 0.4s = 400ms
        assert top["/api/b"] == 500.0
        # /api/b 累计延迟更高,应排第一
        assert snap["top_slow_paths"][0]["path"] == "/api/b"

    def test_status_distribution(self):
        """状态码分布应正确统计"""
        m = RequestMetrics()
        m.record("/api/a", 0.1, 200)
        m.record("/api/a", 0.1, 200)
        m.record("/api/a", 0.1, 404)
        m.record("/api/a", 0.1, 500)
        snap = m.snapshot()
        dist = snap["status_distribution"]
        assert dist["200"] == 2
        assert dist["404"] == 1
        assert dist["500"] == 1


class TestSnapshot:
    """snapshot 方法"""

    def test_snapshot_empty_state(self):
        """无请求时快照应返回零值"""
        m = RequestMetrics()
        snap = m.snapshot()
        assert snap["request_count"] == 0
        assert snap["error_count"] == 0
        assert snap["error_rate"] == 0.0
        assert snap["avg_latency_ms"] == 0.0
        assert snap["top_slow_paths"] == []
        assert snap["status_distribution"] == {}

    def test_snapshot_avg_latency(self):
        """平均延迟应正确计算(毫秒)"""
        m = RequestMetrics()
        m.record("/api/test", 0.1, 200)  # 100ms
        m.record("/api/test", 0.3, 200)  # 300ms
        snap = m.snapshot()
        # (0.1 + 0.3) / 2 = 0.2s = 200ms
        assert snap["avg_latency_ms"] == 200.0

    def test_snapshot_error_rate(self):
        """错误率应保留 4 位小数"""
        m = RequestMetrics()
        # 10 次请求,1 次 500
        for _ in range(9):
            m.record("/api/test", 0.05, 200)
        m.record("/api/test", 0.05, 500)
        snap = m.snapshot()
        assert snap["error_rate"] == 0.1  # 1/10 = 0.1

    def test_snapshot_uptime_positive(self):
        """uptime_seconds 应为非负数(round 到整数,极短时间可能为 0)"""
        m = RequestMetrics()
        time.sleep(0.6)  # >0.5s 保证 round 后 >= 1
        snap = m.snapshot()
        assert snap["uptime_seconds"] >= 1

    def test_snapshot_top_slow_paths_limit_5(self):
        """top_slow_paths 最多返回 5 个"""
        m = RequestMetrics()
        for i in range(10):
            m.record(f"/api/path{i}", 0.01 * (i + 1), 200)
        snap = m.snapshot()
        assert len(snap["top_slow_paths"]) == 5
        # 应按累计延迟降序
        latencies = [item["total_latency_ms"] for item in snap["top_slow_paths"]]
        assert latencies == sorted(latencies, reverse=True)

    def test_snapshot_returns_request_count_field(self):
        """快照应包含 request_count 字段"""
        m = RequestMetrics()
        m.record("/api/test", 0.1, 200)
        snap = m.snapshot()
        assert "request_count" in snap
        assert snap["request_count"] == 1


class TestReset:
    """reset 方法"""

    def test_reset_clears_all_metrics(self):
        """reset 应清空所有指标"""
        m = RequestMetrics()
        m.record("/api/a", 0.1, 200)
        m.record("/api/a", 0.1, 500)
        assert m.snapshot()["request_count"] == 2

        m.reset()
        snap = m.snapshot()
        assert snap["request_count"] == 0
        assert snap["error_count"] == 0
        assert snap["avg_latency_ms"] == 0.0
        assert snap["top_slow_paths"] == []
        assert snap["status_distribution"] == {}


class TestThreadSafety:
    """线程安全"""

    def test_concurrent_record_no_loss(self):
        """并发写入不应丢失数据(线程安全)"""
        m = RequestMetrics()
        n_threads = 10
        n_per_thread = 100

        def worker():
            for _ in range(n_per_thread):
                m.record("/api/concurrent", 0.001, 200)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = m.snapshot()
        assert snap["request_count"] == n_threads * n_per_thread


class TestHealthEndpointIntegration:
    """/api/health 端点应返回 metrics 字段"""

    def test_health_returns_metrics(self, client):
        """/api/health 响应应包含 metrics 子对象"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        metrics_data = data["metrics"]
        assert "uptime_seconds" in metrics_data
        assert "request_count" in metrics_data
        assert "error_count" in metrics_data
        assert "error_rate" in metrics_data
        assert "avg_latency_ms" in metrics_data
        assert "top_slow_paths" in metrics_data
        assert "status_distribution" in metrics_data

    def test_health_metrics_records_this_request(self, client):
        """调用 /api/health 后,metrics.request_count 应递增"""
        # 先记录当前 request_count(全局单例可能已被其他测试累积)
        resp1 = client.get("/api/health")
        before = resp1.json()["metrics"]["request_count"]
        # 再调用 3 次,验证 request_count 递增
        for _ in range(3):
            client.get("/api/health")
        resp2 = client.get("/api/health")
        after = resp2.json()["metrics"]["request_count"]
        # request_count 应至少增加 4(3 次 + 最后一次)
        assert after >= before + 4
        # status_distribution 中 200 计数应增加
        assert resp2.json()["metrics"]["status_distribution"].get("200", 0) >= before

    def test_health_metrics_5xx_tracked(self, client):
        """5xx 响应应被记入 error_count"""
        # 触发一个不存在的路由会产生 404,不会增加 error_count
        # 这里通过直接调用 metrics 验证 5xx 逻辑
        from web.middleware.metrics import metrics as global_metrics
        before = global_metrics.snapshot()["error_count"]
        global_metrics.record("/api/test-5xx", 0.01, 500)
        after = global_metrics.snapshot()["error_count"]
        assert after == before + 1


@pytest.fixture
def client():
    """测试客户端(复用全局 app)"""
    from fastapi.testclient import TestClient
    from web.api import app
    return TestClient(app)

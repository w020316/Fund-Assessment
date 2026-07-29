"""轻量级请求监控指标(借鉴 prometheus/client_python 4.2K Star 指标设计)

设计理念:
- 借鉴 prometheus_client 的 Counter/Histogram/Gauge 指标类型设计
- 但用纯 Python 实现,无 prometheus_client 依赖(避免多进程环境配置复杂)
- 在中间件中自动埋点,无需手动修改每个路由
- /api/health 端点扩展返回监控快照

指标说明:
- uptime_seconds: 应用启动至现在时长(秒)
- request_count: 总请求数
- error_count: 5xx 错误请求数
- error_rate: 错误率(0-1)
- avg_latency_ms: 平均延迟(毫秒)
- latency_by_path: 各路径累计延迟(用于识别慢接口)
- count_by_status: 各状态码计数
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from loguru import logger


class RequestMetrics:
    """轻量级线程安全请求指标统计(借鉴 prometheus_client 设计)

    线程安全:用 threading.Lock 保护共享状态(FastAPI 在异步线程池中运行)
    内存开销:O(1) 聚合值 + O(n) 路径维度(n 为不同路径数,通常 <50)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time: float = time.time()
        self._request_count: int = 0
        self._error_count: int = 0
        self._total_latency: float = 0.0
        self._latency_by_path: dict[str, float] = defaultdict(float)
        self._count_by_path: dict[str, int] = defaultdict(int)
        self._count_by_status: dict[int, int] = defaultdict(int)

    def record(self, path: str, latency: float, status_code: int) -> None:
        """记录一次请求(在中间件中调用)

        Args:
            path: 请求路径(已归一化,如 /api/market/index_realtime)
            latency: 延迟(秒)
            status_code: HTTP 状态码
        """
        with self._lock:
            self._request_count += 1
            self._total_latency += latency
            if status_code >= 500:
                self._error_count += 1
            self._latency_by_path[path] += latency
            self._count_by_path[path] += 1
            self._count_by_status[status_code] += 1

    def snapshot(self) -> dict[str, Any]:
        """获取当前监控快照(供 /api/health 返回)

        Returns:
            dict 含 uptime/request_count/error_count/error_rate/avg_latency_ms
            及 top_slow_paths(累计延迟最高的5个路径)
        """
        with self._lock:
            uptime = time.time() - self._start_time
            avg_latency = (self._total_latency / self._request_count) if self._request_count else 0.0
            # 慢接口 Top5(按累计延迟排序)
            top_slow = sorted(
                self._latency_by_path.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5]
            # 状态码分布
            status_dist = {str(k): v for k, v in sorted(self._count_by_status.items())}
            return {
                "uptime_seconds": round(uptime, 0),
                "request_count": self._request_count,
                "error_count": self._error_count,
                "error_rate": round(self._error_count / self._request_count, 4) if self._request_count else 0.0,
                "avg_latency_ms": round(avg_latency * 1000, 2),
                "top_slow_paths": [
                    {"path": p, "count": self._count_by_path[p], "total_latency_ms": round(lat * 1000, 2)}
                    for p, lat in top_slow
                ],
                "status_distribution": status_dist,
            }

    def reset(self) -> None:
        """重置指标(测试用)"""
        with self._lock:
            self._start_time = time.time()
            self._request_count = 0
            self._error_count = 0
            self._total_latency = 0.0
            self._latency_by_path.clear()
            self._count_by_path.clear()
            self._count_by_status.clear()


# 全局单例(模块级导入即用)
metrics = RequestMetrics()

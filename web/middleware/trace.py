"""请求追踪 trace ID 中间件(借鉴 open-telemetry/opentelemetry-python 2.0K Star 追踪设计)

设计理念:
- 借鉴 OpenTelemetry 的 context 传播思路,但用纯 Python contextvars 实现
- 每个请求生成唯一 trace_id(16 位 hex),贯穿整个请求生命周期
- 支持客户端透传:请求头 X-Trace-Id 优先复用,便于跨服务链路关联
- 响应头返回 X-Trace-Id,便于前端/日志关联
- loguru 通过 patcher 自动注入 trace_id 到每条日志

使用方式:
    # web/api.py
    from web.middleware.trace import trace_middleware, get_trace_id
    app.middleware("http")(trace_middleware)

    # 日志配置(logger.py)
    from web.middleware.trace import trace_id_var
    logger.patch(lambda r: r["extra"].update(trace_id=trace_id_var.get("")))
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

# 请求级 trace_id 上下文(ContextVar 天然支持 asyncio 任务隔离)
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def generate_trace_id() -> str:
    """生成 16 位 hex trace_id(uuid4 前 16 位,碰撞概率极低)"""
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str:
    """获取当前请求的 trace_id(供业务代码/日志使用)"""
    return trace_id_var.get()


async def trace_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """请求追踪中间件

    - 优先复用客户端 X-Trace-Id(支持跨服务链路)
    - 否则生成 16 位 hex trace_id
    - 注入 ContextVar(异步任务隔离)
    - 响应头返回 X-Trace-Id 便于前端关联
    """
    # 客户端透传优先,否则生成新 trace_id
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id()
    trace_id_var.set(trace_id)

    response = await call_next(request)
    # 响应头返回 trace_id,便于前端/日志关联排查
    response.headers["X-Trace-Id"] = trace_id
    return response

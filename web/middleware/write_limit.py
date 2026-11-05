"""写端点限流中间件(10 次/分钟)。

阶段四修复:rate_limiter.py 长期只在注释里声明"写操作 10/min",实际没有任何
装饰器或中间件生效,连续 12 次写请求全部 200。这里用 slowapi 同一个存储统一
拦截所有 POST/PUT/PATCH/DELETE(只读语义的少量 POST 豁免),保证声明即生效。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.requests import Request

from web.rate_limiter import (
    WRITE_LIMIT,
    WRITE_LIMIT_EXEMPT_PATHS,
    WRITE_LIMIT_EXEMPT_PREFIXES,
    WRITE_METHODS,
    limiter,
    write_limit_key,
)


async def write_rate_limit_middleware(request: Request, call_next):
    if request.method not in WRITE_METHODS:
        return await call_next(request)
    path = request.url.path
    if path in WRITE_LIMIT_EXEMPT_PATHS:
        return await call_next(request)
    if path.startswith(WRITE_LIMIT_EXEMPT_PREFIXES):
        return await call_next(request)
    try:
        # 必须走 slowapi 内部的 limits 策略(FixedWindowRateLimiter.hit),它返回
        # "本次是否仍在上限内"的布尔值;直接调 _storage.incr() 拿到的是计数值,
        # 恒为真值 —— 之前 12 次写请求全 200 就是这个原因。
        allowed = limiter.limiter.hit(WRITE_LIMIT, write_limit_key(request), "write")
    except Exception:
        # 存储异常时放行,避免限流组件故障阻断全部写操作(可用性优先)
        allowed = True
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "WRITE_RATE_LIMITED",
                    "message": "写操作过于频繁(每分钟最多 10 次),请稍后重试。",
                    "retryable": True,
                    "limit": "10/minute",
                }
            },
            headers={"Retry-After": "60"},
        )
    return await call_next(request)

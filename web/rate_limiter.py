"""限流器单例模块(独立于 web/api.py 避免循环 import)

设计目的:
- 路由模块(holdings.py/agent.py)需要在函数定义时应用 @limiter.limit 装饰器
- 但 limiter 原本在 web/api.py 中创建,路由模块 import web.api 会触发循环 import
- 抽到独立模块后,路由模块和 web/api.py 都从此处导入同一实例

借鉴 la-deps/slowapi(1.0K Star)FastAPI 限流中间件的设计
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局限流器:按客户端 IP 限流,保护 Render Free 实例(512MB RAM)
# - LLM 高成本端点(/api/agent/*):3次/分钟(在路由上单独装饰)
# - 写操作端点(POST/PUT/DELETE):10次/分钟(在路由上单独装饰)
# - 读操作端点(GET):60次/分钟(default_limits 兜底)
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

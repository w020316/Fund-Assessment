"""限流器单例模块(独立于 web/api.py 避免循环 import)

设计目的:
- 路由模块(holdings.py/agent.py)需要在函数定义时应用 @limiter.limit 装饰器
- 但 limiter 原本在 web/api.py 中创建,路由模块 import web.api 会触发循环 import
- 抽到独立模块后,路由模块和 web/api.py 都从此处导入同一实例

借鉴 la-deps/slowapi(1.0K Star)FastAPI 限流中间件的设计
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os

from limits import parse
from slowapi import Limiter
from starlette.requests import Request


def _is_untrusted_peer(host: str) -> bool:
    """判断对端地址是否属于"中间层/本机/非 IP"，即不足以代表真实客户端。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 非 IP 字面量（如 TestClient 的 "testclient"），一律视为不可直接采信
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def client_ip_key(request: Request) -> str:
    """限流键：真实客户端 IP。

    P1 修复(2026-10-26):原实现直接用 slowapi 的 get_remote_address，
    它只读 `request.client.host`、**不读 X-Forwarded-For**。
    Render 的反代不在回环地址上，而 uvicorn 的 forwarded_allow_ips 默认仅信任
    127.0.0.1，于是 XFF 被忽略、client.host 恒为代理地址 —— 结果是**全站共享一个
    限流桶**：任何人不小心刷 60 次，所有人一起 429；LLM 端点配的 3/minute
    也变成"全站 3 次/分钟"。

    取值顺序：
    1) 若 client.host 已是公网地址（uvicorn 按信任链解析过 XFF），直接用；
    2) 否则从 XFF **右侧**向左找第一个公网地址 —— 右侧由最近的代理写入，
       最可信；从左侧取会被客户端自带的伪造头部绕过限流；
    3) 都没有则回落到对端地址（至少保证"同源同桶"，不会完全失效）。
    """
    peer = ""
    if request.client and request.client.host:
        peer = request.client.host

    if peer and not _is_untrusted_peer(peer):
        return peer

    xff = request.headers.get("x-forwarded-for") or ""
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_untrusted_peer(hop):
            return hop

    return peer or "unknown"


# 全局限流器:按真实客户端 IP 限流,保护 Render Free 实例(512MB RAM)
# - LLM 高成本端点(/api/agent/*):3次/分钟(在路由上单独装饰)
# - 写操作端点(POST/PUT/DELETE):10次/分钟(由 web/middleware/write_limit.py 统一拦截)
# - 读操作端点(GET):60次/分钟(default_limits 兜底)
limiter = Limiter(key_func=client_ip_key, default_limits=["60/minute"])


# ===== 阶段四:写端点限流(10 次/分钟)=====
# 原实现只在注释里声明"写操作 10/min"，没有任何装饰器/中间件真正生效，
# 实测连续 12 次写请求全部 200。这里用同一个 slowapi 存储统一计数。
WRITE_LIMIT = parse("10/minute")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 只读语义的 POST(校验/问答/检索)不计入写配额,避免误伤正常编辑流程
WRITE_LIMIT_EXEMPT_PATHS = frozenset({
    "/api/fund/positions/validate",
    "/api/review/three_question",
    "/api/news/search",
    "/api/skills/dispatch",
})
# /api/agent/* 各端点自身已挂 @limiter.limit("3/minute")(LLM 高成本),
# 若再叠加写配额会出现"LLM 调用吃掉改持仓的 10 次额度"的串味,
# 这里按前缀豁免,让两类限流各自独立计数。
WRITE_LIMIT_EXEMPT_PREFIXES = ("/api/agent/",)


def write_limit_key(request: Request) -> str:
    """写端点限流键:IP 为主,已认证管理员再附加身份后缀。

    - 匿名请求:直接按 client_ip_key(右侧可信 XFF 或对端地址)限流,
      客户端伪造 X-Forwarded-For 左侧内容不会产生新桶。
    - 已认证请求:仅当 X-Admin-Token 与服务端 ADMIN_TOKEN 完全一致时才附加
      用户维度后缀;伪造/错误 token 仍落回 IP 桶,无法靠换头绕过。
    """
    base = client_ip_key(request)
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    expected = _admin_token()
    if expected and token and hmac.compare_digest(token, expected):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]
        base = f"{base}:u{digest}"
    return f"write:{base}"


def _admin_token() -> str:
    """读取服务端 ADMIN_TOKEN(env 优先,与 src.utils.auth 一致)。"""
    try:
        from src.utils.auth import _get_admin_token
        return _get_admin_token()
    except Exception:
        return os.environ.get("ADMIN_TOKEN", "").strip()

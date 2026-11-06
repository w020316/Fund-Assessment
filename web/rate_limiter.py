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
import threading
import time
from collections import deque

from fastapi.responses import JSONResponse
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
    1) 若请求带 X-Forwarded-For，从**右侧**向左找第一个公网地址 —— 右侧由最近的
       代理写入，最可信；客户端在左侧自带的伪造跳不会改变键；
    2) 无 XFF 时才回落到 client.host（直连/本地开发场景）；
    3) 都没有则 "unknown"（至少保证同源同桶）。

    阶段五 O2(2026-11-05 线上实测)：uvicorn 配了 FORWARDED_ALLOW_IPS=* 后会把
    client.host 改写成 XFF 的**最左**一跳。线上连续 12 次写请求全部 401(未触发 429)，
    而固定 XFF 时能稳定 10 次后 429 —— 说明"信任 client.host"等于信任最左跳，
    键会随代理写入顺序漂移且可被客户端伪造。改为**始终优先解析 XFF 右端**。
    """
    xff = request.headers.get("x-forwarded-for") or ""
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_untrusted_peer(hop):
            return hop

    peer = ""
    if request.client and request.client.host:
        peer = request.client.host

    if peer and not _is_untrusted_peer(peer):
        return peer

    return peer or "unknown"


def _admin_token() -> str:
    """读取服务端 ADMIN_TOKEN(env 优先,与 src.utils.auth 一致)。"""
    try:
        from src.utils.auth import _get_admin_token
        return _get_admin_token()
    except Exception:
        return os.environ.get("ADMIN_TOKEN", "").strip()


def rate_limit_key(request: Request) -> str:
    """限流键:认证用户按用户,匿名按全局。

    阶段五 O2(2026-11-05 线上实测):
    Render Free 的边缘代理下,同一客户端连续 12 次写请求的 `client.host` 与
    XFF 右端都在漂移 —— 按 IP 分桶时**从不出现 429**(限流形同虚设);
    而手工固定 XFF 时能稳定 10 次后 429。说明该链路拿不到稳定的客户端 IP,
    继续按 IP 分桶既拦不住刷量,又能被伪造头绕开。

    因此:
    - 与服务端 ADMIN_TOKEN 完全一致的请求 → 独立用户桶(多用户互不影响);
    - 其余匿名请求 → 单一全局桶(宁可全局收紧,也不做"看似有、实际无效"的限流)。
    单管理员场景下,匿名请求本就被鉴权拦截,全局桶不会影响正常使用。
    """
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    expected = _admin_token()
    if expected and token and hmac.compare_digest(token, expected):
        return "u" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]
    return "anon"


# ===== 匿名读请求公平限流(阶段二 R-06,2026-11-06)=====
# 背景:匿名请求统一落在 "anon" 桶,单个匿名访客刷 60 次即可让所有匿名用户 429。
# 方案:在 slowapi 全局限流之外增加一层"指纹分桶 + 全局安全阀":
#   - 每个指纹(IP + UA + Accept-Language)每分钟 60 次,避免一人拖垮所有人;
#   - 全站匿名合计每分钟 300 次,防止伪造指纹无限换桶;
#   - 写端点仍走 write_limit_key 的 "write:anon"(既有测试与语义不变)。
_ANON_READ_RPM = max(int(os.getenv("ANON_READ_RPM", "60")), 1)
_ANON_GLOBAL_RPM = max(int(os.getenv("ANON_GLOBAL_RPM", "300")), 1)
_ANON_WINDOW_SECONDS = 60.0


def is_authenticated_request(request: Request) -> bool:
    """请求是否携带与服务端一致的 ADMIN_TOKEN。"""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    expected = _admin_token()
    return bool(expected and token and hmac.compare_digest(token, expected))


def anon_fingerprint(request: Request) -> str:
    """匿名请求指纹:IP + UA + Accept-Language 的不可逆哈希。

    说明:伪造 UA/XFF 仍可换桶,但会被全站匿名安全阀兜住;
    指纹只用于"公平分桶",不作为安全边界。
    """
    ip = client_ip_key(request)
    ua = request.headers.get("user-agent", "")[:160]
    lang = request.headers.get("accept-language", "")[:80]
    digest = hashlib.sha256(f"{ip}|{ua}|{lang}".encode("utf-8")).hexdigest()
    return digest[:16]


class _SlidingWindow:
    """滑动窗口计数器(单进程内存,线程安全)。"""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = int(limit)
        self.window_seconds = float(window_seconds)
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            while self._events and current - self._events[0] >= self.window_seconds:
                self._events.popleft()
            if len(self._events) >= self.limit:
                return False
            self._events.append(current)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


class AnonymousReadGuard:
    """匿名读请求守卫:每指纹限流 + 全站匿名上限。"""

    def __init__(
        self,
        per_key_limit: int = 60,
        global_limit: int = 300,
        window_seconds: float = 60.0,
        max_keys: int = 4096,
    ):
        self.per_key_limit = int(per_key_limit)
        self.global_limit = int(global_limit)
        self.window_seconds = float(window_seconds)
        self.max_keys = int(max_keys)
        self._keys: dict[str, _SlidingWindow] = {}
        self._keys_lock = threading.Lock()
        self._global = _SlidingWindow(global_limit, window_seconds)

    def check(self, key: str, now: float | None = None) -> bool:
        with self._keys_lock:
            counter = self._keys.get(key)
            if counter is None:
                # 防止伪造 IP/UA 把 key 表撑大:超过上限按插入顺序淘汰最旧
                if len(self._keys) >= self.max_keys:
                    self._keys.pop(next(iter(self._keys)), None)
                counter = _SlidingWindow(self.per_key_limit, self.window_seconds)
                self._keys[key] = counter
        if not counter.allow(now):
            return False
        return self._global.allow(now)

    def reset(self) -> None:
        with self._keys_lock:
            self._keys.clear()
        self._global.reset()


_anon_read_guard = AnonymousReadGuard(
    per_key_limit=_ANON_READ_RPM,
    global_limit=_ANON_GLOBAL_RPM,
    window_seconds=_ANON_WINDOW_SECONDS,
)


async def anonymous_read_guard_middleware(request: Request, call_next):
    """匿名 GET/HEAD(/api/*) 先过公平限流;已认证请求不受影响。"""
    if (
        request.method in ("GET", "HEAD")
        and request.url.path.startswith("/api/")
        and not is_authenticated_request(request)
    ):
        if not _anon_read_guard.check(anon_fingerprint(request)):
            return JSONResponse(
                status_code=429,
                content={
                    "error": f"Rate limit exceeded: {_ANON_READ_RPM} per 1 minute",
                    "detail": {
                        "code": "RATE_LIMITED",
                        "message": "请求过于频繁,请稍后重试。",
                        "retryable": True,
                        "limit": f"{_ANON_READ_RPM}/minute",
                    },
                },
                headers={"Retry-After": "60"},
            )
    return await call_next(request)


# 全局限流器:匿名侧仍用 "anon" 全局桶(写端点测试与既有语义不变),
# 读请求的公平分桶由上面的 anonymous_read_guard_middleware 负责。
limiter = Limiter(key_func=rate_limit_key, default_limits=[f"{_ANON_GLOBAL_RPM}/minute"])


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
    """写端点限流键:与 rate_limit_key 同一策略(认证用户独立桶,匿名全局桶)。

    阶段五 O2 起不再按 IP 分桶:线上实测 Render 边缘代理会让同一客户端的
    client.host / XFF 每请求漂移,按 IP 分桶时 429 从不触发(详见 rate_limit_key)。
    """
    return "write:" + rate_limit_key(request)

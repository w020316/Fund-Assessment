"""请求追踪 trace ID 与安全响应头测试

验证 web/middleware/trace.py 的 trace_middleware:
- 自动生成 16 位 trace_id
- 客户端透传 X-Trace-Id
- 响应头返回 X-Trace-Id
- get_trace_id() 在请求生命周期内可获取
- 安全响应头(借鉴 TypeError/secure)6 个头均设置
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


class TestTraceMiddleware:
    """trace ID 中间件"""

    def test_response_has_trace_id_header(self, client):
        """响应应包含 X-Trace-Id 头"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert "x-trace-id" in resp.headers
        assert len(resp.headers["x-trace-id"]) == 16

    def test_trace_id_is_hex(self, client):
        """trace_id 应为 16 位 hex 字符"""
        resp = client.get("/api/health")
        trace_id = resp.headers["x-trace-id"]
        assert re.fullmatch(r"[0-9a-f]{16}", trace_id), f"trace_id 非法: {trace_id}"

    def test_different_requests_different_trace_id(self, client):
        """不同请求应生成不同 trace_id"""
        r1 = client.get("/api/health")
        r2 = client.get("/api/health")
        assert r1.headers["x-trace-id"] != r2.headers["x-trace-id"]

    def test_client_trace_id_passthrough(self, client):
        """客户端透传 X-Trace-Id 应被复用"""
        custom_id = "abc123def4567890"
        resp = client.get("/api/health", headers={"X-Trace-Id": custom_id})
        assert resp.headers["x-trace-id"] == custom_id

    def test_trace_id_consistent_in_response(self, client):
        """同一请求的响应头 trace_id 应稳定"""
        custom_id = "ffffffffffffffff"
        resp = client.get("/api/health", headers={"X-Trace-Id": custom_id})
        assert resp.headers["x-trace-id"] == custom_id


class TestSecurityHeaders:
    """安全响应头(借鉴 TypeError/secure)"""

    def test_hsts_header(self, client):
        """Strict-Transport-Security 应设置(HSTS 防 SSL 降级)"""
        resp = client.get("/api/health")
        assert "strict-transport-security" in resp.headers
        assert "max-age=31536000" in resp.headers["strict-transport-security"]

    def test_x_frame_options_denied(self, client):
        """X-Frame-Options 应为 DENY(防点击劫持)"""
        resp = client.get("/api/health")
        assert resp.headers["x-frame-options"] == "DENY"

    def test_x_content_type_options_nosniff(self, client):
        """X-Content-Type-Options 应为 nosniff(防 MIME 嗅探)"""
        resp = client.get("/api/health")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_referrer_policy(self, client):
        """Referrer-Policy 应设置"""
        resp = client.get("/api/health")
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_content_security_policy(self, client):
        """Content-Security-Policy 应设置(XSS 防护)"""
        resp = client.get("/api/health")
        csp = resp.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src" in csp

    def test_permissions_policy(self, client):
        """Permissions-Policy 应禁用 geolocation/microphone"""
        resp = client.get("/api/health")
        pp = resp.headers["permissions-policy"]
        assert "geolocation=()" in pp
        assert "microphone=()" in pp


class TestGZipCompression:
    """GZip 响应压缩(借鉴 encode/starlette GZipMiddleware)"""

    def test_gzip_middleware_registered(self):
        """GZipMiddleware 应已在 app 中注册"""
        from web.api import app
        from fastapi.middleware.gzip import GZipMiddleware
        # 检查 user_middleware 列表中存在 GZipMiddleware
        middleware_classes = [m.cls for m in app.user_middleware]
        assert GZipMiddleware in middleware_classes

    def test_small_response_not_gzipped(self, client):
        """小于 1KB 的响应不应压缩(避免 CPU 浪费)"""
        # 根路径返回 307 重定向,响应体很小
        resp = client.get("/", headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
        # 重定向响应体极小,不应压缩
        assert resp.status_code in (307, 302)
        assert resp.headers.get("content-encoding") != "gzip"


class TestRateLimitKey:
    """限流键取值（2026-10-26 P1 修复回归）

    缺陷：原 key_func 用 slowapi 的 get_remote_address，只读 request.client.host、
    不读 X-Forwarded-For。Render 反代不在回环地址上且 forwarded_allow_ips 默认仅信任
    127.0.0.1 → XFF 被忽略 → 全站共享一个限流桶（60/min 变成全站额度）。

    本类锁定取值规则：公网对端直用；否则从 XFF **右侧**（最近一跳）取，
    防止客户端伪造左侧内容绕过限流。
    """

    @staticmethod
    def _req(peer: str | None, xff: str | None = None):
        from starlette.requests import Request
        headers = []
        if xff is not None:
            headers.append((b"x-forwarded-for", xff.encode()))
        scope = {
            "type": "http", "method": "GET", "path": "/", "headers": headers,
            "query_string": b"", "scheme": "http", "server": ("testserver", 80),
        }
        if peer is not None:
            scope["client"] = (peer, 54321)
        return Request(scope)

    def test_public_peer_used_directly(self):
        from web.rate_limiter import client_ip_key
        assert client_ip_key(self._req("8.8.8.8")) == "8.8.8.8"

    def test_private_peer_falls_back_to_xff(self):
        from web.rate_limiter import client_ip_key
        assert client_ip_key(self._req("10.0.0.5", "8.8.4.4")) == "8.8.4.4"

    def test_rightmost_hop_wins_over_spoofed_left(self):
        """客户端伪造左侧内容不得生效：最近一跳（右侧）才是真实来源"""
        from web.rate_limiter import client_ip_key
        # 攻击者自带 "2.2.2.2"，真实 proxy 追加了真实 IP "8.8.4.4"
        assert client_ip_key(self._req("10.0.0.5", "2.2.2.2, 8.8.4.4")) == "8.8.4.4"

    def test_all_private_hops_fall_back_to_peer(self):
        from web.rate_limiter import client_ip_key
        assert client_ip_key(self._req("10.0.0.5", "10.0.0.1, 192.168.1.1")) == "10.0.0.5"

    def test_non_ip_peer_without_xff(self):
        """TestClient 等非 IP 对端不得抛异常"""
        from web.rate_limiter import client_ip_key
        assert client_ip_key(self._req("testclient")) == "testclient"

    def test_missing_client_returns_unknown(self):
        from web.rate_limiter import client_ip_key
        assert client_ip_key(self._req(None)) == "unknown"

"""R-06 回归:匿名读请求公平分桶 + 全站匿名安全阀。"""
from __future__ import annotations

from starlette.requests import Request

from web.rate_limiter import (
    AnonymousReadGuard,
    anon_fingerprint,
    is_authenticated_request,
)


def _req(peer="8.8.8.8", ua="Mozilla/5.0", lang="zh-CN", authorization=None, xff=None):
    headers = [(b"user-agent", ua.encode()), (b"accept-language", lang.encode())]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {"type": "http", "method": "GET", "path": "/api/market/index_realtime", "headers": headers}
    if peer is not None:
        scope["client"] = (peer, 54321)
    return Request(scope)


class TestAnonymousReadGuard:
    def test_per_key_limit(self):
        guard = AnonymousReadGuard(per_key_limit=3, global_limit=5, window_seconds=60)
        assert [guard.check("a", now=100.0) for _ in range(3)] == [True, True, True]
        assert guard.check("a", now=100.0) is False

    def test_global_limit_protects_against_key_rotation(self):
        guard = AnonymousReadGuard(per_key_limit=3, global_limit=5, window_seconds=60)
        for _ in range(3):
            assert guard.check("a", now=100.0) is True
        assert guard.check("b", now=100.0) is True
        assert guard.check("b", now=100.0) is True
        assert guard.check("b", now=100.0) is False  # 全站 5 次已满

    def test_window_slides(self):
        guard = AnonymousReadGuard(per_key_limit=2, global_limit=2, window_seconds=60)
        assert guard.check("a", now=100.0) is True
        assert guard.check("a", now=100.0) is True
        assert guard.check("a", now=100.0) is False
        assert guard.check("a", now=161.0) is True


class TestFingerprint:
    def test_ua_changes_bucket(self):
        assert anon_fingerprint(_req(ua="A")) != anon_fingerprint(_req(ua="B"))

    def test_ip_changes_bucket(self):
        assert anon_fingerprint(_req(peer="8.8.8.8")) != anon_fingerprint(_req(peer="9.9.9.9"))


class TestAuthentication:
    def test_valid_token_is_authenticated(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        assert is_authenticated_request(_req(authorization="Bearer secret-token")) is True

    def test_forged_token_is_not_authenticated(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        assert is_authenticated_request(_req(authorization="Bearer forged")) is False

    def test_no_token_configured_is_not_authenticated(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        assert is_authenticated_request(_req(authorization="Bearer anything")) is False

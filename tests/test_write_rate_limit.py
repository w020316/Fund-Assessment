"""阶段四第 5 项回归:写端点 10 次/分钟、LLM 3 次/分钟必须真实生效。

修复前 `web/rate_limiter.py` 只在注释里声明"写操作 10/min",没有任何装饰器或
中间件真正计数,实测连续 12 次写请求全部 200。这里用 TestClient 固化行为:
- 第 11 次起 429,且 429 响应带结构化 detail(code/message/retryable/limit)
- 伪造 Bearer 无法换到新桶(仍落在同一 IP 桶)
- 已认证管理员(与服务端 ADMIN_TOKEN 一致的 token)有独立桶
- /api/agent/* 自身 3 次/分钟,不再叠加写配额(避免 LLM 调用吃掉改持仓额度)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from web.api import app

client = TestClient(app)

ADD_BODY = {
    "fund_code": "110022",
    "fund_name": "限流测试",
    "shares": 1000.0,
    "cost_nav": 1.0,
    "buy_date": "2026-01-01",
    "note": "rate-limit",
}


@pytest.fixture
def isolated_positions(tmp_path, monkeypatch):
    """把持仓落盘路径改到 tmp_path,绝不触碰真实持仓文件。"""
    from web.routes import fund as fund_route
    target = tmp_path / "positions.json"
    monkeypatch.setattr(fund_route, "_FUND_POSITIONS_FILE", target)
    return target


def _post(headers=None):
    return client.post("/api/fund/positions/add", json=ADD_BODY, headers=headers or {})


def _req(peer="testclient", xff=None, authorization=None):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/fund/positions/add",
        "headers": headers,
    }
    if peer is not None:
        scope["client"] = (peer, 54321)
    return Request(scope)


class TestWriteRateLimit:
    def test_blocks_after_ten_in_one_window(self, isolated_positions):
        seq = [_post().status_code for _ in range(12)]
        assert seq[:10] == [200] * 10, f"前 10 次应放行,实际 {seq}"
        assert seq[10:] == [429, 429], f"第 11、12 次应 429,实际 {seq}"

    def test_429_body_and_headers(self, isolated_positions):
        for _ in range(10):
            _post()
        resp = _post()
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["code"] == "WRITE_RATE_LIMITED"
        assert detail["retryable"] is True
        assert detail["limit"] == "10/minute"
        assert detail["message"], "429 必须带可读原因"
        assert resp.headers.get("Retry-After") == "60"

    def test_forged_token_does_not_open_new_bucket(self, isolated_positions):
        """伪造 Bearer 与匿名请求落在同一个 IP 桶,无法靠换头绕过。"""
        seq = [_post({"Authorization": "Bearer forged-not-admin"}).status_code for _ in range(12)]
        assert seq == [200] * 10 + [429, 429], f"伪造令牌不应获得新额度,实际 {seq}"

    def test_forged_left_hop_keeps_same_key(self, isolated_positions):
        """客户端在 XFF 左侧插入伪造 IP,不得产生新桶(右侧可信跳决定键)。"""
        from web.rate_limiter import client_ip_key
        clean = client_ip_key(_req(peer="10.0.0.5", xff="8.8.4.4"))
        spoof = client_ip_key(_req(peer="10.0.0.5", xff="9.9.9.9, 8.8.4.4"))
        assert clean == "8.8.4.4"
        assert spoof == "8.8.4.4", "伪造的左侧跳不应改变限流键"

    def test_authenticated_admin_has_own_bucket(self, isolated_positions, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret-for-test")
        # 未认证请求先打满 IP 桶(401 也会计数,防止匿名刷量)
        for _ in range(10):
            assert _post().status_code == 401
        assert _post().status_code == 429
        # 携带正确 token:独立用户桶,仍有额度
        auth = {"Authorization": "Bearer admin-secret-for-test"}
        assert _post(auth).status_code == 200
        assert _post(auth).status_code == 200


class TestWriteLimitKey:
    def test_valid_token_adds_user_suffix(self, monkeypatch):
        from web import rate_limiter as rl
        monkeypatch.setenv("ADMIN_TOKEN", "real-secret")
        anon = rl.write_limit_key(_req(peer="8.8.8.8"))
        forged = rl.write_limit_key(_req(peer="8.8.8.8", authorization="Bearer nope"))
        real = rl.write_limit_key(_req(peer="8.8.8.8", authorization="Bearer real-secret"))
        assert anon == forged, "伪造令牌不得改变限流键"
        assert real != anon and real.startswith("write:8.8.8.8:u")

    def test_no_token_configured_all_share_ip_key(self, monkeypatch):
        from web import rate_limiter as rl
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        assert rl.write_limit_key(_req(peer="8.8.8.8")).startswith("write:8.8.8.8")


class TestAgentPathExemptFromWriteQuota:
    def test_agent_posts_do_not_consume_write_quota(self, isolated_positions):
        # 用缺失必填字段的请求快速 422(不触发 LLM 计算),连续 12 次
        seq = [client.post("/api/agent/analyze", json={}).status_code for _ in range(12)]
        assert seq == [422] * 12, f"LLM 端点不应被写配额拦截,实际 {seq}"
        # 写配额仍是完整的 10 次:证明 /api/agent/* 未占用写桶
        write_seq = [_post().status_code for _ in range(12)]
        assert write_seq == [200] * 10 + [429, 429], f"写配额被 LLM 调用侵占,实际 {write_seq}"

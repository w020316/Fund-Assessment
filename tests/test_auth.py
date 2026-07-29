"""鉴权模块单元测试

验证 src/utils/auth.py 的 require_admin 依赖:
- 未配置 ADMIN_TOKEN 时放行(开发模式)
- 生产环境(APP_ENV != "dev")未配置 ADMIN_TOKEN → fail-closed 抛 500
- 已配置时,缺少 Authorization / 令牌不匹配 / 空令牌 均返回 401
- Bearer 与裸 token 两种格式均支持
- secrets.compare_digest 防时序攻击(等长令牌)
- _is_production() 环境判定
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.utils.auth import _is_production, generate_token, require_admin


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(_: None = Depends(require_admin)):
        return {"ok": True}

    return app


class TestRequireAdmin:
    """require_admin 依赖测试"""

    def test_no_token_configured_allows_access(self):
        """ADMIN_TOKEN 未配置时应放行(开发模式)"""
        with patch.dict(os.environ, {"APP_ENV": "dev"}, clear=False):
            os.environ.pop("ADMIN_TOKEN", None)
            client = TestClient(_build_app())
            resp = client.get("/protected")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_production_no_token_returns_500_fail_closed(self):
        """生产环境(APP_ENV != dev)未配置 ADMIN_TOKEN → fail-closed 抛 500

        P1 安全修复(2026-07-29):避免生产裸奔,未配置即拒绝服务。
        """
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            os.environ.pop("ADMIN_TOKEN", None)
            client = TestClient(_build_app())
            resp = client.get("/protected")
            assert resp.status_code == 500
            assert "ADMIN_TOKEN" in resp.json()["detail"]

    def test_production_with_valid_token_passes(self):
        """生产环境配置 ADMIN_TOKEN 后正常鉴权"""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "ADMIN_TOKEN": "prod-secret-token",
        }):
            client = TestClient(_build_app())
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer prod-secret-token"},
            )
            assert resp.status_code == 200

    def test_production_with_invalid_token_returns_401(self):
        """生产环境令牌不匹配仍返回 401(fail-closed 仅针对未配置场景)"""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "ADMIN_TOKEN": "prod-secret-token",
        }):
            client = TestClient(_build_app())
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 401

    def test_is_production_env_variants(self):
        """_is_production 应正确判定各种 APP_ENV 取值"""
        # 默认值视为生产环境(fail-closed 默认安全)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APP_ENV", None)
            assert _is_production() is True
        # 显式 production
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            assert _is_production() is True
        # 大小写不敏感
        with patch.dict(os.environ, {"APP_ENV": "PRODUCTION"}):
            assert _is_production() is True
        with patch.dict(os.environ, {"APP_ENV": "Production"}):
            assert _is_production() is True
        # dev 模式放行
        with patch.dict(os.environ, {"APP_ENV": "dev"}):
            assert _is_production() is False
        with patch.dict(os.environ, {"APP_ENV": "DEV"}):
            assert _is_production() is False
        # 其他值(如 staging)视为生产
        with patch.dict(os.environ, {"APP_ENV": "staging"}):
            assert _is_production() is True

    def test_missing_authorization_header_returns_401(self):
        """已配置 ADMIN_TOKEN 但请求未带 Authorization 应返回 401"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token-xyz"}):
            client = TestClient(_build_app())
            resp = client.get("/protected")
            assert resp.status_code == 401
            assert "缺少 Authorization" in resp.json()["detail"]

    def test_invalid_token_returns_401(self):
        """令牌不匹配应返回 401"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token-xyz"}):
            client = TestClient(_build_app())
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"] == "令牌无效"

    def test_valid_bearer_token_passes(self):
        """Bearer <token> 格式令牌正确时应放行"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token-xyz"}):
            client = TestClient(_build_app())
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer secret-token-xyz"},
            )
            assert resp.status_code == 200

    def test_valid_bare_token_passes(self):
        """裸 token(无 Bearer 前缀)格式也应支持"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token-xyz"}):
            client = TestClient(_build_app())
            resp = client.get(
                "/protected",
                headers={"Authorization": "secret-token-xyz"},
            )
            assert resp.status_code == 200

    def test_empty_token_returns_401(self):
        """Authorization 头为空应返回 401"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token-xyz"}):
            client = TestClient(_build_app())
            resp = client.get("/protected", headers={"Authorization": ""})
            assert resp.status_code == 401

    def test_generate_token_returns_urlsafe_string(self):
        """generate_token 应返回非空 urlsafe 字符串"""
        token = generate_token()
        assert isinstance(token, str)
        assert len(token) >= 32
        # 两次调用应产生不同令牌
        assert generate_token() != token

    def test_timing_safe_comparison_equal_length(self):
        """等长但不同的令牌不应通过(防时序攻击)"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "abcdefgh12345678"}):
            client = TestClient(_build_app())
            # 等长但内容不同
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer abcdefgh00000000"},
            )
            assert resp.status_code == 401

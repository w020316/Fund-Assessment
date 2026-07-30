"""系统配置路由单元测试

验证 web/routes/config.py 的端点:
- GET /api/config/settings         读取配置(敏感字段脱敏)
- PUT /api/config/settings         更新配置(需鉴权)
- GET /api/config/strategies       读取策略
- PUT /api/config/strategies       更新策略(需鉴权)
- POST /api/config/test_notify     测试通知发送
- GET /api/config/user_positions   读取用户持仓
- POST /api/config/user_positions  保存用户持仓(需鉴权)

通过 mock 文件 IO(_load_yaml/_save_yaml)与通知模块,避免真实文件副作用与网络请求。
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, mock_open, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    """确保 ADMIN_TOKEN 未配置(开发模式放行),避免鉴权干扰"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


# 模拟配置数据(含敏感字段)
_MOCK_SETTINGS = {
    "app": {"name": "QuantFlow", "version": "1.0.0"},
    "tushare": {"token": "abcdefgh12345678"},
    "broker": {"api_key": "sk-1234567890abcdef", "api_secret": "secret12345"},
}

_MOCK_STRATEGIES = {
    "ma_cross": {"fast": 5, "slow": 20, "enabled": True},
    "breakout": {"period": 20, "enabled": False},
}


class TestGetSettings:
    """GET /api/config/settings"""

    def test_get_settings_masks_sensitive(self, client):
        """敏感字段(api_key/token)被脱敏"""
        with patch("web.routes.config._load_yaml", return_value=_MOCK_SETTINGS):
            resp = client.get("/api/config/settings")
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        # 非敏感字段保持原值
        assert settings["app"]["name"] == "QuantFlow"
        # 敏感字段被脱敏(包含 ****)
        assert "****" in settings["tushare"]["token"]
        assert "****" in settings["broker"]["api_key"]
        assert "****" in settings["broker"]["api_secret"]
        # 脱敏后不应包含完整原始值
        assert "abcdefgh12345678" not in settings["tushare"]["token"]

    def test_get_settings_empty_returns_empty(self, client):
        """配置文件为空时返回空字典"""
        with patch("web.routes.config._load_yaml", return_value={}):
            resp = client.get("/api/config/settings")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {}


class TestUpdateSettings:
    """PUT /api/config/settings"""

    def test_update_settings_merges_and_masks(self, client):
        """更新配置: 合并后返回脱敏结果"""
        existing = {"app": {"name": "QuantFlow"}, "broker": {"api_key": "sk-1234567890abcdef"}}
        with patch("web.routes.config._load_yaml", return_value=existing) as mock_load, \
             patch("web.routes.config._save_yaml") as mock_save:
            resp = client.put("/api/config/settings", json={
                "settings": {"app": {"name": "NewApp"}}
            })
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        # 合并: app.name 更新为新值
        assert settings["app"]["name"] == "NewApp"
        # 合并: broker.api_key 保留(来自 existing)
        assert "****" in settings["broker"]["api_key"]
        # _save_yaml 被调用
        mock_save.assert_called_once()
        # 保存的是合并后的 existing(已 deep_merge)
        saved = mock_save.call_args[0][1]
        assert saved["app"]["name"] == "NewApp"


class TestStrategies:
    """GET/PUT /api/config/strategies"""

    def test_get_strategies_returns_data(self, client):
        """读取策略配置"""
        with patch("web.routes.config._load_yaml", return_value=_MOCK_STRATEGIES):
            resp = client.get("/api/config/strategies")
        assert resp.status_code == 200
        strategies = resp.json()["strategies"]
        assert "ma_cross" in strategies
        assert strategies["ma_cross"]["fast"] == 5
        assert strategies["breakout"]["enabled"] is False

    def test_update_strategies_saves(self, client):
        """更新策略配置并保存"""
        existing = {"ma_cross": {"fast": 5, "slow": 20, "enabled": True}}
        with patch("web.routes.config._load_yaml", return_value=existing), \
             patch("web.routes.config._save_yaml") as mock_save:
            resp = client.put("/api/config/strategies", json={
                "strategies": {"ma_cross": {"fast": 10}}
            })
        assert resp.status_code == 200
        strategies = resp.json()["strategies"]
        # deep_merge: fast 更新为 10,slow 与 enabled 保留
        assert strategies["ma_cross"]["fast"] == 10
        assert strategies["ma_cross"]["slow"] == 20
        mock_save.assert_called_once()


class TestTestNotify:
    """POST /api/config/test_notify"""

    def test_notify_no_channels_returns_failure(self, client):
        """未启用任何通知渠道时返回 success=False"""
        mock_cfg = MagicMock()
        mock_cfg.get = MagicMock(return_value=False)
        with patch("src.utils.config.get_config", return_value=mock_cfg):
            resp = client.post("/api/config/test_notify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "未启用" in data["message"]

    def test_notify_dingtalk_enabled_returns_success(self, client):
        """启用钉钉通知时返回 success=True"""
        mock_cfg = MagicMock()

        def cfg_get(key, default=None):
            values = {
                "notify.dingtalk.enabled": True,
                "notify.dingtalk.webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
                "notify.dingtalk.secret": "SECxxx",
            }
            return values.get(key, default)

        mock_cfg.get = cfg_get
        mock_notifier = MagicMock()
        with patch("src.utils.config.get_config", return_value=mock_cfg), \
             patch("src.utils.notify.DingTalkNotifier", return_value=mock_notifier):
            resp = client.post("/api/config/test_notify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "钉钉" in data["message"]
        # notifier.send 应被调用
        mock_notifier.send.assert_called_once()


class TestUserPositions:
    """GET/POST /api/config/user_positions"""

    def test_get_user_positions_returns_default(self, client):
        """持仓文件不存在时返回默认结构"""
        with patch("os.path.exists", return_value=False):
            resp = client.get("/api/config/user_positions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["positions"] == []
        assert data["available_cash"] == 800000.0

    def test_save_user_positions_returns_success(self, client):
        """保存持仓返回成功(拦截文件写入)

        P0 修复(2026-07-30):后端改用 atomic_write_json 原子写入,
        内部会调用 open/os.path.exists/os.replace,需全部 patch 才能完整拦截文件 IO。
        """
        with patch("builtins.open", mock_open()), \
             patch("os.path.exists", return_value=False), \
             patch("os.replace"):
            resp = client.post("/api/config/user_positions", json={
                "positions": [{"symbol": "600519", "name": "贵州茅台", "quantity": 100}],
                "available_cash": 500000.0,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "持仓已保存" in data["message"]

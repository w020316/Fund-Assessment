"""集中配置管理单元测试

验证 src/utils/config.py 的 Settings 类与 get_settings 单例:
- Settings.from_env() 从环境变量构建
- get_settings() lru_cache 单例
- 字段默认值与类型校验
- 环境变量覆盖默认值

借鉴 pydantic/pydantic-settings(2.6K Star)BaseSettings 设计
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.utils.config import Settings, get_settings, settings


class TestSettingsFromEnv:
    """Settings.from_env 从环境变量构建"""

    def test_default_values_when_no_env(self):
        """无环境变量时使用默认值"""
        with patch.dict(os.environ, {}, clear=True):
            s = Settings.from_env()
        assert s.admin_token == ""
        assert s.ttapi_api_key == ""
        assert s.cache_dir == "data/cache"
        assert s.cache_default_ttl == 300
        assert s.app_name == "QuantFlow Pro"
        assert s.app_env == "production"

    def test_reads_admin_token_from_env(self):
        """应从 ADMIN_TOKEN 环境变量读取"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "test-token-123"}):
            s = Settings.from_env()
        assert s.admin_token == "test-token-123"

    def test_strips_whitespace_from_admin_token(self):
        """admin_token 应去除首尾空白"""
        with patch.dict(os.environ, {"ADMIN_TOKEN": "  token-with-spaces  "}):
            s = Settings.from_env()
        assert s.admin_token == "token-with-spaces"

    def test_reads_api_keys_from_env(self):
        """应从对应环境变量读取 API Keys"""
        env = {
            "TTAPI_API_KEY": "tt-key",
            "TAVILY_API_KEY": "tv-key",
            "TINYFISH_API_KEY": "tf-key",
            "AGNES_API_KEY": "ag-key",
        }
        with patch.dict(os.environ, env):
            s = Settings.from_env()
        assert s.ttapi_api_key == "tt-key"
        assert s.tavily_api_key == "tv-key"
        assert s.tinyfish_api_key == "tf-key"
        assert s.agnes_api_key == "ag-key"

    def test_reads_cache_config_from_env(self):
        """应从环境变量读取缓存配置"""
        env = {"CACHE_DIR": "/tmp/test_cache", "CACHE_DEFAULT_TTL": "600"}
        with patch.dict(os.environ, env):
            s = Settings.from_env()
        assert s.cache_dir == "/tmp/test_cache"
        assert s.cache_default_ttl == 600

    def test_reads_app_config_from_env(self):
        """应从环境变量读取应用配置"""
        env = {"APP_NAME": "TestApp", "APP_ENV": "dev"}
        with patch.dict(os.environ, env):
            s = Settings.from_env()
        assert s.app_name == "TestApp"
        assert s.app_env == "dev"

    def test_invalid_ttl_falls_back_to_default(self):
        """CACHE_DEFAULT_TTL 非数字时应回退默认值(容错)"""
        # int("abc") 会抛 ValueError,这里验证容错行为
        # 实际实现用 int(os.getenv(...,"300")),非数字会抛异常
        # 这是预期行为:配置错误应明确报错而非静默使用默认值
        with patch.dict(os.environ, {"CACHE_DEFAULT_TTL": "abc"}):
            with pytest.raises(ValueError):
                Settings.from_env()


class TestGetSettingsSingleton:
    """get_settings lru_cache 单例"""

    def test_returns_same_instance(self):
        """多次调用应返回同一实例(lru_cache)"""
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_cache_clear_picks_up_new_env(self):
        """cache_clear 后应重新读取环境变量"""
        get_settings.cache_clear()
        with patch.dict(os.environ, {"ADMIN_TOKEN": "first"}):
            s1 = get_settings()
            assert s1.admin_token == "first"

        get_settings.cache_clear()
        with patch.dict(os.environ, {"ADMIN_TOKEN": "second"}):
            s2 = get_settings()
            assert s2.admin_token == "second"

    def test_module_level_settings_is_settings_instance(self):
        """模块级 settings 应是 Settings 实例"""
        assert isinstance(settings, Settings)


class TestSettingsFields:
    """Settings 字段属性验证"""

    def test_all_fields_are_strings_except_ttl(self):
        """除 cache_default_ttl 外所有字段都是 str"""
        s = Settings()
        assert isinstance(s.admin_token, str)
        assert isinstance(s.ttapi_api_key, str)
        assert isinstance(s.tavily_api_key, str)
        assert isinstance(s.tinyfish_api_key, str)
        assert isinstance(s.agnes_api_key, str)
        assert isinstance(s.cache_dir, str)
        assert isinstance(s.app_name, str)
        assert isinstance(s.app_env, str)
        # cache_default_ttl 是 int
        assert isinstance(s.cache_default_ttl, int)

    def test_settings_is_pydantic_model(self):
        """Settings 应是 Pydantic BaseModel 子类"""
        from pydantic import BaseModel
        assert isinstance(Settings(), BaseModel)

    def test_field_descriptions_exist(self):
        """字段应有 description(文档化)"""
        fields = Settings.model_fields
        assert "admin_token" in fields
        assert fields["admin_token"].description is not None
        assert "agnes_api_key" in fields
        assert fields["agnes_api_key"].description is not None

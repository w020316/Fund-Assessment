"""集中配置管理

本模块提供两套配置体系:

1. **Config 类(原有)**: 从 config/settings.yaml 和 config/strategies.yaml 加载 YAML 配置,
   支持 APP_ 前缀环境变量覆盖。提供 get(dotted_key, default) 方法。
   被 notify.py / __init__.py 使用。

2. **Settings 类(新增,借鉴 pydantic/pydantic-settings 2.6K Star)**: 从环境变量构建,
   用 Pydantic 模型做类型校验。提供 admin_token / api_keys / cache 等字段。
   被 auth.py / ai_service.py 使用。

设计理念(借鉴 pydantic-settings):
- 单一入口 Settings 类,所有模块从 settings 读取环境变量配置
- 用 Pydantic 模型做类型校验,避免散落的 os.getenv 调用
- 渐进式迁移:旧代码 get_config() 可继续工作,新代码优先用 settings

约束:
- 不改变任何环境变量名称(向后兼容 Render 配置)
- 不改变任何函数签名(向后兼容现有调用方)
- Settings 实例在模块加载时创建一次(单例)
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

_CONFIG_DIR = Path("config")


# ===== 原有 YAML 配置加载器(notify.py 等使用) =====

class Config:
    """YAML 配置单例(原有实现,保持不变)"""
    _instance: "Config | None" = None
    _data: dict[str, Any]

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        for filename in ("settings.yaml", "strategies.yaml"):
            filepath = _CONFIG_DIR / filename
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if content:
                        self._data[filepath.stem] = content

        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        for key, value in os.environ.items():
            if key.startswith("APP_"):
                parts = key[4:].lower().split("__")
                self._set_nested(parts, value)

    def _set_nested(self, keys: list[str], value: str) -> None:
        current = self._data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current[k] = current.get(k, {})
            current = current[k]
        current[keys[-1]] = value

    def get(self, dotted_key: str, default: Any = None) -> Any:
        keys = dotted_key.split(".")
        current: Any = self._data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def get_config() -> Config:
    """获取 YAML 配置单例(原有接口)"""
    return Config()


# ===== 新增:环境变量集中配置(借鉴 pydantic/pydantic-settings) =====

class Settings(BaseModel):
    """环境变量配置(借鉴 pydantic/pydantic-settings BaseSettings)

    所有字段自动从环境变量读取,带默认值和类型校验。
    使用方式:
        from src.utils.config import settings
        if settings.admin_token:
            ...

    注意:此处用 BaseModel + os.getenv 而非 BaseSettings,
    因为 BaseSettings 需要额外依赖 pydantic-settings,
    而 BaseModel 已是项目现有依赖,零新增依赖。
    """

    # ===== 鉴权 =====
    admin_token: str = Field(default="", description="管理员令牌,校验写操作鉴权")

    # ===== LLM Provider API Keys =====
    ttapi_api_key: str = Field(default="", description="TTAPI 平台 API Key")
    tavily_api_key: str = Field(default="", description="Tavily 检索 API Key")
    tinyfish_api_key: str = Field(default="", description="TinyFish API Key")
    agnes_api_key: str = Field(default="", description="Agnes AI API Key")

    # ===== 缓存 =====
    cache_dir: str = Field(default="data/cache", description="缓存目录")
    cache_default_ttl: int = Field(default=300, description="默认缓存TTL(秒)")

    # ===== 应用 =====
    app_name: str = Field(default="QuantFlow Pro", description="应用名称")
    app_env: str = Field(default="production", description="运行环境 dev/production")

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构建 Settings 实例"""
        return cls(
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
            ttapi_api_key=os.getenv("TTAPI_API_KEY", ""),
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            tinyfish_api_key=os.getenv("TINYFISH_API_KEY", ""),
            agnes_api_key=os.getenv("AGNES_API_KEY", ""),
            cache_dir=os.getenv("CACHE_DIR", "data/cache"),
            cache_default_ttl=int(os.getenv("CACHE_DEFAULT_TTL", "300")),
            app_name=os.getenv("APP_NAME", "QuantFlow Pro"),
            app_env=os.getenv("APP_ENV", "production"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局 Settings 单例(借鉴 FastAPI 的 get_settings 模式)

    使用 lru_cache 确保全局只有一个 Settings 实例,
    避免重复读取环境变量。测试时可用 get_settings.cache_clear() 重置。
    """
    return Settings.from_env()


# 模块级单例(向后兼容直接 import settings 的用法)
settings = get_settings()

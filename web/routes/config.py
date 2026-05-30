from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class SettingsResponse(BaseModel):
    settings: dict[str, Any]


class SettingsUpdateRequest(BaseModel):
    settings: dict[str, Any]


class StrategiesResponse(BaseModel):
    strategies: dict[str, Any]


class StrategiesUpdateRequest(BaseModel):
    strategies: dict[str, Any]


class NotifyTestResponse(BaseModel):
    success: bool
    message: str


def _load_yaml(filename: str) -> dict[str, Any]:
    filepath = _CONFIG_DIR / filename
    if not filepath.exists():
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
        return content if content else {}


def _save_yaml(filename: str, data: dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _CONFIG_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    data = _load_yaml("settings.yaml")
    return SettingsResponse(settings=data)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsUpdateRequest):
    existing = _load_yaml("settings.yaml")
    _deep_merge(existing, req.settings)
    _save_yaml("settings.yaml", existing)
    return SettingsResponse(settings=existing)


@router.get("/strategies", response_model=StrategiesResponse)
async def get_strategies():
    data = _load_yaml("strategies.yaml")
    return StrategiesResponse(strategies=data)


@router.put("/strategies", response_model=StrategiesResponse)
async def update_strategies(req: StrategiesUpdateRequest):
    existing = _load_yaml("strategies.yaml")
    _deep_merge(existing, req.strategies)
    _save_yaml("strategies.yaml", existing)
    return StrategiesResponse(strategies=existing)


@router.post("/test_notify", response_model=NotifyTestResponse)
async def test_notify():
    try:
        from src.utils.config import get_config
        cfg = get_config()
        dingtalk_enabled = cfg.get("notify.dingtalk.enabled", False)
        wechat_enabled = cfg.get("notify.wechat.enabled", False)

        if dingtalk_enabled:
            webhook = cfg.get("notify.dingtalk.webhook", "")
            secret = cfg.get("notify.dingtalk.secret", "")
            if webhook:
                from src.utils.notify import DingTalkNotifier, LogLevel
                notifier = DingTalkNotifier(webhook=webhook, secret=secret)
                notifier.send("测试通知", "这是一条来自 OpenClaw 量化交易系统的测试通知", LogLevel.INFO)
                return NotifyTestResponse(success=True, message="钉钉通知发送成功")

        if wechat_enabled:
            webhook = cfg.get("notify.wechat.webhook", "")
            if webhook:
                from src.utils.notify import WeComNotifier, LogLevel
                notifier = WeComNotifier(webhook=webhook)
                notifier.send("测试通知", "这是一条来自 OpenClaw 量化交易系统的测试通知", LogLevel.INFO)
                return NotifyTestResponse(success=True, message="企业微信通知发送成功")

        return NotifyTestResponse(success=False, message="未启用任何通知渠道，请在配置中开启钉钉或企业微信")
    except Exception as e:
        return NotifyTestResponse(success=False, message=f"通知测试失败: {str(e)}")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base

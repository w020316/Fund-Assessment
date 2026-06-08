from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

SENSITIVE_KEYS: set[str] = {
    "tushare_token",
    "broker_api_key",
    "broker_api_secret",
    "dingtalk_secret",
    "dingtalk_webhook",
    "wechat_webhook",
    "secret",
    "api_key",
    "api_secret",
    "token",
    "password",
    "webhook",
}

MASK_PATTERN = "****"


def _mask_value(val: str) -> str:
    if not isinstance(val, str) or len(val) <= 8:
        return MASK_PATTERN
    return f"{val[:4]}{MASK_PATTERN}{val[-4:]}"


def _mask_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _mask_value(v) if k in SENSITIVE_KEYS and isinstance(v, str) else _mask_sensitive(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask_sensitive(item) for item in data]
    return data


def _strip_masked(data: Any) -> Any:
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, str) and MASK_PATTERN in v:
                continue
            stripped = _strip_masked(v)
            if not (isinstance(v, dict) and not stripped):
                result[k] = stripped
            else:
                result[k] = stripped
        return result
    if isinstance(data, list):
        return [_strip_masked(item) for item in data]
    return data

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
    return SettingsResponse(settings=_mask_sensitive(data))


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsUpdateRequest):
    existing = _load_yaml("settings.yaml")
    cleaned = _strip_masked(req.settings)
    _deep_merge(existing, cleaned)
    _save_yaml("settings.yaml", existing)
    return SettingsResponse(settings=_mask_sensitive(existing))


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
                notifier.send("测试通知", "这是一条来自 QuantFlow Pro 量化交易系统的测试通知", LogLevel.INFO)
                return NotifyTestResponse(success=True, message="钉钉通知发送成功")

        if wechat_enabled:
            webhook = cfg.get("notify.wechat.webhook", "")
            if webhook:
                from src.utils.notify import WeComNotifier, LogLevel
                notifier = WeComNotifier(webhook=webhook)
                notifier.send("测试通知", "这是一条来自 QuantFlow Pro 量化交易系统的测试通知", LogLevel.INFO)
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


@router.get("/user_positions")
async def get_user_positions():
    import json, os
    pos_file = os.path.join(os.path.dirname(__file__), "..", "user_positions.json")
    if os.path.exists(pos_file):
        try:
            with open(pos_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"positions": [], "available_cash": 800000.0}


class SavePositionsRequest(BaseModel):
    positions: list[dict]
    available_cash: float = 800000.0


@router.post("/user_positions")
async def save_user_positions(req: SavePositionsRequest):
    import json, os
    pos_file = os.path.join(os.path.dirname(__file__), "..", "user_positions.json")
    try:
        with open(pos_file, "w", encoding="utf-8") as f:
            json.dump({"positions": req.positions, "available_cash": req.available_cash}, f, ensure_ascii=False, indent=2)
        return {"success": True, "message": "持仓已保存"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {e}"}

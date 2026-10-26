from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from src.utils.auth import require_admin
from src.utils.file_io import atomic_write_json, safe_read_json

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
    """敏感值一律整体掩码。

    安全修复(2026-10-26):原实现对长度 > 8 的值返回 `val[:4]****val[-4:]`，
    即**泄露首尾各 4 个字符**——对 32 位 API Key 相当于暴露 25% 的密钥空间，
    显著降低爆破难度；而该端点当时还没有鉴权。
    现改为定长掩码，不再泄露任何原文片段（前端只需要识别"已配置"这一状态，
    不需要靠片段辨认值，`_strip_masked` 也只需匹配 **** 即可判断"未修改"）。
    """
    if not isinstance(val, str) or not val:
        return MASK_PATTERN
    return MASK_PATTERN * 2


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
        # 尝试读取备份文件
        bak_path = filepath.with_suffix(filepath.suffix + ".bak")
        if bak_path.exists():
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
            return content if content else {}
    except Exception as e:
        logger.warning(f"load yaml {filename} failed, trying backup: {e}")
        bak_path = filepath.with_suffix(filepath.suffix + ".bak")
        if bak_path.exists():
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}


def _save_yaml(filename: str, data: dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _CONFIG_DIR / filename
    tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
    bak_path = filepath.with_suffix(filepath.suffix + ".bak")
    # 先写临时文件,再原子替换
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        # 备份旧文件
        if filepath.exists():
            shutil.copy2(filepath, bak_path)
        # 原子替换
        tmp_path.replace(filepath)
    except Exception as e:
        logger.warning(f"save yaml {filename} failed: {e}")
        raise


@router.get("/settings", response_model=SettingsResponse, dependencies=[Depends(require_admin)])
async def get_settings():
    """读取系统配置（敏感字段整体掩码）。

    安全修复(2026-10-26):此端点此前**无鉴权**，匿名即可枚举全部配置键名，
    并拿到 11 个密钥字段的首尾各 4 个字符。虽然值已掩码，但"有哪些密钥、
    各是哪种服务、片段是什么"本身就是有效侦察信息；前端 loadSettings()
    也早已做了令牌预检，这里补上鉴权是让实现与前端预期一致。
    """
    try:
        data = _load_yaml("settings.yaml")
        return SettingsResponse(settings=_mask_sensitive(data))
    except Exception as e:
        logger.warning(f"get_settings failed: {e}")
        return SettingsResponse(settings={})


@router.put("/settings", response_model=SettingsResponse, dependencies=[Depends(require_admin)])
async def update_settings(req: SettingsUpdateRequest):
    try:
        existing = _load_yaml("settings.yaml")
        cleaned = _strip_masked(req.settings)
        _deep_merge(existing, cleaned)
        _save_yaml("settings.yaml", existing)
        return SettingsResponse(settings=_mask_sensitive(existing))
    except Exception as e:
        logger.warning(f"update_settings failed: {e}")
        return SettingsResponse(settings={})


@router.get("/strategies", response_model=StrategiesResponse, dependencies=[Depends(require_admin)])
async def get_strategies():
    """读取策略参数配置。

    安全修复(2026-10-26):此前无鉴权。strategies.yaml 里是用户自己的策略参数
    （均线周期、突破阈值、启用开关等），与 /settings 同属私有配置，
    且与本文件其他读接口的鉴权口径不一致。
    注：/api/strategy/list（策略**目录**，硬编码常量）保持匿名——它不暴露任何用户状态。
    """
    try:
        data = _load_yaml("strategies.yaml")
        return StrategiesResponse(strategies=data)
    except Exception as e:
        logger.warning(f"get_strategies failed: {e}")
        return StrategiesResponse(strategies={})


@router.put("/strategies", response_model=StrategiesResponse, dependencies=[Depends(require_admin)])
async def update_strategies(req: StrategiesUpdateRequest):
    try:
        existing = _load_yaml("strategies.yaml")
        _deep_merge(existing, req.strategies)
        _save_yaml("strategies.yaml", existing)
        return StrategiesResponse(strategies=existing)
    except Exception as e:
        logger.warning(f"update_strategies failed: {e}")
        return StrategiesResponse(strategies={})


@router.post("/test_notify", response_model=NotifyTestResponse, dependencies=[Depends(require_admin)])
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
        # P1-1 修复(2026-07-30):异常详情可能含 webhook URL/连接细节等内部信息,
        # 不返回客户端,仅记录日志。客户端只看到通用提示。
        logger.warning(f"通知测试失败: {e}")
        return NotifyTestResponse(success=False, message="通知测试失败,请检查配置或稍后重试")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


@router.get("/sector-map", dependencies=[Depends(require_admin)])
async def get_sector_map():
    """读取基金→板块映射配置(阶段5 T01/P2-2,只读)。

    板块聚合计算在前端(sectors.js),后端仅提供映射;UI 维护端点(PUT)进 backlog。
    配置缺失/损坏时返回空 sectors(前端全部归入 unmapped_label"其他"),不抛 500。
    """
    try:
        data = _load_yaml("sector_map.yaml")
    except Exception as e:
        logger.warning(f"get_sector_map failed: {e}")
        data = {}
    sectors = data.get("sectors") if isinstance(data, dict) else None
    if not isinstance(sectors, dict):
        sectors = {}
    unmapped_label = data.get("unmapped_label") if isinstance(data, dict) else None
    return {
        "success": True,
        "sectors": sectors,
        "unmapped_label": unmapped_label if isinstance(unmapped_label, str) and unmapped_label else "其他",
    }


_USER_POSITIONS_FILE = Path(__file__).resolve().parent.parent / "user_positions.json"


@router.get("/user_positions", dependencies=[Depends(require_admin)])
async def get_user_positions():
    # P0 修复(2026-07-30):改用 safe_read_json,主文件损坏时自动回退 .bak 备份
    data = safe_read_json(_USER_POSITIONS_FILE, default={"positions": [], "available_cash": 800000.0})
    if data is None:
        return {"positions": [], "available_cash": 800000.0}
    return data


class SavePositionsRequest(BaseModel):
    """保存用户持仓。

    P2 加固(2026-10-26)：原定义是裸 `list[dict]`，既无条数上限也无元素约束——
    超长列表可把 JSON 撑大、异常结构会写进文件并在后续读取时引发 KeyError/500。
    这里补条数上限与"元素必须是对象"的约束；具体字段仍由业务层兼容处理
    （持仓字段前后端约定较松，强 schema 会打断既有客户端）。
    """

    positions: list[dict] = Field(default_factory=list, max_length=500,
                                 description="持仓列表，最多 500 条")
    available_cash: float = Field(800000.0, ge=0, le=1e12)


@router.post("/user_positions", dependencies=[Depends(require_admin)])
async def save_user_positions(req: SavePositionsRequest):
    # P0 修复(2026-07-30):改用 atomic_write_json
    # 1. 写 .tmp + fsync → os.replace 原子替换,防止崩溃时数据损坏
    # 2. 自动备份到 .bak,损坏时可恢复
    # 3. 路径粒度 threading.Lock 保护并发写
    try:
        atomic_write_json(
            _USER_POSITIONS_FILE,
            {"positions": req.positions, "available_cash": req.available_cash},
        )
        return {"success": True, "message": "持仓已保存"}
    except Exception as e:
        # P1-1 修复(2026-07-30):异常详情可能含文件路径/权限等内部信息,不返回客户端
        logger.warning(f"保存持仓失败: {e}")
        return {"success": False, "message": "保存失败,请稍后重试"}

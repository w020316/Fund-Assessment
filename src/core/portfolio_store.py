"""data/portfolio/ 存储层(阶段六批次 A,T-A4)。

职责(架构文档 §3.1):
- data/portfolio/*.json 的读写与 schema 校验
- Webhook 推送的**文件级节流**时间戳(2h 窗口,重启不丢——应对 GitHub Actions
  30min 频率的偶发重复触发与单 worker 重启后的重推,架构文档 §6)
- 账本导入前的自动备份(批次 A 功能 #9)

铁律:
- 密钥(SendKey/Webhook URL/ScanKey)**不入库**:配置文件只存 channel/enabled/
  thresholds 等非敏感字段,凭据全部走 Render env
- 原子写入复用 file_io(写 .tmp → os.replace)
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.file_io import atomic_write_json, safe_read_json

# data/portfolio 目录(项目根/data/portfolio)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_DIR = _PROJECT_ROOT / "data" / "portfolio"
BACKUP_DIR = _PROJECT_ROOT / "data" / "backup"

WEBHOOK_CONFIG_FILE = PORTFOLIO_DIR / "webhook_config.json"
THROTTLE_FILE = PORTFOLIO_DIR / "webhook_throttle.json"

#: 默认推送节流窗口(秒):2 小时(主理人拍板,防 GitHub Actions 重复触发刷屏)
DEFAULT_THROTTLE_SECONDS: int = 2 * 3600

# WebhookConfig 默认值(不含任何密钥字段)
DEFAULT_WEBHOOK_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "channel": "serverchan",     # serverchan | generic(预留)
    "enabled": False,
    "thresholds": {
        "est_pct_abs": 2.0,      # 估算涨跌绝对值预警阈值(%)
    },
}

_VALID_CHANNELS = {"serverchan", "generic"}

_config_lock = threading.Lock()
_throttle_lock = threading.Lock()


# ===== 通用读写 =====


def load_webhook_config(path: Path | None = None) -> dict[str, Any]:
    """读取 Webhook 配置(缺失/损坏 → 默认值,含 schema 校验与字段白名单)。"""
    file = path or WEBHOOK_CONFIG_FILE
    data = safe_read_json(file, default=None)
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_WEBHOOK_CONFIG))  # 深拷贝默认值
    merged = json.loads(json.dumps(DEFAULT_WEBHOOK_CONFIG))
    channel = data.get("channel")
    if channel in _VALID_CHANNELS:
        merged["channel"] = channel
    merged["enabled"] = bool(data.get("enabled", merged["enabled"]))
    thresholds = data.get("thresholds")
    if isinstance(thresholds, dict):
        for k, v in thresholds.items():
            if k in merged["thresholds"] and isinstance(v, (int, float)) and v >= 0:
                merged["thresholds"][k] = float(v)
    return merged


def save_webhook_config(config: dict[str, Any], path: Path | None = None) -> bool:
    """保存 Webhook 配置(仅白名单字段落盘,密钥字段一律拒绝写入)。"""
    file = path or WEBHOOK_CONFIG_FILE
    cleaned = {
        "schema_version": 1,
        "channel": config.get("channel") if config.get("channel") in _VALID_CHANNELS else DEFAULT_WEBHOOK_CONFIG["channel"],
        "enabled": bool(config.get("enabled", False)),
        "thresholds": {},
    }
    src_thresholds = config.get("thresholds") or {}
    for k, default_v in DEFAULT_WEBHOOK_CONFIG["thresholds"].items():
        v = src_thresholds.get(k, default_v)
        cleaned["thresholds"][k] = float(v) if isinstance(v, (int, float)) and v >= 0 else default_v
    try:
        with _config_lock:
            atomic_write_json(file, cleaned)
        return True
    except Exception as e:
        logger.error(f"save_webhook_config failed: {e}")
        return False


# ===== 文件级节流(幂等保障) =====


def is_throttled(
    key: str,
    min_interval_seconds: int = DEFAULT_THROTTLE_SECONDS,
    now: float | None = None,
    path: Path | None = None,
) -> bool:
    """判断 key 是否仍在节流窗口内(只读,不写时间戳)。

    时间戳持久化到 webhook_throttle.json,进程重启不丢(架构文档 §6)。
    """
    import time as _time
    ts = now if now is not None else _time.time()
    file = path or THROTTLE_FILE
    data = safe_read_json(file, default={})
    if not isinstance(data, dict):
        return False
    last = data.get(key)
    if not isinstance(last, (int, float)):
        return False
    return (ts - float(last)) < min_interval_seconds


def mark_pushed(key: str, now: float | None = None, path: Path | None = None) -> bool:
    """记录 key 的本次推送时间戳(推送成功后调用)。"""
    import time as _time
    ts = now if now is not None else _time.time()
    file = path or THROTTLE_FILE
    with _throttle_lock:
        data = safe_read_json(file, default={})
        if not isinstance(data, dict):
            data = {}
        data[key] = ts
        # 防膨胀:只保留最近 7 天的时间戳
        cutoff = ts - 7 * 86400
        data = {k: v for k, v in data.items() if isinstance(v, (int, float)) and v >= cutoff}
        try:
            atomic_write_json(file, data)
            return True
        except Exception as e:
            logger.error(f"mark_pushed {key} failed: {e}")
            return False


# ===== 备份(账本导入安全网) =====


def backup_files(files: list[Path], backup_name: str | None = None) -> Path | None:
    """将现有文件复制到 data/backup/<name>_<ts>/,返回备份目录(全部缺失返回 None)。"""
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = backup_name or "import_backup"
    dest = BACKUP_DIR / f"{name}_{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in files:
        try:
            if f is not None and Path(f).exists():
                shutil.copy2(f, dest / Path(f).name)
                copied += 1
        except Exception as e:
            logger.warning(f"backup_files copy {f} failed: {e}")
    if copied == 0:
        return None
    logger.info(f"backup_files: {copied} 个文件已备份到 {dest}")
    return dest

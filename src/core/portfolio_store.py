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
import math
import re
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
GROUPS_FILE = PORTFOLIO_DIR / "groups.json"

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

#: 阈值上限(%):超过即视为录入错误
MAX_THRESHOLD: float = 100.0


def valid_threshold(value: Any) -> float | None:
    """阈值合法性:有限数值且 0 < v <= 100,非法返回 None。

    inf/NaN 一旦落盘,GET /webhook/config 的 JSONResponse(allow_nan=False)会
    持续 500,且扫描阈值恒为 inf 导致预警永不触发。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fv = float(value)
    if not math.isfinite(fv) or not 0 < fv <= MAX_THRESHOLD:
        return None
    return fv

# ===== 持仓分组配置(阶段六批次 B,功能 #8) =====

DEFAULT_GROUP_ID: str = "default"
MAX_GROUPS: int = 20
MAX_FUND_GROUP_ENTRIES: int = 200
_GROUP_ID_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
_FUND_CODE_RE = re.compile(r"^\d{6}$")

DEFAULT_GROUPS: dict[str, Any] = {
    "schema_version": 1,
    "groups": [{"id": DEFAULT_GROUP_ID, "name": "我的持仓"}],
    "fund_groups": {},
}

_config_lock = threading.Lock()
_throttle_lock = threading.Lock()
_groups_lock = threading.Lock()


# ===== 通用读写 =====


def _ensure_parent(file: Path) -> None:
    """原子写入前补建父目录。Render 临时磁盘首次部署时 data/portfolio 不存在。"""
    Path(file).parent.mkdir(parents=True, exist_ok=True)


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
            if k in merged["thresholds"]:
                fv = valid_threshold(v)
                if fv is not None:
                    merged["thresholds"][k] = fv
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
        fv = valid_threshold(src_thresholds.get(k, default_v))
        cleaned["thresholds"][k] = fv if fv is not None else default_v
    try:
        with _config_lock:
            _ensure_parent(file)
            atomic_write_json(file, cleaned)
        return True
    except Exception as e:
        logger.error(f"save_webhook_config failed: {e}")
        return False


# ===== 持仓分组(阶段六批次 B,功能 #8) =====


def _default_groups() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_GROUPS))


def normalize_groups(data: Any) -> tuple[list[str], dict[str, Any]]:
    """校验并归一化分组配置,返回 (errors, cleaned)。

    铁律:
    - ``default`` 分组必须存在且不可删除(旧持仓自动归入该组)
    - 分组 id 仅允许小写字母/数字/下划线/短横线,名称 1-20 字符
    - ``fund_groups`` 只接受 6 位基金代码与已存在的分组 id
    - 校验失败返回错误列表 + 默认配置,由调用方整包拒绝
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["groups 配置需为 JSON 对象"], _default_groups()

    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return ["groups 需为非空数组"], _default_groups()
    if len(raw_groups) > MAX_GROUPS:
        return [f"分组数量超限(最多 {MAX_GROUPS} 个)"], _default_groups()

    groups: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for i, g in enumerate(raw_groups):
        if not isinstance(g, dict):
            errors.append(f"groups[{i}] 需为对象")
            continue
        gid = str(g.get("id", "")).strip()
        name = str(g.get("name", "")).strip()
        if not _GROUP_ID_RE.fullmatch(gid):
            errors.append(f"groups[{i}].id 非法: {gid!r}")
            continue
        if not (1 <= len(name) <= 20):
            errors.append(f"groups[{i}].name 需为 1-20 字符")
            continue
        if gid in seen_ids:
            errors.append(f"groups[{i}].id 重复: {gid}")
            continue
        seen_ids.add(gid)
        groups.append({"id": gid, "name": name})
    if DEFAULT_GROUP_ID not in seen_ids:
        errors.append(f"必须保留 id={DEFAULT_GROUP_ID} 的默认分组")

    raw_fg = data.get("fund_groups") or {}
    if not isinstance(raw_fg, dict):
        errors.append("fund_groups 需为对象")
        raw_fg = {}
    if len(raw_fg) > MAX_FUND_GROUP_ENTRIES:
        errors.append(f"基金分组映射超限(最多 {MAX_FUND_GROUP_ENTRIES} 条)")
    fund_groups: dict[str, str] = {}
    for code, gid in raw_fg.items():
        code_s = str(code).strip()
        gid_s = str(gid).strip()
        if not _FUND_CODE_RE.fullmatch(code_s):
            errors.append(f"fund_groups 基金代码非法: {code_s!r}")
            continue
        if gid_s not in seen_ids:
            errors.append(f"fund_groups[{code_s}] 指向不存在的分组: {gid_s!r}")
            continue
        fund_groups[code_s] = gid_s

    if errors:
        return errors, _default_groups()
    return [], {
        "schema_version": 1,
        "groups": groups,
        "fund_groups": fund_groups,
    }


def load_groups(path: Path | None = None) -> dict[str, Any]:
    """读取分组配置(缺失/损坏/非法 → 默认全归"我的持仓",绝不抛异常)。"""
    file = path or GROUPS_FILE
    data = safe_read_json(file, default=None)
    if data is None:
        return _default_groups()
    errors, cleaned = normalize_groups(data)
    if errors:
        logger.warning(f"load_groups 配置非法,回退默认分组: {errors[:3]}")
        return _default_groups()
    return cleaned


def save_groups(config: dict[str, Any], path: Path | None = None) -> bool:
    """保存分组配置(白名单字段落盘;非法配置拒绝写入)。"""
    file = path or GROUPS_FILE
    errors, cleaned = normalize_groups(config)
    if errors:
        logger.warning(f"save_groups 校验失败: {errors[:3]}")
        return False
    try:
        with _groups_lock:
            _ensure_parent(file)
            atomic_write_json(file, cleaned)
        return True
    except Exception as e:
        logger.error(f"save_groups failed: {e}")
        return False


def group_for_fund(config: dict[str, Any], fund_code: str) -> str:
    """返回基金所属分组 id;未配置/指向未知分组时回退默认组。"""
    mapping = config.get("fund_groups") if isinstance(config, dict) else None
    if not isinstance(mapping, dict):
        return DEFAULT_GROUP_ID
    gid = str(mapping.get(str(fund_code), DEFAULT_GROUP_ID))
    valid_ids = {str(g.get("id")) for g in config.get("groups", []) if isinstance(g, dict)}
    return gid if gid in valid_ids else DEFAULT_GROUP_ID


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
            _ensure_parent(file)
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

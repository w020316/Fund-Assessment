"""策略自进化 · 纯逻辑核心（策略自进化 B1-B5）

职责边界（设计文档 docs/strategy-evolution-design-2026-11-07.md §8）:
- 本模块为纯逻辑层: 代际存储 / 变异器 / 净值择时评估器 / run_evolution 编排;
- **不 import 任何 fastapi/web 模块**（import 闭包由测试断言, 见 tests/test_strategy_evolution.py B7）;
- 不触碰 TradeExecutor / RiskManager, 后台零任务（无定时器/无线程常驻/不产生真实交易信号）;
- 代文件 ≤50KB: 净值序列不落盘（可由缓存重取）, 只落参数与指标。

真源约定: config/strategies.yaml 的 trading_quant 段是参数**真源**;
data/strategy_evolution/ 下 generations/、lineage.json、active.json 是可再生分析产物
（Render 重启丢失可接受, 设计文档 §8 已裁定）。

择时模拟口径说明: 代理分数矩阵基于**历史净值的假设性模拟, 非真实五维数据**
（technical/capital/fundamental/news/sentiment 均为净值可观察量的代理映射,
见设计文档 §1.2, 代码注释与前端详情页均需标注）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.file_io import atomic_write_json, safe_read_json

# ---------- 路径与常量 ----------
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "strategy_evolution"
GENERATIONS_DIR = DATA_DIR / "generations"
LINEAGE_FILE = DATA_DIR / "lineage.json"
ACTIVE_FILE = DATA_DIR / "active.json"
LINEAGE_VERSION = 1
MAX_GENERATIONS = 50        # 代际滚动保留上限(第 51 代写入时清理最旧)
BUDGET_SECONDS = 25.0       # 单次进化预算(time.monotonic 计时, 到点收割)
MIN_FUNDS = 2               # 可用基金数量下限
MIN_SAMPLE_DAYS = 30        # 单只基金净值样本下限
DEFAULT_CANDIDATES = 5      # 每代候选数(1-10 由路由层校验)
NAV_PERIOD = "6m"           # 评估窗: 180 自然日 ≈ 120 交易日
POSITIONS_FILE = Path(__file__).resolve().parents[2] / "web" / "user_fund_positions.json"


class EvolutionDataUnavailable(RuntimeError):
    """净值数据不可用（可用基金 <MIN_FUNDS 或样本 <MIN_SAMPLE_DAYS）。

    路由层转 503 EVOLUTION_DATA_UNAVAILABLE; 铁律: 失败路径零写盘。
    """


# ---------- 时间与摘要工具 ----------

def now_iso() -> str:
    """当前时刻 ISO8601（带本地时区偏移）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_stamp() -> str:
    """文件名/种子用时间戳。"""
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def params_digest(params: dict[str, Any]) -> str:
    """参数摘要（8 位十六进制），用于 lineage 索引比对。"""
    raw = json.dumps(params, ensure_ascii=False, sort_keys=True, default=float)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def ensure_dirs() -> None:
    """建立代际存储目录（幂等）。"""
    GENERATIONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------- lineage / active / generation 读写（原子写 + .bak 回退） ----------

def _empty_lineage() -> dict[str, Any]:
    return {
        "version": LINEAGE_VERSION,
        "updated_at": now_iso(),
        "next_gen_id": 1,
        "generations": [],
    }


def load_lineage() -> dict[str, Any]:
    """读 lineage 索引（损坏走 safe_read_json 的 .bak 回退, 再失败给空谱系）。"""
    data = safe_read_json(LINEAGE_FILE, default=None)
    if not isinstance(data, dict) or not isinstance(data.get("generations"), list):
        return _empty_lineage()
    data.setdefault("next_gen_id", 1)
    return data


def save_lineage(lineage: dict[str, Any]) -> None:
    ensure_dirs()
    lineage["version"] = LINEAGE_VERSION
    lineage["updated_at"] = now_iso()
    atomic_write_json(LINEAGE_FILE, lineage)


def load_active() -> dict[str, Any]:
    """读激活指针; 从未激活时 generation_id=None（当前为出厂参数）。"""
    data = safe_read_json(ACTIVE_FILE, default=None)
    if not isinstance(data, dict):
        return {
            "version": LINEAGE_VERSION,
            "generation_id": None,
            "activated_at": None,
            "activated_by": None,
            "params_snapshot": None,
            "note": "真源为 config/strategies.yaml 的 trading_quant 段;此文件是谱系指针与展示快照",
        }
    return data


def save_active(record: dict[str, Any]) -> None:
    """写激活指针（注意写序铁律: 必须先写 yaml 再写本指针, 见路由层）。"""
    ensure_dirs()
    record["version"] = LINEAGE_VERSION
    atomic_write_json(ACTIVE_FILE, record)


def save_generation(record: dict[str, Any]) -> str:
    """写单代文件（gen_<id>_<ts>.json），返回文件名（相对 generations/）。"""
    ensure_dirs()
    filename = f"gen_{record['generation_id']}_{now_stamp()}.json"
    atomic_write_json(GENERATIONS_DIR / filename, record)
    return filename


def load_generation(gen_id: int) -> dict[str, Any] | None:
    """按代号读单代完整 JSON（经 lineage 索引定位文件）。

    Returns:
        单代记录; 代号不存在返回 None(GEN_NOT_FOUND);
        代号存在但文件损坏/缺失返回 None → 路由层区分转 503 EVOLUTION_CORRUPTED。
    """
    lineage = load_lineage()
    for entry in lineage.get("generations", []):
        if entry.get("generation_id") == int(gen_id):
            data = safe_read_json(GENERATIONS_DIR / str(entry.get("file", "")), default=None)
            if isinstance(data, dict) and data.get("generation_id") == int(gen_id):
                return data
            logger.warning(f"evolution gen {gen_id} file missing/corrupted: {entry.get('file')}")
            return None
    return None


def lineage_entry(lineage: dict[str, Any], gen_id: int) -> dict[str, Any] | None:
    """按代号取 lineage 摘要条目。"""
    for entry in lineage.get("generations", []):
        if entry.get("generation_id") == int(gen_id):
            return entry
    return None


def latest_generation_id(lineage: dict[str, Any]) -> int | None:
    """最新一代代号（lineage 按写入序存储, 取末位）。"""
    gens = lineage.get("generations") or []
    return int(gens[-1]["generation_id"]) if gens else None


def append_lineage(lineage: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """追加代际摘要并执行 50 代滚动清理（被裁剪的最旧代文件同步删除）。"""
    lineage.setdefault("generations", []).append(entry)
    lineage["next_gen_id"] = int(entry["generation_id"]) + 1
    overflow: list[dict[str, Any]] = []
    if len(lineage["generations"]) > MAX_GENERATIONS:
        overflow = lineage["generations"][:-MAX_GENERATIONS]
        lineage["generations"] = lineage["generations"][-MAX_GENERATIONS:]
    save_lineage(lineage)
    for old in overflow:
        try:
            stale = GENERATIONS_DIR / str(old.get("file", ""))
            if stale.exists():
                stale.unlink()
                logger.info(f"evolution pruned oldest generation file: {stale.name}")
        except OSError as e:
            logger.warning(f"evolution prune {old.get('file')} failed: {e}")
    return lineage

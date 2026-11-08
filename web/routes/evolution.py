"""策略自进化 · 7 端点（策略自进化 B6, 设计文档 docs/strategy-evolution-design-2026-11-07.md §4）

铁律:
- 全部端点显式挂 ``Depends(require_admin)``（逐条注释, 接口契约表格第 4 节）;
- 单飞锁唯一真源 = strategy.py 的 ``_BACKTEST_GUARD``（P0-10: 进化与回测共用
  同一把锁对象, 防叠加打满 512MB 实例; 本文件**不新建锁**）;
- 路由层不写业务算法: 只做参数校验 / asyncio 编排（to_thread + wait_for, 与
  strategy.py /analyze、/backtest 同款）/ 错误码映射;
- 激活写序: 备份 → strategies.yaml（参数真源）→ active.json（指针）;
  yaml 写失败不更新指针, 读侧以 yaml 为准自愈。

QF3-02 修复(2026-11-10):原 activate 把样本内最优参数直接写进参数真源, 且
**无备份、无回退路径**——一次错误激活只能靠人工改 yaml 恢复。现在:
1. 写前把当前 yaml **原文**快照到 ``config/backup/strategies_<ts>.yaml``,
   并把 ``backup_path`` / ``restored_from`` 记入 active.json（可审计）;
2. 新增 ``POST /rollback``: 按 active.json 的 backup_path 还原原文,
   并把谱系指针回指 ``restored_from``。无备份 → 409 ``NO_BACKUP_AVAILABLE``。

QF3-10 修复:``wait_for`` + 线程池的组合**不保证**线程随超时结束。旧实现把锁的
释放在依赖的 finally 里（=响应返回时）, 超时后线程仍在写 lineage, 而锁已空 →
下一个 /run 与在途线程并发读-改-写谱系（丢代）。现在锁由**工作线程自己**在真正
结束时释放, 超时路径额外等待线程收尾（带上限）, 期间锁保持占用。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.strategies import evolution as evolution_core
from src.utils.auth import require_admin
from web.routes import config as config_route
from web.routes import strategy as strategy_route
from web.routes.config import load_strategies_yaml, save_strategies_yaml

router = APIRouter()

# wait_for 边界(设计文档 §5): 与 run_evolution 内部 BUDGET_SECONDS 同源语义,
# 兜底收割线程池内偶发的 I/O 阻塞。
EVOLUTION_TIMEOUT_SECONDS = 25

# 激活前 yaml 快照目录（相对 config 目录; 不存在则创建）
BACKUP_SUBDIR = "backup"

# 最近一次运行摘要(status 端点用; 仅内存态, 不落盘)
_LAST_RUN: dict[str, Any] = {"at": None, "improved": None, "generation_id": None}


def _err(code: str, message: str, status_code: int = 503, retryable: bool = True) -> HTTPException:
    """统一错误结构（与 strategy.py _scan_unavailable 同款 detail={"code","message","retryable"}）。"""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "retryable": retryable})


# ---------------------------------------------------------------------------
# 备份 / 还原（QF3-02）: 全部同步函数, 由路由用 asyncio.to_thread 调用
# ---------------------------------------------------------------------------
def _strategies_yaml_path() -> Path:
    """参数真源路径（调用时解析, 便于测试重定向到 tmp_path）。"""
    return config_route.strategies_config_dir() / "strategies.yaml"


def _backup_dir() -> Path:
    return config_route.strategies_config_dir() / BACKUP_SUBDIR


def _backup_stamp() -> str:
    """备份文件名时间戳（YYYYmmdd_HHMMSS, 与任务口径一致）。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup_strategies_yaml(yaml_path: Path, backup_dir: Path) -> str:
    """把当前 strategies.yaml **原文**备份为 ``backup/strategies_<ts>.yaml``。

    用 shutil.copy2 逐字节复制（不经 yaml.dump 再序列化）, 保留注释、键序、
    引号风格——回滚的语义是"回到激活前的那份文件", 不是"回到等价的 dict"。

    Returns:
        相对 config 目录的备份路径（posix 分隔符）, 即 active.json 的 backup_path。

    Raises:
        OSError: 目录/文件创建失败（调用方转 ACTIVATE_BACKUP_FAILED 并中止激活）。
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _backup_stamp()
    target = backup_dir / f"strategies_{stamp}.yaml"
    seq = 1
    while target.exists():
        # 同一秒内二次激活: 追加序号, 绝不覆盖既有回滚点（覆盖=丢掉唯一退路）
        target = backup_dir / f"strategies_{stamp}_{seq}.yaml"
        seq += 1
    if yaml_path.exists():
        shutil.copy2(yaml_path, target)
    else:
        # 从未有过 strategies.yaml(出厂状态): 备份"无段"语义。回滚后 trading_quant
        # 缺段, TradingQuant 自动回退模块常量 = 等价出厂参数, 仍是可用的回退点。
        target.write_text("{}\n", encoding="utf-8")
    logger.info(f"strategies.yaml backed up to {target}")
    return target.relative_to(config_route.strategies_config_dir()).as_posix()


def _resolve_backup_path(raw: str) -> Path:
    """active.json 的 backup_path → 绝对路径。

    兼容两种写法: 相对 config 目录（本实现写入的规范形态, 跨机器可搬）
    与绝对路径（历史/手工写入）, 避免回滚因路径形态不同而失败。
    """
    path = Path(str(raw))
    return path if path.is_absolute() else config_route.strategies_config_dir() / path


def _read_backup_text(backup_path: Path) -> str:
    """读备份原文（UTF-8）。文件缺失/不可读抛 OSError, 由调用方转 409。"""
    return backup_path.read_text(encoding="utf-8")


def _restore_yaml_atomic(backup_path: Path, yaml_path: Path) -> None:
    """把备份原文原子还原到 strategies.yaml（先 copy 到 .tmp, 再 os.replace）。"""
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = yaml_path.with_suffix(yaml_path.suffix + ".tmp")
    shutil.copy2(backup_path, tmp_path)
    os.replace(tmp_path, yaml_path)


# ---------------------------------------------------------------------------
# 单飞锁生命周期（QF3-10）: 唯一真源仍是 strategy.py 的 _BACKTEST_GUARD
# ---------------------------------------------------------------------------
# 未复用 strategy.py 的 _single_backtest_guard 依赖函数: 其 503 detail 为纯文本
# （兼容既有前端）, 而本路由契约要求结构化错误码; 两者获取/释放的是**同一个锁
# 对象**, 互斥语义完全一致。
#
# 超时后在途线程的收尾宽限: 路由最多再等这么久, 期间锁保持占用。
# 超过宽限仍未结束 → 锁**不释放**（宁可拒绝后续请求, 也不让两个线程并发写
# lineage）, 并打 error 日志留排查线索。
RUN_DRAIN_GRACE_SECONDS = 5.0


class _LockLease:
    """恰好释放一次的单飞锁租约（QF3-10）。

    为什么需要它: ``run_in_executor`` / ``to_thread`` 都**不可取消**。``wait_for``
    超时只是让 await 结束, 工作线程仍在跑, 之后照样执行
    save_generation / append_lineage。旧实现在依赖的 finally 里释放锁 → 响应一返回
    锁就空了 → 下一个 /run 与在途线程并发写 lineage（读-改-写交错, 丢代）。
    修法是把释放权交给**工作线程自己**（结束时释放）, 路由层在超时路径不再释放。

    release() 幂等: "线程正常释放"与"路由兜底释放（线程从未提交）"可能先后发生,
    而 threading.Lock.release() 在未持有时会抛 RuntimeError, 幂等标记把双释放
    收敛成恰好一次。
    """

    __slots__ = ("_lock", "_mutex", "_released")

    def __init__(self, lock: Any) -> None:
        self._lock = lock
        self._mutex = threading.Lock()
        self._released = False

    def release(self) -> bool:
        """释放锁; 首次返回 True, 重复调用返回 False（不抛异常）。"""
        with self._mutex:
            if self._released:
                return False
            self._released = True
        self._lock.release()
        return True

    @property
    def released(self) -> bool:
        return self._released


def _acquire_lease(status_code: int, code: str, message: str) -> _LockLease:
    """非阻塞获取单飞锁（与 /backtest 同一把锁对象）; 占用则按给定口径抛错。"""
    lock = strategy_route._BACKTEST_GUARD
    if not lock.acquire(blocking=False):
        raise _err(code, message, status_code=status_code, retryable=True)
    return _LockLease(lock)


async def _drain_worker(finished: threading.Event) -> bool:
    """等超时后的在途线程收尾（上限 RUN_DRAIN_GRACE_SECONDS）。

    Returns:
        True=线程已结束（锁已由线程释放）; False=超过宽限仍在跑（锁保持占用）。
    """
    deadline = time.monotonic() + RUN_DRAIN_GRACE_SECONDS
    while not finished.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    return finished.is_set()


class EvolutionRunRequest(BaseModel):
    """POST /run 请求体: {} 或省略; candidates 可选 1-10（缺省=5）。"""

    candidates: int | None = Field(None, ge=1, le=10)


class EvolutionActivateRequest(BaseModel):
    """POST /activate 请求体: generation_id 必填。"""

    generation_id: int = Field(..., ge=1)


# ---------------------------------------------------------------------------
# 1. POST /run —— 手动触发一次进化（需管理员鉴权 + 单飞锁, 锁持有到线程结束）
# ---------------------------------------------------------------------------
@router.post("/run", dependencies=[Depends(require_admin)])
async def run_evolution_endpoint(req: EvolutionRunRequest | None = None):
    candidates = req.candidates if req is not None else None
    # QF3-10: 锁的释放权交给工作线程（线程结束才放）, 不是响应结束就放
    lease = _acquire_lease(503, "EVOLUTION_BUSY", "上一次进化/回测仍在进行, 请稍后再试")
    finished = threading.Event()

    def worker() -> dict[str, Any]:
        try:
            return evolution_core.run_evolution(
                candidates if candidates else evolution_core.DEFAULT_CANDIDATES,
                evolution_core.BUDGET_SECONDS,
            )
        finally:
            # 无论成功/失败/超时后被丢弃, 线程真正结束才释放锁
            finished.set()
            lease.release()

    loop = asyncio.get_running_loop()
    try:
        # 用 run_in_executor 而非 to_thread: 前者**立即提交**, 后者是惰性协程,
        # 无法区分"提交失败(线程从未启动)"与"线程执行失败"——前者必须由路由
        # 兜底释放锁, 否则单飞锁永久泄漏、进化与回测全被 503。
        fut = loop.run_in_executor(None, worker)
    except RuntimeError as e:
        lease.release()
        logger.exception("evolution run: executor unavailable")
        raise _err("EVOLUTION_FAILED", f"进化执行失败(线程池不可用): {e}")

    try:
        result = await asyncio.wait_for(fut, timeout=EVOLUTION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        # 超时后线程不可取消, 仍在写盘: 等它收尾再返回, 期间锁保持占用
        drained = await _drain_worker(finished)
        logger.warning(
            f"evolution run timeout({EVOLUTION_TIMEOUT_SECONDS}s), "
            f"in-flight thread {'drained' if drained else 'STILL RUNNING (lock kept, 后续请求将被拒绝)'}"
        )
        raise _err("EVOLUTION_TIMEOUT", f"进化超时({EVOLUTION_TIMEOUT_SECONDS}s), 请稍后重试")
    except evolution_core.EvolutionDataUnavailable as e:
        logger.warning(f"evolution run data unavailable: {e}")
        raise _err("EVOLUTION_DATA_UNAVAILABLE", str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("evolution run failed")
        raise _err("EVOLUTION_FAILED", f"进化执行失败: {e}")
    _LAST_RUN.update(
        {
            "at": evolution_core.now_iso(),
            "improved": result.get("improved"),
            "generation_id": result.get("generation_id"),
        }
    )
    return result


# ---------------------------------------------------------------------------
# 2. GET /lineage —— 谱系摘要（需管理员鉴权）
# ---------------------------------------------------------------------------
@router.get("/lineage", dependencies=[Depends(require_admin)])
async def get_lineage():
    lineage = evolution_core.load_lineage()
    active = evolution_core.load_active()
    generations = sorted(
        lineage.get("generations", []),
        key=lambda g: int(g.get("generation_id", 0)),
        reverse=True,
    )
    return {
        "active_generation_id": active.get("generation_id"),
        "count": len(generations),
        "generations": generations,
    }


# ---------------------------------------------------------------------------
# 3. GET /generations/{gen_id} —— 单代完整 JSON（需管理员鉴权）
# ---------------------------------------------------------------------------
@router.get("/generations/{gen_id}", dependencies=[Depends(require_admin)])
async def get_generation(gen_id: int):
    if gen_id < 1:
        raise _err("INVALID_GEN_ID", "代号必须为正整数", status_code=400, retryable=False)
    record = evolution_core.load_generation(gen_id)
    if record is None:
        lineage = evolution_core.load_lineage()
        if evolution_core.lineage_entry(lineage, gen_id) is not None:
            # 谱系索引存在但代文件损坏/缺失
            raise _err("EVOLUTION_CORRUPTED", f"第 {gen_id} 代文件损坏或缺失")
        raise _err("GEN_NOT_FOUND", f"第 {gen_id} 代不存在", status_code=404, retryable=False)
    return record


# ---------------------------------------------------------------------------
# 4. GET /compare?gen={n} —— 激活代 vs 候选代对照（需管理员鉴权）
# ---------------------------------------------------------------------------
def _param_diff(active_params: dict[str, Any], cand_params: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for section in ("weights", "signals"):
        a = (active_params or {}).get(section) or {}
        c = (cand_params or {}).get(section) or {}
        diff[section] = {
            k: {"active": a.get(k), "candidate": c.get(k)}
            for k in dict.fromkeys(list(a.keys()) + list(c.keys()))
        }
    return diff


@router.get("/compare", dependencies=[Depends(require_admin)])
async def compare(gen: int | None = None):
    active = evolution_core.load_active()
    if not active.get("generation_id"):
        raise _err("NO_ACTIVE_GENERATION", "当前无激活代（出厂参数）, 无法对比", status_code=404, retryable=False)
    lineage = evolution_core.load_lineage()
    target_id = gen if gen is not None else evolution_core.latest_generation_id(lineage)
    if target_id is None:
        raise _err("GEN_NOT_FOUND", "谱系为空, 无可对比代", status_code=404, retryable=False)
    candidate = evolution_core.load_generation(int(target_id))
    if candidate is None:
        if evolution_core.lineage_entry(lineage, int(target_id)) is not None:
            raise _err("EVOLUTION_CORRUPTED", f"第 {target_id} 代文件损坏或缺失")
        raise _err("GEN_NOT_FOUND", f"第 {target_id} 代不存在", status_code=404, retryable=False)

    active_id = int(active["generation_id"])
    active_record = evolution_core.load_generation(active_id)
    active_params = active.get("params_snapshot") or (active_record or {}).get("params") or {}
    active_metrics = (active_record or {}).get("metrics")
    cand_metrics = candidate.get("metrics") or {}

    metric_keys = ("annual_return", "max_drawdown", "sharpe", "win_rate", "fitness")
    metric_diff = {
        k: {"active": (active_metrics or {}).get(k), "candidate": cand_metrics.get(k)}
        for k in metric_keys
    }
    return {
        "active": {
            "generation_id": active_id,
            "fitness": (active_record or {}).get("metrics", {}).get("fitness")
            if active_record else (evolution_core.lineage_entry(lineage, active_id) or {}).get("fitness"),
            "params": active_params,
            "metrics": active_metrics,
        },
        "candidate": {
            "generation_id": candidate.get("generation_id"),
            "fitness": cand_metrics.get("fitness"),
            "params": candidate.get("params"),
            "metrics": cand_metrics,
        },
        "param_diff": _param_diff(active_params, candidate.get("params") or {}),
        "metric_diff": metric_diff,
    }


# ---------------------------------------------------------------------------
# 5. POST /activate —— 激活指定代（需管理员鉴权 + 单飞锁; 写序 备份 → yaml → active.json）
# ---------------------------------------------------------------------------
@router.post("/activate", dependencies=[Depends(require_admin)])
async def activate(req: EvolutionActivateRequest):
    gen_id = int(req.generation_id)
    record = evolution_core.load_generation(gen_id)
    if record is None:
        lineage = evolution_core.load_lineage()
        if evolution_core.lineage_entry(lineage, gen_id) is not None:
            raise _err("EVOLUTION_CORRUPTED", f"第 {gen_id} 代文件损坏或缺失")
        raise _err("GEN_NOT_FOUND", f"第 {gen_id} 代不存在", status_code=404, retryable=False)
    params = record.get("params") or {}

    # 先取"激活前指针", 供 active.json 的 restored_from 记录（回滚后应回指此处）
    prev_generation_id = evolution_core.load_active().get("generation_id")

    # ---- QF3-02 第 1 步: 写前快照。备份失败即中止, 绝不产生"无法回退的激活" ----
    yaml_path = _strategies_yaml_path()
    try:
        backup_rel = await asyncio.to_thread(_backup_strategies_yaml, yaml_path, _backup_dir())
    except Exception as e:
        logger.exception(f"activate gen {gen_id}: backup strategies.yaml failed")
        raise _err("ACTIVATE_BACKUP_FAILED", f"备份 strategies.yaml 失败, 激活中止(未改动真源): {e}")

    # ---- QF3-02 第 2 步: 写序铁律 —— 先写参数真源, 失败则不更新指针 ----
    try:
        yaml_data = await asyncio.to_thread(load_strategies_yaml)
        yaml_data["trading_quant"] = {
            "weights": params.get("weights") or {},
            "signals": params.get("signals") or {},
        }
        await asyncio.to_thread(save_strategies_yaml, yaml_data)
    except Exception as e:
        logger.exception(f"activate gen {gen_id}: write strategies.yaml failed")
        raise _err("ACTIVATE_WRITE_FAILED", f"写入 strategies.yaml 失败, 激活中止: {e}")

    active_record = {
        "generation_id": gen_id,
        "activated_at": evolution_core.now_iso(),
        "activated_by": "admin",
        "params_snapshot": params,
        "backup_path": backup_rel,
        "restored_from": prev_generation_id,
        "note": (
            "真源为 config/strategies.yaml 的 trading_quant 段;此文件是谱系指针与展示快照;"
            "backup_path=本次激活前的 yaml 原文快照(供 POST /rollback 还原), "
            "restored_from=回滚后应回指的代号(None=出厂参数)"
        ),
    }
    try:
        await asyncio.to_thread(evolution_core.save_active, active_record)
    except Exception as e:
        # yaml 已写而指针未写: 读侧以 yaml 为准自愈, 明确报给调用方
        logger.exception(f"activate gen {gen_id}: write active.json failed (yaml already updated)")
        raise _err(
            "ACTIVATE_WRITE_FAILED",
            f"参数真源(strategies.yaml)已更新, 但谱系指针写入失败({e}); 读侧以 yaml 为准, "
            f"如需回退请用备份 {backup_rel}",
        )
    logger.info(f"evolution gen {gen_id} activated (prev={prev_generation_id}, backup={backup_rel})")
    return {
        "activated": True,
        "generation_id": gen_id,
        "activated_at": active_record["activated_at"],
        "yaml_updated": True,
        "params_written": params,
        "prev_generation_id": prev_generation_id,
        "backup_path": backup_rel,
        "restored_from": prev_generation_id,
    }


# ---------------------------------------------------------------------------
# 6. POST /rollback —— 用激活前快照还原参数真源（需管理员鉴权 + 单飞锁）
# ---------------------------------------------------------------------------
@router.post("/rollback", dependencies=[Depends(require_admin)])
async def rollback():
    """把 strategies.yaml 还原到"上一次激活之前"的原文, 并把指针回指 restored_from。

    错误口径:
    - 无 active.json / 无 backup_path / 备份文件缺失 / 备份非合法 YAML
      → **409 NO_BACKUP_AVAILABLE**（不可用即不可用, 绝不返回 200 假装成功）;
    - 还原写盘失败 → 503 ROLLBACK_WRITE_FAILED（此时 yaml 未变, 可重试）。
    """
    active = evolution_core.load_active()
    raw_backup = active.get("backup_path")
    if not raw_backup:
        raise _err(
            "NO_BACKUP_AVAILABLE",
            "当前无激活备份(active.json 缺 backup_path): 未激活过, 或激活早于备份能力上线",
            status_code=409,
            retryable=False,
        )
    backup_path = _resolve_backup_path(str(raw_backup))
    if not backup_path.is_file():
        raise _err(
            "NO_BACKUP_AVAILABLE",
            f"备份文件不存在或不可读: {raw_backup}(可能已被清理)",
            status_code=409,
            retryable=False,
        )
    try:
        backup_text = await asyncio.to_thread(_read_backup_text, backup_path)
        parsed = yaml.safe_load(backup_text)
    except Exception as e:
        logger.warning(f"rollback: backup {raw_backup} unreadable/invalid: {e}")
        raise _err("NO_BACKUP_AVAILABLE", f"备份文件不是合法 YAML, 拒绝还原: {e}", status_code=409, retryable=False)
    if parsed is not None and not isinstance(parsed, dict):
        raise _err("NO_BACKUP_AVAILABLE", "备份文件内容不是 YAML 映射, 拒绝还原", status_code=409, retryable=False)

    restored_from = active.get("restored_from")
    yaml_path = _strategies_yaml_path()
    try:
        await asyncio.to_thread(_restore_yaml_atomic, backup_path, yaml_path)
    except Exception as e:
        logger.exception(f"rollback: restore {yaml_path} from {raw_backup} failed")
        raise _err("ROLLBACK_WRITE_FAILED", f"还原 strategies.yaml 失败, 真源未改动: {e}")

    # 指针回指: 还原后"当前生效代"= 激活前那一代(None=出厂参数)
    params_snapshot = None
    if restored_from is not None:
        try:
            restored_record = evolution_core.load_generation(int(restored_from))
        except Exception as e:  # 代号异常/文件损坏: 不影响 yaml 已还原的事实
            logger.warning(f"rollback: load generation {restored_from} failed: {e}")
            restored_record = None
        params_snapshot = (restored_record or {}).get("params")
        if params_snapshot is None:
            logger.warning(f"rollback: gen {restored_from} params unavailable (pruned/corrupted)")

    now = evolution_core.now_iso()
    active_record = {
        "generation_id": restored_from,
        "activated_at": now,
        "activated_by": "admin(rollback)",
        "params_snapshot": params_snapshot,
        "backup_path": None,
        "restored_from": None,
        "rolled_back_at": now,
        "rolled_back_to_backup": str(raw_backup),
        "note": (
            "真源为 config/strategies.yaml 的 trading_quant 段;此文件是谱系指针与展示快照;"
            "本次记录由 POST /rollback 写入(已消费一次回滚点)"
        ),
    }
    try:
        await asyncio.to_thread(evolution_core.save_active, active_record)
    except Exception as e:
        # yaml 已还原而指针未写: 读侧以 yaml 为准, 报错让调用方知晓指针滞后
        logger.exception("rollback: write active.json failed (yaml already restored)")
        raise _err(
            "ROLLBACK_WRITE_FAILED",
            f"参数真源已还原自 {raw_backup}, 但谱系指针写入失败({e}); 读侧以 yaml 为准",
        )
    logger.info(f"evolution rollback: yaml restored from {raw_backup}, pointer -> {restored_from}")
    return {
        "rolled_back": True,
        "yaml_restored": True,
        "backup_path": str(raw_backup),
        "restored_from": restored_from,
        "restored_at": now,
        "generation_id": restored_from,
    }


# ---------------------------------------------------------------------------
# 7. GET /status —— 运行状态（需管理员鉴权）
# ---------------------------------------------------------------------------
@router.get("/status", dependencies=[Depends(require_admin)])
async def status():
    # running 取共享单飞锁的占用态(回测/进化任一进行中均为 True)
    running = strategy_route._BACKTEST_GUARD.locked()
    return {
        "running": running,
        "last_run": dict(_LAST_RUN),
        "lock_holder": "backtest_or_evolution" if running else None,
    }

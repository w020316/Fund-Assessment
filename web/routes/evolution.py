"""策略自进化 · 6 端点（策略自进化 B6, 设计文档 docs/strategy-evolution-design-2026-11-07.md §4）

铁律:
- 全部端点显式挂 ``Depends(require_admin)``（逐条注释, 接口契约表格第 4 节）;
- 单飞锁唯一真源 = strategy.py 的 ``_BACKTEST_GUARD``（P0-10: 进化与回测共用
  同一把锁对象, 防叠加打满 512MB 实例; 本文件**不新建锁**）;
- 路由层不写业务算法: 只做参数校验 / asyncio 编排（to_thread + wait_for, 与
  strategy.py /analyze、/backtest 同款）/ 错误码映射;
- 激活写序: 先 strategies.yaml（参数真源）→ 后 active.json（指针）;
  yaml 写失败不更新指针, 读侧以 yaml 为准自愈。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.strategies import evolution as evolution_core
from src.utils.auth import require_admin
from web.routes import strategy as strategy_route
from web.routes.config import load_strategies_yaml, save_strategies_yaml

router = APIRouter()

# wait_for 边界(设计文档 §5): 与 run_evolution 内部 BUDGET_SECONDS 同源语义,
# 兜底收割线程池内偶发的 I/O 阻塞。
EVOLUTION_TIMEOUT_SECONDS = 25

# 最近一次运行摘要(status 端点用; 仅内存态, 不落盘)
_LAST_RUN: dict[str, Any] = {"at": None, "improved": None, "generation_id": None}


def _err(code: str, message: str, status_code: int = 503, retryable: bool = True) -> HTTPException:
    """统一错误结构（与 strategy.py _scan_unavailable 同款 detail={"code","message","retryable"}）。"""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "retryable": retryable})


def _evolution_guard():
    """单飞锁依赖: 非阻塞获取 strategy.py 的 _BACKTEST_GUARD（与回测同一把锁）。

    说明: 未直接复用 strategy.py 的 _single_backtest_guard 依赖函数, 因其 503
    detail 为纯文本(兼容既有前端), 而本路由契约要求结构化错误码 EVOLUTION_BUSY;
    两者获取/释放的是**同一个锁对象**, 互斥语义完全一致。
    """
    if not strategy_route._BACKTEST_GUARD.acquire(blocking=False):
        raise _err("EVOLUTION_BUSY", "上一次进化/回测仍在进行, 请稍后再试")
    try:
        yield
    finally:
        strategy_route._BACKTEST_GUARD.release()


class EvolutionRunRequest(BaseModel):
    """POST /run 请求体: {} 或省略; candidates 可选 1-10（缺省=5）。"""

    candidates: int | None = Field(None, ge=1, le=10)


class EvolutionActivateRequest(BaseModel):
    """POST /activate 请求体: generation_id 必填。"""

    generation_id: int = Field(..., ge=1)


# ---------------------------------------------------------------------------
# 1. POST /run —— 手动触发一次进化（需管理员鉴权 + 单飞锁）
# ---------------------------------------------------------------------------
@router.post("/run", dependencies=[Depends(require_admin), Depends(_evolution_guard)])
async def run_evolution_endpoint(req: EvolutionRunRequest | None = None):
    candidates = req.candidates if req is not None else None
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                evolution_core.run_evolution,
                candidates if candidates else evolution_core.DEFAULT_CANDIDATES,
                evolution_core.BUDGET_SECONDS,
            ),
            timeout=EVOLUTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(f"evolution run timeout({EVOLUTION_TIMEOUT_SECONDS}s)")
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
# 5. POST /activate —— 激活指定代（需管理员鉴权; 写序 yaml → active.json）
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

    # 写序铁律: 先写参数真源(strategies.yaml), 失败则不更新指针
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

    prev_generation_id = evolution_core.load_active().get("generation_id")
    active_record = {
        "generation_id": gen_id,
        "activated_at": evolution_core.now_iso(),
        "activated_by": "admin",
        "params_snapshot": params,
        "note": "真源为 config/strategies.yaml 的 trading_quant 段;此文件是谱系指针与展示快照",
    }
    try:
        await asyncio.to_thread(evolution_core.save_active, active_record)
    except Exception as e:
        # yaml 已写而指针未写: 读侧以 yaml 为准自愈, 明确报给调用方
        logger.exception(f"activate gen {gen_id}: write active.json failed (yaml already updated)")
        raise _err(
            "ACTIVATE_WRITE_FAILED",
            f"参数真源(strategies.yaml)已更新, 但谱系指针写入失败({e}); 读侧以 yaml 为准, 请重试或检查磁盘",
        )
    logger.info(f"evolution gen {gen_id} activated (prev={prev_generation_id})")
    return {
        "activated": True,
        "generation_id": gen_id,
        "activated_at": active_record["activated_at"],
        "yaml_updated": True,
        "params_written": params,
        "prev_generation_id": prev_generation_id,
    }


# ---------------------------------------------------------------------------
# 6. GET /status —— 运行状态（需管理员鉴权）
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

"""时段预测 API 路由(设计文档 A1~A8 + 主理人补充的 /export 备份端点)。

鉴权状态表(逐端点显式挂载, 曾漏挂出过 P0, 验收时逐一核对):
- GET  /api/prediction/snapshots              公开读
- POST /api/prediction/snapshots/collect      require_admin
- GET  /api/prediction/predictions            公开读
- POST /api/prediction/predictions/generate   require_admin
- GET  /api/prediction/recommendations        公开读
- POST /api/prediction/recommendations/generate require_admin
- GET  /api/prediction/accuracy               公开读
- POST /api/prediction/verify                 require_admin
- GET  /api/prediction/export                 require_admin(SQLite 全库字节流, Action 备份用)

统一响应 {code, message, data}(src/core/response.py); 公开读端点只含公开行情数据。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from loguru import logger

from src.core import prediction_service as svc
from src.core.response import error, success
from src.utils.auth import require_admin

router = APIRouter()


class CollectSnapshotRequest(BaseModel):
    session: str = Field(..., description="时段: morning / noon / close")


@router.get("/snapshots")
async def get_snapshots(
    date: str = Query("", max_length=10, description="交易日 YYYY-MM-DD, 空则返回近 days 日"),
    days: int = Query(5, ge=1, le=90, description="近 N 日(未指定 date 时生效)"),
):
    """A1: 时段快照查询(公开读)。"""
    data = await asyncio.to_thread(svc.get_snapshots, date or None, days)
    return success({"snapshots": data, "count": len(data)})


@router.post("/snapshots/collect", dependencies=[Depends(require_admin)])
async def post_collect_snapshot(req: CollectSnapshotRequest):
    """A2: 采集指定时段快照(admin; 非交易日返回 skipped; 5 分钟幂等)。"""
    try:
        result = await svc.async_collect_snapshot(req.session)
    except ValueError as e:
        return error(str(e))
    except Exception as e:  # noqa: BLE001 — 上游全部失败也不 500
        logger.warning(f"snapshots/collect failed: {e}")
        return error(f"快照采集失败: {e}")
    return success(result)


@router.get("/predictions")
async def get_predictions(days: int = Query(30, ge=1, le=365, description="近 N 日")):
    """A3: 历史预测查询(公开读, R3.2 近 30 日数据源)。"""
    data = await asyncio.to_thread(svc.get_predictions, days)
    return success({"predictions": data, "count": len(data)})


@router.post("/predictions/generate", dependencies=[Depends(require_admin)])
async def post_generate_prediction():
    """A4: 生成当日次日预测(admin; 同日幂等覆盖; 非交易日 skipped)。"""
    try:
        result = await svc.async_generate_prediction()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"predictions/generate failed: {e}")
        return error(f"预测生成失败: {e}")
    return success(result)


@router.get("/recommendations")
async def get_recommendations(
    date: str = Query("", max_length=10, description="交易日 YYYY-MM-DD, 空则当日"),
    days: int = Query(30, ge=1, le=365, description="近 N 日(未指定 date 时生效)"),
):
    """A5: 今日/历史推荐查询(公开读)。"""
    data = await asyncio.to_thread(svc.get_recommendations, date or None, days)
    return success({"recommendations": data, "count": len(data)})


@router.post("/recommendations/generate", dependencies=[Depends(require_admin)])
async def post_generate_recommendations():
    """A6: 生成当日推荐(admin; 标的全部可追溯规则信号, 同日幂等覆盖)。"""
    try:
        result = await svc.async_generate_recommendations()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"recommendations/generate failed: {e}")
        return error(f"推荐生成失败: {e}")
    return success(result)


@router.get("/accuracy")
async def get_accuracy():
    """A7: 三窗口命中率 + 30 日走势(公开读; 双通道同源, 数字与库内一致)。"""
    data = await svc.async_accuracy_dashboard()
    return success(data)


@router.post("/verify", dependencies=[Depends(require_admin)])
async def post_verify():
    """A8: 验证今日预测与推荐(admin; 15:40 首跑/16:30 重试均调它; 幂等 upsert)。

    验证目标 target_date=今日, 即上一交易日生成的次日预测。
    """
    try:
        result = await svc.async_verify_daily()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"verify failed: {e}")
        return error(f"验证失败: {e}")
    return success(result)


@router.get("/export", dependencies=[Depends(require_admin)])
async def export_db():
    """导出 prediction.db 一致性快照(admin; 主理人补充: Render 免费层磁盘非持久,
    每日验证 Action 完成后拉取此端点备份到 daily-reports 分支)。"""
    from src.core.prediction_store import get_store

    blob = await asyncio.to_thread(lambda: get_store().export_bytes())
    if blob is None:
        return error("导出失败: 库不可读")
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=prediction_snapshot.db"},
    )

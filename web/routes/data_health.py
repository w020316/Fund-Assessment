"""数据源健康度路由(借鉴 zvt recorder 状态模型)。

暴露 ``data_source_v2`` 5 条核心链路的运行态,供设置页展示与线上排查。
只读、无凭据、无写入,故不挂 require_admin(与 /api/health 同口径)。
"""
from __future__ import annotations

from fastapi import APIRouter

from src.core import data_health

router = APIRouter()


@router.get("/health")
async def data_source_health() -> dict:
    """各数据源最近成败、连续失败次数与最近返回条数。"""
    return data_health.snapshot()

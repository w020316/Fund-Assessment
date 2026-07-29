"""话术库路由

提供股市/基金话术模板浏览、变量填充、智能匹配。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

# P1 修复(2026-07-29):话术写端点加 admin 鉴权,匹配/生成属业务写操作
from src.utils.auth import require_admin

from src.analysis import script_library as sl
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str

router = APIRouter()


class ResponseMeta(BaseModel):
    data_source: str = ""
    quality_score: float = 0.0
    cached: bool = False
    timestamp: str = ""


def _build_meta(data_source: str = "template") -> dict:
    return ResponseMeta(
        data_source=data_source,
        quality_score=100.0,
        cached=False,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ).model_dump()


class GenerateRequest(BaseModel):
    """话术生成请求"""
    script_id: str
    variables: dict[str, Any] = {}


class MatchFundRequest(BaseModel):
    """基金话术匹配请求"""
    fund_advice: dict[str, Any]


class MatchStockRequest(BaseModel):
    """股票话术匹配请求"""
    stock_data: dict[str, Any]


class AIGenerateRequest(BaseModel):
    """P3 AI建议生成请求

    基于基金/个股数据用免费 LLM 生成个性化投资建议话术
    """
    target_type: str  # "fund" 或 "stock"
    target_data: dict[str, Any]  # 基金/个股数据
    scene: str = ""  # 场景提示(可选)


@router.get("/list")
async def list_scripts(
    category: str = Query("", description="分类:stock/fund,空则全部"),
    scene: str = Query("", description="场景:buy/sell/hold/wait/stop_loss/dca/add/take_profit,空则全部"),
):
    """列出话术模板"""
    result = sl.list_scripts(category=category, scene=scene)
    return {"data": result, "_meta": _build_meta()}


@router.get("/categories")
async def get_categories():
    """获取话术库分类结构"""
    result = sl.get_script_categories()
    return {"data": result, "_meta": _build_meta()}


@router.get("/{script_id}")
async def get_script(script_id: str):
    """按 ID 获取单个话术模板"""
    result = sl.get_script(script_id)
    if not result:
        return {"data": None, "_meta": _build_meta(), "error": "script not found"}
    return {"data": result, "_meta": _build_meta()}


@router.post("/generate", dependencies=[Depends(require_admin)])
async def generate_script(req: GenerateRequest):
    """根据模板 ID + 变量生成话术"""
    result = sl.generate_script(req.script_id, req.variables)
    if not result:
        return {"data": None, "_meta": _build_meta(), "error": "script not found"}
    return {"data": result, "_meta": _build_meta()}


@router.post("/match/fund", dependencies=[Depends(require_admin)])
async def match_fund(req: MatchFundRequest):
    """根据基金建议自动匹配话术"""
    result = sl.match_fund_scripts(req.fund_advice)
    return {"data": result, "_meta": _build_meta(), "count": len(result)}


@router.post("/match/stock", dependencies=[Depends(require_admin)])
async def match_stock(req: MatchStockRequest):
    """根据个股数据自动匹配话术"""
    result = sl.match_stock_scripts(req.stock_data)
    return {"data": result, "_meta": _build_meta(), "count": len(result)}


@router.post("/ai-generate", dependencies=[Depends(require_admin)])
async def ai_generate(req: AIGenerateRequest):
    """P3 AI建议生成 - 基于基金/个股数据用免费 LLM 生成个性化投资建议话术

    使用免费模型(agnes-2.0-flash / glm-4-flash),30s 超时,
    失败时自动降级为模板话术匹配。
    """
    if req.target_type not in ("fund", "stock"):
        return {"data": None, "_meta": _build_meta(), "error": "target_type 必须为 fund 或 stock"}

    result = await asyncio.to_thread(
        sl.ai_generate_script,
        req.target_type,
        req.target_data,
        req.scene,
    )
    return {"data": result, "_meta": _build_meta(data_source=result.get("source", "ai"))}

"""金融技能库与一句话调度路由

- GET  /api/agent/skills            技能目录(可按 category/keyword 过滤)
- GET  /api/agent/skills/categories 分类统计
- POST /api/agent/dispatch          一句话 → 技能调度方案(只生成方案,不自动执行写操作)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.agents.dispatcher import dispatch as dispatch_query
from src.agents.skill_registry import CATEGORY_ORDER, category_summary, list_skills, registry_stats
from web.rate_limiter import limiter

router = APIRouter()


def _meta() -> dict:
    return {
        "data_source": "skill_registry",
        "cached": False,
        "quality_score": 100.0,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/skills")
async def skills_catalog(
    category: str = Query("", description="分类名称"),
    keyword: str = Query("", description="关键词"),
):
    if category and category not in CATEGORY_ORDER:
        raise HTTPException(status_code=400, detail=f"未知分类:{category}")
    data = {
        "skills": list_skills(category=category, keyword=keyword),
        "categories": category_summary(),
        "stats": registry_stats(),
    }
    return {"data": data, "_meta": _meta()}


@router.get("/skills/categories")
async def skills_categories():
    return {"data": {"categories": category_summary(), "stats": registry_stats()}, "_meta": _meta()}


class DispatchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    execute: bool = False


@router.post("/dispatch")
@limiter.limit("20/minute")
async def dispatch(request: Request, req: DispatchRequest):
    """一句话调度

    当前只返回调度方案(execution=plan_only)。`execute` 参数保留给后续版本,
    写操作技能永远需要人工确认,不会自动下单。
    """
    data = dispatch_query(req.query)
    if req.execute and any(item.get("read_only") is False for item in data.get("plan", [])):
        data["notes"].append("包含写操作技能,已忽略 execute 请求,请在前端人工确认")
    return {"data": data, "_meta": _meta()}

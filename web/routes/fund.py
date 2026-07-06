"""基金模块路由

提供:
- 基金持仓 CRUD(GET / POST / DELETE /api/fund/positions)
- 基金建议(GET /api/fund/advice)—— 基于大盘/板块/盈亏的规则引擎
- 基金搜索(GET /api/fund/search?q=xxx)—— 按名称/代码模糊查询
- 基金实时行情聚合(GET /api/fund/realtime?codes=110022,161725)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from src.core.data_source_v2 import (
    get_fund_history_tencent,
    get_fund_realtime_tencent,
)
from src.utils.auth import require_admin
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str

router = APIRouter()

_FUND_POSITIONS_FILE = Path(__file__).resolve().parent.parent / "user_fund_positions.json"

# 默认空持仓(不硬编码示例数据,用户自行添加)


# ============ 数据模型 ============

class FundPosition(BaseModel):
    fund_code: str = Field(..., description="基金代码(6位数字)")
    fund_name: str = Field("", description="基金名称")
    shares: float = Field(0.0, description="持有份额")
    cost_nav: float = Field(0.0, description="成本净值")
    buy_date: str = Field("", description="买入日期 YYYY-MM-DD")
    note: str = Field("", description="备注")


class SaveFundPositionsRequest(BaseModel):
    positions: list[FundPosition]


class FundSearchItem(BaseModel):
    code: str
    name: str
    type: str = ""


class MessageResponse(BaseModel):
    success: bool
    message: str


# ============ 持仓 CRUD ============

def _load_positions() -> list[dict]:
    if _FUND_POSITIONS_FILE.exists():
        try:
            with open(_FUND_POSITIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("positions", [])
        except Exception as e:
            logger.warning(f"load fund positions failed: {e}")
    return []


def _save_positions(positions: list[dict]) -> None:
    try:
        with open(_FUND_POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump({"positions": positions}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"save fund positions failed: {e}")


@router.get("/positions")
async def get_positions():
    """获取基金持仓(含实时净值与盈亏)。"""
    positions = _load_positions()
    codes = [p["fund_code"] for p in positions if p.get("fund_code")]
    quotes = await asyncio.to_thread(get_fund_realtime_tencent, codes) if codes else []
    quote_map = {q.get("code", ""): q for q in quotes}

    enriched: list[dict] = []
    for p in positions:
        code = p.get("fund_code", "")
        quote = quote_map.get(code, {})
        current_nav = _safe_float(quote.get("nav", 0))
        cost_nav = _safe_float(p.get("cost_nav", 0))
        shares = _safe_float(p.get("shares", 0))
        pnl_pct = ((current_nav - cost_nav) / cost_nav * 100) if cost_nav > 0 and current_nav > 0 else 0.0
        pnl_amount = (current_nav - cost_nav) * shares if current_nav > 0 and cost_nav > 0 else 0.0
        market_value = current_nav * shares if current_nav > 0 else 0.0
        enriched.append({
            "fund_code": code,
            "fund_name": p.get("fund_name") or _safe_str(quote.get("name", "")),
            "shares": shares,
            "cost_nav": cost_nav,
            "current_nav": current_nav,
            "buy_date": p.get("buy_date", ""),
            "note": p.get("note", ""),
            "change_pct": _safe_float(quote.get("change_pct", 0)),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_amount": round(pnl_amount, 2),
            "market_value": round(market_value, 2),
            "update_time": _safe_str(quote.get("update_time", "")),
        })

    total_market_value = sum(e["market_value"] for e in enriched)
    total_cost = sum(e["cost_nav"] * e["shares"] for e in enriched)
    total_pnl = total_market_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    today_pnl = sum(e["pnl_amount"] - (e["pnl_amount"] / (1 + e["change_pct"] / 100) if e["change_pct"] != 0 else 0)
                    for e in enriched if e["current_nav"] > 0)

    return {
        "positions": enriched,
        "summary": {
            "total_market_value": round(total_market_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "today_pnl": round(today_pnl, 2),
            "count": len(enriched),
        },
    }


@router.post("/positions", dependencies=[Depends(require_admin)])
async def save_positions(req: SaveFundPositionsRequest):
    """保存基金持仓(全量覆盖)。"""
    positions = [p.model_dump() for p in req.positions]
    _save_positions(positions)
    return {"success": True, "message": f"已保存 {len(positions)} 只基金持仓"}


class AddPositionRequest(BaseModel):
    fund_code: str
    fund_name: str = ""
    shares: float = 0.0
    cost_nav: float = 0.0
    buy_date: str = ""
    note: str = ""


@router.post("/positions/add", dependencies=[Depends(require_admin)])
async def add_position(req: AddPositionRequest):
    """单只基金持仓添加(增量)。"""
    positions = _load_positions()
    # 同代码覆盖
    positions = [p for p in positions if p.get("fund_code") != req.fund_code]
    positions.append(req.model_dump())
    _save_positions(positions)
    return {"success": True, "message": f"已添加 {req.fund_code}"}


@router.delete("/positions/{fund_code}", dependencies=[Depends(require_admin)])
async def delete_position(fund_code: str):
    """删除指定基金持仓。"""
    positions = _load_positions()
    before = len(positions)
    positions = [p for p in positions if p.get("fund_code") != fund_code]
    _save_positions(positions)
    removed = before - len(positions)
    return {"success": removed > 0, "message": f"已删除 {fund_code}" if removed else f"未找到 {fund_code}"}


# ============ 基金建议 ============

@router.get("/advice")
async def get_advice():
    """获取基金建议(基于大盘/板块/盈亏的规则引擎)。"""
    from src.analysis.fund_advisor import generate_fund_advice
    positions = _load_positions()
    result = await asyncio.to_thread(generate_fund_advice, positions)
    return result


# ============ 基金搜索 ============

@router.get("/search")
async def search_fund(q: str = Query(..., min_length=1, description="基金代码或名称关键词")):
    """按名称/代码模糊搜索基金(数据源:东方财富基金搜索)。"""
    if not q.strip():
        return {"data": [], "query": q}

    url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    params = {
        "callback": "jQuery",
        "m": 1,
        "key": q,
        "_": int(time.time() * 1000),
    }
    try:
        resp = await asyncio.to_thread(
            requests.get,
            url,
            params=params,
            timeout=5,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://fund.eastmoney.com/",
            },
        )
        text = resp.text
        # 剥离 JSONP callback: jQuery({...});
        m = re.search(r"jQuery\((.*)\)", text, re.DOTALL)
        if not m:
            return {"data": [], "query": q, "error": "搜索接口返回格式异常"}
        data = json.loads(m.group(1))
        datas = data.get("Datas", [])
        result: list[dict] = []
        for item in datas:
            code = _safe_str(item.get("CODE", ""))
            name = _safe_str(item.get("NAME", ""))
            base_info = item.get("FundBaseInfo") or {}
            fund_type = _safe_str(base_info.get("FTYPE", ""))
            if not code or not name:
                continue
            result.append({
                "code": code,
                "name": name,
                "type": fund_type,
            })
        return {"data": result[:20], "query": q, "count": len(result)}
    except Exception as e:
        logger.warning(f"fund search failed: {e}")
        return {"data": [], "query": q, "error": str(e)}


# ============ 基金实时行情聚合 ============

@router.get("/realtime")
async def fund_realtime(codes: str = Query(..., description="基金代码,逗号分隔")):
    """基金实时行情(腾讯接口,支持批量)。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {"data": []}
    quotes = await asyncio.to_thread(get_fund_realtime_tencent, code_list)
    return {"data": quotes}


# ============ 基金历史净值 ============

@router.get("/history")
async def fund_history(code: str = Query(...), period: str = Query("1y")):
    """基金历史净值。"""
    data = await asyncio.to_thread(get_fund_history_tencent, code, period)
    return {"data": data, "code": code, "period": period}

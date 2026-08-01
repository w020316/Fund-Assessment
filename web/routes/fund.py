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
import re
import time
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from src.core.cache import DataCache
from src.core.data_source_v2 import (
    get_fund_history_tencent,
    get_fund_realtime_tencent,
)
from src.utils.auth import require_admin
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str
from src.utils.file_io import atomic_write_json, safe_read_json

router = APIRouter()

# P2-4 修复(2026-07-30):为 fund GET 端点添加进程内短缓存,
# 避免前端轮询/重复点击触发后端重复请求东方财富/腾讯接口(被反爬封禁风险)。
# TTL 策略:search 60s(基金列表变化慢),realtime 10s(实时性优先),
#          history 300s(历史净值日级更新,5分钟足够)。
_fund_cache = DataCache(default_ttl=60)

_FUND_POSITIONS_FILE = Path(__file__).resolve().parent.parent / "user_fund_positions.json"

# 默认空持仓(不硬编码示例数据,用户自行添加)


# ============ 数据模型 ============

class FundPosition(BaseModel):
    # P0 修复(2026-07-30):shares/cost_nav 增加 ge=0 校验,防止负数持仓
    fund_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$", description="基金代码(6位数字)")
    fund_name: str = Field("", max_length=50, description="基金名称")
    shares: float = Field(0.0, ge=0, description="持有份额,不能为负")
    cost_nav: float = Field(0.0, ge=0, description="成本净值,不能为负")
    buy_date: str = Field("", description="买入日期 YYYY-MM-DD")
    note: str = Field("", max_length=200, description="备注")


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
    # P0 修复(2026-07-30):改用 safe_read_json,主文件损坏时自动回退 .bak 备份
    data = safe_read_json(_FUND_POSITIONS_FILE, default={"positions": []})
    if data is None:
        return []
    return data.get("positions", []) if isinstance(data, dict) else []


def _save_positions(positions: list[dict]) -> bool:
    # P0 修复(2026-07-30):改用 atomic_write_json
    # 1. 写 .tmp + fsync 落盘 → os.replace 原子替换,防止崩溃时数据损坏
    # 2. 写入前自动备份到 .bak,损坏时可恢复
    # 3. 路径粒度 threading.Lock 保护并发写
    # P1 修复(2026-07-30):原代码 except Exception 后 logger.warning 但仍返回 None,
    # 调用方 save_positions/add_position/delete_position 仍返回 {"success": True},
    # 用户以为保存成功但实际数据丢失,无法追溯。改为返回 bool,调用方按结果返回正确 success。
    try:
        atomic_write_json(_FUND_POSITIONS_FILE, {"positions": positions})
        return True
    except Exception as e:
        logger.error(f"save fund positions failed: {e}")
        return False


@router.get("/positions", dependencies=[Depends(require_admin)])
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
    # 修复(2026-07-29): 原代码在 change_pct==0 时误返总盈亏(pnl_amount)
    # 正确逻辑:当日盈亏 = 当前市值 - 昨日市值 = 当前市值 * change_pct / (100 + change_pct)
    today_pnl = 0.0
    for e in enriched:
        if e["current_nav"] <= 0 or e["change_pct"] == 0:
            continue
        # 当日盈亏 = 市值 - 市值/(1+涨幅)
        today_pnl += e["market_value"] - e["market_value"] / (1 + e["change_pct"] / 100)

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
    # P1 修复(2026-07-30):根据 _save_positions 真实结果返回 success,避免静默吞异常误导用户
    ok = _save_positions(positions)
    return {"success": ok, "message": f"已保存 {len(positions)} 只基金持仓" if ok else "保存失败,请查看日志或重试"}


class AddPositionRequest(BaseModel):
    # P0 修复(2026-07-30):同 FundPosition,增加 ge=0 与代码格式校验
    fund_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    fund_name: str = Field("", max_length=50)
    shares: float = Field(0.0, ge=0)
    cost_nav: float = Field(0.0, ge=0)
    buy_date: str = Field("")
    note: str = Field("", max_length=200)


@router.post("/positions/add", dependencies=[Depends(require_admin)])
async def add_position(req: AddPositionRequest):
    """单只基金持仓添加(增量)。"""
    positions = _load_positions()
    # 同代码覆盖
    positions = [p for p in positions if p.get("fund_code") != req.fund_code]
    positions.append(req.model_dump())
    ok = _save_positions(positions)
    return {"success": ok, "message": f"已添加 {req.fund_code}" if ok else "添加失败,请重试"}


@router.delete("/positions/{fund_code}", dependencies=[Depends(require_admin)])
async def delete_position(fund_code: str):
    """删除指定基金持仓。"""
    positions = _load_positions()
    before = len(positions)
    positions = [p for p in positions if p.get("fund_code") != fund_code]
    removed = before - len(positions)
    if removed == 0:
        return {"success": False, "message": f"未找到 {fund_code}"}
    ok = _save_positions(positions)
    return {"success": ok, "message": f"已删除 {fund_code}" if ok else "删除失败,请重试"}


# ============ 基金建议 ============

@router.get("/advice")
async def get_advice():
    """获取基金建议(基于大盘/板块/盈亏的规则引擎)。"""
    try:
        from src.analysis.fund_advisor import generate_fund_advice
        positions = _load_positions()
        result = await asyncio.to_thread(generate_fund_advice, positions)
        return result
    except Exception as e:
        logger.warning(f"get_advice failed: {e}")
        return {"error": "获取基金建议失败,请稍后重试", "advice": None}


@router.get("/advice-v2")
async def get_advice_v2():
    """获取基金建议v2(五信号融合:技术面/基本面/消息面/重仓股板块/大盘环境)。"""
    try:
        from src.analysis.fund_advisor_v2 import generate_fund_advice_v2
        positions = _load_positions()
        result = await generate_fund_advice_v2(positions)
        return result
    except Exception as e:
        logger.warning(f"get_advice_v2 failed: {e}")
        return {"error": "获取基金建议失败,请稍后重试", "signals": None}


# ============ 基金搜索 ============

@router.get("/search")
async def search_fund(q: str = Query(..., min_length=1, description="基金代码或名称关键词")):
    """按名称/代码模糊搜索基金(数据源:东方财富基金搜索)。"""
    if not q.strip():
        return {"data": [], "query": q}

    # P2-4 修复(2026-07-30):搜索结果缓存 60s,避免重复请求东方财富
    cache_key = f"fund_search:{q.strip()}"
    cached = _fund_cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "query": q, "_meta": {"cached": True}}

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
        # P2-4 修复:命中缓存(仅缓存非空结果,空结果不缓存避免错误持久化)
        if result:
            _fund_cache.set(cache_key, result[:20], ttl=60)
        return {"data": result[:20], "query": q, "count": len(result), "_meta": {"cached": False}}
    except Exception as e:
        logger.warning(f"fund search failed: {e}")
        return {"data": [], "query": q, "error": "搜索失败,请稍后重试"}


# ============ 基金实时行情聚合 ============

@router.get("/realtime")
async def fund_realtime(codes: str = Query(..., description="基金代码,逗号分隔")):
    """基金实时行情(腾讯接口,支持批量)。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {"data": []}
    # P2-4 修复:实时行情缓存 10s(实时性优先,但避免 1s 内多次刷新打爆腾讯接口)
    cache_key = f"fund_realtime:{codes.strip()}"
    cached = _fund_cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": {"cached": True}}
    # P1 修复(2026-07-31):添加 try/except,数据源异常时返回空数据而非 500
    try:
        quotes = await asyncio.to_thread(get_fund_realtime_tencent, code_list)
        if quotes:
            _fund_cache.set(cache_key, quotes, ttl=10)
        return {"data": quotes, "_meta": {"cached": False}}
    except Exception as e:
        logger.warning(f"fund_realtime failed: {e}")
        return {"data": [], "_meta": {"cached": False, "quality_score": 0.0}}


# ============ 基金历史净值 ============

@router.get("/history")
async def fund_history(code: str = Query(...), period: str = Query("1y")):
    """基金历史净值。"""
    # P2-4 修复:历史净值缓存 300s(日级更新数据,5分钟足够,避免重复拉取)
    cache_key = f"fund_history:{code}:{period}"
    cached = _fund_cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "code": code, "period": period, "_meta": {"cached": True}}
    # P1 修复(2026-07-31):添加 try/except,数据源异常时返回空数据而非 500
    try:
        data = await asyncio.to_thread(get_fund_history_tencent, code, period)
        if data:
            _fund_cache.set(cache_key, data, ttl=300)
        return {"data": data, "code": code, "period": period, "_meta": {"cached": False}}
    except Exception as e:
        logger.warning(f"fund_history failed: {e}")
        return {"data": [], "code": code, "period": period, "_meta": {"cached": False, "quality_score": 0.0}}

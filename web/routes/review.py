"""短线复盘 / 出手三问路由

- GET  /api/review/board          复盘看板
- GET  /api/review/sentiment      近 N 日情绪周期
- GET  /api/review/first_board    首板分析
- POST /api/review/three_question 出手三问闸门(只读校验,不涉及资金)
"""
import asyncio
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.analysis.market_review import analyze_first_boards, build_review_board, get_sentiment_cycle
from src.analysis.trade_journal import append_entry, journal_report
from src.core.cache import DataCache
from src.strategies.three_question_gate import check_three_questions
from src.utils.auth import require_admin
from web.rate_limiter import limiter

router = APIRouter()
cache = DataCache(default_ttl=300)

_CODE_RE = re.compile(r"^\d{6}$")


def _meta(cached: bool = False, quality: float | None = None) -> dict:
    return {
        "data_source": "akshare+tencent",
        "cached": cached,
        "quality_score": quality if quality is not None else (100.0 if cached else 80.0),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/board")
async def review_board(
    date: str | None = Query(None, pattern=r"^\d{8}$"),
    refresh: bool = Query(False, description="跳过缓存,强制刷新"),
):
    """短线复盘看板(盘面 + 涨停结构 + 情绪温度 + 次日判断)"""
    key = f"review:board:{date or 'today'}"
    if refresh:
        cache.delete(key)
    cached = cache.get(key)
    if cached is not None:
        return {"data": cached, "_meta": _meta(cached=True)}
    data = await asyncio.to_thread(build_review_board, date)
    ttl = 300 if date is None else 1800
    cache.set(key, data, ttl=ttl)
    return {"data": data, "_meta": _meta(cached=False, quality=90.0 if data.get("available") else 0.0)}


@router.get("/sentiment")
async def review_sentiment(
    days: int = Query(5, ge=2, le=10),
    refresh: bool = Query(False, description="跳过缓存,强制刷新"),
):
    """近 N 个交易日情绪周期"""
    key = f"review:sentiment:{days}"
    if refresh:
        cache.delete(key)
    cached = cache.get(key)
    if cached is not None:
        return {"data": cached, "_meta": _meta(cached=True)}
    data = await asyncio.to_thread(get_sentiment_cycle, days)
    cache.set(key, data, ttl=300)
    return {"data": data, "_meta": _meta(cached=False)}


@router.get("/first_board")
async def review_first_board(
    date: str | None = Query(None, pattern=r"^\d{8}$"),
    top: int = Query(20, ge=1, le=100),
    refresh: bool = Query(False, description="跳过缓存,强制刷新"),
):
    """当日首板分析"""
    key = f"review:first_board:{date or 'today'}:{top}"
    if refresh:
        cache.delete(key)
    cached = cache.get(key)
    if cached is not None:
        return {"data": cached, "_meta": _meta(cached=True)}
    data = await asyncio.to_thread(analyze_first_boards, date, top=top)
    cache.set(key, data, ttl=300 if date is None else 1800)
    return {"data": data, "_meta": _meta(cached=False)}


class ThreeQuestionRequest(BaseModel):
    stock_code: str = Field(..., min_length=6, max_length=6)
    mode: str = Field("auto", pattern="^(auto|manual)$")
    sector: str = ""
    stock_name: str = ""
    prev_close: float | None = None
    break_close: float | None = None
    auction_price: float | None = None
    sector_change_pct: float | None = None
    sector_limit_up_count: int | None = None
    is_sector_leader: bool | None = None


@router.post("/three_question")
@limiter.limit("20/minute")
async def three_question(request: Request, req: ThreeQuestionRequest) -> dict[str, Any]:
    """出手三问:身份 / 断板 / 竞价,全部通过才返回可出手结论"""
    code = req.stock_code.strip()
    if not _CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="股票代码需为 6 位数字")
    try:
        data = await asyncio.to_thread(
            check_three_questions,
            code,
            mode=req.mode,
            sector=req.sector.strip(),
            stock_name=req.stock_name.strip(),
            prev_close=req.prev_close,
            break_close=req.break_close,
            auction_price=req.auction_price,
            sector_change_pct=req.sector_change_pct,
            sector_limit_up_count=req.sector_limit_up_count,
            is_sector_leader=req.is_sector_leader,
        )
    except Exception as e:
        logger.exception(f"three_question {code} failed")
        raise HTTPException(status_code=503, detail=f"三问校验失败:{type(e).__name__}") from e
    return {"data": data, "_meta": _meta(cached=False)}


class JournalEntryRequest(BaseModel):
    action: str = Field(..., pattern="^(buy|sell|abandon|watch)$")
    stock_code: str = Field(..., min_length=6, max_length=6)
    stock_name: str = ""
    price: float | None = None
    quantity: float | None = None
    gate_passed: bool | None = None
    gate_mode: str = ""
    gate_missing: list[str] = Field(default_factory=list)
    gate_failed: list[str] = Field(default_factory=list)
    note: str = ""
    emotion: str = ""
    trade_date: str = ""


@router.post("/journal", dependencies=[Depends(require_admin)])
@limiter.limit("20/minute")
async def add_journal_entry(request: Request, req: JournalEntryRequest) -> dict[str, Any]:
    """记录一条交易日志(买入/卖出/放弃/观察),写入 data/trade_journal.jsonl"""
    try:
        entry = await asyncio.to_thread(append_entry, req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"data": entry, "_meta": _meta(cached=False)}


@router.get("/journal", dependencies=[Depends(require_admin)])
async def get_journal(limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    """交易日志 + 纪律归因统计"""
    report = await asyncio.to_thread(journal_report, None, limit)
    return {"data": report, "_meta": _meta(cached=False)}

"""基金重仓股板块分析路由

端点:
- GET  /api/holdings/{fund_code}    基金重仓股分析(持仓/板块/集中度/净值影响)
- GET  /api/holdings/sector-rotation  板块轮动总览
- POST /api/holdings/upload         上传文件/图片识别持仓(CSV/Excel/图片)
"""
from __future__ import annotations

import asyncio
import io
import os
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from loguru import logger

from src.analysis.fund_holdings import (
    analyze_fund_holdings,
    get_sector_rotation,
)
from src.core.cache import DataCache
from src.utils.auth import require_admin
# limiter 从独立模块导入,避免 import web.api 触发循环 import
from web.rate_limiter import limiter

router = APIRouter()
cache = DataCache(default_ttl=300)


def _build_meta(data_source: str, cached: bool = False) -> dict:
    return {
        "data_source": data_source,
        "quality_score": 100.0 if cached else 80.0,
        "cached": cached,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/{fund_code}")
async def fund_holdings_analysis(
    fund_code: str,
    refresh: bool = Query(False, description="强制刷新(忽略缓存)"),
):
    """基金重仓股板块分析

    返回:
    - holdings: 前十大重仓股
    - sector_exposure: 板块暴露度
    - concentration: 集中度指标(Top5/Top10/HHI)
    - nav_impact: 净值影响预估(基于重仓股当日涨跌)
    - sector_rotation: 板块轮动信号
    """
    if refresh:
        cache.delete(f"holdings:{fund_code}")
    else:
        cache_key = f"holdings:{fund_code}"
        cached = cache.get(cache_key)
        if cached is not None:
            return {"data": cached, "_meta": _build_meta("em_fund+akshare", cached=True)}

    data = await analyze_fund_holdings(fund_code)
    # 缓存5分钟(持仓数据变化慢,但净值影响需刷新)
    cache.set(f"holdings:{fund_code}", data, ttl=300)
    return {"data": data, "_meta": _build_meta("em_fund+akshare", cached=False)}


@router.get("/sector-rotation/overview")
async def sector_rotation_overview():
    """板块轮动总览(全局,非基金特定)"""
    cache_key = "holdings:sector_rotation"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"data": cached, "_meta": _build_meta("em_sector_ranking", cached=True)}
    data = await get_sector_rotation()
    cache.set(cache_key, data, ttl=60)
    return {"data": data, "_meta": _build_meta("em_sector_ranking", cached=False)}


# ===== 文件/图片持仓识别(P1 新功能:上传持仓截图或表格批量导入) =====

_ALLOWED_EXCEL_EXTS = {".xlsx", ".xls"}
_ALLOWED_CSV_EXTS = {".csv"}
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB,Render Free 内存友好

# 列名别名映射(兼容中英文表头,参考常见券商导出格式)
_CODE_ALIASES = {"code", "代码", "基金代码", "股票代码", "证券代码"}
_NAME_ALIASES = {"name", "名称", "基金名称", "股票名称", "证券名称", "简称"}
_WEIGHT_ALIASES = {"weight", "占比", "权重", "比例", "仓位", "持仓比例", "持有比例", "比重"}


def _find_column(cols: list, aliases: set[str]):
    """从列名中查找匹配的别名(忽略大小写)"""
    for c in cols:
        if str(c).strip().lower() in aliases:
            return c
    return None


def _normalize_holdings_df(df) -> list[dict]:
    """将持仓 DataFrame 规范化为统一结构"""
    df.columns = [str(c).strip() for c in df.columns]
    code_col = _find_column(df.columns, _CODE_ALIASES)
    name_col = _find_column(df.columns, _NAME_ALIASES)
    weight_col = _find_column(df.columns, _WEIGHT_ALIASES)
    if not code_col and not name_col:
        return []
    holdings: list[dict] = []
    for _, row in df.iterrows():
        code = str(row[code_col]).strip() if code_col else ""
        name = str(row[name_col]).strip() if name_col else ""
        if not code and not name:
            continue
        # pandas 读 csv 数字 code 可能是 "161725.0",清理小数点
        if code.endswith(".0"):
            code = code[:-2]
        weight = 0.0
        if weight_col:
            try:
                w = row[weight_col]
                if isinstance(w, str):
                    w = w.replace("%", "").replace("，", "").strip()
                weight = float(w)
            except (ValueError, TypeError):
                weight = 0.0
        holdings.append({"code": code, "name": name, "weight": round(weight, 3)})
    return holdings


def _parse_csv_holdings(contents: bytes) -> list[dict]:
    """解析 CSV 持仓文件"""
    try:
        df = pd.read_csv(io.BytesIO(contents), dtype=str)
    except Exception as e:
        logger.warning(f"_parse_csv_holdings failed: {e}")
        return []
    return _normalize_holdings_df(df)


def _parse_excel_holdings(contents: bytes) -> list[dict]:
    """解析 Excel 持仓文件(需 openpyxl)"""
    try:
        df = pd.read_excel(io.BytesIO(contents), dtype=str)
    except Exception as e:
        logger.warning(f"_parse_excel_holdings failed: {e}")
        return []
    return _normalize_holdings_df(df)


@router.post("/upload", dependencies=[Depends(require_admin)])
@limiter.limit("3/minute")
async def upload_holdings_file(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """上传文件/图片识别持仓

    支持:
    - CSV/Excel 文件: 直接解析表格列(代码/名称/占比)
    - 图片(png/jpg/webp): 调用 agnes-2.0-flash 多模态 OCR 识别持仓截图

    限流: 3次/分钟/客户端(保护 LLM vision 调用,借鉴 la-deps/slowapi)
    鉴权: 需管理员 token(写操作,避免匿名调用消耗免费 vision 额度)
    返回: {holdings: [{code, name, weight}], source: "csv"|"excel"|"image", count: N}
    """
    contents = await file.read()
    if len(contents) > _MAX_FILE_SIZE:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "文件过大,最大10MB(Render Free 内存友好)",
        )
    filename = (file.filename or "").lower()
    ext = os.path.splitext(filename)[1]

    if ext in _ALLOWED_CSV_EXTS:
        holdings = await asyncio.to_thread(_parse_csv_holdings, contents)
        source = "csv"
    elif ext in _ALLOWED_EXCEL_EXTS:
        holdings = await asyncio.to_thread(_parse_excel_holdings, contents)
        source = "excel"
    elif ext in _ALLOWED_IMAGE_EXTS:
        from src.core.ai_service import recognize_holdings_from_image
        mime = file.content_type or "image/png"
        holdings = await asyncio.to_thread(recognize_holdings_from_image, contents, mime)
        source = "image"
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"不支持的文件类型: {ext},支持 csv/xlsx/xls/png/jpg/webp",
        )

    if not holdings:
        return {
            "holdings": [],
            "source": source,
            "count": 0,
            "message": "未能识别到持仓数据,请检查文件格式或截图清晰度",
        }

    return {"holdings": holdings, "source": source, "count": len(holdings)}

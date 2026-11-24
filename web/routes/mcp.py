"""最小可用的 MCP（Model Context Protocol）Streamable HTTP 端点。

只读：把已有的行情/板块/K线/缠论/信号/股东/复盘能力暴露给 AI Agent。
- 不写入、不调用 LLM、不涉及交易；
- 工具入参做白名单与范围校验，错误以 MCP isError 返回，不回传堆栈；
- 限流 60 次/分钟，避免被当成免费行情代理刷爆。

公开端点声明（S9-03, 2026-11-24）：/mcp 为刻意匿名开放的只读端点——
7 个工具仅返回公开市场数据（指数/板块/K线/缠论/信号/十大流通股东/复盘），
不含用户持仓、交易、策略参数等敏感数据，因此不挂 require_admin；
它不受「未配令牌 fail-closed 503」模型约束（那是针对敏感数据的防线）。
若未来新增任何触及用户数据的工具，必须先挂鉴权再上线。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from loguru import logger

from web.rate_limiter import limiter

router = APIRouter()

PROTOCOL_VERSION = "2025-03-26"
_MAX_COUNT = 250


def _version() -> str:
    return (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "1.0.0")[:7]


def _code(args: dict[str, Any]) -> str:
    code = str(args.get("code", "")).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("code 需为 6 位数字")
    return code


def _period(args: dict[str, Any]) -> str:
    period = str(args.get("period", "daily")).strip()
    if period not in ("daily", "weekly", "monthly"):
        raise ValueError("period 仅支持 daily / weekly / monthly")
    return period


def _count(args: dict[str, Any], default: int = 120) -> int:
    raw = args.get("count", default)
    try:
        n = int(raw)
    except (TypeError, ValueError) as e:
        raise ValueError("count 需为整数") from e
    if n < 30 or n > _MAX_COUNT:
        raise ValueError(f"count 范围 30-{_MAX_COUNT}")
    return n


def _index_realtime(_args: dict[str, Any]) -> dict[str, Any]:
    from src.core import data_source_v2 as ds2

    data = ds2.get_index_realtime() or []
    return {"data": data[:20], "source": "tencent", "note": "指数实时行情；缺失字段为 null，不补 0。"}


def _sector_flow(_args: dict[str, Any]) -> dict[str, Any]:
    from src.core import data_source_v2 as ds2

    rows = ds2.get_sector_ranking() or []
    data = [
        {
            "name": str(r.get("name", "")),
            "code": str(r.get("code", "")),
            "change_pct": r.get("change_pct"),
            "main_net_inflow": r.get("main_net_inflow"),
        }
        for r in rows[:60]
    ]
    return {"data": data, "source": "eastmoney/sina", "note": "板块涨跌幅与主力净流入；缺失为 null。"}


def _stock_kline(args: dict[str, Any]) -> dict[str, Any]:
    from src.core import data_source_v2 as ds2

    code = _code(args)
    period = _period(args)
    count = _count(args)
    rows = ds2.get_kline_mootdx(code, period=period, count=count) or []
    return {"code": code, "period": period, "count": len(rows), "data": rows[-count:],
            "note": "K 线未复权，缺失行已剔除，不补 0。"}


def _chanlun(args: dict[str, Any]) -> dict[str, Any]:
    from src.analysis.chanlun import analyze_chanlun
    from src.core import data_source_v2 as ds2

    code = _code(args)
    period = _period(args)
    rows = ds2.get_kline_mootdx(code, period=period, count=180) or []
    out = analyze_chanlun(rows, code=code, period=period)
    if isinstance(out.get("bars"), list):
        out["bars"] = out["bars"][-60:]  # MCP 响应控制体积
    return out


def _signals(args: dict[str, Any]) -> dict[str, Any]:
    from src.analysis.signal_scan import scan_signals
    from src.core import data_source_v2 as ds2

    code = _code(args)
    period = _period(args)
    rows = ds2.get_kline_mootdx(code, period=period, count=180) or []
    return scan_signals(rows, code=code, period=period)


def _holdings(args: dict[str, Any]) -> dict[str, Any]:
    from src.analysis.institution_holdings import analyze_holders
    from src.core import data_source_v2 as ds2

    code = _code(args)
    return analyze_holders(ds2.get_top10_free_holders(code))


def _review_board(_args: dict[str, Any]) -> dict[str, Any]:
    from src.analysis.market_review import build_review_board

    data = build_review_board(None) or {}
    keys = ("date", "available", "data_quality", "market", "limit", "temperature", "tonight", "review")
    return {k: data.get(k) for k in keys if k in data}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_index_realtime",
        "description": "A 股核心指数实时行情（上证/深证/创业板/科创等）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _index_realtime,
    },
    {
        "name": "get_sector_flow",
        "description": "行业板块涨跌幅与主力净流入排行（最多 60 个板块）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _sector_flow,
    },
    {
        "name": "get_stock_kline",
        "description": "个股历史 K 线（未复权）；缺失行剔除，不补 0。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6 位股票代码，如 600519"},
                "period": {"type": "string", "enum": ["daily", "weekly", "monthly"], "default": "daily"},
                "count": {"type": "integer", "minimum": 30, "maximum": _MAX_COUNT, "default": 120},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "handler": _stock_kline,
    },
    {
        "name": "get_chanlun_structure",
        "description": "简化缠论结构：分型/笔/中枢/MACD 面积背驰/买卖点观察（未实现线段与级别递归）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6 位股票代码"},
                "period": {"type": "string", "enum": ["daily", "weekly", "monthly"], "default": "daily"},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "handler": _chanlun,
    },
    {
        "name": "scan_stock_signals",
        "description": "个股多信号扫描：均线/MACD/RSI/KDJ/布林/量能/新高新低，strength 为规则权重。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6 位股票代码"},
                "period": {"type": "string", "enum": ["daily", "weekly", "monthly"], "default": "daily"},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        "handler": _signals,
    },
    {
        "name": "get_institution_holdings",
        "description": "十大流通股东归类：国家队/机构/外资/产业资本/个人/其他及变动汇总。",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "6 位股票代码"}},
            "required": ["code"],
            "additionalProperties": False,
        },
        "handler": _holdings,
    },
    {
        "name": "get_review_board",
        "description": "短线复盘看板：涨跌家数、涨跌停、情绪温度、次日规则判断。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _review_board,
    },
]

_TOOL_MAP: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    t["name"]: t["handler"] for t in TOOLS
}


def _public_tools() -> list[dict[str, Any]]:
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]


def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = _TOOL_MAP[name]
    try:
        payload = handler(args or {})
        text = json.dumps({"ok": True, "tool": name, "data": payload}, ensure_ascii=False, default=str)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except ValueError as e:
        # ValueError 只由本模块的入参校验(_code/_period/_count)抛出,文案自产可回传
        logger.warning(f"mcp tool {name} invalid args: {e}")
        msg = str(e) or "参数不合法"
        text = json.dumps({"ok": False, "tool": name, "error": msg}, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": True}
    except Exception as e:  # noqa: BLE001 — 工具错误按 MCP 约定返回 isError
        # 上游/未知异常不回传原文(S9-11):str(e) 可能含内部路径与上游 URL
        logger.warning(f"mcp tool {name} failed: {e}")
        text = json.dumps(
            {"ok": False, "tool": name, "error": "工具执行失败：上游数据暂不可用，请稍后重试。"},
            ensure_ascii=False,
        )
        return {"content": [{"type": "text", "text": text}], "isError": True}


def _rpc_error(req_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
    )


@router.post("/mcp")
@limiter.limit("60/minute")
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
        )
    if isinstance(body, list):
        return _rpc_error(None, -32600, "Batch requests are not supported")

    method = body.get("method")
    req_id = body.get("id")
    is_notification = "id" not in body

    if is_notification and method != "initialize":
        # notifications/initialized 等通知按规范返回 202，无响应体
        return Response(status_code=202)

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "quantflow-pro", "version": _version()},
            "instructions": "只读 A 股数据：指数、板块、K线、缠论、信号、十大流通股东、短线复盘。不提供下单与交易。",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _public_tools()}
    elif method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or name not in _TOOL_MAP:
            return _rpc_error(req_id, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _rpc_error(req_id, -32602, "arguments must be an object")
        result = await asyncio.to_thread(_call_tool, name, args)
    else:
        return _rpc_error(req_id, -32601, f"Method not found: {method}")

    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": req_id, "result": result},
    )


@router.get("/mcp")
async def mcp_get():
    """Streamable HTTP 允许服务端拒绝 SSE 长连接；这里只接受 POST JSON-RPC。"""
    return JSONResponse(
        status_code=405,
        content={"error": "Use POST with JSON-RPC 2.0; SSE stream is not provided."},
        headers={"Allow": "POST"},
    )

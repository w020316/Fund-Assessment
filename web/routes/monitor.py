from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger
from pydantic import BaseModel

from src.core import data_source_v2 as ds2
from src.utils.auth import require_admin
from src.utils.convert import safe_float as _safe_float

router = APIRouter()

_HAS_MONITOR = False
try:
    from src.analysis.capital_flow import analyze_capital_flow
    from src.strategies.stock_monitor import AlertType, StockMonitor
    _HAS_MONITOR = True
except ImportError as e:
    logger.warning(f"import monitor modules failed: {e}")


class AlertItem(BaseModel):
    stock_code: str
    alert_type: str
    severity: str
    message: str
    detail: dict[str, Any]


class WatchlistItem(BaseModel):
    stock_code: str
    rules: list[str]


class AddWatchlistRequest(BaseModel):
    stock_code: str
    rules: list[str] | None = None


class MessageResponse(BaseModel):
    success: bool
    message: str


class CapitalFlowResponse(BaseModel):
    main_net_inflow: float
    large_order_ratio: float
    medium_order_ratio: float
    small_order_ratio: float
    northbound_change: float
    # P2-5 修复(2026-07-30):新增 data_quality 字段,
    # 标记资金流数据是否因接口失败而用 0 兜底,避免用户误以为"无主力资金动向"。
    data_quality: str = "normal"  # normal | degraded


class NorthboundResponse(BaseModel):
    total_net_inflow: float
    sh_net_inflow: float
    sz_net_inflow: float
    top_stocks: list[dict[str, Any]]
    # P2-5 修复(2026-07-30):同上,标记北向资金数据是否降级
    data_quality: str = "normal"  # normal | degraded


# 自选股持久化(文件存储,避免内存 mock)
_WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "..", "user_watchlist.json")


def _load_watchlist() -> dict[str, list[str]]:
    """读取自选股规则,文件缺失时返回空 dict"""
    if os.path.exists(_WATCHLIST_FILE):
        try:
            with open(_WATCHLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"load watchlist failed: {e}")
    return {}


def _save_watchlist(data: dict[str, list[str]]) -> None:
    """持久化自选股规则"""
    try:
        with open(_WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"save watchlist failed: {e}")


@router.get("/alerts", response_model=list[AlertItem])
async def alerts(stock_code: str = ""):
    if not stock_code:
        return await _generate_default_alerts()
    if not _HAS_MONITOR:
        return [AlertItem(
            stock_code=stock_code, alert_type="price_surge",
            severity="info",
            message=f"{stock_code} 暂无监控数据",
            detail={"change_pct": 0},
        )]
    try:
        monitor = StockMonitor()
        results = await asyncio.to_thread(monitor.check_alerts, stock_code)
        return [AlertItem(
            stock_code=a["stock_code"], alert_type=a["alert_type"],
            severity=a["severity"], message=a["message"],
            detail=a.get("detail", {}),
        ) for a in results]
    except Exception as e:
        # P1 修复(2026-07-30):原静默返回空列表,用户以为"无告警"但实际是系统故障。
        # 改为日志记录 + 返回错误告警项,前端可明确区分"无告警"与"获取失败"。
        logger.warning(f"check_alerts {stock_code} failed: {e}")
        return [AlertItem(
            stock_code=stock_code, alert_type="system_error",
            severity="warning",
            message=f"{stock_code} 告警检查失败,请稍后重试",
            detail={"error": str(e)},
        )]


async def _generate_default_alerts() -> list[AlertItem]:
    alert_items: list[AlertItem] = []
    watchlist_data = _load_watchlist()
    watchlist_codes = list(watchlist_data.keys())
    if not watchlist_codes:
        return [AlertItem(
            stock_code="", alert_type="market_status",
            severity="info",
            message="暂无自选股,请先添加自选股",
            detail={},
        )]
    try:
        quotes = await asyncio.to_thread(ds2.get_realtime_quote_tencent, watchlist_codes)
    except Exception as e:
        # P1 修复(2026-07-30):记录日志,避免静默掩盖接口故障
        logger.warning(f"_generate_default_alerts: 行情接口失败,告警检查降级: {e}")
        quotes = []
    quote_map = {q.get("code", ""): q for q in quotes}
    for code in watchlist_codes:
        q = quote_map.get(code, {})
        change_pct = 0.0
        try:
            change_pct = float(q.get("change_pct", 0))
        except (ValueError, TypeError) as e:
            logger.warning(f"parse change_pct failed: {e}")
        name = q.get("name", code)
        if abs(change_pct) >= 5:
            alert_items.append(AlertItem(
                stock_code=code, alert_type="price_surge",
                severity="critical" if abs(change_pct) >= 8 else "warning",
                message=f"{name}({code}) 涨跌幅 {change_pct:.2f}%，波动异常",
                detail={"change_pct": round(change_pct, 2), "price": float(q.get("price", 0))},
            ))
        elif abs(change_pct) >= 3:
            alert_items.append(AlertItem(
                stock_code=code, alert_type="price_alert",
                severity="info",
                message=f"{name}({code}) 涨跌幅 {change_pct:.2f}%，需关注",
                detail={"change_pct": round(change_pct, 2), "price": float(q.get("price", 0))},
            ))
    try:
        index_data = await asyncio.to_thread(ds2.get_index_realtime)
        for idx in index_data:
            idx_change = 0.0
            try:
                idx_change = float(idx.get("change_pct", 0))
            except (ValueError, TypeError) as e:
                logger.warning(f"parse index change_pct failed: {e}")
            idx_name = idx.get("name", "")
            idx_code = idx.get("code", "")
            if abs(idx_change) >= 2:
                alert_items.append(AlertItem(
                    stock_code=idx_code, alert_type="market_alert",
                    severity="warning",
                    message=f"大盘指数 {idx_name} 涨跌幅 {idx_change:.2f}%，市场波动较大",
                    detail={"change_pct": round(idx_change, 2)},
                ))
    except Exception as e:
        logger.warning(f"fetch index_realtime for alerts failed: {e}")
    if not alert_items:
        alert_items.append(AlertItem(
            stock_code="000001", alert_type="market_status",
            severity="info",
            message="当前市场运行平稳，暂无异常警报",
            detail={},
        ))
    return alert_items


@router.get("/watchlist", response_model=list[WatchlistItem], dependencies=[Depends(require_admin)])
async def watchlist():
    watchlist_data = _load_watchlist()
    return [WatchlistItem(stock_code=code, rules=rules)
            for code, rules in watchlist_data.items()]


@router.post("/watchlist", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def add_watchlist(req: AddWatchlistRequest):
    watchlist_data = _load_watchlist()
    watchlist_data[req.stock_code] = req.rules or ["price_surge"]
    _save_watchlist(watchlist_data)
    return MessageResponse(success=True, message=f"已添加 {req.stock_code} 到自选")


@router.delete("/watchlist/{stock_code}", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def remove_watchlist(stock_code: str):
    watchlist_data = _load_watchlist()
    if stock_code in watchlist_data:
        watchlist_data.pop(stock_code, None)
        _save_watchlist(watchlist_data)
        return MessageResponse(success=True, message=f"已移除 {stock_code}")
    return MessageResponse(success=False, message=f"{stock_code} 不在自选列表")


@router.get("/capital_flow", response_model=CapitalFlowResponse)
async def capital_flow(stock_code: str = ""):
    # P2-5 修复(2026-07-30):跟踪数据获取是否失败,失败时标记 degraded
    northbound_change = 0.0
    nb_degraded = False
    try:
        nb_data = await asyncio.to_thread(ds2.get_northbound_flow_realtime)
        if nb_data:
            total_nb = _safe_float(nb_data.get("total_net_inflow", 0))
            northbound_change = total_nb / 1e8 if total_nb != 0 else 0.0
        else:
            nb_degraded = True
    except Exception as e:
        logger.warning(f"fetch northbound_flow_realtime failed: {e}")
        nb_degraded = True
    if not stock_code:
        return CapitalFlowResponse(
            main_net_inflow=0, large_order_ratio=0, medium_order_ratio=0,
            small_order_ratio=0, northbound_change=round(northbound_change, 2),
            data_quality="degraded" if nb_degraded else "normal",
        )
    try:
        data = await asyncio.to_thread(ds2.get_capital_flow_detail, stock_code)
        if data:
            main_net = _safe_float(data.get("main_net_inflow", 0))
            large_net = _safe_float(data.get("large_net_inflow", 0))
            super_large_net = _safe_float(data.get("super_large_net_inflow", 0))
            medium_net = _safe_float(data.get("medium_net_inflow", 0))
            small_net = _safe_float(data.get("small_net_inflow", 0))
            total_amount = abs(large_net) + abs(super_large_net) + abs(medium_net) + abs(small_net)
            large_ratio = (large_net + super_large_net) / total_amount * 100 if total_amount > 0 else 0
            medium_ratio = medium_net / total_amount * 100 if total_amount > 0 else 0
            small_ratio = small_net / total_amount * 100 if total_amount > 0 else 0
            return CapitalFlowResponse(
                main_net_inflow=round(main_net, 0),
                large_order_ratio=round(large_ratio, 2),
                medium_order_ratio=round(medium_ratio, 2),
                small_order_ratio=round(small_ratio, 2),
                northbound_change=round(northbound_change, 2),
                data_quality="degraded" if nb_degraded else "normal",
            )
    except Exception as e:
        logger.warning(f"fetch capital_flow_detail failed: {e}")
        # capital_flow_detail 失败也算降级(主力资金明细缺失)
        return CapitalFlowResponse(
            main_net_inflow=0, large_order_ratio=0, medium_order_ratio=0,
            small_order_ratio=0, northbound_change=round(northbound_change, 2),
            data_quality="degraded",
        )


@router.get("/northbound", response_model=NorthboundResponse)
async def northbound():
    # P2-5 修复(2026-07-30):原代码失败时静默返回 0,用户误以为"北向无流入"。
    # 改为标记 data_quality=degraded,前端据此显示"数据源不可达"提示。
    try:
        data = await asyncio.to_thread(ds2.get_northbound_flow_realtime)
        if data:
            top_stocks_raw = data.get("top_stocks", [])
            top_stocks = []
            if isinstance(top_stocks_raw, list):
                for s in top_stocks_raw:
                    if isinstance(s, dict):
                        top_stocks.append({str(k): v for k, v in s.items()})
            return NorthboundResponse(
                total_net_inflow=_safe_float(data.get("total_net_inflow", 0)),
                sh_net_inflow=_safe_float(data.get("sh_net_inflow", 0)),
                sz_net_inflow=_safe_float(data.get("sz_net_inflow", 0)),
                top_stocks=top_stocks,
            )
    except Exception as e:
        logger.warning(f"fetch northbound_flow_realtime failed: {e}")
    if not _HAS_MONITOR:
        return NorthboundResponse(
            total_net_inflow=0,
            sh_net_inflow=0,
            sz_net_inflow=0,
            top_stocks=[],
            data_quality="degraded",
        )
    try:
        from src.strategies.trading_quant import TradingQuant
        quant = TradingQuant()
        result = quant.northbound_flow()
        top_stocks_raw = result.get("top_stocks", [])
        top_stocks = []
        if isinstance(top_stocks_raw, list):
            for s in top_stocks_raw:
                if isinstance(s, dict):
                    top_stocks.append({str(k): v for k, v in s.items()})
        return NorthboundResponse(
            total_net_inflow=_safe_float(result.get("total_net_inflow", 0)),
            sh_net_inflow=_safe_float(result.get("sh_net_inflow", 0)),
            sz_net_inflow=_safe_float(result.get("sz_net_inflow", 0)),
            top_stocks=top_stocks,
        )
    except Exception:
        return NorthboundResponse(
            total_net_inflow=0, sh_net_inflow=0, sz_net_inflow=0, top_stocks=[],
            data_quality="degraded",
        )

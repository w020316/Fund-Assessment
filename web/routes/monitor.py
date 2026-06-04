from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel

from src.core import data_source_v2 as ds2

router = APIRouter()

_HAS_MONITOR = False
try:
    from src.analysis.capital_flow import analyze_capital_flow
    from src.strategies.stock_monitor import AlertType, StockMonitor
    _HAS_MONITOR = True
except ImportError:
    pass


def _safe_float(val: object, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


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


class NorthboundResponse(BaseModel):
    total_net_inflow: float
    sh_net_inflow: float
    sz_net_inflow: float
    top_stocks: list[dict[str, Any]]


_mock_watchlist: dict[str, list[str]] = {
    "000001": ["price_surge", "rsi"],
    "600519": ["northbound_flow", "volume_price"],
}


@router.get("/alerts", response_model=list[AlertItem])
async def alerts(stock_code: str = ""):
    if not stock_code:
        return _generate_default_alerts()
    if not _HAS_MONITOR:
        return [AlertItem(
            stock_code=stock_code, alert_type="price_surge",
            severity="info",
            message=f"{stock_code} 暂无监控数据",
            detail={"change_pct": 0},
        )]
    try:
        monitor = StockMonitor()
        results = monitor.check_alerts(stock_code)
        return [AlertItem(
            stock_code=a["stock_code"], alert_type=a["alert_type"],
            severity=a["severity"], message=a["message"],
            detail=a.get("detail", {}),
        ) for a in results]
    except Exception:
        return []


def _generate_default_alerts() -> list[AlertItem]:
    alert_items: list[AlertItem] = []
    watchlist_codes = list(_mock_watchlist.keys())
    if not watchlist_codes:
        watchlist_codes = ["000001", "600519"]
    try:
        quotes = ds2.get_realtime_quote_tencent(watchlist_codes)
    except Exception:
        quotes = []
    quote_map = {q.get("code", ""): q for q in quotes}
    for code in watchlist_codes:
        q = quote_map.get(code, {})
        change_pct = 0.0
        try:
            change_pct = float(q.get("change_pct", 0))
        except (ValueError, TypeError):
            pass
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
        index_data = ds2.get_index_realtime()
        for idx in index_data:
            idx_change = 0.0
            try:
                idx_change = float(idx.get("change_pct", 0))
            except (ValueError, TypeError):
                pass
            idx_name = idx.get("name", "")
            idx_code = idx.get("code", "")
            if abs(idx_change) >= 2:
                alert_items.append(AlertItem(
                    stock_code=idx_code, alert_type="market_alert",
                    severity="warning",
                    message=f"大盘指数 {idx_name} 涨跌幅 {idx_change:.2f}%，市场波动较大",
                    detail={"change_pct": round(idx_change, 2)},
                ))
    except Exception:
        pass
    if not alert_items:
        alert_items.append(AlertItem(
            stock_code="000001", alert_type="market_status",
            severity="info",
            message="当前市场运行平稳，暂无异常警报",
            detail={},
        ))
    return alert_items


@router.get("/watchlist", response_model=list[WatchlistItem])
async def watchlist():
    if not _HAS_MONITOR:
        return [WatchlistItem(stock_code=code, rules=rules)
                for code, rules in _mock_watchlist.items()]
    try:
        monitor = StockMonitor()
        items: list[WatchlistItem] = []
        for code, rules in monitor._watchlist.items():
            items.append(WatchlistItem(stock_code=code, rules=[r.value for r in rules]))
        return items
    except Exception:
        return [WatchlistItem(stock_code=code, rules=rules)
                for code, rules in _mock_watchlist.items()]


@router.post("/watchlist", response_model=MessageResponse)
async def add_watchlist(req: AddWatchlistRequest):
    if not _HAS_MONITOR:
        _mock_watchlist[req.stock_code] = req.rules or ["price_surge"]
        return MessageResponse(success=True, message=f"已添加 {req.stock_code} 到自选（模拟）")
    try:
        monitor = StockMonitor()
        rules = None
        if req.rules:
            rule_map = {r.value: r for r in AlertType}
            rules = [rule_map[r] for r in req.rules if r in rule_map]
        monitor.add_watch(req.stock_code, rules)
        return MessageResponse(success=True, message=f"已添加 {req.stock_code} 到自选")
    except Exception:
        return MessageResponse(success=False, message="添加失败")


@router.delete("/watchlist/{stock_code}", response_model=MessageResponse)
async def remove_watchlist(stock_code: str):
    if not _HAS_MONITOR:
        _mock_watchlist.pop(stock_code, None)
        return MessageResponse(success=True, message=f"已移除 {stock_code}（模拟）")
    try:
        monitor = StockMonitor()
        monitor.remove_watch(stock_code)
        return MessageResponse(success=True, message=f"已移除 {stock_code}")
    except Exception:
        return MessageResponse(success=False, message="移除失败")


class RemoveWatchlistBodyRequest(BaseModel):
    stock_code: str


@router.delete("/watchlist", response_model=MessageResponse)
async def remove_watchlist_by_body(req: RemoveWatchlistBodyRequest):
    stock_code = req.stock_code
    if not _HAS_MONITOR:
        _mock_watchlist.pop(stock_code, None)
        return MessageResponse(success=True, message=f"已移除 {stock_code}（模拟）")
    try:
        monitor = StockMonitor()
        monitor.remove_watch(stock_code)
        return MessageResponse(success=True, message=f"已移除 {stock_code}")
    except Exception:
        return MessageResponse(success=False, message="移除失败")


@router.get("/capital_flow", response_model=CapitalFlowResponse)
async def capital_flow(stock_code: str = ""):
    northbound_change = 0.0
    try:
        nb_data = ds2.get_northbound_flow_realtime()
        if nb_data:
            total_nb = _safe_float(nb_data.get("total_net_inflow", 0))
            northbound_change = total_nb / 1e8 if total_nb != 0 else 0.0
    except Exception:
        pass
    if not stock_code:
        return CapitalFlowResponse(
            main_net_inflow=0, large_order_ratio=0, medium_order_ratio=0,
            small_order_ratio=0, northbound_change=round(northbound_change, 2),
        )
    try:
        data = ds2.get_capital_flow_detail(stock_code)
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
            )
    except Exception:
        pass
    return CapitalFlowResponse(
        main_net_inflow=0, large_order_ratio=0, medium_order_ratio=0,
        small_order_ratio=0, northbound_change=round(northbound_change, 2),
    )


@router.get("/northbound", response_model=NorthboundResponse)
async def northbound():
    try:
        data = ds2.get_northbound_flow_realtime()
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
    except Exception:
        pass
    if not _HAS_MONITOR:
        return NorthboundResponse(
            total_net_inflow=0,
            sh_net_inflow=0,
            sz_net_inflow=0,
            top_stocks=[],
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
        )

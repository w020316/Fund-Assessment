from __future__ import annotations

import asyncio
from pathlib import Path

from typing import Any

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel

from src.core import data_source_v2 as ds2
from src.utils.auth import require_admin
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str
from src.utils.file_io import safe_read_json

router = APIRouter()

_HAS_CORE = False
try:
    from src.core.data_source import DataSourceManager
    from src.core.executor import SimulatedBroker, TradeExecutor
    from src.core.risk_manager import RiskManager
    _HAS_CORE = True
except ImportError as e:
    logger.warning(f"import src.core failed: {e}")


_USER_POSITIONS_FILE = Path(__file__).resolve().parent.parent / "user_positions.json"


def _load_user_positions() -> list[dict]:
    """读取用户持仓,文件缺失或损坏时返回空列表(不返回 mock 数据)

    P0 修复(2026-07-30):改用 safe_read_json,主文件损坏自动回退 .bak 备份
    """
    data = safe_read_json(_USER_POSITIONS_FILE, default={"positions": []})
    if data is None or not isinstance(data, dict):
        return []
    return data.get("positions", [])


def _load_user_cash() -> float:
    """读取用户现金,文件缺失时返回 0(不硬编码 80 万)"""
    data = safe_read_json(_USER_POSITIONS_FILE, default={"available_cash": 0.0})
    if data is None or not isinstance(data, dict):
        return 0.0
    try:
        return float(data.get("available_cash", 0.0))
    except (TypeError, ValueError):
        return 0.0


async def _enrich_positions_with_realtime(positions: list[dict]) -> list[dict]:
    symbols = [p["symbol"] for p in positions]
    if not symbols:
        return positions
    # P1 修复(2026-07-30):原代码 except Exception 后 quotes=[] 静默吞异常,
    # 用户会看到错误的市值/盈亏(用成本价计算)。改为日志记录,并在 positions 上标记 data_quality。
    try:
        quotes = await asyncio.to_thread(ds2.get_realtime_quote_tencent, symbols)
    except Exception as e:
        logger.warning(f"_enrich_positions_with_realtime: 行情接口失败,降级用成本价: {e}")
        quotes = []
        for p in positions:
            p["data_quality"] = "degraded"  # 标记行情数据降级,前端可显示提示
    quote_map = {q.get("code", ""): q for q in quotes}
    for p in positions:
        q = quote_map.get(p["symbol"], {})
        current_price = _safe_float(q.get("price"), p.get("cost_price", 0))
        p["current_price"] = current_price
        p["market_value"] = round(current_price * p["quantity"], 2)
        cost = p["cost_price"] * p["quantity"]
        p["profit"] = round(p["market_value"] - cost, 2)
        p["profit_pct"] = round((current_price / p["cost_price"] - 1) * 100, 2) if p["cost_price"] else 0.0
        p["_change_pct"] = _safe_float(q.get("change_pct"), 0)
        p["_prev_close"] = _safe_float(q.get("prev_close"), 0)
        if q.get("name"):
            p["name"] = _safe_str(q.get("name"))
    return positions


class OverviewResponse(BaseModel):
    available_cash: float
    total_assets: float
    market_value: float
    daily_pnl: float
    daily_pnl_pct: float
    position_count: int
    risk_level: str
    risk_message: str
    # P2-6 修复(2026-07-30):新增 data_quality 字段,
    # 标记 daily_pnl 是否因实时行情缺失/计算异常而降级为 0。
    # 前端据此显示"盈亏数据降级"提示,避免用户误以为当日无盈亏。
    data_quality: str = "normal"  # normal | degraded


class PositionItem(BaseModel):
    symbol: str
    name: str
    quantity: float
    available_quantity: float
    cost_price: float
    current_price: float
    market_value: float
    profit: float
    profit_pct: float


class TradeItem(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    amount: float
    commission: float
    stamp_tax: float
    net_amount: float
    strategy: str
    reason: str
    created_at: str


class RiskResponse(BaseModel):
    level: str
    total_assets: float
    peak_assets: float
    drawdown_pct: float
    daily_pnl: float
    daily_pnl_pct: float
    consecutive_stop_losses: int
    is_paused: bool
    pause_until: str | None
    is_emergency_stopped: bool
    no_new_positions: bool
    position_reduction: float
    message: str


def _get_state(request: Request) -> dict[str, Any]:
    if hasattr(request.app.state, "app_state"):
        return request.app.state.app_state
    from web.api import app_state
    return app_state


@router.get("/overview", response_model=OverviewResponse, dependencies=[Depends(require_admin)])
async def overview(request: Request):
    if not _HAS_CORE:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        daily_pnl = 0.0
        # P2-6 修复(2026-07-30):标记 daily_pnl 计算是否降级。
        # 触发降级的情形:1) 实时行情获取失败(_change_pct/_prev_close 缺失)
        #                 2) 计算过程异常。前端据此显示"盈亏降级"提示。
        daily_pnl_degraded = False
        try:
            for p in enriched:
                change_pct = _safe_float(p.get("_change_pct", 0))
                prev_close = _safe_float(p.get("_prev_close", 0))
                if prev_close > 0:
                    daily_pnl += p["quantity"] * prev_close * change_pct / 100.0
                else:
                    # 有持仓但缺 prev_close → 行情数据不完整 → 降级
                    if p.get("quantity", 0) > 0:
                        daily_pnl_degraded = True
        except Exception as e:
            logger.warning(f"calc daily_pnl for overview failed: {e}")
            daily_pnl_degraded = True
        available_cash = _load_user_cash()
        total_assets = available_cash + market_value
        daily_pnl_pct = round(daily_pnl / (total_assets - daily_pnl) * 100, 2) if (total_assets - daily_pnl) > 0 else 0.0
        return OverviewResponse(
            available_cash=available_cash,
            total_assets=round(total_assets, 2),
            market_value=round(market_value, 2),
            daily_pnl=round(daily_pnl, 2),
            daily_pnl_pct=daily_pnl_pct,
            position_count=len(enriched),
            risk_level="NORMAL",
            risk_message="系统正常运行",
            data_quality="degraded" if daily_pnl_degraded else "normal",
        )
    state = _get_state(request)
    broker = state.get("broker")
    risk_manager = state.get("risk_manager")
    if not broker or not risk_manager:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        return OverviewResponse(
            available_cash=1_000_000.0,
            total_assets=round(1_000_000.0 + market_value, 2),
            market_value=round(market_value, 2),
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            position_count=len(enriched),
            risk_level="NORMAL",
            risk_message="模拟模式",
        )
    balance, positions, risk_status = await asyncio.gather(
        asyncio.to_thread(broker.get_balance),
        asyncio.to_thread(broker.get_positions),
        asyncio.to_thread(risk_manager.get_risk_status),
    )
    return OverviewResponse(
        available_cash=balance.available_cash,
        total_assets=balance.total_assets,
        market_value=balance.market_value,
        daily_pnl=risk_status.daily_pnl,
        daily_pnl_pct=risk_status.daily_pnl_pct,
        position_count=len(positions),
        risk_level=risk_status.level.value,
        risk_message=risk_status.message,
    )


@router.get("/positions", response_model=list[PositionItem], dependencies=[Depends(require_admin)])
async def positions(request: Request):
    if not _HAS_CORE:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        return [
            PositionItem(
                symbol=p["symbol"], name=p["name"], quantity=p["quantity"],
                available_quantity=p["available_quantity"], cost_price=p["cost_price"],
                current_price=p["current_price"], market_value=p["market_value"],
                profit=p["profit"], profit_pct=p["profit_pct"],
            )
            for p in enriched
        ]
    state = _get_state(request)
    broker = state["broker"]
    pos_list = await asyncio.to_thread(broker.get_positions)
    return [
        PositionItem(
            symbol=p.symbol, name=p.name, quantity=p.quantity,
            available_quantity=p.available_quantity, cost_price=p.cost_price,
            current_price=p.current_price, market_value=p.market_value,
            profit=p.profit, profit_pct=p.profit_pct,
        )
        for p in pos_list
    ]


@router.get("/trades", response_model=list[TradeItem], dependencies=[Depends(require_admin)])
async def trades(request: Request, limit: int = 20):
    if not _HAS_CORE:
        return []
    state = _get_state(request)
    executor = state["executor"]
    history = await asyncio.to_thread(executor.get_trade_history, limit=limit)
    return [
        TradeItem(
            trade_id=str(row.get("trade_id", "")),
            order_id=str(row.get("order_id", "")),
            symbol=str(row.get("symbol", "")),
            side=str(row.get("side", "")),
            price=float(row.get("price", 0)),
            quantity=float(row.get("quantity", 0)),
            amount=float(row.get("amount", 0)),
            commission=float(row.get("commission", 0)),
            stamp_tax=float(row.get("stamp_tax", 0)),
            net_amount=float(row.get("net_amount", 0)),
            strategy=str(row.get("strategy", "")),
            reason=str(row.get("reason", "")),
            created_at=str(row.get("created_at", "")),
        )
        for row in history
    ]


@router.get("/risk", response_model=RiskResponse, dependencies=[Depends(require_admin)])
async def risk(request: Request):
    if not _HAS_CORE:
        enriched = await _enrich_positions_with_realtime([dict(p) for p in _load_user_positions()])
        market_value = sum(p.get("market_value", 0) for p in enriched)
        daily_pnl = 0.0
        try:
            for p in enriched:
                change_pct = _safe_float(p.get("_change_pct", 0))
                prev_close = _safe_float(p.get("_prev_close", 0))
                if prev_close > 0:
                    daily_pnl += p["quantity"] * prev_close * change_pct / 100.0
        except Exception as e:
            logger.warning(f"calc daily_pnl for risk failed: {e}")
        available_cash = _load_user_cash()
        total_assets = available_cash + market_value
        total_profit = sum(p.get("profit", 0) for p in enriched)
        total_cost = sum(p.get("cost_price", 0) * p.get("quantity", 0) for p in enriched)
        drawdown_pct = 0.0
        if total_cost > 0 and total_profit < 0:
            drawdown_pct = round(abs(total_profit) / total_cost * 100, 2)
        daily_pnl_pct = round(daily_pnl / (total_assets - daily_pnl) * 100, 2) if (total_assets - daily_pnl) > 0 else 0.0
        level = "NORMAL"
        if drawdown_pct >= 15:
            level = "CRITICAL"
        elif drawdown_pct >= 10:
            level = "DANGER"
        elif drawdown_pct >= 5:
            level = "WARNING"
        return RiskResponse(
            level=level, total_assets=round(total_assets, 2),
            peak_assets=round(total_assets, 2),
            drawdown_pct=drawdown_pct, daily_pnl=round(daily_pnl, 2),
            daily_pnl_pct=daily_pnl_pct, consecutive_stop_losses=0,
            is_paused=False, pause_until=None, is_emergency_stopped=False,
            no_new_positions=False, position_reduction=1.0,
            message="风控正常" if level == "NORMAL" else f"回撤 {drawdown_pct}%，需关注",
        )
    state = _get_state(request)
    risk_manager = state["risk_manager"]
    status = await asyncio.to_thread(risk_manager.get_risk_status)
    return RiskResponse(
        level=status.level.value,
        total_assets=status.total_assets,
        peak_assets=status.peak_assets,
        drawdown_pct=status.drawdown_pct,
        daily_pnl=status.daily_pnl,
        daily_pnl_pct=status.daily_pnl_pct,
        consecutive_stop_losses=status.consecutive_stop_losses,
        is_paused=status.is_paused,
        pause_until=status.pause_until.isoformat() if status.pause_until else None,
        is_emergency_stopped=status.is_emergency_stopped,
        no_new_positions=status.no_new_positions,
        position_reduction=status.position_reduction,
        message=status.message,
    )


# ===== 组合风险分析(2026-07-30 新增) =====
# 复用 backtest 模块的指标计算函数(_calc_sortino_ratio 已有),
# 但组合分析逻辑独立:按持仓权重构建组合日收益率序列,再计算风险指标。
# 设计约束:
# - 并行拉取 K 线(asyncio.gather + to_thread),整体超时 25s(Render Free 30s 限制)
# - 60s 进程内缓存,避免短时间内重复计算
# - 复用 numpy/pandas,不引入新依赖

import time as _time
import numpy as np
_portfolio_risk_cache: dict = {"ts": 0.0, "data": None}
_PORTFOLIO_RISK_CACHE_TTL = 60.0  # 60 秒缓存


class PortfolioRiskResponse(BaseModel):
    """组合风险分析响应"""
    position_count: int
    trading_days: int  # 实际使用的交易日数
    annual_volatility: float  # 年化波动率(%)
    sharpe_ratio: float  # 夏普比率(年化超额收益/年化波动)
    sortino_ratio: float  # Sortino 比率(只惩罚下行波动)
    max_drawdown: float  # 最大回撤(%)
    calmar_ratio: float  # Calmar 比率(年化收益/最大回撤)
    weighted_returns: list[float]  # 近 30 日组合日收益率(%)
    weights: list[dict]  # 各持仓权重 {symbol, name, weight}
    data_quality: str = "normal"  # normal | degraded | insufficient


def _calc_max_drawdown(cumulative: list[float]) -> float:
    """计算最大回撤(%)。借鉴 mementum/backtrader DrawDown Analyzer。"""
    if not cumulative:
        return 0.0
    peak = cumulative[0]
    max_dd = 0.0
    for v in cumulative:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd * 100, 2)


@router.get("/portfolio_risk", response_model=PortfolioRiskResponse, dependencies=[Depends(require_admin)])
async def portfolio_risk(request: Request):
    """组合风险分析:基于持仓权重计算夏普/Sortino/最大回撤/Calmar。

    算法:
    1. 读取用户持仓,计算各股市值权重
    2. 并行拉取每只股票近 120 日 K 线(超时 25s)
    3. 按权重构建组合日收益率序列: r_p(t) = Σ w_i * r_i(t)
    4. 年化波动 = std(daily_returns) * sqrt(252)
    5. Sharpe = (mean(daily) - rf/252) / std(daily) * sqrt(252)
    6. Sortino = 同 Sharpe 但分母用下行标准差(复用 backtest._calc_sortino_ratio)
    7. 最大回撤基于组合累计收益曲线
    8. Calmar = 年化收益 / 最大回撤
    """
    # 缓存检查
    now = _time.monotonic()
    if _portfolio_risk_cache["data"] is not None and now - _portfolio_risk_cache["ts"] < _PORTFOLIO_RISK_CACHE_TTL:
        return _portfolio_risk_cache["data"]

    positions = _load_user_positions()
    if not positions:
        result = PortfolioRiskResponse(
            position_count=0, trading_days=0,
            annual_volatility=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, calmar_ratio=0.0,
            weighted_returns=[], weights=[],
            data_quality="insufficient",
        )
        _portfolio_risk_cache["data"] = result
        _portfolio_risk_cache["ts"] = now
        return result

    # 获取实时行情以计算市值权重(复用 _enrich)
    enriched = await _enrich_positions_with_realtime([dict(p) for p in positions])
    valid = [p for p in enriched if p.get("market_value", 0) > 0 and p.get("quantity", 0) > 0]
    if not valid:
        result = PortfolioRiskResponse(
            position_count=len(positions), trading_days=0,
            annual_volatility=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, calmar_ratio=0.0,
            weighted_returns=[], weights=[],
            data_quality="insufficient",
        )
        _portfolio_risk_cache["data"] = result
        _portfolio_risk_cache["ts"] = now
        return result

    total_mv = sum(p["market_value"] for p in valid)
    weights_map = {p["symbol"]: p["market_value"] / total_mv for p in valid}

    # 并行拉取 K 线(超时 25s 保护,避免 Render Free 30s 限制)
    async def fetch_kline(sym: str) -> list[dict]:
        return await asyncio.to_thread(ds2.get_kline_mootdx, sym, "daily", 120)

    try:
        kline_results = await asyncio.wait_for(
            asyncio.gather(*[fetch_kline(p["symbol"]) for p in valid], return_exceptions=True),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        logger.warning("portfolio_risk: fetch klines timeout(25s)")
        result = PortfolioRiskResponse(
            position_count=len(valid), trading_days=0,
            annual_volatility=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, calmar_ratio=0.0,
            weighted_returns=[], weights=[],
            data_quality="degraded",
        )
        _portfolio_risk_cache["data"] = result
        _portfolio_risk_cache["ts"] = now
        return result

    # 构建各股票的日收益率(用 numpy 避免新增 pandas 依赖到 dashboard 模块)
    returns_dict = {}
    for p, klines in zip(valid, kline_results):
        if isinstance(klines, Exception) or not klines or len(klines) < 2:
            continue
        try:
            # klines 按 date 升序还是降序不确定,先按 date 排序保证时序正确
            sorted_klines = sorted(klines, key=lambda x: str(x.get("date", "")))
            closes = []
            for k in sorted_klines:
                try:
                    c = float(k.get("close", 0))
                    if c > 0:
                        closes.append(c)
                except (TypeError, ValueError):
                    continue
            if len(closes) < 2:
                continue
            # 日收益率: r(t) = close(t)/close(t-1) - 1
            closes_arr = np.array(closes, dtype=float)
            rets = (closes_arr[1:] / closes_arr[:-1] - 1.0).tolist()
            returns_dict[p["symbol"]] = rets
        except Exception as e:
            logger.warning(f"portfolio_risk: calc returns for {p['symbol']} failed: {e}")

    if not returns_dict:
        result = PortfolioRiskResponse(
            position_count=len(valid), trading_days=0,
            annual_volatility=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, calmar_ratio=0.0,
            weighted_returns=[], weights=[],
            data_quality="degraded",
        )
        _portfolio_risk_cache["data"] = result
        _portfolio_risk_cache["ts"] = now
        return result

    # 对齐到最短长度(各股票交易日数可能不同)
    min_len = min(len(r) for r in returns_dict.values())
    if min_len < 5:
        logger.warning(f"portfolio_risk: insufficient trading days ({min_len})")
        result = PortfolioRiskResponse(
            position_count=len(valid), trading_days=min_len,
            annual_volatility=0.0, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, calmar_ratio=0.0,
            weighted_returns=[], weights=[],
            data_quality="insufficient",
        )
        _portfolio_risk_cache["data"] = result
        _portfolio_risk_cache["ts"] = now
        return result

    # 按权重构建组合日收益率
    portfolio_returns = []
    for i in range(min_len):
        r = 0.0
        for sym, rets in returns_dict.items():
            w = weights_map.get(sym, 0)
            r += w * rets[i]
        portfolio_returns.append(r)

    # 计算指标
    arr = np.array(portfolio_returns, dtype=float)
    trading_days = 252

    # 年化波动率(ddof=1 样本标准差,与 backtest 统一)
    daily_std = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    annual_vol = daily_std * float(np.sqrt(trading_days))

    # 年化收益(基于日收益均值)
    daily_mean = float(arr.mean())
    annual_return = daily_mean * trading_days

    # Sharpe 比率
    risk_free = 0.03
    daily_rf = risk_free / trading_days
    sharpe = ((daily_mean - daily_rf) / daily_std * float(np.sqrt(trading_days))) if daily_std > 0 else 0.0

    # Sortino(复用 backtest 模块函数)
    try:
        from src.core.backtest import _calc_sortino_ratio
        sortino = _calc_sortino_ratio(portfolio_returns, risk_free)
    except Exception:
        sortino = 0.0

    # 累计收益曲线 + 最大回撤
    cumulative = [1.0]
    for r in portfolio_returns:
        cumulative.append(cumulative[-1] * (1 + r))
    max_dd = _calc_max_drawdown(cumulative)

    # Calmar = 年化收益 / 最大回撤
    calmar = (annual_return / (max_dd / 100)) if max_dd > 0 else 0.0

    # 权重列表
    weights_list = [
        {"symbol": p["symbol"], "name": p.get("name", ""), "weight": round(weights_map[p["symbol"]] * 100, 2)}
        for p in valid
    ]

    # 近 30 日组合日收益率(%),供前端图表
    recent_returns = [round(r * 100, 4) for r in portfolio_returns[-30:]]

    result = PortfolioRiskResponse(
        position_count=len(valid),
        trading_days=min_len,
        annual_volatility=round(annual_vol * 100, 2),
        sharpe_ratio=round(sharpe, 3),
        sortino_ratio=round(sortino, 3),
        max_drawdown=max_dd,
        calmar_ratio=round(calmar, 3),
        weighted_returns=recent_returns,
        weights=weights_list,
        data_quality="normal",
    )
    _portfolio_risk_cache["data"] = result
    _portfolio_risk_cache["ts"] = now
    return result

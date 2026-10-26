"""组合组合路由(阶段六批次 A)—— 盯盘 / 风险 / X-Ray / Webhook / 账本导入导出。

鉴权约定(架构文档 §5 + 主理人拍板):
- 本 Router 全部端点挂 ``require_admin``(Bearer 管理员令牌)
- 唯一例外: ``POST /webhook/scan`` 使用 ``X-Api-Key`` 头 == env ``PORTFOLIO_SCAN_KEY``
  (GitHub Actions cron 专用,批次 C4 实现)

铁律:
- 数据源失败一律降级返回 last-known + 标识,**绝不 500**
- 所有存储经 tmp/注入路径测试,绝不触碰 web/user_fund_positions.json
"""
from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from loguru import logger

from src.core import data_source_v2 as ds2
from src.core import fund_nav_source as fns
from src.analysis import portfolio_risk as prisk
from src.core.cache import DataCache
from src.utils.auth import require_admin
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str

# 复用 fund 路由的持仓读取与净值置信度规则(唯一可信源仍是 user_fund_positions.json)
from web.routes import fund as fund_route

router = APIRouter()

# 进程内缓存:内存条目硬预算 ≤200(架构文档 §6 内存预算),TTL 按端点分别指定
_portfolio_cache = DataCache(default_ttl=60, memory_maxsize=200, max_files=200)

# last-known 估值(内存,进程重启即失效):数据源故障时降级展示,绝不编数据
_last_known: dict[str, dict] = {}


def _now_iso() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================================================================
# STAGE6: watchboard 盘中估值盯盘(批次 A 功能 #1)
# ==================================================================

_WB_REALTIME_TTL = 60  # 实时估值缓存 60s(架构文档 §3.3)


def _prev_nav_from_pct(nav: float, pct: float) -> float:
    """由官方净值与估算涨跌% 反推昨日净值: prev = nav / (1 + pct/100)。

    与 data_source_v2 的涨跌额反推口径一致;异常涨幅(≥20%)时保守返回 nav。
    """
    if nav <= 0 or abs(pct) >= 20:
        return nav
    return nav / (1 + pct / 100)


def _estimate_day_pnl(shares: float, nav: float, est_nav: float | None, est_pct: float | None) -> float:
    """单基金估算当日盈亏(元)。

    est_nav 可用时: shares × (est_nav − 昨日净值);
    仅 est_pct 可用时(备源拟合): shares × nav × pct/100(近似)。
    """
    if shares <= 0 or nav <= 0:
        return 0.0
    if est_pct is None:
        return 0.0
    if est_nav is not None and est_nav > 0:
        return shares * (est_nav - _prev_nav_from_pct(nav, est_pct))
    return shares * nav * est_pct / 100.0


def _confidence_for_quote(quote: dict, fund_name: str, today: _dt.date) -> tuple[str, str]:
    """腾讯估值直连的置信度判定。

    规则(主理人拍板): QDII / 净值滞后 → low;其余 high。
    """
    name = fund_name or _safe_str(quote.get("name", ""))
    if fns.is_qdii(name):
        return "low", "QDII 估值参考意义有限(海外市场时差)"
    confidence, reason = fund_route._nav_confidence(_safe_str(quote.get("update_time", "")), today)
    if confidence == "low":
        return "low", reason
    return "high", ""


async def _fit_fallback_estimate(code: str, fund_name: str) -> dict | None:
    """备源拟合:披露持仓 × 个股实时涨跌。失败返回 None(调用方标 low)。"""
    try:
        holdings = await asyncio.to_thread(fns.get_fund_holdings, code)
        if holdings is None:
            return None
        stock_codes = [h["stock_code"] for h in holdings.holdings]
        stock_quotes = await asyncio.to_thread(ds2.get_realtime_quote_tencent, stock_codes) if stock_codes else []
        stock_pct = {q.get("code", ""): _safe_float(q.get("change_pct")) for q in stock_quotes}
        index_pct = 0.0
        try:
            indices = await asyncio.to_thread(ds2.get_index_realtime)
            for idx in indices:
                if idx.get("code") == "000001":  # 上证指数作为基准近似(架构文档: sector_map 批次 C 配置化)
                    index_pct = _safe_float(idx.get("change_pct"))
                    break
        except Exception as e:
            logger.debug(f"watchboard index pct fallback: {e}")
        fitted = fns.fit_estimate_from_holdings(holdings, stock_pct, index_pct)
        if fitted is None:
            return None
        est_pct, coverage = fitted
        return {
            "source": "fitted",
            "est_pct": est_pct,
            "est_nav": None,
            "confidence": "medium",
            "confidence_reason": f"披露持仓拟合(覆盖率 {coverage:.0%},截至 {holdings.as_of})",
        }
    except Exception as e:
        logger.warning(f"_fit_fallback_estimate {code} failed: {e}")
        return None


def _summarize_watchboard(items: list[dict]) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0}
    total_pnl = 0.0
    for it in items:
        counts[it.get("confidence", "low")] = counts.get(it.get("confidence", "low"), 0) + 1
        total_pnl += _safe_float(it.get("est_day_pnl"))
    return {
        "total_est_day_pnl": round(total_pnl, 2),
        "count": len(items),
        "confidence_counts": counts,
    }


def _watchboard_from_last_known(reason: str) -> dict:
    """降级路径:数据源故障时用 last-known 拼装响应,绝不 500。"""
    positions = fund_route._load_positions()
    items: list[dict] = []
    for p in positions:
        code = p.get("fund_code", "")
        known = _last_known.get(code)
        if known is not None:
            item = dict(known)
            item["stale"] = True
        else:
            item = {
                "fund_code": code,
                "fund_name": p.get("fund_name", ""),
                "est_nav": None,
                "est_pct": None,
                "nav": 0.0,
                "nav_date": "",
                "est_day_pnl": 0.0,
                "confidence": "low",
                "confidence_reason": "数据源暂不可用,且无 last-known 数据",
                "source": "none",
                "stale": False,
            }
        items.append(item)
    summary = _summarize_watchboard(items)
    summary["total_est_day_pnl"] = 0.0 if all(it.get("est_pct") is None for it in items) else summary["total_est_day_pnl"]
    return {
        "positions": items,
        "summary": summary,
        "degraded": True,
        "degraded_reason": reason[:200],
        "generated_at": _now_iso(),
        "note": "估算≠净值,确认以官方净值为准",
    }


async def _compute_watchboard() -> dict:
    """聚合盘中估值(主源腾讯直连 → 备源披露持仓拟合 → last-known 降级)。"""
    positions = fund_route._load_positions()
    if not positions:
        return {
            "positions": [],
            "summary": {"total_est_day_pnl": 0.0, "count": 0, "confidence_counts": {"high": 0, "medium": 0, "low": 0}},
            "degraded": False,
            "generated_at": _now_iso(),
            "note": "估算≠净值,确认以官方净值为准",
        }

    codes = [p.get("fund_code", "") for p in positions if p.get("fund_code")]
    quotes: list[dict] = []
    try:
        quotes = await asyncio.to_thread(ds2.get_fund_realtime_tencent, codes)
    except Exception as e:
        logger.warning(f"watchboard realtime fetch failed: {e}")
        return _watchboard_from_last_known(f"实时估值接口异常: {e}")
    quote_map = {q.get("code", ""): q for q in quotes}
    today = fund_route._today()

    items: list[dict] = []
    for p in positions:
        code = p.get("fund_code", "")
        fund_name = p.get("fund_name", "")
        shares = _safe_float(p.get("shares"))
        quote = quote_map.get(code) or {}
        nav = _safe_float(quote.get("nav"))
        est_nav = _safe_float(quote.get("estimated_nav"))
        est_pct = _safe_float(quote.get("change_pct"))
        update_time = _safe_str(quote.get("update_time", ""))

        if nav > 0 and est_nav > 0 and quote:
            confidence, reason = _confidence_for_quote(quote, fund_name, today)
            item = {
                "fund_code": code,
                "fund_name": fund_name or _safe_str(quote.get("name", "")),
                "est_nav": round(est_nav, 4),
                "est_pct": round(est_pct, 2),
                "nav": round(nav, 4),
                "nav_date": update_time[:10],
                "update_time": update_time,
                "confidence": confidence,
                "confidence_reason": reason,
                "source": "tencent",
                "stale": False,
            }
        else:
            # 主源缺数据 → 备源披露持仓拟合(medium);再失败 → last-known / low
            fallback = await _fit_fallback_estimate(code, fund_name)
            if fallback is not None:
                item = {
                    "fund_code": code,
                    "fund_name": fund_name,
                    "est_nav": None,
                    "est_pct": fallback["est_pct"],
                    "nav": round(nav, 4),
                    "nav_date": update_time[:10],
                    "update_time": update_time,
                    "confidence": "medium",
                    "confidence_reason": fallback["confidence_reason"],
                    "source": "fitted",
                    "stale": False,
                }
            else:
                known = _last_known.get(code)
                if known is not None:
                    item = dict(known)
                    item["stale"] = True
                else:
                    item = {
                        "fund_code": code,
                        "fund_name": fund_name,
                        "est_nav": None,
                        "est_pct": None,
                        "nav": round(nav, 4),
                        "nav_date": update_time[:10],
                        "update_time": update_time,
                        "confidence": "low",
                        "confidence_reason": "主源与备源均无数据",
                        "source": "none",
                        "stale": False,
                    }
        item["est_day_pnl"] = round(_estimate_day_pnl(shares, item["nav"], item["est_nav"], item["est_pct"]), 2)
        if item["est_pct"] is not None:
            _last_known[code] = dict(item)  # 更新 last-known(仅内存)
        items.append(item)

    return {
        "positions": items,
        "summary": _summarize_watchboard(items),
        "degraded": any(it["source"] == "none" for it in items),
        "generated_at": _now_iso(),
        "note": "估算≠净值,确认以官方净值为准",
    }


@router.get("/watchboard", dependencies=[Depends(require_admin)])
async def get_watchboard(force: bool = Query(False, description="跳过缓存强制刷新")):
    """盘中估值盯盘聚合(批次 A 功能 #1)。

    - 主源: 腾讯估值直连(high 置信度)
    - 备源: 披露持仓拟合(medium)
    - 降级: last-known + stale 标识,绝不 500
    - QDII / 净值滞后基金标 low 置信度
    """
    try:
        if not force:
            cached = _portfolio_cache.get("watchboard")
            if cached is not None:
                return cached
        data = await _compute_watchboard()
        _portfolio_cache.set("watchboard", data, ttl=_WB_REALTIME_TTL)
        return data
    except Exception as e:
        logger.warning(f"get_watchboard failed: {e}")
        return _watchboard_from_last_known(f"聚合异常: {e}")


# ==================================================================
# STAGE6: risk-panel 组合风险指标(批次 A 功能 #3)
# ==================================================================

_RISK_CACHE_TTL = 3600          # 历史净值日级数据,1h 缓存(架构文档 §3.3)
_RISK_EMPTY_METRICS: dict[str, Any] = {
    "total_return": None, "annual_return": None, "max_drawdown": None,
    "annual_vol": None, "sharpe": None, "sortino": None, "calmar": None,
    "nav_points": 0,
}


def _fetch_fund_nav_series(code: str, period: str) -> list[dict]:
    """单基金历史净值(带 1h 进程内缓存,备源 akshare 由 fund_nav_source 内部降级)。"""
    cache_key = f"pf_risk_nav_{code}_{period}"
    cached = _portfolio_cache.get(cache_key)
    if cached is not None:
        return list(cached)
    try:
        data = fns.get_fund_nav_history(code, period)
    except Exception as e:
        logger.warning(f"_fetch_fund_nav_series {code} failed: {e}")
        data = []
    if data:
        _portfolio_cache.set(cache_key, data, ttl=_RISK_CACHE_TTL)
    return data


@router.get("/risk", dependencies=[Depends(require_admin)])
async def get_portfolio_risk(
    window: str = Query("3m", max_length=8, description="窗口: 1m|3m|6m|1y|3y|max"),
):
    """组合与单基金风险指标(批次 A 功能 #3)。

    口径(架构文档 §5,主理人拍板): rf=2.0%、年化 244 交易日、样本 ≤250 点;
    组合层为"当前份额静态合成"(method=static-shares),忽略申赎现金流。
    任一基金历史缺失 → 该基金指标为 None + degraded 标识,绝不 500。
    """
    try:
        period = prisk.resolve_period(window)
    except ValueError as e:
        return {"error": str(e), "window": window, "portfolio": None, "funds": []}

    try:
        positions = fund_route._load_positions()
        if not positions:
            return {
                "window": window,
                "rf": prisk.RISK_FREE_RATE,
                "trading_days": prisk.TRADING_DAYS_PER_YEAR,
                "method": "static-shares",
                "portfolio": dict(_RISK_EMPTY_METRICS),
                "funds": [],
                "degraded": False,
                "note": "暂无持仓",
                "generated_at": _now_iso(),
            }

        funds_out: list[dict] = []
        series_map: dict[str, list[tuple[str, float]]] = {}
        shares_map: dict[str, float] = {}
        degraded = False

        for p in positions:
            code = p.get("fund_code", "")
            shares = _safe_float(p.get("shares"))
            name = p.get("fund_name", "")
            if not code or shares <= 0:
                continue
            shares_map[code] = shares
            series = _fetch_fund_nav_series(code, period)
            if series:
                seq = [(_safe_str(row.get("date")), _safe_float(row.get("acc_nav"), _safe_float(row.get("nav")))) for row in series]
                seq = [(d, v) for d, v in seq if d and v > 0]
                series_map[code] = seq
                metrics = prisk.compute_risk_metrics([v for _, v in seq])
            else:
                metrics = dict(_RISK_EMPTY_METRICS)
                degraded = True
            funds_out.append({
                "fund_code": code,
                "fund_name": name,
                "window": window,
                **metrics,
            })

        # 组合层:当前份额静态加权合成
        combined = prisk.combine_portfolio_nav(series_map, shares_map)
        portfolio_metrics = prisk.compute_risk_metrics([v for _, v in combined]) if combined else dict(_RISK_EMPTY_METRICS)

        return {
            "window": window,
            "rf": prisk.RISK_FREE_RATE,
            "trading_days": prisk.TRADING_DAYS_PER_YEAR,
            "method": "static-shares",
            "portfolio": portfolio_metrics,
            "funds": funds_out,
            "degraded": degraded,
            "note": "按当前份额静态合成,忽略申赎现金流;估算≠净值" if combined else "历史净值不足,无法合成组合序列",
            "generated_at": _now_iso(),
        }
    except Exception as e:
        logger.warning(f"get_portfolio_risk failed: {e}")
        # 降级:绝不 500
        return {
            "window": window,
            "rf": prisk.RISK_FREE_RATE,
            "trading_days": prisk.TRADING_DAYS_PER_YEAR,
            "method": "static-shares",
            "portfolio": dict(_RISK_EMPTY_METRICS),
            "funds": [],
            "degraded": True,
            "note": f"风险计算异常: {e}",
            "generated_at": _now_iso(),
        }

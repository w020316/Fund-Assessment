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
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi import status as _status
from loguru import logger
from pydantic import BaseModel, Field

from src.core import data_source_v2 as ds2
from src.core import fund_nav_source as fns
from src.core import portfolio_store as pstore
from src.analysis import portfolio_risk as prisk
from src.analysis import portfolio_xray as pxray
from src.core import webhook_push as wpush
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


# ==================================================================
# STAGE6: xray-report 持仓重叠 X-Ray(批次 A 功能 #4,简化版)
# ==================================================================

_XRAY_CACHE_TTL = 6 * 3600  # 重叠矩阵缓存 6h(季报级数据,架构文档 §3.3)


async def _compute_xray() -> dict:
    """聚合披露持仓并计算重叠度(持仓不可得的基金标 missing,绝不 500)。"""
    positions = fund_route._load_positions()
    codes = [p.get("fund_code", "") for p in positions if p.get("fund_code")]
    if not codes:
        return {
            "schema_version": pxray.SCHEMA_VERSION,
            "funds": [], "overlap_matrix": [], "stock_exposure": [],
            "concentration_warnings": [], "missing_funds": [],
            "data_as_of": {}, "degraded": False, "status": "ok",
            "message": "暂无持仓", "generated_at": _now_iso(),
        }

    # 并发抓取披露持仓(批量限 5 并发,内存/上游压力可控)
    sem = asyncio.Semaphore(5)

    async def _fetch_one(code: str):
        async with sem:
            return await asyncio.to_thread(fns.get_fund_holdings, code)

    holdings_list = await asyncio.gather(*[_fetch_one(c) for c in codes])
    holdings_map: dict[str, fns.FundHoldings | None] = dict(zip(codes, holdings_list))

    # 基金市值占比(官方净值 × 份额;行情失败则不参与单基金集中度预警,不编数据)
    fund_weights: dict[str, float] = {}
    try:
        quotes = await asyncio.to_thread(ds2.get_fund_realtime_tencent, codes)
        nav_map = {q.get("code", ""): _safe_float(q.get("nav")) for q in quotes}
        mv = {c: nav_map.get(c, 0.0) * _safe_float(next((p.get("shares") for p in positions if p.get("fund_code") == c), 0.0)) for c in codes}
        total_mv = sum(v for v in mv.values() if v > 0)
        if total_mv > 0:
            fund_weights = {c: v / total_mv for c, v in mv.items() if v > 0}
    except Exception as e:
        logger.warning(f"xray fund weights fetch failed: {e}")

    report = pxray.build_xray(holdings_map, fund_weights)
    report["generated_at"] = _now_iso()
    return report


@router.get("/xray", dependencies=[Depends(require_admin)])
async def get_portfolio_xray(force: bool = Query(False, description="跳过缓存强制刷新")):
    """持仓重叠 X-Ray(批次 A 功能 #4,简化版)。

    - 基于最新季报前十大重仓:两两重叠度矩阵、单股跨基金暴露 TopN、集中度预警
    - 成功结果缓存 6h;降级结果不缓存(下次调用重试)
    - 披露持仓全部不可得 → status=insufficient_data + "数据不足",绝不 500/空结果
    """
    try:
        if not force:
            cached = _portfolio_cache.get("xray")
            if cached is not None:
                return cached
        data = await _compute_xray()
        # 仅缓存非降级结果:降级结果下次调用重试,给数据源恢复机会
        if data.get("status") == "ok" and not data.get("degraded"):
            _portfolio_cache.set("xray", data, ttl=_XRAY_CACHE_TTL)
        return data
    except Exception as e:
        logger.warning(f"get_portfolio_xray failed: {e}")
        return {
            "schema_version": pxray.SCHEMA_VERSION,
            "funds": [], "overlap_matrix": [], "stock_exposure": [],
            "concentration_warnings": [], "missing_funds": [],
            "data_as_of": {}, "degraded": True, "status": "insufficient_data",
            "message": f"X-Ray 计算异常: {e}",
            "generated_at": _now_iso(),
        }


# ==================================================================
# STAGE6: webhook-push 净值/估值异动推送(批次 A 功能 #2)
# ==================================================================


class WebhookConfigRequest(BaseModel):
    """Webhook 配置更新(密钥不入此模型,凭据走 Render env)。"""

    channel: str | None = Field(None, description="推送渠道: serverchan | generic")
    enabled: bool | None = Field(None, description="是否启用推送")
    thresholds: dict[str, float] | None = Field(None, description="预警阈值,如 est_pct_abs")


class ScanRequest(BaseModel):
    """scan 请求体(幂等,force 仅为人工调试预留)。"""

    force: bool = Field(False, description="跳过节流强制推送(仅人工调试用)")


def _require_scan_key(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> None:
    """scan 端点专用鉴权: X-Api-Key 头 == env PORTFOLIO_SCAN_KEY。

    与 require_admin 同范式:
    - env 未配置 → 500 fail-closed(生产裸奔拒绝)
    - 头缺失/不匹配 → 401,secrets.compare_digest 防时序攻击
    """
    expected = os.getenv("PORTFOLIO_SCAN_KEY", "").strip()
    if not expected:
        logger.error("PORTFOLIO_SCAN_KEY 未配置, scan 端点拒绝服务(fail-closed)")
        raise HTTPException(
            status_code=_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务端鉴权未配置, 请设置 PORTFOLIO_SCAN_KEY",
        )
    if not x_api_key or not x_api_key.strip():
        raise HTTPException(status_code=_status.HTTP_401_UNAUTHORIZED, detail="缺少 X-Api-Key 请求头")
    if not secrets.compare_digest(x_api_key.strip(), expected):
        # 日志脱敏:只记长度,不记内容(与 require_admin 同范式)
        logger.warning(f"scan 鉴权失败: X-Api-Key 不匹配 (len={len(x_api_key)})")
        raise HTTPException(status_code=_status.HTTP_401_UNAUTHORIZED, detail="X-Api-Key 无效")


@router.get("/webhook/config", dependencies=[Depends(require_admin)])
async def get_webhook_config():
    """读取 Webhook 配置(密钥只写不读——GET 不返回任何凭据,仅返回是否已配置)。"""
    config = pstore.load_webhook_config()
    channel = wpush.get_channel(config["channel"])
    return {
        "success": True,
        "config": {
            "channel": config["channel"],
            "enabled": config["enabled"],
            "thresholds": config["thresholds"],
            "sendkey_configured": channel.is_configured(),  # 布尔值,不含凭据
        },
        "generated_at": _now_iso(),
    }


@router.put("/webhook/config", dependencies=[Depends(require_admin)])
async def put_webhook_config(req: WebhookConfigRequest):
    """更新 Webhook 配置(仅 channel/enabled/thresholds 白名单字段落盘)。"""
    if req.channel is not None and req.channel not in wpush._VALID_CHANNELS:
        return {"success": False, "message": f"未知推送渠道: {req.channel},支持 {wpush._VALID_CHANNELS}"}
    config = pstore.load_webhook_config()
    if req.channel is not None:
        config["channel"] = req.channel
    if req.enabled is not None:
        config["enabled"] = req.enabled
    if req.thresholds is not None:
        config["thresholds"].update({k: v for k, v in req.thresholds.items() if k in config["thresholds"]})
    ok = pstore.save_webhook_config(config)
    return {
        "success": ok,
        "message": "已保存" if ok else "保存失败,请查看日志",
        "config": {
            "channel": config["channel"],
            "enabled": config["enabled"],
            "thresholds": config["thresholds"],
        },
    }


@router.post("/webhook/test", dependencies=[Depends(require_admin)])
async def test_webhook():
    """向配置渠道发送测试消息(人工验发)。"""
    config = pstore.load_webhook_config()
    result = wpush.push(
        "QuantFlow Pro 测试推送",
        f"这是一条测试消息。\n\n- 时间: {_now_iso()}\n- 渠道: {config['channel']}\n\n收到即代表推送链路正常。",
        channel=config["channel"],
    )
    return {
        "success": result.success,
        "channel": result.channel,
        "status_code": result.status_code,
        "message": result.message,
        "error": result.error,
    }


def _build_scan_alerts(watchboard: dict, threshold: float) -> tuple[list[dict], list[dict]]:
    """阈值判断:估算涨跌绝对值 ≥ est_pct_abs 的基金触发预警。

    Returns:
        (triggered, skipped): triggered 为待推送项;skipped 为
        {fund_code, reason} 列表(reason= throttled | below_threshold | no_data)。
    """
    triggered: list[dict] = []
    skipped: list[dict] = []
    for item in watchboard.get("positions", []):
        code = item.get("fund_code", "")
        est_pct = item.get("est_pct")
        if est_pct is None:
            skipped.append({"fund_code": code, "reason": "no_data"})
            continue
        if pstore.is_throttled(f"est_{code}"):
            # 幂等:2h 窗口内该基金已推送过,跳过(文件级节流,重启不丢)
            skipped.append({"fund_code": code, "reason": "throttled"})
            continue
        if abs(float(est_pct)) < threshold:
            skipped.append({"fund_code": code, "reason": "below_threshold"})
            continue
        triggered.append(item)
    return triggered, skipped


@router.post("/webhook/scan", dependencies=[Depends(_require_scan_key)])
async def webhook_scan(req: ScanRequest | None = None):
    """预警扫描 + 推送(GitHub Actions cron 每 30min 调用,X-Api-Key 鉴权)。

    幂等保障(主理人拍板):
    - 文件级节流 2h(webhook_throttle.json 持久化,重启不丢):
      同一基金 2h 内不重复推送,偶发重复触发无害
    - 推送成功才落时间戳,失败下次重试
    - 推送内容含: 触发时间/基金/阈值/当前估值涨跌/置信度(不含持仓金额明细)
    """
    body = req or ScanRequest()
    config = pstore.load_webhook_config()
    if not config["enabled"]:
        return {
            "success": True, "pushed": False, "triggered": [], "skipped": [],
            "reason": "disabled", "channel": config["channel"],
            "message": "推送未启用(webhook config enabled=false)",
        }

    # scan 始终取实时数据(不走 60s watchboard 缓存,保证触发判断新鲜)
    watchboard = await _compute_watchboard()
    threshold = float(config["thresholds"].get("est_pct_abs", 2.0))
    triggered, skipped = _build_scan_alerts(watchboard, threshold)

    if not triggered:
        return {
            "success": True, "pushed": False, "triggered": [], "skipped": skipped,
            "reason": "no_trigger", "channel": config["channel"],
            "message": f"无基金触发阈值(±{threshold}%)",
        }

    # 组装推送内容(Server酱 desp 为 Markdown)
    lines = [
        f"**触发时间**: {_now_iso()}",
        f"**阈值**: 估算涨跌 ±{threshold}%",
        "",
        "| 基金 | 估算涨跌 | 估算净值 | 置信度 |",
        "| --- | --- | --- | --- |",
    ]
    for it in triggered:
        est_nav = it.get("est_nav")
        lines.append(
            f"| {it.get('fund_name') or it.get('fund_code')} ({it.get('fund_code')}) "
            f"| {it.get('est_pct')}% "
            f"| {est_nav if est_nav is not None else '—'} "
            f"| {it.get('confidence', 'low')} |"
        )
    lines += ["", "> 估算≠净值,确认以官方净值为准"]
    title = f"估值预警: {len(triggered)} 只基金波动超阈值"
    result = wpush.push(title, "\n".join(lines), channel=config["channel"])

    if result.success:
        for it in triggered:
            pstore.mark_pushed(f"est_{it.get('fund_code', '')}")
    else:
        # 失败不落节流时间戳,GitHub Actions 下个周期重试
        logger.warning(f"webhook_scan push failed: {result.error}")

    return {
        "success": result.success,
        "pushed": result.success,
        "triggered": [
            {
                "fund_code": it.get("fund_code"),
                "fund_name": it.get("fund_name"),
                "est_pct": it.get("est_pct"),
                "est_nav": it.get("est_nav"),
                "confidence": it.get("confidence"),
            }
            for it in triggered
        ],
        "skipped": skipped,
        "threshold": threshold,
        "channel": result.channel,
        "error": result.error,
        "message": result.message,
    }

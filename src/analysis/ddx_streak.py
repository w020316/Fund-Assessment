"""资金连增：连续主力净流入天数。

数据源（按优先级）：
1. 东方财富 ``push2his`` 资金流历史直连（走 data_source_v2 的共享限速器）；
2. akshare ``stock_individual_fund_flow``（同源，作为解析兼容兜底）；
3. 新浪 ``MoneyFlow.ssl_qsfx_zjlrqs`` 多日历史（部分网络环境会拦掉 push2his，
   新浪是独立可用源，仍能算出真实连增）；
4. 新浪 ``MoneyFlow.ssi_ssfx_flzjtj`` 当日快照（只给当日净流入，算不出连增，
   用于个股详情在历史源不可用时仍能展示"今日主力净流入"）。

口径说明：本项目把"连增"定义为——从最近一个交易日起，主力净流入额
连续为正的交易日数。它与通达信 DDX（大单净量）不是同一指标，前端统一
展示为"资金连增"，不冒充原指标口径。

失败语义：单只股票取数失败 → 返回 None，由调用方计入 failed；
不抛异常、不用 0 顶替。快照兜底只标 ``history_available=False``，
绝不把"只有今日快照"当成"N 日连增"。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime

import akshare as ak
import pandas as pd
import requests
from loguru import logger

from src.core import data_health

_MAIN_COL_CANDIDATES = ("主力净流入-净额", "主力净流入")
_RATIO_COL_CANDIDATES = ("主力净流入-净占比", "主力净占比")
_DATE_COL = "日期"
_MAX_ROWS = 60

_EM_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_SINA_FLOW_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssi_ssfx_flzjtj"
_SINA_HIST_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"
# 新浪历史对部分北交所标的会返回一年前的旧快照，超过该天数视为不可用，
# 宁可判缺失也不把旧数据当"最近连增"。
_SINA_HIST_MAX_AGE_DAYS = 15
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def market_of(code: str) -> str:
    """6xx→sh，0xx/3xx→sz，8xx/4xx/920xxx→bj。"""
    code = str(code or "")
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("8", "4", "920")):
        return "bj"
    return "sz"


def compute_streak(df: pd.DataFrame | None) -> dict | None:
    """从资金流历史（升序）计算连增天数；无正流入或数据不可用时返回 None。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None

    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    main_col = next((c for c in _MAIN_COL_CANDIDATES if c in work.columns), None)
    if main_col is None:
        return None
    ratio_col = next((c for c in _RATIO_COL_CANDIDATES if c in work.columns), None)
    date_col = _DATE_COL if _DATE_COL in work.columns else None

    rows = work.tail(_MAX_ROWS)
    values = rows[main_col].tolist()
    dates = rows[date_col].tolist() if date_col else [None] * len(values)
    ratios = rows[ratio_col].tolist() if ratio_col else [None] * len(values)

    if not values or pd.isna(values[-1]):
        return None

    streak = 0
    total = 0.0
    for v in reversed(values):
        if v is None or pd.isna(v) or float(v) <= 0:
            break
        streak += 1
        total += float(v)
    if streak == 0:
        return None

    def _date(idx: int) -> str | None:
        d = dates[idx]
        if d is None or pd.isna(d):
            return None
        return str(d)[:10]

    ratio_today = ratios[-1]
    return {
        "streak": streak,
        "net_today": round(float(values[-1]), 2),
        "net_sum": round(total, 2),
        "ratio_today": (
            round(float(ratio_today), 2)
            if ratio_today is not None and not pd.isna(ratio_today)
            else None
        ),
        "start_date": _date(len(values) - streak),
        "end_date": _date(len(values) - 1),
    }


def _last_row_snapshot(df: pd.DataFrame) -> dict | None:
    """历史数据可读但最近一日没有净流入时，给出当日事实，不伪造连增。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    main_col = next((c for c in _MAIN_COL_CANDIDATES if c in work.columns), None)
    if main_col is None or pd.isna(work[main_col].iloc[-1]):
        return None
    last = work.iloc[-1]
    ratio_col = next((c for c in _RATIO_COL_CANDIDATES if c in work.columns), None)
    date_col = _DATE_COL if _DATE_COL in work.columns else None
    ratio = last.get(ratio_col) if ratio_col else None
    date = last.get(date_col) if date_col else None
    return {
        "streak": 0,
        "net_today": round(float(last[main_col]), 2),
        "net_sum": 0.0,
        "ratio_today": round(float(ratio), 2) if ratio is not None and not pd.isna(ratio) else None,
        "start_date": None,
        "end_date": str(date)[:10] if date is not None and not pd.isna(date) else None,
    }


def _fetch_em_history(code: str) -> pd.DataFrame | None:
    """东财资金流历史直连。走 ds2.em_get 以获得进程内共享的请求间隔。"""
    try:
        from src.core.data_source_v2 import em_get

        resp = em_get(
            _EM_FLOW_URL,
            params={
                "secid": f"{'1' if market_of(code) == 'sh' else '0'}.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "lmt": str(_MAX_ROWS),
                "end": "20500101",
            },
        )
        data = (resp.json() or {}).get("data") or {}
        klines = data.get("klines") or []
    except Exception as e:
        logger.warning(f"ddx_streak {code} em history failed: {type(e).__name__}: {e}")
        return None

    rows: list[dict] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        try:
            rows.append({
                _DATE_COL: str(parts[0])[:10],
                "主力净流入-净额": float(parts[1]),
                "主力净流入-净占比": float(parts[6]),
            })
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows) if rows else None


def _fetch_akshare_history(code: str) -> pd.DataFrame | None:
    try:
        return ak.stock_individual_fund_flow(stock=str(code), market=market_of(code))
    except Exception as e:
        logger.warning(f"ddx_streak {code} akshare failed: {type(e).__name__}: {e}")
        return None


def _fetch_sina_history(code: str) -> pd.DataFrame | None:
    """新浪多日资金流历史（升序），字段对齐东财历史口径。

    ``netamount`` 为主力（超大单+大单）净流入额，单位元；``ratioamount``
    是净占比的小数，这里换算成百分比，与东财 ``主力净流入-净占比`` 一致。
    """
    symbol = f"{market_of(code)}{code}"
    try:
        resp = requests.get(
            _SINA_HIST_URL,
            params={"page": 1, "num": _MAX_ROWS, "sort": "opendate", "asc": 0, "daima": symbol},
            timeout=6,
            headers={"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"},
        )
        data = resp.json()
        if not isinstance(data, list) or not data:
            raise ValueError("empty payload")
    except Exception as e:
        logger.warning(f"ddx_streak {code} sina history failed: {type(e).__name__}: {e}")
        data_health.record_failure("ddx_sina_hist", str(e))
        return None

    rows: list[dict] = []
    for item in data:
        try:
            rows.append({
                _DATE_COL: str(item.get("opendate") or "")[:10],
                "主力净流入-净额": float(item.get("netamount") or 0),
                "主力净流入-净占比": round(float(item.get("ratioamount") or 0) * 100, 4),
            })
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[_DATE_COL])
    if not rows:
        data_health.record_failure("ddx_sina_hist", "unparsable rows")
        return None

    try:
        latest = datetime.strptime(rows[-1][_DATE_COL], "%Y-%m-%d").date()
    except ValueError:
        data_health.record_failure("ddx_sina_hist", "bad date")
        return None
    if (date.today() - latest).days > _SINA_HIST_MAX_AGE_DAYS:
        data_health.record_failure("ddx_sina_hist", f"stale history {rows[-1][_DATE_COL]}")
        return None
    if not any(r["主力净流入-净额"] for r in rows):
        # 全 0 通常意味着上游没有该标的的资金数据，不能当成"净流入 0 元"
        data_health.record_failure("ddx_sina_hist", "empty history")
        return None

    data_health.record_success("ddx_sina_hist", len(rows))
    return pd.DataFrame(rows)


def _fetch_sina_snapshot(code: str) -> dict | None:
    """新浪当日资金快照。字段 netamount 为主力（超大单+大单）净流入，单位元。"""
    symbol = f"{market_of(code)}{code}"
    try:
        resp = requests.get(
            _SINA_FLOW_URL,
            params={"daima": symbol},
            timeout=5,
            headers={"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"},
        )
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected payload")
        net = float(data.get("netamount") or 0)
        # 全 0 通常意味着上游没有该标的的资金数据，不能当成"净流入 0 元"
        traded = sum(abs(float(data.get(k) or 0)) for k in ("r0", "r1", "r2"))
        if traded <= 0:
            raise ValueError("empty snapshot")
    except Exception as e:
        logger.warning(f"ddx_streak {code} sina snapshot failed: {type(e).__name__}: {e}")
        data_health.record_failure("ddx_sina", str(e))
        return None

    data_health.record_success("ddx_sina", 1)
    return {
        "streak": None,
        "net_today": round(net, 2),
        "net_sum": None,
        "ratio_today": None,
        "start_date": None,
        "end_date": None,
        "source": "sina",
        "history_available": False,
    }


def fetch_flow_streak(code: str) -> dict | None:
    """个股资金流详情：优先历史连增，失败时退到当日快照。

    返回字典中 ``history_available`` 明确区分"算出了连增"与"只有今日快照"；
    两者都不可用时返回 None，由调用方按缺失处理。
    """
    df = _fetch_em_history(code)
    source = "eastmoney"
    if df is not None and not df.empty:
        data_health.record_success("ddx_em", len(df))
    else:
        data_health.record_failure("ddx_em", "empty or unreachable")
        df = _fetch_akshare_history(code)
        source = "akshare"
        if df is not None and not df.empty:
            data_health.record_success("ddx_akshare", len(df))
        else:
            data_health.record_failure("ddx_akshare", "empty or unreachable")
            df = _fetch_sina_history(code)
            source = "sina_history"
            if df is None or df.empty:
                return _fetch_sina_snapshot(code)

    stats = compute_streak(df)
    if stats:
        stats["source"] = source
        stats["history_available"] = True
        return stats
    snapshot = _last_row_snapshot(df)
    if snapshot:
        snapshot["source"] = source
        snapshot["history_available"] = True
        return snapshot
    return None


def fetch_streak(code: str) -> dict | None:
    """候选池用：只接受真实历史数据，快照兜底不算连增。

    取数成功但当日主力净流出时返回 streak=0 的统计，而不是 None —— 否则
    调用方会把"筛不出连增"误报成"取数失败"。
    """
    stats = fetch_flow_streak(code)
    if not stats or not stats.get("history_available"):
        return None
    return stats


def build_ddx_streak(
    candidates: list[dict],
    min_days: int = 2,
    workers: int = 6,
    deadline: float = 12.0,
) -> dict:
    """并行拉取候选股资金流，返回连增 ≥ min_days 的标的。

    调用方应把本函数放进 ``asyncio.to_thread``，避免阻塞事件循环。
    """
    pool = [c for c in (candidates or []) if str(c.get("code") or "").strip()]
    if not pool:
        return {"items": [], "sampled": 0, "failed": 0}

    items: list[dict] = []
    failed = 0
    sources: dict[str, int] = {}
    executor = ThreadPoolExecutor(max_workers=max(1, min(workers, len(pool))))
    try:
        futures = {executor.submit(fetch_streak, str(c["code"])): c for c in pool}
        done, pending = wait(futures, timeout=deadline)
        failed += len(pending)  # 超时未完成的按失败计，不参与排名
        for future in done:
            cand = futures[future]
            try:
                stats = future.result()
            except Exception:  # fetch_streak 已兜底，这里只防御 future 层异常
                stats = None
            if not stats:
                failed += 1
                continue
            if stats["streak"] < min_days:
                continue
            src = str(stats.get("source") or "unknown")
            sources[src] = sources.get(src, 0) + 1
            items.append({
                "code": str(cand.get("code") or ""),
                "name": str(cand.get("name") or ""),
                "price": cand.get("price"),
                "change_pct": cand.get("change_pct"),
                **stats,
            })
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    items.sort(key=lambda x: (-x["streak"], -(x.get("net_today") or 0.0)))
    return {"items": items, "sampled": len(pool), "failed": failed, "sources": sources}

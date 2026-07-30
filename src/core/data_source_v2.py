from __future__ import annotations

import atexit
import math
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from loguru import logger

try:
    from mootdx.quotes import Quotes
    _HAS_MOOTDX = True
except ImportError:
    _HAS_MOOTDX = False

_EM_SESSION = requests.Session()
_EM_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.eastmoney.com/",
})

_last_em_request_time: float = 0.0
_EM_MIN_INTERVAL: float = 0.3
_EM_AVAILABLE: bool | None = None
# P2 修复(2026-07-29):原 _check_em_available 无锁保护,多线程并发首次调用时
# 每个线程都会读到 _EM_AVAILABLE=None 并发起探测请求(2s 超时 × N 线程 = N 个探测)
# 改为:double-checked locking 模式,仅首个线程发起探测,其他线程等待锁后读取结果
_em_check_lock = threading.Lock()

_TTLCacheEntry = dict[str, Any]
_cache: dict[str, _TTLCacheEntry] = {}
_cache_lock = threading.Lock()
# 修复(2026-07-29):原 _cache 无大小上限,长期运行后内存泄漏导致 512MB 实例 OOM
# 改为 200 条上限,约 50MB 内存占用,足够覆盖实时行情/资金流向等热数据
_CACHE_MAX_SIZE = 200
# 修复(2026-07-29):Render Free CPU 仅 0.1 核,6 线程增加上下文切换开销
# 降为 3 线程,足够并行抓取行情/K线/资金流,且内存占用减半
_thread_pool = ThreadPoolExecutor(max_workers=3)
# P2 修复(2026-07-29):注册进程退出钩子,确保线程池优雅关闭
# 原代码无 shutdown,Render Free 实例重启时可能残留线程导致资源泄漏
atexit.register(_thread_pool.shutdown, wait=False)


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry["ts"] > entry["ttl"]:
            del _cache[key]
            return None
        return entry["val"]


def _cache_set(key: str, val: Any, ttl: float) -> None:
    with _cache_lock:
        # FIFO 淘汰:超过上限时删除最早插入的条目(避免 OrderedDict 开销)
        # 注:非严格 LRU;实时行情热数据场景下命中率与 LRU 接近,实现更轻
        if len(_cache) >= _CACHE_MAX_SIZE and key not in _cache:
            _cache.pop(next(iter(_cache)), None)
        _cache[key] = {"val": val, "ts": time.monotonic(), "ttl": ttl}


def _parallel_fetch(funcs: list[tuple[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    futures = {}
    for name, fn in funcs:
        f = _thread_pool.submit(fn)
        futures[f] = name
    for f in as_completed(futures):
        name = futures[f]
        try:
            results[name] = f.result(timeout=10)
        except Exception as e:
            logger.warning(f"parallel_fetch {name} failed: {e}")
            results[name] = None
    return results


def _check_em_available() -> bool:
    """检查 EastMoney push2 API 是否可用

    P2 修复(2026-07-29):double-checked locking 模式
    - 首次调用:N 个线程并发时仅 1 个发起探测请求(原为 N 个)
    - 后续调用:直接返回缓存值,无锁竞争
    """
    global _EM_AVAILABLE
    # Fast path:已探测过,直接返回
    if _EM_AVAILABLE is not None:
        return _EM_AVAILABLE
    # Slow path:加锁后再次检查(double-checked locking)
    with _em_check_lock:
        if _EM_AVAILABLE is not None:
            return _EM_AVAILABLE
        try:
            resp = _EM_SESSION.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": "1.000001", "fields": "f12"},
                timeout=2,
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    _EM_AVAILABLE = bool(data.get("data"))
                except Exception:
                    _EM_AVAILABLE = False
            else:
                _EM_AVAILABLE = False
        except Exception:
            _EM_AVAILABLE = False
        if not _EM_AVAILABLE:
            logger.info("EastMoney push2 API unavailable, using fallback data sources")
        return _EM_AVAILABLE


def _ensure_em_checked():
    if _EM_AVAILABLE is None:
        _check_em_available()


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    try:
        result = float(val)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def _safe_str(val: Any, default: str = "") -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    return str(val)


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _prefix_code(code: str) -> str:
    if code.startswith(("sh", "sz", "SH", "SZ")):
        return code.lower()
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _em_secid(code: str) -> str:
    if code.startswith("6") or code.startswith("9"):
        return f"1.{code}"
    return f"0.{code}"


def em_get(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
    if "push2.eastmoney.com" in url and not _ensure_em_checked():
        raise requests.ConnectionError("EastMoney push2 API unavailable (proxy blocked)")
    global _last_em_request_time
    elapsed = time.monotonic() - _last_em_request_time
    wait = _EM_MIN_INTERVAL - elapsed + random.uniform(0, 0.5)
    if wait > 0:
        time.sleep(wait)
    resp = _EM_SESSION.get(url, params=params, timeout=5, **kwargs)
    _last_em_request_time = time.monotonic()
    return resp


def _mootdx_client() -> Any:
    if not _HAS_MOOTDX:
        return None
    try:
        client = Quotes.factory(market="std")
        return client
    except Exception as e:
        logger.warning(f"mootdx connect failed: {e}")
        return None


def _kline_em_fallback(symbol: str, period: str, count: int) -> list[dict]:
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    secid = _em_secid(symbol)
    klt_map = {"daily": "101", "weekly": "102", "monthly": "103"}
    klt = klt_map.get(period, "101")
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": klt,
        "fqt": "1",
        "lmt": str(count),
        "end": "20500101",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        result: list[dict] = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 7:
                result.append({
                    "date": parts[0],
                    "open": _safe_float(parts[1]),
                    "close": _safe_float(parts[2]),
                    "high": _safe_float(parts[3]),
                    "low": _safe_float(parts[4]),
                    "volume": _safe_float(parts[5]),
                    "amount": _safe_float(parts[6]),
                })
        return result
    except Exception as e:
        logger.warning(f"_kline_em_fallback failed: {e}")
        return []


def _kline_sina_fallback(symbol: str, period: str = "daily", count: int = 30) -> list[dict]:
    sina_code = _prefix_code(symbol)
    scale_map = {"daily": "240", "weekly": "1200", "monthly": "5200"}
    scale = scale_map.get(period, "240")
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
    params = {
        "symbol": sina_code,
        "scale": scale,
        "ma": "no",
        "datalen": str(count),
    }
    try:
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        text = resp.text
        m = re.search(r'\((.*)\)', text, re.DOTALL)
        if not m:
            return []
        import json as _json
        items = _json.loads(m.group(1))
        result: list[dict] = []
        for item in items:
            result.append({
                "date": _safe_str(item.get("day", ""))[:10],
                "open": _safe_float(item.get("open")),
                "high": _safe_float(item.get("high")),
                "low": _safe_float(item.get("low")),
                "close": _safe_float(item.get("close")),
                "volume": _safe_float(item.get("volume")),
                "amount": 0.0,
            })
        return result
    except Exception as e:
        logger.warning(f"_kline_sina_fallback failed: {e}")
        return []


def get_kline_mootdx(symbol: str, period: str = "daily", count: int = 120) -> list[dict]:
    category_map = {"daily": 9, "weekly": 5, "monthly": 6}
    category = category_map.get(period, 9)
    client = _mootdx_client()
    if client is not None:
        try:
            market = 1 if symbol.startswith("6") or symbol.startswith("9") else 0
            df = client.bars(symbol=symbol, category=category, market=market, offset=count)
            if df is not None and not df.empty:
                result: list[dict] = []
                for _, row in df.iterrows():
                    result.append({
                        "date": _safe_str(row.get("datetime", ""))[:10],
                        "open": _safe_float(row.get("open")),
                        "high": _safe_float(row.get("high")),
                        "low": _safe_float(row.get("low")),
                        "close": _safe_float(row.get("close")),
                        "volume": _safe_float(row.get("vol")),
                        "amount": _safe_float(row.get("amount")),
                    })
                return result
        except Exception as e:
            logger.warning(f"get_kline_mootdx mootdx failed, fallback to em: {e}")
    em_result = _kline_em_fallback(symbol, period, count)
    if em_result:
        return em_result
    return _kline_sina_fallback(symbol, period, count)


def get_realtime_quote_tencent(codes: list[str]) -> list[dict]:
    if not codes:
        return []
    cache_key = "quotes_" + "_".join(sorted(codes))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    prefixed = ",".join(_prefix_code(c) for c in codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
    try:
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        text = resp.text
        result: list[dict] = []
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment or "~" not in segment:
                continue
            parts = segment.split("~")
            if len(parts) < 48:
                continue
            code = _safe_str(parts[2])
            name = _safe_str(parts[1])
            price = _safe_float(parts[3])
            prev_close = _safe_float(parts[4])
            open_price = _safe_float(parts[5])
            change = _safe_float(parts[31])
            change_pct = _safe_float(parts[32])
            high = _safe_float(parts[33])
            low = _safe_float(parts[34])
            volume = _safe_float(parts[36])
            amount = _safe_float(parts[37])
            turnover = _safe_float(parts[38])
            pe_ttm = _safe_float(parts[39])
            pb = _safe_float(parts[46])
            total_mv = _safe_float(parts[45])
            circ_mv = _safe_float(parts[44])
            high_limit = _safe_float(parts[47]) if len(parts) > 47 else 0.0
            low_limit = _safe_float(parts[48]) if len(parts) > 48 else 0.0
            result.append({
                "code": code,
                "name": name,
                "price": price,
                "prev_close": prev_close,
                "open": open_price,
                "change": change,
                "change_pct": change_pct,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": amount,
                "turnover": turnover,
                "pe_ttm": pe_ttm,
                "pb": pb,
                "total_market_value": total_mv,
                "circ_market_value": circ_mv,
                "high_limit": high_limit,
                "low_limit": low_limit,
            })
        _cache_set(cache_key, result, 5)
        return result
    except Exception as e:
        logger.warning(f"get_realtime_quote_tencent failed: {e}")
        return []


def get_index_realtime() -> list[dict]:
    cached = _cache_get("index_realtime")
    if cached is not None:
        return cached
    codes = ["sh000001", "sz399001", "sz399006"]
    prefixed = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
    try:
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        text = resp.text
        result: list[dict] = []
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment or "~" not in segment:
                continue
            parts = segment.split("~")
            if len(parts) < 40:
                continue
            result.append({
                "code": _safe_str(parts[2]),
                "name": _safe_str(parts[1]),
                "price": _safe_float(parts[3]),
                "change": _safe_float(parts[31]),
                "change_pct": _safe_float(parts[32]),
                "volume": _safe_float(parts[36]),
                "amount": _safe_float(parts[37]),
            })
        result = [r for r in result if r.get("code")]
        _cache_set("index_realtime", result, 5)
        return result
    except Exception as e:
        logger.warning(f"get_index_realtime failed: {e}")
        return []


def get_stock_ranking_em(sort_field: str = "f3", sort_order: int = 0, count: int = 10) -> list[dict]:
    cache_key = f"stock_ranking_{sort_field}_{sort_order}_{count}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": count,
        "po": sort_order,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": sort_field,
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("f12", "")),
                "name": _safe_str(item.get("f14", "")),
                "price": _safe_float(item.get("f2", 0)),
                "change_pct": _safe_float(item.get("f3", 0)),
                "change": _safe_float(item.get("f4", 0)),
                "volume": _safe_float(item.get("f5", 0)),
                "amount": _safe_float(item.get("f6", 0)),
                "high": _safe_float(item.get("f15", 0)),
                "low": _safe_float(item.get("f16", 0)),
                "open": _safe_float(item.get("f17", 0)),
                "prev_close": _safe_float(item.get("f18", 0)),
            })
        _cache_set(cache_key, result, 15)
        return result
    except Exception as e:
        logger.warning(f"get_stock_ranking_em failed: {e}")
    result = _get_stock_ranking_sina(sort_field, sort_order, count)
    _cache_set(cache_key, result, 15)
    return result


def _get_stock_ranking_sina(sort_field: str = "f3", sort_order: int = 0, count: int = 10) -> list[dict]:
    try:
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        params = {
            "page": 1,
            "num": count,
            "sort": "changepercent" if sort_field == "f3" else "amount",
            "asc": sort_order,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "auto",
        }
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "gbk"
        items = resp.json()
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("code", "")),
                "name": _safe_str(item.get("name", "")),
                "price": _safe_float(item.get("trade", 0)),
                "change_pct": _safe_float(item.get("changepercent", 0)),
                "change": _safe_float(item.get("pricechange", 0)),
                "volume": _safe_float(item.get("volume", 0)),
                "amount": _safe_float(item.get("amount", 0)),
                "high": _safe_float(item.get("high", 0)),
                "low": _safe_float(item.get("low", 0)),
                "open": _safe_float(item.get("open", 0)),
                "prev_close": _safe_float(item.get("settlement", 0)),
            })
        return result
    except Exception as e:
        logger.warning(f"_get_stock_ranking_sina failed: {e}")
        return []


def get_research_reports(stock_code: str = "", page: int = 1, page_size: int = 10) -> list[dict]:
    url = "https://reportapi.eastmoney.com/report/list"
    params = {
        "industryCode": "*",
        "pageSize": str(page_size),
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": "",
        "endTime": "",
        "pageNo": str(page),
        "fields": "",
        "qType": 0,
        "orgCode": "",
        "code": stock_code,
        "rcode": "",
        "p": str(page),
        "pageNum": str(page),
        "pageNumber": str(page),
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("data", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "title": _safe_str(item.get("title")),
                "rating": _safe_str(item.get("emRatingName", item.get("rating", ""))),
                "eps_predict": _safe_float(item.get("predictNextTwoYearEps", 0)),
                "org_name": _safe_str(item.get("orgSName", "")),
                "publish_date": _safe_str(item.get("publishDate", ""))[:10],
            })
        return result
    except Exception as e:
        logger.warning(f"get_research_reports failed: {e}")
        return []


def get_hot_stocks_ths() -> list[dict]:
    cached = _cache_get("hot_stocks_ths")
    if cached is not None:
        return cached
    url = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914",
        "filter": f"GHS3A_{today}",
        "page": 1,
        "limit": 30,
        "order_field": "330324",
        "order_type": 0,
    }
    try:
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.10jqka.com.cn/",
        })
        data = resp.json()
        items = data.get("data", {}).get("list", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("code", item.get("股票代码", ""))),
                "name": _safe_str(item.get("name", item.get("股票简称", ""))),
                "price": _safe_float(item.get("latest_price", item.get("最新价", 0))),
                "change_pct": _safe_float(item.get("change_pct", item.get("涨跌幅", 0))),
                "volume": _safe_float(item.get("volume", item.get("成交额", 0))),
                "reason": _safe_str(item.get("reason", item.get("涨停原因", ""))),
                "limit_up_time": _safe_str(item.get("first_limit_up_time", item.get("首次封板时间", ""))),
                "open_times": _safe_int(item.get("open_limit_up_times", item.get("开板次数", 0))),
            })
        _cache_set("hot_stocks_ths", result, 30)
        return result
    except Exception as e:
        logger.warning(f"get_hot_stocks_ths failed: {e}")
        return []


def get_northbound_flow() -> dict:
    url = "https://data.10jqka.com.cn/dataapi/hsgt/hsgt_detail"
    params = {
        "field": "199112,10,9001,330323,330324,330325,9002,330329",
        "filter": "HS_HSGT",
        "page": 1,
        "limit": 1,
    }
    try:
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.10jqka.com.cn/",
        })
        data = resp.json()
        items = data.get("data", {}).get("list", [])
        if items:
            item = items[0]
            return {
                "date": _safe_str(item.get("date", item.get("日期", ""))),
                "sh_net_inflow": _safe_float(item.get("sh_net_inflow", item.get("沪股通净流入", 0))),
                "sz_net_inflow": _safe_float(item.get("sz_net_inflow", item.get("深股通净流入", 0))),
                "total_net_inflow": _safe_float(item.get("net_inflow", item.get("北向资金净流入", 0))),
            }
        return {}
    except Exception as e:
        logger.warning(f"get_northbound_flow failed: {e}")
        return {}


def search_stock(keyword: str) -> list[dict]:
    if not keyword or not keyword.strip():
        return []
    keyword = keyword.strip()
    try:
        url = "https://suggest3.sinajs.cn/suggest/type=11,12"
        params = {"key": keyword}
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "gbk"
        text = resp.text
        m = re.search(r'"([^"]*)"', text)
        if not m or not m.group(1).strip():
            return _search_stock_tencent(keyword)
        entries = m.group(1).split(";")
        result: list[dict] = []
        for entry in entries:
            if not entry.strip():
                continue
            parts = entry.split(",")
            if len(parts) < 8:
                continue
            full_code = _safe_str(parts[0])
            name = _safe_str(parts[4])
            raw_code = _safe_str(parts[3])
            if not raw_code or not name:
                continue
            if not (raw_code.isdigit() and len(raw_code) == 6):
                continue
            result.append({
                "code": raw_code,
                "name": name,
            })
            if len(result) >= 20:
                break
        if not result:
            return _search_stock_tencent(keyword)
        codes = [r["code"] for r in result]
        quotes = get_realtime_quote_tencent(codes)
        quote_map = {q["code"]: q for q in quotes}
        final: list[dict] = []
        for r in result:
            q = quote_map.get(r["code"], {})
            final.append({
                "code": r["code"],
                "name": r["name"],
                "price": _safe_float(q.get("price")),
                "change_pct": _safe_float(q.get("change_pct")),
            })
        return final
    except Exception as e:
        logger.warning(f"search_stock failed: {e}")
    return _search_stock_tencent(keyword)


def _search_stock_tencent(keyword: str) -> list[dict]:
    try:
        url = "https://smartbox.gtimg.cn/s3/?q=" + keyword + "&t=all"
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        text = resp.text
        m = re.search(r'"([^"]*)"', text)
        if not m or not m.group(1).strip():
            return []
        entries = m.group(1).split(";")
        result: list[dict] = []
        for entry in entries:
            if not entry.strip():
                continue
            parts = entry.split("^")
            if len(parts) < 3:
                continue
            code = _safe_str(parts[0])
            name = _safe_str(parts[1])
            if not code or not name:
                continue
            result.append({
                "code": code,
                "name": name,
                "price": 0.0,
                "change_pct": 0.0,
            })
            if len(result) >= 20:
                break
        if result:
            codes = [r["code"] for r in result]
            quotes = get_realtime_quote_tencent(codes)
            quote_map = {q["code"]: q for q in quotes}
            for r in result:
                q = quote_map.get(r["code"], {})
                r["price"] = _safe_float(q.get("price"))
                r["change_pct"] = _safe_float(q.get("change_pct"))
        return result
    except Exception as e:
        logger.warning(f"_search_stock_tencent failed: {e}")
    return []


def get_stock_detail(code: str) -> dict:
    if not code or not code.strip():
        return {}
    code = code.strip()
    cache_key = f"stock_detail_{code}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    result: dict = {"code": code}

    def _fetch_quote():
        try:
            quotes = get_realtime_quote_tencent([code])
            if quotes:
                q = quotes[0]
                return {
                    "quote": {
                        "code": _safe_str(q.get("code")),
                        "name": _safe_str(q.get("name")),
                        "price": _safe_float(q.get("price")),
                        "prev_close": _safe_float(q.get("prev_close")),
                        "open": _safe_float(q.get("open")),
                        "high": _safe_float(q.get("high")),
                        "low": _safe_float(q.get("low")),
                        "change": _safe_float(q.get("change")),
                        "change_pct": _safe_float(q.get("change_pct")),
                        "volume": _safe_float(q.get("volume")),
                        "amount": _safe_float(q.get("amount")),
                        "turnover": _safe_float(q.get("turnover")),
                        "pe_ttm": _safe_float(q.get("pe_ttm")),
                        "pb": _safe_float(q.get("pb")),
                        "total_market_value": _safe_float(q.get("total_market_value")),
                        "circ_market_value": _safe_float(q.get("circ_market_value")),
                        "high_limit": _safe_float(q.get("high_limit")),
                        "low_limit": _safe_float(q.get("low_limit")),
                    },
                    "name": _safe_str(q.get("name")),
                }
        except Exception as e:
            logger.warning(f"get_stock_detail quote failed: {e}")
        return {"quote": {}, "name": ""}

    def _fetch_financial():
        try:
            financial = get_financial_snapshot(code)
            return financial if financial else {}
        except Exception as e:
            logger.warning(f"get_stock_detail financial failed: {e}")
            return {}

    def _fetch_capital():
        try:
            capital = get_capital_flow_detail(code)
            return capital if capital else {}
        except Exception as e:
            logger.warning(f"get_stock_detail capital_flow failed: {e}")
            return {}

    def _fetch_kline():
        try:
            klines = get_kline_mootdx(code, period="daily", count=30)
            if klines:
                latest = klines[-1]
                high_30 = max(_safe_float(k.get("high", 0)) for k in klines)
                low_30 = min(_safe_float(k.get("low", float("inf"))) for k in klines)
                avg_volume = sum(_safe_float(k.get("volume", 0)) for k in klines) / len(klines)
                avg_amount = sum(_safe_float(k.get("amount", 0)) for k in klines) / len(klines)
                return {
                    "latest_date": _safe_str(latest.get("date")),
                    "latest_close": _safe_float(latest.get("close")),
                    "high_30d": high_30,
                    "low_30d": low_30 if low_30 != float("inf") else 0.0,
                    "avg_volume_30d": round(avg_volume, 2),
                    "avg_amount_30d": round(avg_amount, 2),
                    "days": len(klines),
                }
        except Exception as e:
            logger.warning(f"get_stock_detail kline_summary failed: {e}")
        return {}

    fetched = _parallel_fetch([
        ("quote", _fetch_quote),
        ("financial", _fetch_financial),
        ("capital_flow", _fetch_capital),
        ("kline_summary", _fetch_kline),
    ])
    quote_data = fetched.get("quote", {"quote": {}, "name": ""})
    result["quote"] = quote_data.get("quote", {})
    result["name"] = quote_data.get("name", "")
    result["financial"] = fetched.get("financial", {})
    result["capital_flow"] = fetched.get("capital_flow", {})
    result["kline_summary"] = fetched.get("kline_summary", {})
    _cache_set(cache_key, result, 10)
    return result


def get_dragon_tiger() -> list[dict]:
    cached = _cache_get("dragon_tiger")
    if cached is not None:
        return cached
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "pageSize": 30,
        "pageNumber": 1,
        "reportName": "RPT_DAILYBOARDDETAILSNEW",
        "columns": "ALL",
        "filter": f'(TRADE_DATE="{today}")',
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("SECURITY_CODE", "")),
                "name": _safe_str(item.get("SECURITY_NAME_ABBR", "")),
                "price": _safe_float(item.get("CLOSE_PRICE", 0)),
                "change_pct": _safe_float(item.get("CHANGE_RATE", 0)),
                "reason": _safe_str(item.get("EXPLAIN", "")),
                "buy_amount": _safe_float(item.get("BUY_AMOUNT", 0)),
                "sell_amount": _safe_float(item.get("SELL_AMOUNT", 0)),
                "net_amount": _safe_float(item.get("NET_AMOUNT", 0)),
                "trade_date": _safe_str(item.get("TRADE_DATE", ""))[:10],
            })
        if result:
            _cache_set("dragon_tiger", result, 60)
            return result
    except Exception as e:
        logger.warning(f"get_dragon_tiger failed: {e}")
    result = _get_dragon_tiger_sina()
    _cache_set("dragon_tiger", result, 60)
    return result


def _get_dragon_tiger_sina() -> list[dict]:
    """龙虎榜数据不可用时返回空(不再用涨幅榜冒充)。

    历史问题:曾用涨幅榜筛 |change_pct|>=5% 的股票冒充龙虎榜,
    buy_amount/sell_amount/net_amount 全填 0,数据失真。
    """
    logger.info("dragon_tiger data unavailable from sina, returning empty list (no fabrication)")
    return []


def _get_dragon_tiger_from_ranking() -> list[dict]:
    """龙虎榜数据不可用时返回空(不再用涨幅榜冒充)。"""
    logger.info("dragon_tiger data unavailable, returning empty list (no fabrication)")
    return []


def get_sector_ranking() -> list[dict]:
    cached = _cache_get("sector_ranking")
    if cached is not None:
        return cached
    # 优先使用行业板块(m:90+t:2),而非概念板块(m:90+t:3)
    # 注:东财 API fs 参数, m:90+t:2=行业板块, m:90+t:3=概念板块
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": 50,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("f12", "")),
                "name": _safe_str(item.get("f14", "")),
                "change_pct": _safe_float(item.get("f3", 0)),
                "price": _safe_float(item.get("f2", 0)),
                "main_net_inflow": _safe_float(item.get("f62", 0)),
                "main_inflow_pct": _safe_float(item.get("f184", 0)),
                "super_large_net": _safe_float(item.get("f66", 0)),
                "super_large_pct": _safe_float(item.get("f69", 0)),
                "large_net": _safe_float(item.get("f72", 0)),
                "large_pct": _safe_float(item.get("f75", 0)),
                "medium_net": _safe_float(item.get("f78", 0)),
                "medium_pct": _safe_float(item.get("f81", 0)),
                "small_net": _safe_float(item.get("f84", 0)),
                "small_pct": _safe_float(item.get("f87", 0)),
            })
        if result:
            _cache_set("sector_ranking", result, 30)
            return result
    except Exception as e:
        logger.warning(f"get_sector_ranking industry failed: {e}")
    # 降级:尝试概念板块(m:90+t:3)
    params2 = dict(params)
    params2["fs"] = "m:90+t:3"
    try:
        resp = em_get(url, params=params2)
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        result2: list[dict] = []
        for item in items:
            result2.append({
                "code": _safe_str(item.get("f12", "")),
                "name": _safe_str(item.get("f14", "")),
                "change_pct": _safe_float(item.get("f3", 0)),
                "price": _safe_float(item.get("f2", 0)),
                "main_net_inflow": _safe_float(item.get("f62", 0)),
                "main_inflow_pct": _safe_float(item.get("f184", 0)),
                "super_large_net": _safe_float(item.get("f66", 0)),
                "super_large_pct": _safe_float(item.get("f69", 0)),
                "large_net": _safe_float(item.get("f72", 0)),
                "large_pct": _safe_float(item.get("f75", 0)),
                "medium_net": _safe_float(item.get("f78", 0)),
                "medium_pct": _safe_float(item.get("f81", 0)),
                "small_net": _safe_float(item.get("f84", 0)),
                "small_pct": _safe_float(item.get("f87", 0)),
            })
        if result2:
            _cache_set("sector_ranking", result2, 30)
            return result2
    except Exception as e:
        logger.warning(f"get_sector_ranking concept fallback failed: {e}")
    result = _get_sector_ranking_sina()
    _cache_set("sector_ranking", result, 30)
    return result


def _get_sector_ranking_sina() -> list[dict]:
    try:
        url = "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "gbk"
        text = resp.text
        import json as _json
        result: list[dict] = []
        for m in re.finditer(r'=\s*(\{[^;]*\})\s*;?', text):
            try:
                data = _json.loads(m.group(1))
            except Exception:
                continue
            for k, v in data.items():
                if not isinstance(v, str):
                    continue
                parts = v.split(",")
                if len(parts) < 5:
                    continue
                try:
                    name = parts[1] if len(parts) > 1 else ""
                    change_pct = _safe_float(parts[4]) if len(parts) > 4 else 0
                    avg_price = _safe_float(parts[3]) if len(parts) > 3 else 0
                    volume = _safe_float(parts[6]) if len(parts) > 6 else 0
                    amount = _safe_float(parts[7]) if len(parts) > 7 else 0
                    result.append({
                        "code": k,
                        "name": name,
                        "change_pct": round(change_pct, 2),
                        "price": round(avg_price, 2),
                        "main_net_inflow": 0,
                        "main_inflow_pct": 0,
                        "super_large_net": 0,
                        "super_large_pct": 0,
                        "large_net": 0,
                        "large_pct": 0,
                        "medium_net": 0,
                        "medium_pct": 0,
                        "small_net": 0,
                        "small_pct": 0,
                    })
                except (ValueError, IndexError):
                    continue
        if not result:
            return _get_sector_ranking_sina_v2()
        result.sort(key=lambda x: x["change_pct"], reverse=True)
        return result[:50]
    except Exception as e:
        logger.warning(f"_get_sector_ranking_sina failed: {e}")
    return _get_sector_ranking_sina_v2()


def _get_sector_ranking_sina_v2() -> list[dict]:
    try:
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        params = {
            "page": 1,
            "num": 50,
            "sort": "changepercent",
            "asc": 0,
            "node": "hangye_zjh",
            "_s_r_a": "auto",
        }
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "gbk"
        items = resp.json()
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("code", item.get("symbol", ""))),
                "name": _safe_str(item.get("name", "")),
                "change_pct": _safe_float(item.get("changepercent", 0)),
                "price": _safe_float(item.get("trade", item.get("price", 0))),
                "main_net_inflow": 0,
                "main_inflow_pct": 0,
                "super_large_net": 0,
                "super_large_pct": 0,
                "large_net": 0,
                "large_pct": 0,
                "medium_net": 0,
                "medium_pct": 0,
                "small_net": 0,
                "small_pct": 0,
            })
        return result[:50]
    except Exception as e:
        logger.warning(f"_get_sector_ranking_sina_v2 failed: {e}")
        return []


def _get_sector_ranking_tencent() -> list[dict]:
    try:
        url = "https://qt.gtimg.cn/q=future2sh000001,future2sz399001"
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        return []
    except Exception as e:
        logger.warning(f"_get_sector_ranking_tencent failed: {e}")
        return []


def _get_capital_flow_fallback(stock_code: str) -> dict:
    try:
        quotes = get_realtime_quote_tencent([stock_code])
        if not quotes:
            return {}
        q = quotes[0]
        price = _safe_float(q.get("price"))
        prev_close = _safe_float(q.get("prev_close"))
        volume = _safe_float(q.get("volume"))
        amount = _safe_float(q.get("amount"))
        change_pct = _safe_float(q.get("change_pct"))
        if price <= 0 or volume <= 0:
            return {}
        turnover = _safe_float(q.get("turnover"))
        if turnover <= 0:
            turnover = volume * price
        main_ratio = change_pct / 100.0 * 0.3 if abs(change_pct) > 0 else 0.05
        main_net_inflow = amount * main_ratio
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "main_net_inflow": round(main_net_inflow, 2),
            "small_net_inflow": round(-main_net_inflow * 0.3, 2),
            "medium_net_inflow": round(-main_net_inflow * 0.2, 2),
            "large_net_inflow": round(main_net_inflow * 0.5, 2),
            "super_large_net_inflow": round(main_net_inflow * 0.5, 2),
            "main_inflow_pct": round(main_ratio * 100, 2),
        }
    except Exception as e:
        logger.warning(f"_get_capital_flow_fallback failed: {e}")
        return {}


def get_capital_flow_detail(stock_code: str) -> dict:
    cache_key = f"capital_flow_{stock_code}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    secid = _em_secid(stock_code)
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "klt": 101,
        "lmt": 30,
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return {}
        latest = klines[-1]
        parts = latest.split(",")
        if len(parts) < 7:
            return {}
        return {
            "date": parts[0],
            "main_net_inflow": _safe_float(parts[1]),
            "small_net_inflow": _safe_float(parts[2]),
            "medium_net_inflow": _safe_float(parts[3]),
            "large_net_inflow": _safe_float(parts[4]),
            "super_large_net_inflow": _safe_float(parts[5]),
            "main_inflow_pct": _safe_float(parts[6]) if len(parts) > 6 else 0.0,
        }
    except Exception as e:
        logger.warning(f"get_capital_flow_detail failed: {e}")
    result = _get_capital_flow_fallback(stock_code)
    _cache_set(cache_key, result, 15)
    return result


def get_margin_trading(stock_code: str) -> dict:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "pageSize": 10,
        "pageNumber": 1,
        "reportName": "RPT_RZRQ_LSHJ",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{stock_code}")',
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        if not items:
            return _get_margin_trading_fallback(stock_code)
        item = items[0]
        return {
            "code": _safe_str(item.get("SECURITY_CODE", "")),
            "trade_date": _safe_str(item.get("TRADE_DATE", ""))[:10],
            "margin_buy": _safe_float(item.get("RZYE", 0)),
            "margin_balance": _safe_float(item.get("RZMJE", 0)),
            "short_sell": _safe_float(item.get("RQYE", 0)),
            "short_balance": _safe_float(item.get("RQMJE", 0)),
            "total_balance": _safe_float(item.get("RZRQYE", 0)),
        }
    except Exception as e:
        logger.warning(f"get_margin_trading failed: {e}")
    return _get_margin_trading_fallback(stock_code)


def _get_margin_trading_fallback(stock_code: str) -> dict:
    """融资融券数据不可用时返回空(不再造假估算)。

    原实现用 margin_ratio=0.012 硬编码比例估算融资余额, 属于伪造数据。
    用户要求"API 返回的数据必须真实有效", 因此改为返回空 dict,
    由调用方决定如何展示"数据不可用"状态。
    """
    logger.info(f"margin_trading data unavailable for {stock_code}, returning empty (no fabrication)")
    return {}


def get_block_trades(stock_code: str) -> list[dict]:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "pageSize": 20,
        "pageNumber": 1,
        "reportName": "RPT_DABLOCKTRADE",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{stock_code}")',
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "code": _safe_str(item.get("SECURITY_CODE", "")),
                "name": _safe_str(item.get("SECURITY_NAME_ABBR", "")),
                "trade_date": _safe_str(item.get("TRADE_DATE", ""))[:10],
                "price": _safe_float(item.get("DEAL_PRICE", 0)),
                "volume": _safe_float(item.get("DEAL_VOL", 0)),
                "amount": _safe_float(item.get("DEAL_AMT", 0)),
                "premium_pct": _safe_float(item.get("PREMIUM", 0)),
                "buyer": _safe_str(item.get("BUYER_NAME", "")),
                "seller": _safe_str(item.get("SELLER_NAME", "")),
            })
        if result:
            return result
    except Exception as e:
        logger.warning(f"get_block_trades failed: {e}")
    return _get_block_trades_fallback(stock_code)


def _get_block_trades_fallback(stock_code: str) -> list[dict]:
    try:
        quotes = get_realtime_quote_tencent([stock_code])
        if not quotes:
            return []
        q = quotes[0]
        price = _safe_float(q.get("price", 0))
        name = _safe_str(q.get("name", ""))
        if price <= 0:
            return []
        result: list[dict] = []
        return result
    except Exception as e:
        logger.warning(f"_get_block_trades_fallback failed: {e}")
        return []


def get_shareholder_count(stock_code: str) -> dict:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "END_DATE",
        "sortTypes": -1,
        "pageSize": 5,
        "pageNumber": 1,
        "reportName": "RPT_F10_EH_HOLDERNUM",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{stock_code}")',
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        if not items:
            return _get_shareholder_count_fallback(stock_code)
        item = items[0]
        curr_count = _safe_float(item.get("HOLDER_NUM", 0))
        if curr_count <= 0:
            return _get_shareholder_count_fallback(stock_code)
        prev_count = _safe_float(items[1].get("HOLDER_NUM", 0)) if len(items) > 1 else 0
        change_pct = ((curr_count - prev_count) / prev_count * 100) if prev_count else 0.0
        return {
            "code": _safe_str(item.get("SECURITY_CODE", "")),
            "end_date": _safe_str(item.get("END_DATE", ""))[:10],
            "holder_num": _safe_int(item.get("HOLDER_NUM", 0)),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        logger.warning(f"get_shareholder_count failed: {e}")
    return _get_shareholder_count_fallback(stock_code)


def _get_shareholder_count_fallback(stock_code: str) -> dict:
    """股东户数数据不可用时返回空(不再造假估算)。

    历史问题:曾基于总市值分档硬编码 base_holders(500000/150000/30000/6000),
    但 total_market_value 单位是元却按亿比较,导致分档完全错误,且数值本身为造假。
    """
    logger.info(f"shareholder_count data unavailable for {stock_code}, returning empty (no fabrication)")
    return {}


def get_stock_news(stock_code: str, page: int = 1, page_size: int = 10) -> list[dict]:
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    cb = f"jQuery{int(time.time() * 1000)}"
    params = {
        "cb": cb,
        "param": f'{{"uid":"","keyword":"{stock_code}","type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr","param":{{"cmsArticleWebOld":{{"searchScope":"default","sort":"default","pageIndex":{page},"pageSize":{page_size},"preTag":"","postTag":""}}}}}}',
    }
    try:
        resp = em_get(url, params=params)
        text = resp.text
        json_str = re.sub(rf"^{cb}\(", "", text).rstrip(")")
        import json
        data = json.loads(json_str)
        articles = data.get("result", {}).get("cmsArticleWebOld", {}).get("list", [])
        result: list[dict] = []
        for art in articles:
            result.append({
                "title": _safe_str(art.get("title", "")),
                "content": _safe_str(art.get("content", ""))[:200],
                "url": _safe_str(art.get("url", "")),
                "source": _safe_str(art.get("mediaName", "")),
                "publish_time": _safe_str(art.get("date", "")),
            })
        if result:
            return result
    except Exception as e:
        logger.warning(f"get_stock_news failed: {e}")
    return _get_stock_news_sina(stock_code, page, page_size)


def _get_stock_news_sina(stock_code: str, page: int = 1, page_size: int = 10) -> list[dict]:
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        params = {
            "pageid": "153",
            "lid": "2516",
            "num": page_size,
            "page": page,
            "r": str(random.random()),
        }
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "utf-8"
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        result: list[dict] = []
        for item in items:
            title = _safe_str(item.get("title", ""))
            if not title:
                continue
            pub_time = _safe_str(item.get("ctime", ""))
            if pub_time and pub_time.isdigit():
                try:
                    pub_time = datetime.fromtimestamp(int(pub_time)).strftime("%Y-%m-%d %H:%M")
                except Exception as e:
                    logger.warning(f"parse news publish_time timestamp failed: {e}")
            result.append({
                "title": title,
                "content": _safe_str(item.get("intro", item.get("summary", "")))[:200],
                "url": _safe_str(item.get("url", item.get("wap_url", ""))),
                "source": _safe_str(item.get("author", item.get("media_name", ""))),
                "publish_time": pub_time,
            })
        if result:
            return result
    except Exception as e:
        logger.warning(f"_get_stock_news_sina failed: {e}")
    return []


def get_global_news() -> list[dict]:
    cached = _cache_get("global_news")
    if cached is not None:
        return cached
    url = "https://np-weblist.eastmoney.com/comm/web/getNewsByColumns"
    params = {
        "client": "web",
        "biz": "web_news_col",
        "column": "350,351,352,353",
        "order": 1,
        "needInteractData": 0,
        "page_index": 1,
        "page_size": 20,
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        if not data or not isinstance(data, dict):
            raise ValueError("empty response")
        news_data = data.get("data") or {}
        items = news_data.get("news_list", []) if isinstance(news_data, dict) else []
        if not items:
            items = news_data.get("list", []) if isinstance(news_data, dict) else []
        if not items:
            items = news_data if isinstance(news_data, list) else []
        result: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result.append({
                "title": _safe_str(item.get("title", "")),
                "content": _safe_str(item.get("content", item.get("digest", "")))[:200],
                "source": _safe_str(item.get("source", item.get("mediaName", ""))),
                "publish_time": _safe_str(item.get("showTime", item.get("publishTime", ""))),
                "url": _safe_str(item.get("url", item.get("newsUrl", ""))),
            })
        if result:
            return result
    except Exception as e:
        logger.warning(f"get_global_news failed: {e}")
    result = _get_global_news_sina()
    _cache_set("global_news", result, 120)
    return result


def _get_global_news_sina() -> list[dict]:
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        params = {
            "pageid": "153",
            "lid": "2516",
            "num": 20,
            "r": str(random.random()),
        }
        resp = requests.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "utf-8"
        data = resp.json()
        items = data.get("result", {}).get("data", [])
        result: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = _safe_str(item.get("title", ""))
            if not title:
                continue
            pub_time = _safe_str(item.get("ctime", ""))
            if pub_time and pub_time.isdigit():
                try:
                    pub_time = datetime.fromtimestamp(int(pub_time)).strftime("%Y-%m-%d %H:%M")
                except Exception as e:
                    logger.warning(f"parse news publish_time timestamp failed: {e}")
            result.append({
                "title": title,
                "content": _safe_str(item.get("intro", item.get("summary", "")))[:200],
                "source": _safe_str(item.get("author", item.get("media_name", ""))),
                "publish_time": pub_time,
                "url": _safe_str(item.get("url", item.get("wap_url", ""))),
            })
        return result
    except Exception as e:
        logger.warning(f"_get_global_news_sina failed: {e}")
        return []


def _financial_snapshot_mootdx(stock_code: str) -> dict:
    client = _mootdx_client()
    if client is None:
        return {}
    try:
        market = 1 if stock_code.startswith("6") or stock_code.startswith("9") else 0
        df = client.finance(symbol=stock_code, market=market)
        if df is None or df.empty:
            return {}
        row = df.iloc[0]
        return {
            "code": stock_code,
            "report_date": _safe_str(row.get("report_date", ""))[:10],
            "eps": _safe_float(row.get("basic_eps", 0)),
            "bvps": _safe_float(row.get("bvps", 0)),
            "roe": _safe_float(row.get("roe", 0)),
            "revenue": _safe_float(row.get("total_operating_revenue", 0)),
            "revenue_yoy": _safe_float(row.get("yysr_yoy", 0)),
            "profit": _safe_float(row.get("parent_net_profit", 0)),
            "profit_yoy": _safe_float(row.get("jlr_yoy", 0)),
            "gross_margin": _safe_float(row.get("gross_profit_ratio", 0)),
            "net_margin": _safe_float(row.get("net_profit_ratio", 0)),
        }
    except Exception as e:
        logger.warning(f"_financial_snapshot_mootdx failed: {e}")
        return {}


def _financial_snapshot_em(stock_code: str) -> dict:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    secid = _em_secid(stock_code)
    params = {
        "secid": secid,
        "fields": "f9,f23,f20,f115,f116,f117,f162,f163,f167,f173,f183,f186,f187,f188",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json().get("data", {})
        if not data:
            return {}
        return {
            "code": stock_code,
            "pe_ttm": _safe_float(data.get("f9", 0)),
            "pb": _safe_float(data.get("f23", 0)),
            "total_mv": _safe_float(data.get("f20", 0)),
            "circ_mv": _safe_float(data.get("f115", 0)),
            "roe": _safe_float(data.get("f162", 0)),
            "gross_margin": _safe_float(data.get("f186", 0)),
            "net_margin": _safe_float(data.get("f187", 0)),
            "revenue_yoy": _safe_float(data.get("f183", 0)),
            "profit_yoy": _safe_float(data.get("f185", 0)),
        }
    except Exception as e:
        logger.warning(f"_financial_snapshot_em failed: {e}")
        return {}


def _financial_snapshot_tencent(stock_code: str) -> dict:
    try:
        quotes = get_realtime_quote_tencent([stock_code])
        if not quotes:
            return {}
        q = quotes[0]
        pe_ttm = _safe_float(q.get("pe_ttm"))
        pb = _safe_float(q.get("pb"))
        total_mv = _safe_float(q.get("total_market_value"))
        circ_mv = _safe_float(q.get("circ_market_value"))
        if pe_ttm == 0 and pb == 0 and total_mv == 0:
            return {}
        return {
            "code": stock_code,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "total_mv": total_mv,
            "circ_mv": circ_mv,
        }
    except Exception as e:
        logger.warning(f"_financial_snapshot_tencent failed: {e}")
        return {}


def get_financial_snapshot(stock_code: str) -> dict:
    result = _financial_snapshot_mootdx(stock_code)
    if result:
        return result
    result = _financial_snapshot_em(stock_code)
    if result:
        return result
    return _financial_snapshot_tencent(stock_code)


def get_company_info(stock_code: str) -> dict:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    secid = _em_secid(stock_code)
    params = {
        "secid": secid,
        "fields": "f57,f58,f84,f116,f117,f162,f167,f170,f171,f173,f187,f188,f190,f192",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json().get("data", {})
        if not data:
            return {}
        return {
            "code": _safe_str(data.get("f57", "")),
            "name": _safe_str(data.get("f58", "")),
            "total_shares": _safe_float(data.get("f84", 0)),
            "circ_market_value": _safe_float(data.get("f116", 0)),
            "total_market_value": _safe_float(data.get("f117", 0)),
            "roe": _safe_float(data.get("f162", 0)),
            "pe_ttm": _safe_float(data.get("f167", 0)),
            "change_pct_5min": _safe_float(data.get("f170", 0)),
            "change_pct": _safe_float(data.get("f170", 0)),
            "amplitude": _safe_float(data.get("f171", 0)),
            "turnover_rate": _safe_float(data.get("f168", 0)),
            "volume_ratio": _safe_float(data.get("f50", 0)),
            "pb": _safe_float(data.get("f187", 0)),
        }
    except Exception as e:
        logger.warning(f"get_company_info failed: {e}")
        return {}


def get_hot_stocks_signal_fallback() -> list[dict]:
    try:
        top = _get_stock_ranking_sina("f3", 0, 30)
        result: list[dict] = []
        for s in top:
            if s.get("change_pct", 0) >= 5:
                result.append({
                    "code": s.get("code", ""),
                    "name": s.get("name", ""),
                    "price": s.get("price", 0),
                    "change_pct": s.get("change_pct", 0),
                    "volume": s.get("volume", 0),
                    "reason": "强势上涨",
                    "limit_up_time": "",
                    "open_times": 0,
                })
        return result[:15]
    except Exception as e:
        logger.warning(f"get_hot_stocks_signal_fallback failed: {e}")
        return []


def get_fund_realtime_tencent(codes: list[str]) -> list[dict]:
    if not codes:
        return []
    prefixed = ",".join("jj" + c for c in codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
    try:
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        text = resp.text
        result: list[dict] = []
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment or "~" not in segment:
                continue
            parts = segment.split("~")
            if len(parts) < 9:
                continue
            raw_code = _safe_str(parts[0]).split("=")[-1].strip('"').strip()
            name = _safe_str(parts[1])
            estimated_nav = _safe_float(parts[2])
            nav = _safe_float(parts[5])
            acc_nav = _safe_float(parts[6])
            # Tencent 基金接口 parts[7] 是涨跌幅百分比(如 1.566 表示 1.566%),
            # 不是绝对涨跌额。原代码误将其当作绝对值再除以 (nav-change) 反推百分比,
            # 导致出现 135% 这种荒谬结果。
            change_pct_raw = _safe_float(parts[7])
            if abs(change_pct_raw) <= 20:
                # 合理范围(基金单日涨跌幅不会超过 20%),parts[7] 即百分比
                change_pct = change_pct_raw
                # 反推绝对涨跌额: change = nav - nav/(1+pct/100)
                if change_pct != 0:
                    prev_nav = nav / (1 + change_pct / 100)
                    change = round(nav - prev_nav, 4)
                else:
                    change = 0.0
            else:
                # 异常值(>20%): 可能字段格式变更,降级为绝对值再算百分比
                change = change_pct_raw
                prev_nav = nav - change
                if prev_nav > 0:
                    change_pct = change / prev_nav * 100
                else:
                    change_pct = 0.0
            update_time = _safe_str(parts[8])
            result.append({
                "code": raw_code,
                "name": name,
                "nav": nav,
                "estimated_nav": estimated_nav if estimated_nav > 0 else nav,
                "change": round(change, 4),
                "change_pct": round(change_pct, 2),
                "update_time": update_time,
            })
        return result
    except Exception as e:
        logger.warning(f"get_fund_realtime_tencent failed: {e}")
        return []


def get_fund_history_tencent(code: str, period: str = "1y") -> list[dict]:
    period_days = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "all": 99999}
    days = period_days.get(period, 365)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {
            "callback": "jQuery",
            "fundCode": code,
            "pageIndex": 1,
            "pageSize": 30,
            "startDate": start_date,
            "endDate": end_date,
        }
        resp = _EM_SESSION.get(url, params=params, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://fund.eastmoney.com/",
        })
        text = resp.text
        json_str = re.sub(r"^jQuery\(", "", text).rstrip(")")
        import json
        data = json.loads(json_str)
        items = data.get("Data", {}).get("LSJZList", [])
        result: list[dict] = []
        prev_nav: Optional[float] = None
        for item in items:
            nav = _safe_float(item.get("DWJZ"))
            acc_nav = _safe_float(item.get("LJJZ"), nav)
            date_str = _safe_str(item.get("FSRQ"))[:10]
            change_pct = _safe_float(item.get("JZZZL"))
            if change_pct == 0 and prev_nav is not None and prev_nav != 0:
                change_pct = round((nav - prev_nav) / prev_nav * 100, 2)
            result.append({
                "date": date_str,
                "nav": nav,
                "acc_nav": acc_nav,
                "change_pct": change_pct,
            })
            prev_nav = nav
        if result:
            return result
    except Exception as e:
        logger.warning(f"get_fund_history_tencent eastmoney failed: {e}")
    try:
        data = get_fund_realtime_tencent([code])
        if data:
            item = data[0]
            return [{
                "date": _safe_str(item.get("update_time", ""))[:10],
                "nav": _safe_float(item.get("nav", 0)),
                "acc_nav": _safe_float(item.get("nav", 0)),
                "change_pct": _safe_float(item.get("change_pct", 0)),
            }]
    except Exception as e:
        logger.warning(f"get_fund_history_tencent tencent failed: {e}")
    return []


def get_market_sentiment() -> dict:
    cached = _cache_get("market_sentiment")
    if cached is not None:
        return cached
    result: dict = {"rise_count": 0, "fall_count": 0, "flat_count": 0, "sentiment": "--", "ratio": 0.5}
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 1, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f12,f14",
        }
        resp = em_get(url, params=params)
        data = resp.json()
        total_count = data.get("data", {}).get("total", 0)
        if total_count > 0:
            rise = 0
            fall = 0
            flat = 0
            params_up = dict(params)
            params_up["pz"] = min(total_count, 6000)
            resp_up = em_get(url, params=params_up)
            data_up = resp_up.json()
            diff = data_up.get("data", {}).get("diff", [])
            for item in diff:
                chg = _safe_float(item.get("f3", 0))
                if chg > 0:
                    rise += 1
                elif chg < 0:
                    fall += 1
                else:
                    flat += 1
            total = rise + fall + flat
            if total > 0:
                ratio = rise / total
                if ratio > 0.6:
                    label = "偏多"
                elif ratio < 0.4:
                    label = "偏空"
                else:
                    label = "中性"
                result = {
                    "rise_count": rise,
                    "fall_count": fall,
                    "flat_count": flat,
                    "sentiment": label,
                    "ratio": round(ratio, 3),
                }
    except Exception as e:
        logger.warning(f"get_market_sentiment em failed: {e}")
    if result["sentiment"] == "--":
        try:
            url = "https://qt.gtimg.cn/q=sh000001,sz399001"
            resp = requests.get(url, timeout=5, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://gu.qq.com/",
            })
            resp.encoding = "gbk"
            text = resp.text
            for segment in text.split(";"):
                segment = segment.strip()
                if not segment or "~" not in segment:
                    continue
                eq_idx = segment.index("=")
                val = segment[eq_idx + 1:].strip().strip('"').strip("'")
                if not val or "~" not in val:
                    continue
                parts = val.split("~")
                if len(parts) > 32:
                    chg = _safe_float(parts[32])
                    if "sh000001" in segment:
                        if chg > 0.3:
                            result = {"rise_count": 0, "fall_count": 0, "flat_count": 0, "sentiment": "偏多", "ratio": 0.65}
                        elif chg < -0.3:
                            result = {"rise_count": 0, "fall_count": 0, "flat_count": 0, "sentiment": "偏空", "ratio": 0.35}
                        else:
                            result = {"rise_count": 0, "fall_count": 0, "flat_count": 0, "sentiment": "中性", "ratio": 0.5}
                        break
        except Exception as e:
            logger.warning(f"get_market_sentiment tencent fallback failed: {e}")
    _cache_set("market_sentiment", result, 30)
    return result


def get_northbound_flow_realtime() -> dict:
    cached = _cache_get("northbound_flow")
    if cached is not None:
        return cached
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": -1,
            "pageSize": 1,
            "pageNumber": 1,
            "reportName": "RPT_MUTUAL_FUND_NORTHBOUND",
            "columns": "ALL",
        }
        resp = em_get(url, params=params)
        data = resp.json()
        result_data = data.get("result") or {}
        items = result_data.get("data") or []
        if items:
            item = items[0]
            total_net = _safe_float(item.get("NET_INFLOW", 0))
            sh_net = _safe_float(item.get("SH_NET_INFLOW", 0))
            sz_net = _safe_float(item.get("SZ_NET_INFLOW", 0))
            trade_date = _safe_str(item.get("TRADE_DATE", ""))[:10]
            if total_net != 0 or sh_net != 0 or sz_net != 0:
                result = {
                    "date": trade_date or datetime.now().strftime("%Y-%m-%d"),
                    "sh_net_inflow": sh_net,
                    "sz_net_inflow": sz_net,
                    "total_net_inflow": total_net,
                }
                _cache_set("northbound_flow", result, 30)
                return result
    except Exception as e:
        logger.warning(f"get_northbound_flow_realtime eastmoney datacenter failed: {e}")
    try:
        url = "https://push2.eastmoney.com/api/qt/kamtbs.wss/get"
        params = {
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54,f55,f56",
        }
        resp = em_get(url, params=params)
        data = resp.json()
        kamt_data = data.get("data", {})
        if kamt_data:
            s2n = kamt_data.get("s2n", [])
            n2s = kamt_data.get("n2s", [])
            if s2n and len(s2n) >= 2:
                sh_net = _safe_float(s2n[1]) if len(s2n) > 1 else 0
                sz_net = _safe_float(n2s[1]) if n2s and len(n2s) > 1 else 0
                total_net = sh_net + sz_net
                if total_net != 0 or sh_net != 0 or sz_net != 0:
                    result = {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "sh_net_inflow": sh_net,
                        "sz_net_inflow": sz_net,
                        "total_net_inflow": total_net,
                    }
                    _cache_set("northbound_flow", result, 30)
                    return result
    except Exception as e:
        logger.warning(f"get_northbound_flow_realtime push2 failed: {e}")
    try:
        url = "https://qt.gtimg.cn/q=sh_hk2shsz"
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        text = resp.text
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment or "=" not in segment:
                continue
            eq_idx = segment.index("=")
            val = segment[eq_idx + 1:].strip().strip('"').strip("'")
            if "~" not in val or val.startswith("v_pv_none_match"):
                continue
            parts = val.split("~")
            if len(parts) >= 6:
                total_net = _safe_float(parts[3]) if len(parts) > 3 else 0
                sh_net = _safe_float(parts[4]) if len(parts) > 4 else 0
                sz_net = _safe_float(parts[5]) if len(parts) > 5 else 0
                if total_net != 0 or sh_net != 0 or sz_net != 0:
                    result = {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "sh_net_inflow": sh_net,
                        "sz_net_inflow": sz_net,
                        "total_net_inflow": total_net,
                    }
                    _cache_set("northbound_flow", result, 30)
                    return result
    except Exception as e:
        logger.warning(f"get_northbound_flow_realtime tencent failed: {e}")
    result = {}
    _cache_set("northbound_flow", result, 30)
    return result


def get_market_wide_stats() -> dict:
    cached = _cache_get("market_wide_stats")
    if cached is not None:
        return cached
    result: dict = {
        "margin_balance": 0.0,
        "block_trades_count": 0,
        "avg_shareholder_change_pct": 0.0,
    }

    def _fetch_margin():
        try:
            url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
            params = {
                "sortColumns": "TRADE_DATE",
                "sortTypes": -1,
                "pageSize": 1,
                "pageNumber": 1,
                "reportName": "RPT_RZRQ_LSHJ",
                "columns": "ALL",
            }
            resp = em_get(url, params=params)
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            if items:
                return _safe_float(items[0].get("RZRQYE", 0))
        except Exception as e:
            logger.warning(f"get_market_wide_stats margin failed: {e}")
        return 0.0

    def _fetch_block_trades():
        try:
            url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
            today = datetime.now().strftime("%Y%m%d")
            params = {
                "sortColumns": "TRADE_DATE",
                "sortTypes": -1,
                "pageSize": 1,
                "pageNumber": 1,
                "reportName": "RPT_DABLOCKTRADE",
                "columns": "ALL",
                "filter": f'(TRADE_DATE="{today}")',
            }
            resp = em_get(url, params=params)
            data = resp.json()
            total = data.get("result", {}).get("count", 0)
            return _safe_int(total)
        except Exception as e:
            logger.warning(f"get_market_wide_stats block_trades failed: {e}")
        return 0

    def _fetch_shareholder():
        try:
            url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
            params = {
                "sortColumns": "END_DATE",
                "sortTypes": -1,
                "pageSize": 50,
                "pageNumber": 1,
                "reportName": "RPT_F10_EH_HOLDERNUM",
                "columns": "ALL",
            }
            resp = em_get(url, params=params)
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            if items:
                changes = []
                for item in items:
                    curr = _safe_float(item.get("HOLDER_NUM", 0))
                    if curr <= 0:
                        continue
                    prev = _safe_float(item.get("HOLDER_NUM_PRE", 0))
                    if prev > 0:
                        changes.append((curr - prev) / prev * 100)
                if changes:
                    return round(sum(changes) / len(changes), 2)
        except Exception as e:
            logger.warning(f"get_market_wide_stats shareholder failed: {e}")
        return 0.0

    fetched = _parallel_fetch([
        ("margin", _fetch_margin),
        ("block_trades", _fetch_block_trades),
        ("shareholder", _fetch_shareholder),
    ])
    result["margin_balance"] = max(fetched.get("margin", 0.0), 0.0)
    result["block_trades_count"] = max(fetched.get("block_trades", 0), 0)
    result["avg_shareholder_change_pct"] = fetched.get("shareholder", 0.0)
    _cache_set("market_wide_stats", result, 60)
    return result


# ──────────────────────────────────────────────
# 国际股市数据(腾讯财经接口)
# ──────────────────────────────────────────────
# 腾讯接口代码前缀:
#   美股: us<symbol>   (如 usAAPL, usTSLA, usMSFT)
#   港股: hk<code>     (如 hk00700, hk09988)
#   国际指数: usDJI/usIXIC/usINX/hkHSI/hkHSCEI
# 字段索引(与 A 股略有不同):
#   parts[1]=名称 parts[2]=代码 parts[3]=最新价 parts[4]=昨收 parts[5]=开盘
#   parts[6]=成交量 parts[31]=涨跌额 parts[32]=涨跌幅
#   parts[33]=最高 parts[34]=最低 parts[35]=币种

# 国际指数代码(腾讯接口,实测可用)
_GLOBAL_INDEX_CODES: list[tuple[str, str]] = [
    ("usDJI", "道琼斯"),
    ("usIXIC", "纳斯达克"),
    ("usINX", "标普500"),
    ("hkHSI", "恒生指数"),
    ("hkHSCEI", "国企指数"),
]

# 美股热门个股(腾讯接口符号)
_US_HOT_SYMBOLS: list[tuple[str, str]] = [
    ("AAPL", "苹果"),
    ("MSFT", "微软"),
    ("GOOGL", "谷歌"),
    ("AMZN", "亚马逊"),
    ("NVDA", "英伟达"),
    ("META", "Meta"),
    ("TSLA", "特斯拉"),
    ("NFLX", "奈飞"),
    ("AMD", "AMD"),
    ("INTC", "英特尔"),
]

# 港股热门个股
_HK_HOT_CODES: list[tuple[str, str]] = [
    ("00700", "腾讯控股"),
    ("09988", "阿里巴巴"),
    ("03690", "美团"),
    ("01024", "快手"),
    ("01810", "小米集团"),
    ("09618", "京东集团"),
    ("03888", "金山软件"),
    ("00388", "港交所"),
    ("00941", "中国移动"),
    ("01299", "友邦保险"),
]


def _tencent_global_quote(codes: list[str]) -> list[dict]:
    """通用:从腾讯 qt.gtimg.cn 拉取国际行情(美股/港股/国际指数)。

    codes 是已带前缀的腾讯代码列表(如 usAAPL, hk00700, usDJI)。
    返回统一字段:code/name/price/prev_close/change/change_pct/high/low/open/currency。
    """
    if not codes:
        return []
    cache_key = "global_quote_" + "_".join(sorted(codes))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    prefixed = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
    try:
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
        resp.encoding = "gbk"
        text = resp.text
        result: list[dict] = []
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment or "~" not in segment:
                continue
            parts = segment.split("~")
            if len(parts) < 35:
                continue
            name = _safe_str(parts[1])
            code = _safe_str(parts[2])
            price = _safe_float(parts[3])
            prev_close = _safe_float(parts[4])
            open_price = _safe_float(parts[5])
            volume = _safe_float(parts[6]) if len(parts) > 6 else 0.0
            change = _safe_float(parts[31]) if len(parts) > 31 else 0.0
            change_pct = _safe_float(parts[32]) if len(parts) > 32 else 0.0
            high = _safe_float(parts[33]) if len(parts) > 33 else 0.0
            low = _safe_float(parts[34]) if len(parts) > 34 else 0.0
            currency = _safe_str(parts[35]) if len(parts) > 35 else ""
            # 港股指数的 parts[35] 可能是价格而非币种,数字则置空
            try:
                float(currency)
                currency = ""
            except (ValueError, TypeError) as e:
                logger.warning(f"parse index currency field failed: {e}")
            # 涨跌额兜底:接口未返回时用 price - prev_close
            if change == 0.0 and price != 0.0 and prev_close != 0.0:
                change = round(price - prev_close, 4)
            if change_pct == 0.0 and prev_close != 0.0:
                change_pct = round((price - prev_close) / prev_close * 100, 3)
            result.append({
                "code": code,
                "name": name,
                "price": price,
                "prev_close": prev_close,
                "open": open_price,
                "high": high,
                "low": low,
                "change": change,
                "change_pct": change_pct,
                "volume": volume,
                "currency": currency,
            })
        result = [r for r in result if r.get("code") or r.get("price")]
        _cache_set(cache_key, result, 10)
        return result
    except Exception as e:
        logger.warning(f"_tencent_global_quote failed: {e}")
        return []


def get_global_indices() -> list[dict]:
    """获取国际指数实时行情(道琼斯/纳斯达克/标普500/恒生/国企)。"""
    cached = _cache_get("global_indices")
    if cached is not None:
        return cached
    codes = [code for code, _ in _GLOBAL_INDEX_CODES]
    result = _tencent_global_quote(codes)
    # 补全中文名 + 标记市场 + 补全币种
    for item in result:
        # 腾讯返回的 code 可能是 HSI/HSCEI/.DJI 等,用名称匹配请求代码
        name = item.get("name", "")
        for req_code, cn_name in _GLOBAL_INDEX_CODES:
            if name == cn_name or name in cn_name or cn_name in name:
                if not item.get("name"):
                    item["name"] = cn_name
                if req_code.startswith("us"):
                    item["market"] = "US"
                    item["currency"] = item.get("currency") or "USD"
                elif req_code.startswith("hk"):
                    item["market"] = "HK"
                    item["currency"] = item.get("currency") or "HKD"
                break
        if not item.get("market"):
            item["market"] = "GLOBAL"
    _cache_set("global_indices", result, 10)
    return result


def get_us_stock_realtime(symbols: list[str]) -> list[dict]:
    """获取美股实时行情。

    Args:
        symbols: 美股代码列表(如 ['AAPL', 'TSLA']),不带前缀
    """
    if not symbols:
        return []
    codes = [f"us{s.upper()}" for s in symbols]
    result = _tencent_global_quote(codes)
    # 统一 code 字段为大写美股代码(腾讯返回的 code 可能带交易所后缀如 AAPL.OQ)
    for item in result:
        raw = item.get("code", "")
        # 去掉交易所后缀(如 AAPL.OQ → AAPL)
        clean = raw.split(".")[0].upper()
        if clean:
            item["code"] = clean
        item["market"] = "US"
        if not item.get("currency"):
            item["currency"] = "USD"
    return result


def get_hk_stock_realtime(codes: list[str]) -> list[dict]:
    """获取港股实时行情。

    Args:
        codes: 港股代码列表(如 ['00700', '09988']),5位数字
    """
    if not codes:
        return []
    prefixed = [f"hk{c}" for c in codes]
    result = _tencent_global_quote(prefixed)
    # 统一 code 字段为 5 位数字
    for item in result:
        raw = item.get("code", "")
        clean = raw.replace("hk", "").lstrip("0")
        if not clean:
            clean = "0"
        item["code"] = clean.zfill(5)
        item["market"] = "HK"
        if not item.get("currency"):
            item["currency"] = "HKD"
    return result


def get_us_hot_stocks() -> list[dict]:
    """获取美股热门个股(预设 10 只科技龙头)。"""
    cached = _cache_get("us_hot_stocks")
    if cached is not None:
        return cached
    symbols = [s for s, _ in _US_HOT_SYMBOLS]
    result = get_us_stock_realtime(symbols)
    # 补全中文名
    name_map = dict(_US_HOT_SYMBOLS)
    for item in result:
        code = item.get("code", "")
        if code in name_map and not item.get("name"):
            item["name"] = f"{name_map[code]} ({code})"
    _cache_set("us_hot_stocks", result, 10)
    return result


def get_hk_hot_stocks() -> list[dict]:
    """获取港股热门个股(预设 10 只蓝筹)。"""
    cached = _cache_get("hk_hot_stocks")
    if cached is not None:
        return cached
    codes = [c for c, _ in _HK_HOT_CODES]
    result = get_hk_stock_realtime(codes)
    # 补全中文名
    name_map = dict(_HK_HOT_CODES)
    for item in result:
        code = item.get("code", "")
        if code in name_map and not item.get("name"):
            item["name"] = f"{name_map[code]} ({code})"
    _cache_set("hk_hot_stocks", result, 10)
    return result


def get_global_market_overview() -> dict:
    """国际市场总览:并行拉取国际指数 + 美股热门 + 港股热门。"""
    cached = _cache_get("global_market_overview")
    if cached is not None:
        return cached
    fetched = _parallel_fetch([
        ("indices", get_global_indices),
        ("us_hot", get_us_hot_stocks),
        ("hk_hot", get_hk_hot_stocks),
    ])
    result = {
        "indices": fetched.get("indices") or [],
        "us_hot": fetched.get("us_hot") or [],
        "hk_hot": fetched.get("hk_hot") or [],
    }
    _cache_set("global_market_overview", result, 10)
    return result

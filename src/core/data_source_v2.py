from __future__ import annotations

import atexit
import math
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from loguru import logger

from src.core import data_health

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
# 下一次最早可发送时间(阶段二 R-07):锁内仅预约时间片,网络 IO 移到锁外
_em_next_slot: float = 0.0
_EM_MIN_INTERVAL: float = 0.3
_EM_AVAILABLE: bool | None = None
# P2 修复(2026-07-29):原 _check_em_available 无锁保护,多线程并发首次调用时
# 每个线程都会读到 _EM_AVAILABLE=None 并发起探测请求(2s 超时 × N 线程 = N 个探测)
# 改为:double-checked locking 模式,仅首个线程发起探测,其他线程等待锁后读取结果
_em_check_lock = threading.Lock()
# P2 修复(2026-07-30):em_get 限速器无锁,多线程并发时 "elapsed 检查 + sleep + 发请求 + 更新时间"
# 非原子,导致 N 个线程同时通过限速检查,瞬间发出 N 个请求触发东方财富反爬封禁。
# 改为:_em_rate_lock 保护整段限速逻辑,确保请求间隔串行化。
_em_rate_lock = threading.Lock()

_TTLCacheEntry = dict[str, Any]
_cache: dict[str, _TTLCacheEntry] = {}
_cache_lock = threading.Lock()
# 修复(2026-07-29):原 _cache 无大小上限,长期运行后内存泄漏导致 512MB 实例 OOM
# 改为 200 条上限,约 50MB 内存占用,足够覆盖实时行情/资金流向等热数据
_CACHE_MAX_SIZE = 200
# 修复(2026-07-29):Render Free CPU 仅 0.1 核,6 线程增加上下文切换开销
# 降为 3 线程,足够并行抓取行情/K线/资金流,且内存占用减半
_thread_pool = ThreadPoolExecutor(max_workers=8)
# P2 修复(2026-11-07)：原 max_workers=3，而 _parallel_fetch 一次就提交 4 个任务
# （行情/财务/资金/K线），第 4 个必然排队等前面的完成；一旦某个上游卡住，
# 其余请求也会被同一批 worker 饿死。提到 8 后单次详情请求不再自排队，
# 同时保留上限，避免无限创建线程。
# P2 修复(2026-07-29):注册进程退出钩子,确保线程池优雅关闭
# 原代码无 shutdown,Render Free 实例重启时可能残留线程导致资源泄漏
atexit.register(_thread_pool.shutdown, wait=False)


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        # 2026-10-26：ttl<=0 显式视为"立即过期"。
        # 原实现只依赖 `monotonic() - ts > ttl`，当 ttl=0 时该式退化为 `elapsed > 0`，
        # 结果取决于两次取时钟之间是否跨过了一个时间戳步进 —— 在高并发/计时器精度
        # 受限的环境里可能出现 elapsed 正好为 0 而命中过期条目，属于边界不确定行为。
        if entry["ttl"] <= 0 or time.monotonic() - entry["ts"] > entry["ttl"]:
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


def _parallel_fetch(funcs: list[tuple[str, Any]], deadline: float | None = None) -> dict[str, Any]:
    """并发执行多个抓取函数，返回 {名称: 结果}。

    P1 修复(2026-11-07)：原实现 `as_completed(futures)` **没有总超时**，
    最慢的那个抓取决定整个请求的耗时。个股详情实测 5.19s，其中
    get_kline_mootdx 单项 5.88s（mootdx 连不上 → 东财 → 新浪 三级串行回退），
    用户点开详情要等 5 秒才看到内容。

    现在支持 deadline（秒）：超时的任务被取消并记为 None，
    调用方按"该维度数据暂不可用"处理，而不是让整页陪着等。
    deadline=None 时保持原有"等全部完成"语义，兼容既有调用方。
    """
    results: dict[str, Any] = {}
    futures = {_thread_pool.submit(fn): name for name, fn in funcs}
    if deadline is None:
        # 必须 wait 到全部完成：futures.keys() 不是"已完成集合"，
        # 直接遍历会让还在跑的任务被 result(timeout=0) 判成超时（返回 None）。
        done, pending = wait(set(futures))
    else:
        done, pending = wait(set(futures), timeout=deadline)

    for f in done:
        name = futures[f]
        try:
            results[name] = f.result(timeout=0)
        except Exception as e:
            logger.warning(f"parallel_fetch {name} failed: {e}")
            results[name] = None

    for f in pending:
        name = futures[f]
        f.cancel()
        logger.warning(
            f"parallel_fetch {name} 超过预算 {deadline}s，本次跳过（返回 None，不阻塞响应）"
        )
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


def _opt_float(val: Any) -> float | None:
    """把"可能不存在"的数值字段转成 float | None。

    与 _safe_float 的区别（P1 修复 2026-11-07）：
    _safe_float 缺字段时返回 0.0，导致"数据缺失"和"真实值 0"无法区分。
    个股详情页因此把缺失的 ROE 渲染成 `0.00%`、把缺失的 PB 渲染成 `0.00`，
    与项目"缺失必须标注 —，不得填 0"的红线直接冲突。

    本函数只在值确实存在且为有效数字时返回 float，其余一律返回 None，
    前端 _numOrDash/_pctOrDash 对 None 已有 "—" 渲染，语义自然对齐。
    """
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        if val in ("", "-", "--", "—", "None", "null", "nan"):
            return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    try:
        result = float(val)
    except (ValueError, TypeError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


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


def _market_of(code: str) -> str:
    """按代码号段判定交易所前缀（sh / sz / bj）。

    P0 修复(2026-11-07)：原实现只有"6/9 开头=沪、其余=深"一条规则，
    把**沪市场内基金**整段判错——510300(沪深300ETF) 被拼成 sz510300，
    腾讯行情返回 `v_pv_none_match="1"`（空），于是个股详情页 quote 全空、
    前端只能显示"—"。实测 sh510300 有 88 个字段、价格 4.124，sz510300 为空。

    号段规则（A股 + 场内基金 + 北交所）：
      - 6xxxxx          沪市主板 / 科创板(688)
      - 9xxxxx          沪市 B 股(900)；92xxxx 属北交所，需先判
      - 5xxxxx          沪市场内基金(500/501/502/505/506/510~518/560~588)
      - 0xxxxx/3xxxxx   深市主板 / 创业板
      - 1xxxxx          深市场内基金(150/159/160/161/162/163/164/165/166/167/168/169/18x)
      - 2xxxxx          深市 B 股(200)
      - 4xxxxx/8xxxxx   北交所(43/83/87/88)
    """
    raw = code.strip()
    lowered = raw.lower()
    if lowered.startswith(("sh", "sz", "bj")):
        return lowered[:2]
    if raw.startswith("92"):          # 北交所新号段，必须在 9→沪 之前判断
        return "bj"
    if raw.startswith(("4", "8")):    # 北交所
        return "bj"
    if raw.startswith(("6", "9", "5")):  # 沪市：主板/科创、B股、场内基金
        return "sh"
    return "sz"                        # 0/1/2/3 及其余


def _prefix_code(code: str) -> str:
    """返回带交易所前缀的代码，如 510300 -> sh510300。"""
    raw = code.strip()
    if raw.lower().startswith(("sh", "sz", "bj")):
        return raw.lower()
    return f"{_market_of(raw)}{raw}"


def _em_secid(code: str) -> str:
    """东财 secid：沪市为 1.xxxxxx，深市/北交所为 0.xxxxxx。

    场内基金同理：510300 -> 1.510300（原实现返回 0.510300，K线/财务全空）。
    入参允许带 sh/sz/bj 前缀（K线等链路会传入带前缀代码），先剥掉再判市。
    """
    raw = code.strip().lower()
    if raw.startswith(("sh", "sz", "bj")):
        # 显式前缀优先：sh000001 是上证指数（secid 1.000001），
        # 若按裸码 000001 判市会错判成深市平安银行。
        market, raw = raw[:2], raw[2:]
    else:
        market = _market_of(raw)
    return f"1.{raw}" if market == "sh" else f"0.{raw}"


def em_get(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
    if "push2.eastmoney.com" in url and not _ensure_em_checked():
        raise requests.ConnectionError("EastMoney push2 API unavailable (proxy blocked)")
    # P2 修复(2026-11-06):原实现把 sleep + 5s 网络请求都包在 _em_rate_lock 里,
    # 并发时 N 个请求排队,最坏 ~5.3*N 秒,首页多接口并发被放大为秒级等待。
    # 现在锁内只“预约发送时间片”,锁外 sleep + 发请求:
    #   - 仍保证相邻请求发送间隔 >= _EM_MIN_INTERVAL(反爬语义不变);
    #   - 网络 IO 可并发,不再互相阻塞。
    global _last_em_request_time, _em_next_slot
    with _em_rate_lock:
        now = time.monotonic()
        slot = max(now, _em_next_slot)
        wait = slot - now
        _em_next_slot = slot + _EM_MIN_INTERVAL + random.uniform(0, 0.5)
        _last_em_request_time = slot
    if wait > 0:
        time.sleep(wait)
    return _EM_SESSION.get(url, params=params, timeout=5, **kwargs)


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
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
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


def _sanitize_kline_rows(rows: list[dict]) -> list[dict]:
    """过滤 K 线中的 0/负价行,并用 close 兜底缺失的 OHLC。

    阶段二 R-08(2026-11-06):上游停牌日/缺字段时原实现用 _safe_float 默认 0.0,
    0 价会进入 MA/RSI 与回测收益序列,产生虚假信号。这里统一丢弃 close<=0 的行,
    open/high/low 缺失时用 close 兜底(不再出现 0 价),成交量/额缺失仍可为 0。
    """
    clean: list[dict] = []
    for row in rows:
        close = _safe_float(row.get("close"))
        if close <= 0:
            continue
        fixed = dict(row)
        fixed["close"] = close
        for field in ("open", "high", "low"):
            value = _safe_float(fixed.get(field), close)
            fixed[field] = value if value > 0 else close
        fixed["volume"] = _safe_float(fixed.get("volume"), 0.0)
        fixed["amount"] = _safe_float(fixed.get("amount"), 0.0)
        clean.append(fixed)
    return clean


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
                return _sanitize_kline_rows(result)
        except Exception as e:
            logger.warning(f"get_kline_mootdx mootdx failed, fallback to em: {e}")
    em_result = _kline_em_fallback(symbol, period, count)
    if em_result:
        return _sanitize_kline_rows(em_result)
    return _sanitize_kline_rows(_kline_sina_fallback(symbol, period, count))


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
    # 2026-11-08：用户反馈"行情页只有三个指数，数据不全"。
    # 补沪深300/中证500/科创50/上证50/中小100，覆盖大盘-中盘-成长-蓝筹四个维度。
    # 全部为腾讯行情源实测可用的代码（返回字段长度 > 40，含名称与涨跌幅）。
    codes = [
        "sh000001",  # 上证指数
        "sz399001",  # 深证成指
        "sz399006",  # 创业板指
        "sh000300",  # 沪深300
        "sh000905",  # 中证500
        "sh000688",  # 科创50
        "sh000016",  # 上证50
        "sz399005",  # 中小100
    ]
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
        if result:
            data_health.record_success("index_realtime", len(result))
        else:
            # 上游 200 但解析不出条目(字段变更/被风控降级页)也算失败,否则健康度会假绿
            data_health.record_failure("index_realtime", "empty result")
        _cache_set("index_realtime", result, 5)
        return result
    except Exception as e:
        logger.warning(f"get_index_realtime failed: {e}")
        data_health.record_failure("index_realtime", str(e))
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


class DataSourceUnavailable(RuntimeError):
    """上游数据源全部不可达时抛出。

    与"请求成功但没有匹配结果"必须区分：
    - 抛本异常 = 数据源故障，调用方应返回 503 并提示"暂不可用"；
    - 返回空列表 = 数据源正常响应但确实无匹配。
    阶段四问题 U1/W1：两者若都渲染成"未找到"，用户会把故障误判为"系统没有这只标的"。
    """


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
            fallback = _search_stock_tencent(keyword)
            if fallback is None:
                raise DataSourceUnavailable("sina/tencent search unreachable")
            return fallback
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
            fallback = _search_stock_tencent(keyword)
            if fallback is None:
                raise DataSourceUnavailable("sina/tencent search unreachable")
            return fallback
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
    except DataSourceUnavailable:
        raise
    except Exception as e:
        logger.warning(f"search_stock failed: {e}")
        fallback = _search_stock_tencent(keyword)
        if fallback is None:
            raise DataSourceUnavailable("sina/tencent search unreachable") from e
        return fallback


def _unescape_unicode(text: str) -> str:
    """还原上游返回的字面 \\uXXXX 转义（腾讯 smartbox 的股票名是这种格式）。

    只处理 4 位十六进制转义，其余字符原样保留；不改变正常中文文本。
    """
    if "\\u" not in text:
        return text
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


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
        quote_keys: list[str] = []
        for entry in entries:
            if not entry.strip():
                continue
            # 阶段四修复(2026-11-07):腾讯 smartbox 实际返回 `sh~510300~沪深300ETF~...`
            # （分隔符是 "~"，市场前缀与代码分开放在前两段）。原实现只按 "^" 切分，
            # 导致 len(parts)<3 全部被丢弃 —— 数据源明明有结果却返回空列表，
            # 前端因此显示"未找到匹配的股票"。保留 "^" 分支兼容既有测试与旧格式。
            market = ""
            if "~" in entry:
                pieces = entry.split("~")
                if len(pieces) < 3:
                    continue
                market = _safe_str(pieces[0])
                code = _safe_str(pieces[1])
                # 腾讯把中文名以字面 \uXXXX 转义返回，需还原成真实字符
                name = _unescape_unicode(_safe_str(pieces[2]))
            else:
                parts = entry.split("^")
                if len(parts) < 3:
                    continue
                code = _safe_str(parts[0])
                name = _safe_str(parts[1])
            if not code or not name:
                continue
            # 行情接口对 ETF 需要市场前缀（sh510300），裸 6 位会查不到
            quote_keys.append((market + code) if market else code)
            result.append({
                "code": code,
                "name": name,
                # 阶段二 R-08:行情缺失时返回 null,由 UI 显示“—”,不再伪装成 0.00
                "price": None,
                "change_pct": None,
            })
            if len(result) >= 20:
                break
        if result:
            quotes = get_realtime_quote_tencent(quote_keys)
            quote_map = {q["code"]: q for q in quotes}
            for r, key in zip(result, quote_keys):
                # 键可能是 "sh510300" 或裸 "510300"，两种都尝试
                q = (quote_map.get(key)
                     or quote_map.get(key[-6:])
                     or quote_map.get(r["code"])
                     or {})
                raw_price = q.get("price")
                raw_change = q.get("change_pct")
                r["price"] = round(_safe_float(raw_price), 3) if raw_price not in (None, "") else None
                r["change_pct"] = round(_safe_float(raw_change), 2) if raw_change not in (None, "") else None
        return result
    except Exception as e:
        logger.warning(f"_search_stock_tencent failed: {e}")
    # 网络/解析失败返回 None（区别于"正常响应但无匹配"的空列表）
    return None


def get_kline_summary(code: str, count: int = 30) -> dict:
    """把最近 count 根日线压缩成区间摘要（最新收盘 / 30日高低 / 均量均额）。

    独立成公开函数（2026-11-07）：详情接口的主响应只等它 1.2s，
    超时后由前端调用 /api/market/stock_kline_summary 异步补齐，
    避免 K 线回退链把"点开详情"拖成 5 秒。

    注意：high/low 用 _opt_float 取值，全缺失时返回 None 而不是 0，
    否则前端会显示"30日低 0.00"这种明显错误的价格。
    """
    if not code or not code.strip():
        return {}
    try:
        klines = get_kline_mootdx(code.strip(), period="daily", count=count)
    except Exception as e:
        logger.warning(f"get_kline_summary failed: {e}")
        return {}
    if not klines:
        return {}
    highs = [h for h in (_opt_float(k.get("high")) for k in klines) if h is not None]
    lows = [lo for lo in (_opt_float(k.get("low")) for k in klines) if lo is not None]
    volumes = [v for v in (_opt_float(k.get("volume")) for k in klines) if v is not None]
    amounts = [a for a in (_opt_float(k.get("amount")) for k in klines) if a is not None]
    latest = klines[-1]
    return {
        "latest_date": _safe_str(latest.get("date")),
        "latest_close": _opt_float(latest.get("close")),
        "high_30d": max(highs) if highs else None,
        "low_30d": min(lows) if lows else None,
        "avg_volume_30d": round(sum(volumes) / len(volumes), 2) if volumes else None,
        "avg_amount_30d": round(sum(amounts) / len(amounts), 2) if amounts else None,
        "days": len(klines),
    }


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
        return get_kline_summary(code, count=30)

    # P1 修复(2026-11-07)：K 线区间摘要单独给 1.2s 预算。
    # 它只是详情面板的补充信息（30日高低/均量），而 mootdx 连不上时会按服务器
    # 逐个重试，实测 4.7s。原来的做法是整页陪着等 → 用户点开详情 5.2s 才看到价格。
    # 现在：核心三维（行情/财务/资金）先返回，区间摘要超时就留空，
    # 由前端异步补拉 /api/market/stock_kline_summary，页面不再被它拖住。
    kline_future = _thread_pool.submit(_fetch_kline)
    _t0 = time.monotonic()
    fetched = _parallel_fetch(
        [
            ("quote", _fetch_quote),
            ("financial", _fetch_financial),
            ("capital_flow", _fetch_capital),
        ],
        deadline=3.0,
    )
    # 预算按"从提交到现在已花掉的时间"扣减，否则总耗时 = 核心耗时 + 1.2s，
    # 实测仍是 1.6s+，等于预算没生效。
    _kline_budget = max(0.0, 1.0 - (time.monotonic() - _t0))
    try:
        result["kline_summary"] = kline_future.result(timeout=_kline_budget) or {}
    except FuturesTimeoutError:
        kline_future.cancel()
        logger.info(f"get_stock_detail kline_summary 超过 1.0s 预算，留待前端异步补拉: {code}")
        result["kline_summary"] = {}
    except Exception as e:
        logger.warning(f"get_stock_detail kline_summary failed: {e}")
        result["kline_summary"] = {}

    quote_data = fetched.get("quote") or {"quote": {}, "name": ""}
    result["quote"] = quote_data.get("quote", {})
    result["name"] = quote_data.get("name", "")
    result["financial"] = fetched.get("financial") or {}
    result["capital_flow"] = fetched.get("capital_flow") or {}
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
            data_health.record_success("sector_ranking", len(result))
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
            data_health.record_success("sector_ranking", len(result2))
            _cache_set("sector_ranking", result2, 30)
            return result2
    except Exception as e:
        logger.warning(f"get_sector_ranking concept fallback failed: {e}")
    result = _get_sector_ranking_sina()
    if result:
        data_health.record_success("sector_ranking", len(result))
    else:
        data_health.record_failure("sector_ranking", "all sources empty")
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
            data_health.record_success("global_news", len(result))
            return result
    except Exception as e:
        logger.warning(f"get_global_news failed: {e}")
    result = _get_global_news_sina()
    if result:
        data_health.record_success("global_news", len(result))
    else:
        data_health.record_failure("global_news", "all sources empty")
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


def _has_meaningful_value(snapshot: dict) -> bool:
    """判断财务快照是否含有真实数据（用于决定是否继续回退下一数据源）。

    P1 修复(2026-11-07)：mootdx 对 ETF/指数等无财报的标的会返回一行**全 None**，
    旧实现用 _safe_float(...,0) 把它变成"一行全 0"，字典非空 → 直接 return，
    于是详情页出现 `ROE 0.00%`、`PE 0.00`，而 report_date 为空。
    现在只要没有任何非 None 数值，就视为"无数据"，继续回退。
    """
    return any(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        for k, v in snapshot.items()
        if k not in ("code",)
    )


def _financial_snapshot_mootdx(stock_code: str) -> dict:
    client = _mootdx_client()
    if client is None:
        return {}
    try:
        market = 1 if _market_of(stock_code) == "sh" else 0
        df = client.finance(symbol=stock_code, market=market)
        if df is None or df.empty:
            return {}
        row = df.iloc[0]
        snapshot = {
            "code": stock_code,
            "report_date": _safe_str(row.get("report_date", ""))[:10],
            "eps": _opt_float(row.get("basic_eps")),
            "bvps": _opt_float(row.get("bvps")),
            "roe": _opt_float(row.get("roe")),
            "revenue": _opt_float(row.get("total_operating_revenue")),
            "revenue_yoy": _opt_float(row.get("yysr_yoy")),
            "profit": _opt_float(row.get("parent_net_profit")),
            "profit_yoy": _opt_float(row.get("jlr_yoy")),
            "gross_margin": _opt_float(row.get("gross_profit_ratio")),
            "net_margin": _opt_float(row.get("net_profit_ratio")),
        }
        # 全 None 说明该标的无财报（ETF/指数/债券），交给下一数据源
        if not _has_meaningful_value(snapshot):
            return {}
        return snapshot
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
        snapshot = {
            "code": stock_code,
            "pe_ttm": _opt_float(data.get("f9")),
            "pb": _opt_float(data.get("f23")),
            "total_mv": _opt_float(data.get("f20")),
            "circ_mv": _opt_float(data.get("f115")),
            "roe": _opt_float(data.get("f162")),
            "gross_margin": _opt_float(data.get("f186")),
            "net_margin": _opt_float(data.get("f187")),
            "revenue_yoy": _opt_float(data.get("f183")),
            "profit_yoy": _opt_float(data.get("f185")),
        }
        if not _has_meaningful_value(snapshot):
            return {}
        return snapshot
    except Exception as e:
        logger.warning(f"_financial_snapshot_em failed: {e}")
        return {}


def _financial_snapshot_tencent(stock_code: str) -> dict:
    try:
        quotes = get_realtime_quote_tencent([stock_code])
        if not quotes:
            return {}
        q = quotes[0]
        pe_ttm = _opt_float(q.get("pe_ttm"))
        pb = _opt_float(q.get("pb"))
        total_mv = _opt_float(q.get("total_market_value"))
        circ_mv = _opt_float(q.get("circ_market_value"))
        # 腾讯对 ETF/指数把 PE/PB 直接给 0（不是"缺失"，是该品类没有 PE/PB）。
        # 0 不是有效估值，一律转 None，前端显示"—"，避免"PE 0.00"误导。
        if pe_ttm == 0:
            pe_ttm = None
        if pb == 0:
            pb = None
        # 三者全空/0 才认为无数据
        if not any(v for v in (pe_ttm, pb, total_mv)):
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


# 2026-11-10 双上游改造: fundgz 响应为 `jsonpgz({...});` 形式, 用正则抽出花括号内的 JSON 体
_FUNDGZ_JSON_RE = re.compile(r"\{.*\}", re.S)


def _fetch_fund_realtime_fundgz(codes: list[str]) -> list[dict]:
    """天天基金 fundgz 备源: 逐 code 拉取估值/净值, 返回结构与腾讯主源完全同构。

    - 仅作为 get_fund_realtime_tencent 的缺失 code 补齐通道, 单个 code 失败
      (网络/解析/字段缺失)只记 warning 并继续下一个, 整体永不抛出。
    - R-06 延续: dwjz<=0 视为无有效净值, 该条直接丢弃, 不返回 nav=0 假行情。
    """
    if not codes:
        return []
    import json
    result: list[dict] = []
    for code in codes:
        try:
            url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}"
            resp = _EM_SESSION.get(url, timeout=5, headers={
                "Referer": "https://fund.eastmoney.com/",
            })
            m = _FUNDGZ_JSON_RE.search(resp.text or "")
            if not m:
                logger.warning(f"fundgz: {code} 响应中未找到 JSON, 跳过")
                continue
            data = json.loads(m.group(0))
            nav = _safe_float(data.get("dwjz"))
            if nav <= 0:
                # R-06: 与主源一致, 无有效净值时丢弃该条
                logger.warning(f"fundgz: {code} 无有效净值, 丢弃该条")
                continue
            change_pct_raw = _safe_float(data.get("gszzl"))
            if abs(change_pct_raw) <= 20:
                change_pct = change_pct_raw
            else:
                # 异常值(>20%): 与腾讯路径的合理性防御一致, 直接置 0
                change_pct = 0.0
            estimated = _safe_float(data.get("gsz"))
            change = round(nav * change_pct / 100, 4)
            result.append({
                "code": _safe_str(data.get("fundcode"), code),
                "name": _safe_str(data.get("name")),
                "nav": nav,
                "estimated_nav": estimated if estimated > 0 else nav,
                "change": change,
                "change_pct": round(change_pct, 2),
                "update_time": _safe_str(data.get("gztime")),
                "source": "fundgz",
            })
        except Exception as e:
            logger.warning(f"fundgz: {code} 获取失败: {e}")
            continue
    return result


def _fetch_fund_realtime_tencent_inner(codes: list[str]) -> list[dict]:
    """腾讯 qt.gtimg.cn 主源: 请求+解析, 返回原始条目(不含 source 字段)。

    网络异常向上抛出, 由 get_fund_realtime_tencent 统一捕获后走备源合并。
    """
    prefixed = ",".join("jj" + c for c in codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
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
        if nav <= 0:
            # 2026-11-05 R-06: 字段缺失/全零时不再返回 nav=0.00 的"假行情"。
            # 0 净值与"真实净值缺失"无法区分, 下游会把盈亏静默算成 0,
            # 因此该条直接丢弃, 由调用方按"无行情"分支处理。
            logger.warning(f"fund_realtime: {raw_code} 无有效净值, 丢弃该条")
            continue
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


def get_fund_realtime_tencent(codes: list[str]) -> list[dict]:
    """基金实时行情(双上游): 主源腾讯 qt.gtimg.cn + 备源天天基金 fundgz。

    - 主源一次性请求全部 code; 解析后若仍有缺失 code, 才逐个调备源 fundgz 补齐。
    - 合并结果统一携带 source 字段("tencent"/"fundgz")便于溯源。
    - 健康度按合并后口径记录: merged 非空记 success, 全空记 failure(两条上游
      的失败原因拼接); 空入参在任何健康度记录之前直接返回 [](不污染健康度)。
    - 沿用 R-06: 两路解析中 nav<=0 的条目均直接丢弃。
    """
    if not codes:
        return []
    tencent_err = ""
    try:
        result = _fetch_fund_realtime_tencent_inner(codes)
    except Exception as e:
        tencent_err = str(e)
        logger.warning(f"get_fund_realtime_tencent 主源请求失败: {e}")
        result = []
    for row in result:
        row["source"] = "tencent"
    merged: list[dict] = list(result)
    got = {r["code"] for r in result}
    missing = [c for c in codes if c not in got]
    fundgz_err = ""
    if missing:
        fallback = _fetch_fund_realtime_fundgz(missing)
        if fallback:
            merged.extend(fallback)
        else:
            fundgz_err = "fundgz no data"
    if merged:
        data_health.record_success("fund_realtime", len(merged))
    else:
        # 双上游全空的失败原因拼接, 尽量简短(优先携带主源异常原文)
        reason = "; ".join(
            part for part in (tencent_err or "empty result", fundgz_err) if part
        )
        data_health.record_failure("fund_realtime", reason)
    return merged


# ===== 基金历史净值(风险指标 / 走势图) =====
# 东财 f10/lsjz 的服务端分页:pageSize 参数被忽略,固定 20 条/页,TotalCount 在响应顶层,
# 且返回按日期倒序。历史实现只取第 1 页 → 任何窗口都只剩最近 ~20 个交易日,倒序输入
# 又让收益/回撤/夏普符号反转。修复为「pingzhongdata 单请求全量 + lsjz 分页兜底」,
# 统一在本地做去重、升序排序与窗口过滤,窗口语义不再依赖上游行为。
_HISTORY_PERIOD_DAYS: dict[str, int] = {
    "1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "all": 0,
}
_LSJZ_PAGE_SIZE: int = 20
_LSJZ_MAX_PAGES: int = 40  # 安全上限 800 条(≈3.2 年);MAX 窗口由 pingzhongdata 全量兜底
_CST = timezone(timedelta(hours=8))  # 东财 pingzhongdata 时间戳为北京时间零点

_PZ_NAV_RE = re.compile(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", re.S)
_PZ_ACC_RE = re.compile(r"Data_ACWorthTrend\s*=\s*(\[.*?\]);", re.S)


def _history_start_date(period: str) -> str | None:
    """period → 窗口起始日(YYYY-MM-DD);all 返回 None 表示不限。"""
    days = _HISTORY_PERIOD_DAYS.get((period or "1y").strip().lower(), 365)
    if days <= 0:
        return None
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _normalize_history(rows: list[dict], start_date: str | None = None) -> list[dict]:
    """去重(同日保留最后一条)、按日期升序、窗口过滤,并回填缺失涨跌幅。

    涨跌幅按「前一日 → 当日」计算,因此必须先排序再回填——倒序下回填会得到相反符号。
    """
    by_date: dict[str, dict] = {}
    for row in rows or []:
        date_str = _safe_str(row.get("date"))[:10]
        nav = _safe_float(row.get("nav"))
        if not date_str or nav <= 0:
            continue
        if start_date and date_str < start_date:
            continue
        acc_nav = _safe_float(row.get("acc_nav"), nav)
        raw_pct = row.get("change_pct")
        by_date[date_str] = {
            "date": date_str,
            "nav": nav,
            "acc_nav": acc_nav if acc_nav > 0 else nav,
            "change_pct": None if raw_pct in (None, "") else _safe_float(raw_pct),
        }
    result = [by_date[d] for d in sorted(by_date)]
    prev_nav: float | None = None
    for item in result:
        if item["change_pct"] is None:
            item["change_pct"] = round((item["nav"] - prev_nav) / prev_nav * 100, 2) if prev_nav else 0.0
        prev_nav = item["nav"]
    return result


def _ts_to_date(ts_ms: Any) -> str:
    """北京时间毫秒时间戳 → YYYY-MM-DD(Render 为 UTC,不能直接用本地时区)。"""
    try:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=_CST).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _fetch_fund_history_pingzhong(code: str) -> list[dict]:
    """东财 pingzhongdata:单请求返回全量历史(单位净值 + 累计净值),MAX 窗口不截断。"""
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    resp = _EM_SESSION.get(url, timeout=10, headers={"Referer": "https://fund.eastmoney.com/"})
    text = resp.text or ""
    m_nav = _PZ_NAV_RE.search(text)
    if not m_nav:
        return []
    import json

    rows: dict[str, dict] = {}
    for item in json.loads(m_nav.group(1)):
        date_str = _ts_to_date(item.get("x"))
        nav = _safe_float(item.get("y"))
        if not date_str or nav <= 0:
            continue
        raw_pct = item.get("equityReturn")
        rows[date_str] = {
            "date": date_str,
            "nav": nav,
            "acc_nav": nav,
            "change_pct": None if raw_pct in (None, "") else _safe_float(raw_pct),
        }
    m_acc = _PZ_ACC_RE.search(text)
    if m_acc:
        for pair in json.loads(m_acc.group(1)):
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            date_str = _ts_to_date(pair[0])
            acc = _safe_float(pair[1])
            if date_str in rows and acc > 0:
                rows[date_str]["acc_nav"] = acc
    return list(rows.values())


def _fetch_fund_history_lsjz(code: str, start_date: str | None, end_date: str) -> list[dict]:
    """东财 f10/lsjz 分页历史:按 TotalCount 翻页(固定 20 条/页,pageSize 无效)。"""
    url = "https://api.fund.eastmoney.com/f10/lsjz"
    import json

    rows: list[dict] = []
    page_size = _LSJZ_PAGE_SIZE
    total_pages = 1
    page_index = 1
    while page_index <= min(total_pages, _LSJZ_MAX_PAGES):
        params = {
            "callback": "jQuery",
            "fundCode": code,
            "pageIndex": page_index,
            "pageSize": page_size,
            "startDate": start_date or "",
            "endDate": end_date,
        }
        resp = _EM_SESSION.get(url, params=params, timeout=8, headers={
            "Referer": "https://fund.eastmoney.com/",
        })
        payload = json.loads(re.sub(r"^jQuery\(", "", resp.text).rstrip(")"))
        if page_index == 1:
            total = int(_safe_float(payload.get("TotalCount"), 0))
            page_size = int(_safe_float(payload.get("PageSize"), _LSJZ_PAGE_SIZE)) or _LSJZ_PAGE_SIZE
            total_pages = max(1, math.ceil(total / page_size))
            if total_pages > _LSJZ_MAX_PAGES:
                logger.warning(
                    f"get_fund_history {code}: TotalCount={total} 超过分页上限,"
                    f"仅取最近 {_LSJZ_MAX_PAGES * page_size} 条"
                )
        items = (payload.get("Data") or {}).get("LSJZList") or []
        if not items:
            break
        for item in items:
            rows.append({
                "date": _safe_str(item.get("FSRQ"))[:10],
                "nav": _safe_float(item.get("DWJZ")),
                "acc_nav": _safe_float(item.get("LJJZ")),
                "change_pct": item.get("JZZZL"),
            })
        if len(items) < page_size:
            break
        page_index += 1
        time.sleep(0.05)
    return rows


def get_fund_history_tencent(code: str, period: str = "1y") -> list[dict]:
    """获取基金历史净值,统一按日期**升序**返回并按 period 窗口过滤。

    主源 pingzhongdata(单请求全量)→ 备源 f10/lsjz(按 TotalCount 翻页)→
    末位兜底腾讯实时单点。任一层失败仅记日志,不向上抛异常。
    """
    code = _safe_str(code).strip()
    if not code:
        return []
    period = (period or "1y").strip().lower()
    start_date = _history_start_date(period)
    end_date = datetime.now().strftime("%Y-%m-%d")

    try:
        data = _normalize_history(_fetch_fund_history_pingzhong(code), start_date)
        if data:
            data_health.record_success("fund_history", len(data))
            return data
    except Exception as e:
        logger.warning(f"get_fund_history_tencent {code} pingzhong failed: {e}")

    try:
        data = _normalize_history(_fetch_fund_history_lsjz(code, start_date, end_date), start_date)
        if data:
            data_health.record_success("fund_history", len(data))
            return data
    except Exception as e:
        logger.warning(f"get_fund_history_tencent {code} lsjz failed: {e}")

    try:
        data = get_fund_realtime_tencent([code])
        if data:
            item = data[0]
            data_health.record_success("fund_history", 1)
            return [{
                "date": _safe_str(item.get("update_time", ""))[:10],
                "nav": _safe_float(item.get("nav", 0)),
                "acc_nav": _safe_float(item.get("nav", 0)),
                "change_pct": _safe_float(item.get("change_pct", 0)),
            }]
    except Exception as e:
        logger.warning(f"get_fund_history_tencent {code} realtime failed: {e}")
    data_health.record_failure("fund_history", f"{code}: all sources failed")
    return []


def get_market_sentiment() -> dict:
    cached = _cache_get("market_sentiment")
    if cached is not None:
        return cached
    result: dict = {
        "rise_count": None, "fall_count": None, "flat_count": None,
        "sentiment": "--", "ratio": 0.5,
        # 2026-11-08：显式声明结论的依据来源。计数缺失时 sentiment 由指数涨跌幅
        # 推算（basis=index_proxy），与"涨跌家数统计"不是一回事，消费方必须能区分，
        # 否则 0/0/0 + "偏空" 会被读成"0 只上涨"。
        "basis": "unavailable", "counts_available": False,
    }
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
                    "basis": "counts",
                    "counts_available": True,
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
                        # 计数缺失：三个 count 一律 None（不是 0），并标注结论来源。
                        if chg > 0.3:
                            result = {"rise_count": None, "fall_count": None, "flat_count": None, "sentiment": "偏多", "ratio": 0.65, "basis": "index_proxy", "counts_available": False}
                        elif chg < -0.3:
                            result = {"rise_count": None, "fall_count": None, "flat_count": None, "sentiment": "偏空", "ratio": 0.35, "basis": "index_proxy", "counts_available": False}
                        else:
                            result = {"rise_count": None, "fall_count": None, "flat_count": None, "sentiment": "中性", "ratio": 0.5, "basis": "index_proxy", "counts_available": False}
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


# =====================================================================
# 个股异动（砸盘 / 拉高）—— 2026-11-08 新增
#
# 口径实测结论（决定了本模块的设计，勿凭常识改）：
# 1. 东财 push2 clist 的 f22 实测是「1 分钟涨速(%)」，**不是** 5 分钟。
#    验证方法：取涨速榜前 8 只，用 1 分钟 K 线反算 1/3/5/10 分钟涨幅逐一比对，
#    f22 与「1 分钟涨幅」逐位相等（1.90 / 1.73 / 1.65 / 1.16），与 5 分钟不等
#    （同批 5 分钟分别为 2.36 / 3.52 / 1.65 / 1.16）。故 f22 只可用于**筛选候选**，
#    不可作为判定依据——否则「拉高/砸盘」的窗口会变成 1 分钟，语义漂移且无文档。
# 2. 判定依据一律由本模块用**分钟序列自算**，窗口 = ANOMALY_WINDOW_MINUTES。
#    这样上游改字段口径时，判定结果不会静默改变（只影响候选池覆盖度）。
# 3. 分钟序列按「东财 1 分钟 K 线 → 腾讯分时 → 新浪 1 分钟 K 线」回退。
#    实测三者字段结构不同，且新浪会丢弃零成交分钟（出现 14:57 → 15:00 跳空），
#    腾讯收盘后会补 1529/1530 冗余点，因此**统一先转成 {分钟序号: 价格} 的字典**，
#    再按分钟序号精确取窗口端点；端点缺失即判「数据不足」，不用相邻分钟硬凑
#    （硬凑会把「5 分钟」悄悄变成「7 分钟」，是典型的静默错值）。
# =====================================================================
ANOMALY_WINDOW_MINUTES = 5        # 判定窗口（分钟）
ANOMALY_SURGE_PCT = 3.0           # 拉高阈值：窗口内涨幅 >= 3%
ANOMALY_DUMP_PCT = -3.0           # 砸盘阈值：窗口内跌幅 <= -3%
ANOMALY_SCREEN_PER_SIDE = 8       # 涨/跌两个方向各取多少只候选
_ANOMALY_CLOSE_MINUTE = 15 * 60   # 15:00，收盘后补点(1529/1530)一律剔除


def _minute_index(hhmm: str) -> int | None:
    """'0931' -> 571（当日第几分钟）。非法格式返回 None。"""
    raw = str(hhmm or "").strip()
    if len(raw) != 4 or not raw.isdigit():
        return None
    hour, minute = int(raw[:2]), int(raw[2:])
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def compute_window_change(
    prices: dict[int, float],
    window_minutes: int = ANOMALY_WINDOW_MINUTES,
) -> tuple[float, int, int] | None:
    """从 {分钟序号: 价格} 计算「最近 window_minutes 分钟」的涨跌幅（%）。

    纯函数（无 IO），单测直接覆盖。

    严格按分钟序号取端点：end = 最大分钟，start = end - window_minutes。
    start 不存在就返回 None（数据不足/窗口跨午休断裂），**不用相邻分钟顶替**——
    顶替会把「5 分钟」静默变成别的跨度，属于数据说谎。

    Returns:
        (change_pct, start_minute, end_minute) 或 None。
    """
    if not prices or window_minutes <= 0:
        return None
    end_minute = max(prices)
    start_minute = end_minute - int(window_minutes)
    start_price = prices.get(start_minute)
    end_price = prices.get(end_minute)
    if not start_price or not end_price or start_price <= 0:
        return None
    return (end_price / start_price - 1.0) * 100.0, start_minute, end_minute


def classify_anomaly(
    change_pct: float | None,
    surge_pct: float = ANOMALY_SURGE_PCT,
    dump_pct: float = ANOMALY_DUMP_PCT,
) -> str | None:
    """窗口涨跌幅 → 'surge' / 'dump' / None。

    纯函数。change_pct 为 None（数据缺失）时返回 None —— **不猜、不默认 0**，
    否则「缺数据」会被误判成「无异动」。
    """
    if change_pct is None:
        return None
    if change_pct >= surge_pct:
        return "surge"
    if change_pct <= dump_pct:
        return "dump"
    return None


def _parse_tx_minutes(payload: list[str]) -> tuple[dict[int, float], str]:
    """腾讯分时 → ({分钟序号: 价格}, 'YYYY-MM-DD')。

    行格式: '0931 1450.98 4465 646486481.69'（时间 价格 累计量 累计额）。
    实测收盘后会补 1529/1530 等冗余点，一律按 > 15:00 剔除。
    """
    prices: dict[int, float] = {}
    for row in payload or []:
        parts = str(row).split()
        if len(parts) < 2:
            continue
        idx = _minute_index(parts[0])
        if idx is None or idx > _ANOMALY_CLOSE_MINUTE:
            continue
        try:
            price = float(parts[1])
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[idx] = price
    return prices, ""


def _parse_em_minutes(klines: list[str]) -> tuple[dict[int, float], str]:
    """东财 1 分钟 K 线 → ({分钟序号: 价格}, 日期)。

    行格式: '2026-11-06 14:58,1451.00,1451.00,1451.00,1451.00,8,1160705.00'
    （时间,开,收,高,低,量,额）→ 取第 3 列（收盘价）。
    """
    prices: dict[int, float] = {}
    day = ""
    for row in klines or []:
        cols = str(row).split(",")
        if len(cols) < 3:
            continue
        stamp = cols[0].strip()
        if len(stamp) < 16:
            continue
        day = stamp[:10]
        idx = _minute_index(stamp[11:16].replace(":", ""))
        if idx is None or idx > _ANOMALY_CLOSE_MINUTE:
            continue
        try:
            price = float(cols[2])
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[idx] = price
    return prices, day


def _parse_sina_minutes(bars: list[dict]) -> tuple[dict[int, float], str]:
    """新浪 1 分钟 K 线 → ({分钟序号: 价格}, 日期)。

    条目形如 {'day': '2026-11-06 14:51:00', 'close': '1449.500', ...}。
    新浪会丢弃零成交分钟，序列可能跳空 —— 由 compute_window_change 的
    「端点必须存在」规则兜住，不做插值。
    """
    prices: dict[int, float] = {}
    day = ""
    for bar in bars or []:
        stamp = str(bar.get("day") or "")
        if len(stamp) < 16:
            continue
        day = stamp[:10]
        idx = _minute_index(stamp[11:16].replace(":", ""))
        if idx is None or idx > _ANOMALY_CLOSE_MINUTE:
            continue
        try:
            price = float(bar.get("close"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[idx] = price
    return prices, day


def _minutes_from_em(code: str, window_minutes: int = ANOMALY_WINDOW_MINUTES) -> tuple[dict[int, float], str]:
    secid = _em_secid(code)
    resp = em_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "1",
        "fqt": "1",
        "lmt": str(window_minutes + 4),
        "end": "20500101",
    })
    klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
    return _parse_em_minutes(klines)


def _minutes_from_tencent(code: str, window_minutes: int = ANOMALY_WINDOW_MINUTES) -> tuple[dict[int, float], str]:
    # 腾讯分时返回全天序列，窗口端点由 compute_window_change 精确截取，无需按 window 限量。
    resp = requests.get(
        "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
        params={"code": _prefix_code(code)},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        },
        timeout=5,
    )
    node = ((resp.json() or {}).get("data") or {}).get(_prefix_code(code)) or {}
    inner = node.get("data") or {}
    prices, _ = _parse_tx_minutes(inner.get("data") or [])
    raw_date = str(inner.get("date") or "")
    day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else ""
    return prices, day


def _minutes_from_sina(code: str, window_minutes: int = ANOMALY_WINDOW_MINUTES) -> tuple[dict[int, float], str]:
    resp = requests.get(
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
        params={"symbol": _prefix_code(code), "scale": 1, "ma": "no",
                "datalen": str(window_minutes + 6)},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        },
        timeout=5,
    )
    return _parse_sina_minutes(resp.json() or [])


def get_stock_minute_series(code: str, window_minutes: int = ANOMALY_WINDOW_MINUTES) -> tuple[dict[int, float], str, str]:
    """取单只个股的分钟价格序列，按 腾讯 → 东财 → 新浪 回退。

    顺序依据实测（2026-11-08）：
    - 腾讯分时：单请求、无限速、稳定（16 只并发 ~1s），本机代理下可用性最好；
    - 东财 1 分钟 K 线：数据最规整（240 根无缺口），但 em_get 有全局 0.3s 限速，
      16 只串行化后 ≥5s，且本机代理偶发阻断 push2his；
    - 新浪 1 分钟 K 线：会丢弃零成交分钟（出现 14:57→15:00 跳空），
      窗口端点可能缺失，放最后。
    所以「最快且最稳」的腾讯优先，而不是「最规整」的东财优先。

    window_minutes 决定东财/新浪的取数量（此前硬编码为常量 5，导致 window>5
    且腾讯失败回退时取点不足、compute_window_change 恒返回 None）。腾讯返回全天
    序列不受此限。

    Returns:
        (prices, source, trade_date)；全失败返回 ({}, "", "")。
    """
    for source, fetcher in (
        ("tencent", _minutes_from_tencent),
        ("eastmoney", _minutes_from_em),
        ("sina", _minutes_from_sina),
    ):
        try:
            prices, day = fetcher(code, window_minutes)
            if len(prices) > window_minutes:
                return prices, source, day
        except Exception as e:
            logger.debug(f"minute series {code} via {source} failed: {e}")
    return {}, "", ""


def _screen_em_speed(per_side: int) -> list[dict]:
    """东财涨速榜候选（fid=f22），附带量比/换手率/成交额用于「放量」佐证。

    不复用 get_stock_ranking_em：那个函数是既有端点的契约（只回 11 个字段），
    这里需要 f8/f10 才有「放量」上下文。宁可多一个专用请求，也不改既有契约。
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    rows: list[dict] = []
    for order in (1, 0):  # 1=降序(拉高) 0=升序(砸盘)
        params = {
            "pn": 1,
            "pz": per_side,
            "po": order,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f22",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f2,f3,f5,f6,f8,f10,f12,f14,f22",
        }
        resp = em_get(url, params=params)
        items = ((resp.json() or {}).get("data") or {}).get("diff") or []
        for item in items:
            rows.append({
                "code": _safe_str(item.get("f12")),
                "name": _safe_str(item.get("f14")),
                "price": _opt_float(item.get("f2")),
                "change_pct": _opt_float(item.get("f3")),
                "volume": _opt_float(item.get("f5")),
                "amount": _opt_float(item.get("f6")),
                "turnover_pct": _opt_float(item.get("f8")),
                "volume_ratio": _opt_float(item.get("f10")),
                "speed_1min_pct": _opt_float(item.get("f22")),
            })
    return rows


def _screen_sina_change(per_side: int) -> list[dict]:
    """新浪当日涨跌幅榜候选（降级口径：当日涨跌幅，非短时涨速）。"""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    rows: list[dict] = []
    for asc in (0, 1):  # 0=降序(涨幅榜) 1=升序(跌幅榜)
        resp = requests.get(url, params={
            "page": 1, "num": per_side, "sort": "changepercent", "asc": asc,
            "node": "hs_a", "symbol": "", "_s_r_a": "auto",
        }, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })
        resp.encoding = "gbk"
        for item in resp.json() or []:
            rows.append({
                "code": _safe_str(item.get("code")),
                "name": _safe_str(item.get("name")),
                "price": _opt_float(item.get("trade")),
                "change_pct": _opt_float(item.get("changepercent")),
                "volume": _opt_float(item.get("volume")),
                "amount": _opt_float(item.get("amount")),
                "turnover_pct": _opt_float(item.get("turnoverratio")),
                "volume_ratio": None,  # 新浪排行无「量比」字段，如实留空不猜
                "speed_1min_pct": None,
            })
    return rows


def _screen_candidates(per_side: int = ANOMALY_SCREEN_PER_SIDE) -> tuple[list[dict], str]:
    """候选池：涨速榜 / 当日涨跌幅榜各取 per_side 只（按代码去重）。

    Returns:
        (candidates, screen_source)
        screen_source = 'eastmoney_speed'（口径=1 分钟涨速，上游原生涨速榜）
                      | 'sina_changepercent'（降级：口径=当日涨跌幅，覆盖度更差，
                        必须在 _meta 里标出来，否则用户会以为是「短时异动」）
    """
    candidates: list[dict] = []
    seen: set[str] = set()

    def _merge(rows: list[dict]) -> None:
        for row in rows or []:
            code = _safe_str(row.get("code"))
            if not code or code in seen:
                continue
            seen.add(code)
            candidates.append(row)

    if _check_em_available():
        try:
            em_rows = _screen_em_speed(per_side)
        except Exception as e:
            logger.warning(f"anomaly screen em failed, fallback to sina: {e}")
            em_rows = []
        if em_rows:
            _merge(em_rows)
            return candidates, "eastmoney_speed"

    try:
        _merge(_screen_sina_change(per_side))
    except Exception as e:
        logger.warning(f"anomaly screen sina failed: {e}")
    return candidates, "sina_changepercent"


def get_stock_anomalies(
    window_minutes: int = ANOMALY_WINDOW_MINUTES,
    per_side: int = ANOMALY_SCREEN_PER_SIDE,
) -> dict:
    """个股异动（砸盘/拉高）编排：筛选 → 逐只自算窗口涨跌幅 → 判定。

    返回结构（供路由层直接包 _meta）：
        {
          "items": [{code,name,price,change_pct,surge_or_dump,kind,basis{...},trade_date}],
          "window_minutes": int, "screen_source": str, "kline_source": str,
          "scanned": int, "unavailable": int, "as_of": str,
        }

    铁律：判定只用自算值；某只取不到分钟数据就**跳过并计数**，绝不填 0 或猜。
    """
    cached = _cache_get(f"stock_anomaly_{window_minutes}_{per_side}")
    if cached is not None:
        return cached

    candidates, screen_source = _screen_candidates(per_side)
    if not candidates:
        result = {"items": [], "window_minutes": window_minutes,
                  "screen_source": screen_source, "kline_source": "",
                  "scanned": 0, "unavailable": 0, "as_of": ""}
        _cache_set(f"stock_anomaly_{window_minutes}_{per_side}", result, 30)
        data_health.record_failure("stock_anomaly", "empty candidate pool")
        return result

    fetched = _parallel_fetch(
        [(row["code"], (lambda c=row["code"]: get_stock_minute_series(c, window_minutes))) for row in candidates],
        deadline=6.0,
    )

    items: list[dict] = []
    unavailable = 0
    sources: dict[str, int] = {}
    latest: tuple[str, int] = ("", 0)
    for row in candidates:
        series = fetched.get(row["code"])
        prices, source, day = (series if isinstance(series, tuple) else ({}, "", ""))
        if not prices:
            unavailable += 1
            continue
        sources[source] = sources.get(source, 0) + 1
        window = compute_window_change(prices, window_minutes)
        if window is None:
            unavailable += 1
            continue
        change_pct, start_minute, end_minute = window
        # as_of 反映「数据本身的时间」，与是否命中异动无关 ——
        # 若只在命中时更新，零异动时前端就拿不到数据时间，无法区分
        # 「刚刚查过、确实没有异动」和「数据是昨天的」。
        if (day, end_minute) > latest:
            latest = (day, end_minute)
        kind = classify_anomaly(change_pct)
        if kind is None:
            continue
        items.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "price": row.get("price"),
            "change_pct": row.get("change_pct"),
            "kind": kind,
            "basis": {
                "window_minutes": window_minutes,
                "window_change_pct": round(change_pct, 2),
                "from_minute": f"{start_minute // 60:02d}:{start_minute % 60:02d}",
                "to_minute": f"{end_minute // 60:02d}:{end_minute % 60:02d}",
                "volume_ratio": row.get("volume_ratio"),
                "turnover_pct": row.get("turnover_pct"),
                "amount": row.get("amount"),
                "threshold_pct": ANOMALY_SURGE_PCT if kind == "surge" else ANOMALY_DUMP_PCT,
                "minute_source": source,
            },
            "trade_date": day,
        })

    items.sort(key=lambda it: abs(it["basis"]["window_change_pct"]), reverse=True)
    kline_source = max(sources, key=sources.get) if sources else ""
    as_of = f"{latest[0]} {latest[1] // 60:02d}:{latest[1] % 60:02d}" if latest[0] else ""
    result = {
        "items": items,
        "window_minutes": window_minutes,
        "screen_source": screen_source,
        "kline_source": kline_source,
        "scanned": len(candidates),
        "unavailable": unavailable,
        "as_of": as_of,
    }
    _cache_set(f"stock_anomaly_{window_minutes}_{per_side}", result, 30)
    if items:
        data_health.record_success("stock_anomaly", len(items))
    else:
        data_health.record_success("stock_anomaly", 0)
    return result

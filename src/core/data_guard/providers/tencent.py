# -*- coding: utf-8 -*-
"""腾讯行情适配器(M2 试点 → M3 绞杀迁移第一步)。

M3-A(2026-11-19): get_realtime_quote_tencent 的「网络取数 + 解析」本体迁入
fetch_quotes_raw, data_source_v2 保留同名门面(缓存/TTL/签名/行为零变更)。
解析逻辑逐行原样迁移(仍用 _safe_float 0 兜底), 语义变更留给 M4 契约层收口。

M4-B3(2026-11-19): 基金实时主源(qt.gtimg.cn jj 前缀)/腾讯财务快照/
详情页行情字段映射解析体迁入, 门面同名转发, 行为零变更。

M4-D(2026-11-19): 市场情绪指数代理(指数涨跌幅)与腾讯分时取数+解析体迁入,
情绪判定语义与「腾讯→东财→新浪」分钟回退链保留在 data_source_v2 门面。

M4-E(2026-11-19): 国际行情(qt.gtimg.cn 美股/港股/全球指数)取数+解析体与
指数/美股/港股/行业 ETF 的后处理映射迁入; 缓存、健康度、来源标记与
并行聚合编排保留在 data_source_v2 门面。
"""
from __future__ import annotations

import requests
from loguru import logger

from src.core.data_guard.contract import QuoteContract, normalize_quote
from src.core.data_source_v2 import (
    ANOMALY_WINDOW_MINUTES,
    _minute_index,
    _opt_float,
    _prefix_code,
    _safe_float,
    _safe_str,
)

# 财务快照需复用行情门面(含缓存): 经 _ds2 属性调用, 既有测试
# patch ds2.get_realtime_quote_tencent 的契约不变
from src.core import data_source_v2 as _ds2


def fetch_quotes_raw(codes: list[str]) -> list[dict]:
    """腾讯批量行情取数+解析(原 data_source_v2.get_realtime_quote_tencent 主体, 无缓存)。"""
    if not codes:
        return []
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
        return result
    except Exception as e:  # noqa: BLE001 - 与迁移前行为逐位一致: 失败返回 []
        import loguru
        loguru.logger.warning(f"get_realtime_quote_tencent failed: {e}")
        return []


def fetch_index_quotes_raw() -> list[dict]:
    """指数行情取数+解析(原 data_source_v2.get_index_realtime 主体, 无缓存/无健康度副作用)。

    覆盖 8 指数(上证/深证/创业板/沪深300/中证500/科创50/上证50/中小100),
    全部为腾讯行情源实测可用代码(字段长度 > 40)。异常向上抛出, 由门面
    统一记 data_health 并返回 [](与迁移前行为逐位一致)。
    """
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
    return [r for r in result if r.get("code")]


def fetch_fund_realtime_raw(codes: list[str]) -> list[dict]:
    """腾讯 qt.gtimg.cn 基金主源: 请求+解析(原
    data_source_v2._fetch_fund_realtime_tencent_inner 主体)。

    返回原始条目(不含 source 字段)。网络异常向上抛出, 由
    get_fund_realtime_tencent 统一捕获后走备源合并。
    R-06: nav<=0 的条目直接丢弃, 不返回 0.00 假行情。
    parts[7] 是涨跌幅百分比; >20% 异常值降级为绝对值再反推百分比。
    requests 经模块对象调用, 既有测试 patch ds2.requests.get 的契约不变。
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


def fetch_financial_snapshot_tencent_raw(stock_code: str) -> dict:
    """腾讯财务快照取数+解析(原 data_source_v2._financial_snapshot_tencent 主体)。

    PE/PB 为 0 是"该品类不适用"(ETF/指数)而非缺失, 一律转 None 让前端
    显示"—"; 三者全空/0 → {} 交给 failover 判定为无数据。
    异常就地吞掉返回 {}(与迁移前行为逐位一致)。行情经 _ds2 属性调用。
    """
    try:
        quotes = _ds2.get_realtime_quote_tencent([stock_code])
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


def map_detail_quote(q: dict) -> dict:
    """详情页行情字段映射(原 data_source_v2.get_stock_detail._fetch_quote
    的映射体, 纯函数)。

    quote 字段 0 兜底 + 顶层 name 两个字段原样保留, 行为零变更。
    """
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


def fetch_sector_ranking_tencent_raw() -> list[dict]:
    """腾讯板块占位 stub(原 data_source_v2._get_sector_ranking_tencent 主体)。

    历史上探测过 future2 指数接口后未启用, 恒返回 []。原样迁移保持
    行为零变更(含异常只记 warning 的语义)。"""
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


def fetch_global_quotes_raw(codes: list[str]) -> list[dict]:
    """腾讯国际行情取数+解析(原 data_source_v2._tencent_global_quote 主体, 无缓存)。

    codes 是已带前缀的腾讯代码(usAAPL / hk00700 / usDJI / hkHSI / sh000001)。
    返回统一字段:code/name/price/prev_close/open/high/low/change/change_pct/volume/currency。
    异常向上抛出, 由门面记 warning 并返回 [](与迁移前行为逐位一致)。
    """
    if not codes:
        return []
    prefixed = ",".join(codes)
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
    return [r for r in result if r.get("code") or r.get("price")]


def map_global_index_quotes(quotes: list[dict]) -> list[dict]:
    """国际/核心指数补全中文名+市场+币种(原 get_global_indices 映射体)。

    腾讯返回的 code 可能是 HSI/.DJI/000001 等, 用名称匹配请求代码;
    sh/sz 前缀为 A 股指数(market=CN, currency=CNY)。
    清单常量经 _ds2 属性调用, 既有 patch ds2._GLOBAL_INDEX_CODES 契约不变。
    """
    for item in quotes:
        name = item.get("name", "")
        for req_code, cn_name in _ds2._GLOBAL_INDEX_CODES:
            if name == cn_name or name in cn_name or cn_name in name:
                if not item.get("name"):
                    item["name"] = cn_name
                if req_code.startswith("us"):
                    item["market"] = "US"
                    item["currency"] = item.get("currency") or "USD"
                elif req_code.startswith("hk"):
                    item["market"] = "HK"
                    item["currency"] = item.get("currency") or "HKD"
                elif req_code.startswith(("sh", "sz")):
                    item["market"] = "CN"
                    item["currency"] = item.get("currency") or "CNY"
                break
        if not item.get("market"):
            item["market"] = "GLOBAL"
    return quotes


def map_us_quotes(quotes: list[dict]) -> list[dict]:
    """美股后处理(原 get_us_stock_realtime 映射体): 去交易所后缀 + US/USD。"""
    for item in quotes:
        raw = item.get("code", "")
        # 去掉交易所后缀(如 AAPL.OQ → AAPL)
        clean = raw.split(".")[0].upper()
        if clean:
            item["code"] = clean
        item["market"] = "US"
        if not item.get("currency"):
            item["currency"] = "USD"
    return quotes


def map_hk_quotes(quotes: list[dict]) -> list[dict]:
    """港股后处理(原 get_hk_stock_realtime 映射体): code 补齐 5 位 + HK/HKD。"""
    for item in quotes:
        raw = item.get("code", "")
        clean = raw.replace("hk", "").lstrip("0")
        if not clean:
            clean = "0"
        item["code"] = clean.zfill(5)
        item["market"] = "HK"
        if not item.get("currency"):
            item["currency"] = "HKD"
    return quotes


def map_sector_etfs(quotes: list[dict]) -> list[dict]:
    """行业板块 ETF 代理映射(原 get_global_sector_etfs 映射体)。

    固定 11 条(GICS 口径); 缺失保持 None(不伪造 0)。
    ETF 清单常量经 _ds2 属性调用, 既有 patch 契约不变。
    """
    by_code = {q.get("code"): q for q in quotes}
    result: list[dict] = []
    for tencent_code, sector_cn in _ds2._GLOBAL_SECTOR_ETFS:
        # 腾讯返回的 code 形如 XLK.AM，按前缀匹配
        q = next((v for k, v in by_code.items()
                  if str(k).split(".")[0].upper() == tencent_code[2:].upper()), None)
        if not q:
            result.append({
                "code": tencent_code[2:], "sector": sector_cn,
                "name": f"{sector_cn}ETF-SPDR",
                "price": None, "change_pct": None,
            })
            continue
        result.append({
            "code": tencent_code[2:],
            "sector": sector_cn,
            "name": q.get("name") or f"{sector_cn}ETF-SPDR",
            "price": q.get("price") if q.get("price") else None,
            "change_pct": q.get("change_pct") if q.get("price") else None,
        })
    return result


def fetch_index_change_pct_raw(codes: list[str]) -> dict[str, float]:
    """腾讯指数涨跌幅取数+解析(原 get_market_sentiment 的指数代理分支)。

    返回 {code: 涨跌幅%}; 段内字段不足(len<=32)的指数不出现, 门面据此
    维持 unavailable 结论。异常向上抛出, 由门面记 warning 后兜底。
    """
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    resp = requests.get(url, timeout=5, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://gu.qq.com/",
    })
    resp.encoding = "gbk"
    text = resp.text
    changes: dict[str, float] = {}
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
            for code in codes:
                if code in segment:
                    changes[code] = _safe_float(parts[32])
                    break
    return changes


def parse_minutes(payload: list[str]) -> tuple[dict[int, float], str]:
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
        if idx is None or idx > _ds2._ANOMALY_CLOSE_MINUTE:
            continue
        try:
            price = float(parts[1])
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[idx] = price
    return prices, ""


def fetch_minute_series_raw(
    code: str,
    window_minutes: int = ANOMALY_WINDOW_MINUTES,
) -> tuple[dict[int, float], str]:
    """腾讯分时取数+解析(原 data_source_v2._minutes_from_tencent 主体)。

    腾讯分时返回全天序列, 窗口端点由门面 compute_window_change 精确截取,
    无需按 window 限量(window_minutes 仅为与另两源签名对齐)。
    异常向上抛出, 由门面回退下一源。
    """
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
    prices, _ = parse_minutes(inner.get("data") or [])
    raw_date = str(inner.get("date") or "")
    day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else ""
    return prices, day


class TencentProvider:
    """腾讯行情(qt.gtimg.cn)。priority 10: 个股实时行情首选源。"""

    name = "tencent"
    priority = 10

    def fetch_quote(self, code: str) -> QuoteContract | None:
        from src.core.data_source_v2 import get_realtime_quote_tencent  # 门面(含缓存)

        quotes = get_realtime_quote_tencent([code])
        if not quotes:
            return None
        return normalize_quote(quotes[0], source=self.name)


class TencentQuoteListProvider:
    """批量行情(多标的): 供后续路由扩展使用, 保持同一规范化出口。"""

    name = "tencent_batch"
    priority = 11

    def fetch_quotes(self, codes: list[str]) -> list[QuoteContract] | None:
        from src.core.data_source_v2 import get_realtime_quote_tencent

        quotes = get_realtime_quote_tencent(codes)
        if not quotes:
            return None
        return [normalize_quote(q, source="tencent") for q in quotes]

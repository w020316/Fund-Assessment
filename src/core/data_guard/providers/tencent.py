# -*- coding: utf-8 -*-
"""腾讯行情适配器(M2 试点 → M3 绞杀迁移第一步)。

M3-A(2026-11-19): get_realtime_quote_tencent 的「网络取数 + 解析」本体迁入
fetch_quotes_raw, data_source_v2 保留同名门面(缓存/TTL/签名/行为零变更)。
解析逻辑逐行原样迁移(仍用 _safe_float 0 兜底), 语义变更留给 M4 契约层收口。

M4-B3(2026-11-19): 基金实时主源(qt.gtimg.cn jj 前缀)/腾讯财务快照/
详情页行情字段映射解析体迁入, 门面同名转发, 行为零变更。
"""
from __future__ import annotations

import requests
from loguru import logger

from src.core.data_guard.contract import QuoteContract, normalize_quote
from src.core.data_source_v2 import _opt_float, _prefix_code, _safe_float, _safe_str

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

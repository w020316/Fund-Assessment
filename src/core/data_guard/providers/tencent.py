# -*- coding: utf-8 -*-
"""腾讯行情适配器(M2 试点 → M3 绞杀迁移第一步)。

M3-A(2026-11-19): get_realtime_quote_tencent 的「网络取数 + 解析」本体迁入
fetch_quotes_raw, data_source_v2 保留同名门面(缓存/TTL/签名/行为零变更)。
解析逻辑逐行原样迁移(仍用 _safe_float 0 兜底), 语义变更留给 M4 契约层收口。
"""
from __future__ import annotations

import requests

from src.core.data_guard.contract import QuoteContract, normalize_quote
from src.core.data_source_v2 import _prefix_code, _safe_float, _safe_str


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

# -*- coding: utf-8 -*-
"""新浪 K 线适配器(M3-C 绞杀迁移)。

fetch_kline_raw 为 _kline_sina_fallback 的取数+解析本体(逐行原样迁移,
仍用 _safe_float 0 兜底, 下游 _sanitize_kline_rows 负责 0 价清洗),
data_source_v2 保留同名私有门面, 签名/输出行为零变更。
"""
from __future__ import annotations

import re

import requests

from src.core.data_source_v2 import _prefix_code, _safe_float, _safe_str
from loguru import logger


def fetch_kline_raw(symbol: str, period: str = "daily", count: int = 30) -> list[dict]:
    """新浪日/周/月 K 取数+解析(原 data_source_v2._kline_sina_fallback 主体)。"""
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
    except Exception as e:  # noqa: BLE001 - 与迁移前行为逐位一致: 失败返回 []
        logger.warning(f"_kline_sina_fallback failed: {e}")
        return []

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


def fetch_stock_ranking_raw(sort_field: str = "f3", sort_order: int = 0, count: int = 10) -> list[dict]:
    """新浪 A 股榜单取数+解析(原 _get_stock_ranking_sina 主体, 无健康度副作用)。

    异常向上抛出, 由门面统一 record_failure 并返回 [](行为逐位一致)。
    """
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


def fetch_stock_news_raw(stock_code: str, page: int = 1, page_size: int = 10) -> list[dict]:
    """新浪滚动新闻取数+解析(原 _get_stock_news_sina 主体)。异常向上抛出。"""
    import random
    from datetime import datetime

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
    return result

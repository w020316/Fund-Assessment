# -*- coding: utf-8 -*-
"""新浪 K 线适配器(M3-C 绞杀迁移)。

fetch_kline_raw 为 _kline_sina_fallback 的取数+解析本体(逐行原样迁移,
仍用 _safe_float 0 兜底, 下游 _sanitize_kline_rows 负责 0 价清洗),
data_source_v2 保留同名私有门面, 签名/输出行为零变更。

M4-C(2026-11-19): 板块群三路新浪取数+解析体迁入(板块资金/旧接口/newFLJK
兜底), 冷却状态(_sina_fund_failed_at)与降级编排保留在 data_source_v2 门面。

M4-D(2026-11-19): 新浪 1 分钟 K 线取数+解析体迁入; 分钟多源回退链与
「窗口端点可算」接纳判定保留在 data_source_v2 门面。

M4-F(2026-11-19): 异动扫描的新浪涨跌幅榜候选取数+解析体迁入;
候选池降级编排与判定仍保留在 data_source_v2 门面。
"""
from __future__ import annotations

import json
import re

import requests

from src.core.data_source_v2 import (
    ANOMALY_WINDOW_MINUTES,
    _minute_index,
    _opt_float,
    _prefix_code,
    _safe_float,
    _safe_str,
)
from loguru import logger

# _ANOMALY_CLOSE_MINUTE 等常量保留在门面, 经 _ds2 属性调用保证 patch 契约不变
from src.core import data_source_v2 as _ds2


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


def fetch_sector_fund_raw(fenlei: int) -> list[dict]:
    """新浪板块资金(MoneyFlow.ssl_bkzj_bk)取数+解析(原
    data_source_v2._get_sector_ranking_sina_fund 的单跳请求+解析体)。

    fenlei=2 为行业口径(对齐东财 m:90+t:2)。返回按涨跌幅降序的 top50;
    响应非列表/为空或无有效行 → [](门面据此进入冷却); 网络异常向上抛出。
    requests 经模块对象调用, 既有测试 patch ds2.requests.get 的契约不变。
    """
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_bk"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn/",
    }
    resp = requests.get(url, params={
        "page": 1, "num": 100, "sort": "netamount", "asc": 0, "fenlei": fenlei,
    }, timeout=6, headers=headers)
    items = resp.json()
    if not isinstance(items, list) or not items:
        return []
    result: list[dict] = []
    for item in items:
        code = _safe_str(item.get("category"))
        name = _safe_str(item.get("name"))
        if not code or not name:
            continue
        result.append({
            "code": code,
            "name": name,
            "change_pct": round(_safe_float(item.get("avg_changeratio")) * 100, 2),
            "price": round(_safe_float(item.get("avg_price")), 2),
            "main_net_inflow": _safe_float(item.get("netamount")),
            "main_inflow_pct": round(_safe_float(item.get("ratioamount")) * 100, 2),
            "super_large_net": 0,
            "super_large_pct": 0,
            "large_net": 0,
            "large_pct": 0,
            "medium_net": 0,
            "medium_pct": 0,
            "small_net": 0,
            "small_pct": 0,
        })
    if not result:
        return []
    # 与东财口径对齐：按涨跌幅降序，而非上游的净流入排序
    result.sort(key=lambda x: x["change_pct"], reverse=True)
    return result[:50]


def fetch_sector_ranking_sina_raw() -> list[dict]:
    """新浪旧接口(newFLJK.php param=class)取数+解析(原
    data_source_v2._get_sector_ranking_sina 的请求+解析体)。

    返回按涨跌幅降序的 top50; 无有效行 → [](门面降级 v2); 异常向上抛出。
    """
    url = "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
    resp = requests.get(url, timeout=5, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.sina.com.cn/",
    })
    resp.encoding = "gbk"
    text = resp.text
    result: list[dict] = []
    for m in re.finditer(r'=\s*(\{[^;]*\})\s*;?', text):
        try:
            data = json.loads(m.group(1))
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
        return []
    result.sort(key=lambda x: x["change_pct"], reverse=True)
    return result[:50]


def fetch_sector_ranking_sina_v2_raw() -> list[dict]:
    """新浪 Market_Center.getHQNodeData(hangye_zjh)取数+解析(原
    data_source_v2._get_sector_ranking_sina_v2 主体)。异常向上抛出。"""
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


def parse_minutes(bars: list[dict]) -> tuple[dict[int, float], str]:
    """新浪 1 分钟 K 线 → ({分钟序号: 价格}, 日期)。

    条目形如 {'day': '2026-11-06 14:51:00', 'close': '1449.500', ...}。
    新浪会丢弃零成交分钟，序列可能跳空 —— 由门面 compute_window_change 的
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
        if idx is None or idx > _ds2._ANOMALY_CLOSE_MINUTE:
            continue
        try:
            price = float(bar.get("close"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[idx] = price
    return prices, day


def fetch_minute_series_raw(
    code: str,
    window_minutes: int = ANOMALY_WINDOW_MINUTES,
) -> tuple[dict[int, float], str]:
    """新浪 1 分钟 K 线取数+解析(原 data_source_v2._minutes_from_sina 主体)。

    datalen 必须随 window 缩放(window+6), 否则 window>5 时取点不足。
    异常向上抛出, 由门面记 debug 并按回退链收尾。
    """
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
    return parse_minutes(resp.json() or [])


def fetch_anomaly_screen_sina_raw(per_side: int) -> list[dict]:
    """新浪当日涨跌幅榜候选取数+解析(原 data_source_v2._screen_sina_change 主体)。

    降级口径: 当日涨跌幅(非短时涨速), 覆盖度更差, 由门面在 _meta 标注
    screen_source=sina_changepercent。新浪无「量比/1分钟涨速」字段 → None。
    异常向上抛出, 由门面记 warning。
    """
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


def fetch_global_news_raw() -> list[dict]:
    """新浪全球滚动快讯取数+解析(原 data_source_v2._get_global_news_sina 主体)。

    标题为空丢弃; ctime 为秒级时间戳时转 'YYYY-MM-DD HH:MM'。
    异常向上抛出, 由门面记 warning 并按「全源失败」收尾。
    """
    import random
    from datetime import datetime

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

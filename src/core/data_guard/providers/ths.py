# -*- coding: utf-8 -*-
"""同花顺(10jqka)适配器(M4-A 绞杀迁移): 涨停池热搜 + 北向资金明细。

data_health 副作用与缓存保留在 data_source_v2 门面, 本层异常向上抛出。
"""
from __future__ import annotations

from datetime import datetime

import requests

from src.core.data_source_v2 import _safe_float, _safe_int, _safe_str

_THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.10jqka.com.cn/",
}


def ths_time(value) -> str:
    """同花顺封板时间是 unix 秒（新版）或 HH:MM:SS 字符串（旧版）。

    (原 data_source_v2._ths_time, 逐行原样迁移)
    """
    try:
        return datetime.fromtimestamp(int(value)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return _safe_str(value)


def fetch_hot_stocks_raw() -> list[dict]:
    """涨停池热搜取数+解析(原 get_hot_stocks_ths 主体, 无缓存/无健康度副作用)。"""
    url = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
    params = {
        "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914",
        # 2026-11-14: 上游把 filter 从日期式 GHS3A_YYYYMMDD 改成了市场集合式，
        # 旧参数直接返回 "filter参数不合法"，涨停池因此长期为空。
        "filter": "HS,GEM2STAR",
        "page": 1,
        "limit": 30,
        "order_field": "330324",
        "order_type": 0,
    }
    resp = requests.get(url, params=params, timeout=5, headers=_THS_HEADERS)
    data = resp.json()
    if data.get("status_code") not in (0, None):
        raise ValueError(f"status_code={data.get('status_code')} {data.get('status_msg', '')}")
    body = data.get("data") or {}
    items = body.get("info") or body.get("list") or []
    result: list[dict] = []
    for item in items:
        result.append({
            "code": _safe_str(item.get("code", item.get("股票代码", ""))),
            "name": _safe_str(item.get("name", item.get("股票简称", ""))),
            "price": _safe_float(item.get("latest", item.get("latest_price", item.get("最新价", 0)))),
            "change_pct": _safe_float(item.get("change_rate", item.get("change_pct", item.get("涨跌幅", 0)))),
            # 新版响应不再给成交额，保持 0 并在前端按缺失处理，不拿封单额冒充
            "volume": _safe_float(item.get("volume", item.get("成交额", 0))),
            "reason": _safe_str(item.get("reason_type", item.get("reason", item.get("涨停原因", "")))),
            "limit_up_time": ths_time(item.get("first_limit_up_time", item.get("首次封板时间", ""))),
            "open_times": _safe_int(item.get("open_num", item.get("open_limit_up_times", item.get("开板次数", 0)))),
        })
    return result


def fetch_northbound_raw() -> dict:
    """北向资金明细取数+解析(原 get_northbound_flow 主体)。异常向上抛出。"""
    url = "https://data.10jqka.com.cn/dataapi/hsgt/hsgt_detail"
    params = {
        "field": "199112,10,9001,330323,330324,330325,9002,330329",
        "filter": "HS_HSGT",
        "page": 1,
        "limit": 1,
    }
    resp = requests.get(url, params=params, timeout=5, headers=_THS_HEADERS)
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

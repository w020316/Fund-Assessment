# -*- coding: utf-8 -*-
"""东方财富适配器(M2 试点 → M3 绞杀迁移第一步)。

M3-B(2026-11-19): _kline_em_fallback 的「网络取数 + 解析」本体迁入
fetch_kline_raw, data_source_v2 保留同名私有门面(签名/行为零变更,
get_kline_mootdx 等内部调用方与既有测试的 patch 路径不受影响)。
解析逻辑逐行原样迁移(仍用 _safe_float 0 兜底, 下游 _sanitize_kline_rows
负责 0 价清洗), 语义变更留给 M4 契约层收口。
"""
from __future__ import annotations

from src.core.data_guard.contract import KlineContract, normalize_kline_row
from src.core.data_source_v2 import (
    _em_secid,
    _opt_float,
    _safe_float,
    _safe_int,
    _safe_str,
)
from loguru import logger

# em_get 走模块属性查找(_ds2.em_get): 既有测试以 monkeypatch.setattr(ds2,
# "em_get", ...) 为契约, 迁移后 patch 目标不变(调用时解析, 值绑定会失配)
from src.core import data_source_v2 as _ds2


def fetch_kline_raw(symbol: str, period: str = "daily", count: int = 30) -> list[dict]:
    """东财日/周/月 K 取数+解析(原 data_source_v2._kline_em_fallback 主体)。"""
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
        resp = _ds2.em_get(url, params=params)
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
    except Exception as e:  # noqa: BLE001 - 与迁移前行为逐位一致: 失败返回 []
        logger.warning(f"_kline_em_fallback failed: {e}")
        return []


def fetch_stock_ranking_raw(sort_field: str, sort_order: int, count: int) -> list[dict]:
    """东财 A 股榜单取数+解析(原 get_stock_ranking_em 主体, 无缓存/无降级)。

    M-04 语义保持: 停牌/退市股 f2/f3 等返回 "-" 时 → None(缺失), 不填 0。
    异常向上抛出, 由门面统一降级到新浪并记录来源。
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": count,
        # 2026-11-14: 东财 clist 的 po=1 是降序、po=0 是升序，而本模块沿用新浪
        # 约定 sort_order=0 表示降序。原样透传会得到"涨幅最低/成交额最低"（含退市股），
        # 之前 push2 被误判不可用、总走新浪兜底所以没暴露。
        "po": 0 if sort_order else 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": sort_field,
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18",
    }
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    items = data.get("data", {}).get("diff", [])
    result: list[dict] = []
    for item in items:
        result.append({
            "code": _safe_str(item.get("f12", "")),
            "name": _safe_str(item.get("f14", "")),
            "price": _opt_float(item.get("f2")),
            "change_pct": _opt_float(item.get("f3")),
            "change": _opt_float(item.get("f4")),
            "volume": _opt_float(item.get("f5")),
            "amount": _opt_float(item.get("f6")),
            "high": _opt_float(item.get("f15")),
            "low": _opt_float(item.get("f16")),
            "open": _opt_float(item.get("f17")),
            "prev_close": _opt_float(item.get("f18")),
        })
    return result


def fetch_research_reports_raw(stock_code: str, page: int, page_size: int) -> list[dict]:
    """东财研报取数+解析(原 get_research_reports 主体, 无缓存)。异常向上抛出。"""
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
    resp = _ds2.em_get(url, params=params)
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


def fetch_latest_trade_date_raw(report_name: str, sort_column: str = "TRADE_DATE") -> str:
    """取东财报表最近一条记录的交易日(原 _em_latest_trade_date 主体)。

    异常向上抛出, 由门面记录并返回 ""(行为逐位一致)。
    """
    resp = _ds2.em_get("https://datacenter-web.eastmoney.com/api/data/v1/get", params={
        "reportName": report_name,
        "columns": sort_column,
        "pageSize": 1,
        "pageNumber": 1,
        "sortColumns": sort_column,
        "sortTypes": -1,
    })
    items = (resp.json().get("result") or {}).get("data") or []
    return _safe_str(items[0].get(sort_column, ""))[:10] if items else ""


def fetch_dragon_tiger_raw() -> list[dict]:
    """龙虎榜取数+解析(原 get_dragon_tiger 主体, 无缓存/无降级)。

    交易日取返回集里的最近一天(周末写死"今天"必空); 异常向上抛出,
    由门面降级到 sina 诚实空列表(不用涨幅榜冒充)。
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "pageSize": 100,
        "pageNumber": 1,
        "reportName": "RPT_BILLBOARD_DAILYDETAILS",
        "columns": "ALL",
    }
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    items = data.get("result", {}).get("data", [])
    if items:
        latest = _safe_str(items[0].get("TRADE_DATE", ""))[:10]
        items = [it for it in items if _safe_str(it.get("TRADE_DATE", ""))[:10] == latest][:30]
    result: list[dict] = []
    for item in items:
        result.append({
            "code": _safe_str(item.get("SECURITY_CODE", "")),
            "name": _safe_str(item.get("SECURITY_NAME_ABBR", "")),
            "price": _safe_float(item.get("CLOSE_PRICE", 0)),
            "change_pct": _safe_float(item.get("CHANGE_RATE", 0)),
            "reason": _safe_str(item.get("EXPLANATION", "")),
            "buy_amount": _safe_float(item.get("TOTAL_BUY", 0)),
            "sell_amount": _safe_float(item.get("TOTAL_SELL", 0)),
            "net_amount": _safe_float(item.get("TOTAL_NET", 0)),
            "trade_date": _safe_str(item.get("TRADE_DATE", ""))[:10],
        })
    return result


def fetch_capital_flow_raw(stock_code: str) -> dict | None:
    """个股资金流取数+解析(原 get_capital_flow_detail 主体, 无缓存/无降级)。

    klines 空/字段不足 → None(门面直接返回 {}, 不走 fallback, 与迁移前一致);
    异常向上抛出 → 门面走估算 fallback。
    """
    secid = _em_secid(stock_code)
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "klt": 101,
        "lmt": 30,
    }
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return None
    latest = klines[-1]
    parts = latest.split(",")
    if len(parts) < 7:
        return None
    return {
        "date": parts[0],
        "main_net_inflow": _safe_float(parts[1]),
        "small_net_inflow": _safe_float(parts[2]),
        "medium_net_inflow": _safe_float(parts[3]),
        "large_net_inflow": _safe_float(parts[4]),
        "super_large_net_inflow": _safe_float(parts[5]),
        "main_inflow_pct": _safe_float(parts[6]) if len(parts) > 6 else 0.0,
    }


def fetch_margin_trading_raw(stock_code: str) -> dict | None:
    """融资融券取数+解析(原 get_margin_trading 主体, 无降级)。

    2026-11-17 单位校准保留: short_sell 金额语义上游无字段 → 恒 None;
    RQMCL 是股数填 short_sell_volume, 缺失时 None(不是 0)。
    items 空 → None(门面走诚实空 fallback); 异常向上抛出。
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "DATE",
        "sortTypes": -1,
        "pageSize": 10,
        "pageNumber": 1,
        "reportName": "RPTA_WEB_RZRQ_GGMX",
        "columns": "ALL",
        "filter": f'(SCODE="{stock_code}")',
    }
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    items = data.get("result", {}).get("data", [])
    if not items:
        return None
    item = items[0]
    return {
        "code": _safe_str(item.get("SCODE", "")),
        "trade_date": _safe_str(item.get("DATE", ""))[:10],
        "margin_buy": _safe_float(item.get("RZMRE", 0)),
        "margin_balance": _safe_float(item.get("RZYE", 0)),
        "short_sell": None,
        "short_sell_volume": _opt_float(item.get("RQMCL")),
        "short_balance": _safe_float(item.get("RQYE", 0)),
        "total_balance": _safe_float(item.get("RZRQYE", 0)),
    }


def fetch_block_trades_raw(stock_code: str) -> list[dict]:
    """大宗交易取数+解析(原 get_block_trades 主体, 无降级)。

    2026-11-17 口径校准保留: PREMIUM_RATIO 是小数, 乘 100 才是百分数;
    DEAL_VOLUME 单位是股。异常向上抛出。
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": -1,
        "pageSize": 20,
        "pageNumber": 1,
        "reportName": "RPT_DATA_BLOCKTRADE",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{stock_code}")',
    }
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    items = data.get("result", {}).get("data", [])
    result: list[dict] = []
    for item in items:
        premium_raw = _opt_float(item.get("PREMIUM_RATIO"))
        result.append({
            "code": _safe_str(item.get("SECURITY_CODE", "")),
            "name": _safe_str(item.get("SECURITY_NAME_ABBR", "")),
            "trade_date": _safe_str(item.get("TRADE_DATE", ""))[:10],
            "price": _safe_float(item.get("DEAL_PRICE", 0)),
            "volume": _safe_float(item.get("DEAL_VOLUME", 0)),
            "amount": _safe_float(item.get("DEAL_AMT", 0)),
            "premium_pct": round(premium_raw * 100, 2) if premium_raw is not None else None,
            "buyer": _safe_str(item.get("BUYER_NAME", "")),
            "seller": _safe_str(item.get("SELLER_NAME", "")),
        })
    return result


def fetch_shareholder_count_raw(stock_code: str) -> dict | None:
    """股东户数取数+解析(原 get_shareholder_count 主体, 无降级)。

    HOLDER_TOTAL_NUM 改名语义保持; TOTAL_NUM_RATIO 缺失时用上期计算。
    items 空/curr<=0 → None(门面走诚实空 fallback); 异常向上抛出。
    """
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
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    items = data.get("result", {}).get("data", [])
    if not items:
        return None
    item = items[0]
    curr_count = _safe_float(item.get("HOLDER_TOTAL_NUM", 0))
    if curr_count <= 0:
        return None
    change_pct = item.get("TOTAL_NUM_RATIO")
    if change_pct is None:
        prev_count = _safe_float(items[1].get("HOLDER_TOTAL_NUM", 0)) if len(items) > 1 else 0
        change_pct = ((curr_count - prev_count) / prev_count * 100) if prev_count else 0.0
    return {
        "code": _safe_str(item.get("SECURITY_CODE", "")),
        "end_date": _safe_str(item.get("END_DATE", ""))[:10],
        "holder_num": _safe_int(curr_count),
        "change_pct": round(_safe_float(change_pct), 2),
    }


def fetch_stock_news_raw(stock_code: str, page: int = 1, page_size: int = 10) -> list[dict]:
    """东财个股新闻(jsonp)取数+解析(原 get_stock_news 主体, 无来源标记)。异常向上抛出。"""
    import json
    import re
    import time

    url = "https://search-api-web.eastmoney.com/search/jsonp"
    cb = f"jQuery{int(time.time() * 1000)}"
    params = {
        "cb": cb,
        "param": f'{{"uid":"","keyword":"{stock_code}","type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr","param":{{"cmsArticleWebOld":{{"searchScope":"default","sort":"default","pageIndex":{page},"pageSize":{page_size},"preTag":"","postTag":""}}}}}}',
    }
    resp = _ds2.em_get(url, params=params)
    text = resp.text
    json_str = re.sub(rf"^{cb}\(", "", text).rstrip(")")
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
    return result


class EastmoneyProvider:
    """东方财富(push2his 日 K)。priority 10: K 线首选源。"""

    name = "eastmoney"
    priority = 10

    def fetch_kline(self, symbol: str, count: int) -> list[KlineContract] | None:
        from src.core.data_source_v2 import _kline_em_fallback  # 门面

        rows = _kline_em_fallback(symbol, "daily", count)
        if not rows:
            return None
        return [normalize_kline_row(r, source=self.name) for r in rows]

# -*- coding: utf-8 -*-
"""东方财富适配器(M2 试点 → M3 绞杀迁移第一步)。

M3-B(2026-11-19): _kline_em_fallback 的「网络取数 + 解析」本体迁入
fetch_kline_raw, data_source_v2 保留同名私有门面(签名/行为零变更,
get_kline_mootdx 等内部调用方与既有测试的 patch 路径不受影响)。
解析逻辑逐行原样迁移(仍用 _safe_float 0 兜底, 下游 _sanitize_kline_rows
负责 0 价清洗), 语义变更留给 M4 契约层收口。

M4-B3(2026-11-19): EM 财务快照/公司资料/基金实时备源 fundgz/基金历史
(pingzhongdata + f10/lsjz 分页)解析体迁入, 门面同名私有转发, 行为零变更。

M4-D(2026-11-19): 市场广度(ulist 聚合家数/北交所家数/全市场 total)与
分钟线(1 分钟 K 线)取数+解析体迁入; 自洽校验/fail-closed 编排/回退链
仍保留在 data_source_v2 门面。

M4-E(2026-11-19): 全球指数全景(m:100 缩放/区域分组)与全球快讯取数+解析体
迁入; 健康度/来源标记/缓存与新浪快讯降级编排仍保留在门面。
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone

from src.core.data_guard.contract import KlineContract, normalize_kline_row
from src.core.data_source_v2 import (
    ANOMALY_WINDOW_MINUTES,
    _em_secid,
    _has_meaningful_value,
    _minute_index,
    _opt_float,
    _safe_float,
    _safe_int,
    _safe_str,
)
from loguru import logger

# em_get 走模块属性查找(_ds2.em_get): 既有测试以 monkeypatch.setattr(ds2,
# "em_get", ...) 为契约, 迁移后 patch 目标不变(调用时解析, 值绑定会失配)
from src.core import data_source_v2 as _ds2

# 天天基金 fundgz 响应为 `jsonpgz({...});` 形式, 用正则抽出花括号内的 JSON 体
_FUNDGZ_JSON_RE = re.compile(r"\{.*\}", re.S)

# 基金历史 pingzhongdata: 北京时间毫秒时间戳与单位/累计净值数组正则
_CST = timezone(timedelta(hours=8))  # 东财 pingzhongdata 时间戳为北京时间零点
_PZ_NAV_RE = re.compile(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", re.S)
_PZ_ACC_RE = re.compile(r"Data_ACWorthTrend\s*=\s*(\[.*?\]);", re.S)

# f10/lsjz 的服务端分页: pageSize 参数被忽略, 固定 20 条/页(实测)。
# 分页上限常量保留在 data_source_v2 门面(既有测试契约目标), 此处经
# _ds2 属性调用, 保证 patch ds2._LSJZ_* 仍然生效


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


def fetch_financial_snapshot_em_raw(stock_code: str) -> dict:
    """EM 财务快照取数+解析(原 data_source_v2._financial_snapshot_em 主体)。

    f185 映射 profit_yoy; 全 None 快照 → {} 交给下一数据源(tencent)。
    异常就地吞掉返回 {}(与迁移前行为逐位一致)。
    """
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    secid = _em_secid(stock_code)
    params = {
        "secid": secid,
        "fields": "f9,f23,f20,f115,f116,f117,f162,f163,f167,f173,f183,f186,f187,f188",
    }
    try:
        resp = _ds2.em_get(url, params=params)
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


def fetch_company_info_raw(stock_code: str) -> dict:
    """公司资料取数+解析(原 data_source_v2.get_company_info 主体)。

    历史怪癖原样保留: f170 同时作为 change_pct_5min 与 change_pct 返回;
    缺失字段用 _safe_float(x, 0) 兜 0(与迁移前一致, 语义收口留给 M4)。
    异常向上抛出, 由门面记录并返回 {}。
    """
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    secid = _em_secid(stock_code)
    params = {
        "secid": secid,
        "fields": "f57,f58,f84,f116,f117,f162,f167,f170,f171,f173,f187,f188,f190,f192",
    }
    resp = _ds2.em_get(url, params=params)
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


def fetch_fund_realtime_fundgz_raw(codes: list[str]) -> list[dict]:
    """天天基金 fundgz 备源: 逐 code 拉取估值/净值(原
    data_source_v2._fetch_fund_realtime_fundgz 主体)。

    返回结构与腾讯主源完全同构。单个 code 失败(网络/解析/字段缺失)只记
    warning 并继续下一个, 整体永不抛出。R-06: dwjz<=0 视为无有效净值,
    该条直接丢弃, 不返回 nav=0 假行情。gszzl 超过 ±20 钳为 0。
    会话经 _ds2._EM_SESSION 属性调用, 既有测试 patch 目标不变。
    """
    if not codes:
        return []
    result: list[dict] = []
    for code in codes:
        try:
            url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}"
            resp = _ds2._EM_SESSION.get(url, timeout=5, headers={
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


def _ts_to_date(ts_ms) -> str:
    """北京时间毫秒时间戳 → YYYY-MM-DD(Render 为 UTC, 不能直接用本地时区)。"""
    try:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=_CST).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def fetch_fund_history_pingzhong_raw(code: str) -> list[dict]:
    """东财 pingzhongdata 基金历史取数+解析(原
    data_source_v2._fetch_fund_history_pingzhong 主体)。

    单请求返回全量历史(单位净值 + 累计净值), MAX 窗口不截断。
    累计净值仅对单位净值已有的日期对齐回填。异常向上抛出。
    """
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    resp = _ds2._EM_SESSION.get(url, timeout=10, headers={"Referer": "https://fund.eastmoney.com/"})
    text = resp.text or ""
    m_nav = _PZ_NAV_RE.search(text)
    if not m_nav:
        return []
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


def fetch_fund_history_lsjz_raw(code: str, start_date: str | None, end_date: str) -> list[dict]:
    """东财 f10/lsjz 分页历史取数+解析(原 data_source_v2._fetch_fund_history_lsjz 主体)。

    按 TotalCount 翻页(固定 20 条/页, pageSize 参数无效)。异常向上抛出。
    """
    url = "https://api.fund.eastmoney.com/f10/lsjz"

    rows: list[dict] = []
    page_size = _ds2._LSJZ_PAGE_SIZE
    total_pages = 1
    page_index = 1
    while page_index <= min(total_pages, _ds2._LSJZ_MAX_PAGES):
        params = {
            "callback": "jQuery",
            "fundCode": code,
            "pageIndex": page_index,
            "pageSize": page_size,
            "startDate": start_date or "",
            "endDate": end_date,
        }
        resp = _ds2._EM_SESSION.get(url, params=params, timeout=8, headers={
            "Referer": "https://fund.eastmoney.com/",
        })
        payload = json.loads(re.sub(r"^jQuery\(", "", resp.text).rstrip(")"))
        if page_index == 1:
            total = int(_safe_float(payload.get("TotalCount"), 0))
            page_size = int(_safe_float(payload.get("PageSize"), _ds2._LSJZ_PAGE_SIZE)) or _ds2._LSJZ_PAGE_SIZE
            total_pages = max(1, math.ceil(total / page_size))
            if total_pages > _ds2._LSJZ_MAX_PAGES:
                logger.warning(
                    f"get_fund_history {code}: TotalCount={total} 超过分页上限,"
                    f"仅取最近 {_ds2._LSJZ_MAX_PAGES * page_size} 条"
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


def fetch_sector_page_raw(page: int, attempts: int = 2) -> tuple[list[dict], int] | None:
    """板块全量分页单页取数+解析(原 data_source_v2._fetch_sector_page 主体)。

    重试 1 次仍失败/空页 → None(调用方据此 fail-closed)。URL/分页常量/
    字段映射经 _ds2 属性调用, 保证 patch ds2.em_get 与常量契约不变。
    """
    base = {
        "pz": _ds2._SECTOR_PAGE_SIZE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f3", "fs": "m:90+t:2", "fields": _ds2._SECTOR_FIELDS,
    }
    for attempt in range(max(1, attempts)):
        try:
            resp = _ds2.em_get(_ds2._SECTOR_URL, params=dict(base, pn=page))
            data = (resp.json() or {}).get("data") or {}
            items = data.get("diff") or []
            if isinstance(items, list) and items:
                total = int(_safe_float(data.get("total") or 0))
                return [_ds2._map_sector_row(item) for item in items if isinstance(item, dict)], total
            logger.warning(f"sector_ranking_full 第 {page} 页为空(第 {attempt + 1} 次)")
        except Exception as e:
            logger.warning(f"sector_ranking_full 第 {page} 页失败(第 {attempt + 1} 次): {e}")
        if attempt + 1 < attempts:
            time.sleep(0.3)
    logger.warning(f"sector_ranking_full 第 {page} 页重试后仍失败, fail-closed")
    return None


def fetch_sector_ranking_em_raw(fs: str) -> list[dict]:
    """板块排行单跳 clist 取数+解析(原 data_source_v2
    ._fetch_sector_ranking_uncached 的行业/概念两个分支公共体)。

    fs="m:90+t:2" 行业 / "m:90+t:3" 概念。返回解析结果(可能为空, 由门面
    决定是否降级下一跳); 网络异常向上抛出, 健康度/缓存/来源标记保留在门面。
    """
    params = {
        "pn": 1,
        "pz": 50,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": fs,
        "fields": "f2,f3,f4,f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87",
    }
    resp = _ds2.em_get(_ds2._SECTOR_URL, params=params)
    data = resp.json()
    items = data.get("data", {}).get("diff", [])
    return [_ds2._map_sector_row(item) for item in items]


def fetch_ulist_breadth_raw(secid: str, attempts: int = 2) -> tuple[int, int, int] | None:
    """ulist.np 单市场涨跌家数聚合取数+解析(原 data_source_v2._ulist_breadth_one)。

    取 (涨, 跌, 平) 家数; 失败重试 1 次(实测沪市偶发连接被关闭)。
    字段常量经 _ds2 属性调用, 既有 patch ds2._ULIST_FIELDS 契约不变。
    """
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {"fltt": 2, "invt": 2, "fields": _ds2._ULIST_FIELDS, "secids": secid}
    for attempt in range(max(1, attempts)):
        try:
            resp = _ds2.em_get(url, params=params)
            data = resp.json().get("data") or {}
            diff = data.get("diff")
            row = None
            if isinstance(diff, list) and diff:
                row = diff[0]
            elif isinstance(diff, dict) and diff:
                row = next(iter(diff.values()))
            if isinstance(row, dict):
                up = _opt_float(row.get("f104"))
                down = _opt_float(row.get("f105"))
                flat = _opt_float(row.get("f106"))
                if up is not None and down is not None and flat is not None:
                    return int(up), int(down), int(flat)
            logger.warning(f"ulist breadth {secid} 第 {attempt + 1} 次无有效字段: {str(data)[:120]}")
        except Exception as e:
            logger.warning(f"ulist breadth {secid} 第 {attempt + 1} 次失败: {e}")
        if attempt + 1 < attempts:
            _ds2.time.sleep(0.4)
    return None


def fetch_bj_exchange_total_raw() -> int | None:
    """北交所家数取数+解析(原 data_source_v2._bj_exchange_total)。

    clist 的 `fs` 把北交所也算进 `total`, 而 ulist 的沪/深聚合家数**不含**北交所,
    因此自洽校验前需减掉本值。取不到时返回 None, 调用方退回原始 total;
    异常就地吞掉(与迁移前行为逐位一致)。
    """
    try:
        resp = _ds2.em_get("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn": 1, "pz": 1, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:0+t:81+s:2048", "fields": "f2,f3,f12,f14",
        })
        total = _opt_float((resp.json().get("data") or {}).get("total"))
        return int(total) if total is not None else None
    except Exception as e:
        logger.warning(f"北交所家数获取失败(按原始 total 校验): {e}")
        return None


def fetch_market_total_count_raw() -> int:
    """全市场总家数(clist total)取数+解析(原 get_market_sentiment 内联块)。

    只作自洽校验分母, 不用于统计涨跌家数(clist pz 有服务端上限)。
    异常向上抛出, 由门面走指数代理兜底。
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 1, "po": 1, "np": 1,
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f2,f3,f12,f14",
    }
    resp = _ds2.em_get(url, params=params)
    data = resp.json()
    return int(_safe_float(data.get("data", {}).get("total", 0)))


def parse_minutes(klines: list[str]) -> tuple[dict[int, float], str]:
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
        if idx is None or idx > _ds2._ANOMALY_CLOSE_MINUTE:
            continue
        try:
            price = float(cols[2])
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[idx] = price
    return prices, day


def fetch_minute_series_raw(
    code: str,
    window_minutes: int = ANOMALY_WINDOW_MINUTES,
) -> tuple[dict[int, float], str]:
    """东财 1 分钟 K 线取数+解析(原 data_source_v2._minutes_from_em 主体)。

    lmt 必须随 window 缩放(window+4), 否则 window>5 时取点不足、端点缺失。
    异常向上抛出, 由门面回退下一源。
    """
    secid = _em_secid(code)
    resp = _ds2.em_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "1",
        "fqt": "1",
        "lmt": str(window_minutes + 4),
        "end": "20500101",
    })
    klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
    return parse_minutes(klines)


def fetch_global_news_raw() -> list[dict]:
    """东财全球快讯取数+解析(原 get_global_news 的 EM 分支)。

    news_list / list / 顶层数组三种响应结构都兼容; 无条目 → []。
    响应非 dict → ValueError(与迁移前一致, 门面据此降级新浪快讯);
    网络异常向上抛出。
    """
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
    resp = _ds2.em_get(url, params=params)
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
    return result


def fetch_world_indices_raw() -> list[dict]:
    """东财 m:100 全球指数精选取数+解析(原 get_global_indices_world 主体)。

    返回固定精选清单(缺失行 price/change/change_pct 均为 None, 不伪造 0);
    是否 fail-closed 由门面按「有用行数」判定。缩放走门面 _em_scale(纯函数),
    清单/区域标签常量经 _ds2 属性调用, 既有 patch 契约不变。
    """
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    secids = ",".join(f"100.{code}" for code, _, _ in _ds2._GLOBAL_WORLD_INDICES)
    params = {
        "fields": "f1,f2,f3,f4,f12,f14",
        "secids": secids,
        # 注意：不加 fltt=2 —— 加了东财会返回已缩放浮点(f2=58860.43)，
        # _em_scale 会再除一次 100 造成 100 倍错误；保持原始整数自己缩放。
    }
    resp = _ds2.em_get(url, params=params)
    payload = resp.json().get("data") or {}
    rows = payload.get("diff") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    by_code = {row.get("f12"): row for row in rows if isinstance(row, dict)}
    result: list[dict] = []
    for em_code, cn_name, region in _ds2._GLOBAL_WORLD_INDICES:
        row = by_code.get(em_code)
        if not row:
            result.append({
                "code": em_code, "name": cn_name, "region": region,
                "region_label": _ds2._REGION_LABELS[region],
                "price": None, "change": None, "change_pct": None,
            })
            continue
        precision = row.get("f1")
        result.append({
            "code": em_code,
            "name": row.get("f14") or cn_name,
            "region": region,
            "region_label": _ds2._REGION_LABELS[region],
            "price": _ds2._em_scale(row.get("f2"), precision),
            "change": _ds2._em_scale(row.get("f4"), precision),
            "change_pct": _ds2._em_scale(row.get("f3"), precision),
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

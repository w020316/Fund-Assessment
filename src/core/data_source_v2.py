from __future__ import annotations

import math
import random
import re
import time
from datetime import datetime
from typing import Any, Optional

import requests
from loguru import logger

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
_EM_MIN_INTERVAL: float = 1.0


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


def _prefix_code(code: str) -> str:
    if code.startswith(("sh", "sz", "SH", "SZ")):
        return code.lower()
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _em_secid(code: str) -> str:
    if code.startswith("6") or code.startswith("9"):
        return f"1.{code}"
    return f"0.{code}"


def em_get(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
    global _last_em_request_time
    elapsed = time.monotonic() - _last_em_request_time
    wait = _EM_MIN_INTERVAL - elapsed + random.uniform(0, 0.5)
    if wait > 0:
        time.sleep(wait)
    resp = _EM_SESSION.get(url, params=params, timeout=15, **kwargs)
    _last_em_request_time = time.monotonic()
    return resp


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
                return result
        except Exception as e:
            logger.warning(f"get_kline_mootdx mootdx failed, fallback to em: {e}")
    return _kline_em_fallback(symbol, period, count)


def get_realtime_quote_tencent(codes: list[str]) -> list[dict]:
    if not codes:
        return []
    prefixed = ",".join(_prefix_code(c) for c in codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
    try:
        resp = requests.get(url, timeout=10, headers={
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
            change_pct = _safe_float(parts[32])
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
                "change_pct": change_pct,
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
    except Exception as e:
        logger.warning(f"get_realtime_quote_tencent failed: {e}")
        return []


def get_index_realtime() -> list[dict]:
    codes = ["sh000001", "sz399001", "sz399006"]
    prefixed = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={prefixed}"
    try:
        resp = requests.get(url, timeout=10, headers={
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
        return result
    except Exception as e:
        logger.warning(f"get_index_realtime failed: {e}")
        return []


def get_research_reports(stock_code: str, page: int = 1, page_size: int = 10) -> list[dict]:
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
        resp = requests.get(url, params=params, timeout=10, headers={
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
        resp = requests.get(url, params=params, timeout=10, headers={
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


def get_dragon_tiger() -> list[dict]:
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
        return result
    except Exception as e:
        logger.warning(f"get_dragon_tiger failed: {e}")
        return []


def get_sector_ranking() -> list[dict]:
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
        return result
    except Exception as e:
        logger.warning(f"get_sector_ranking failed: {e}")
        return []


def get_capital_flow_detail(stock_code: str) -> dict:
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
        return {}


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
            return {}
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
        return result
    except Exception as e:
        logger.warning(f"get_block_trades failed: {e}")
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
            return {}
        item = items[0]
        prev_count = _safe_float(items[1].get("HOLDER_NUM", 0)) if len(items) > 1 else 0
        curr_count = _safe_float(item.get("HOLDER_NUM", 0))
        change_pct = ((curr_count - prev_count) / prev_count * 100) if prev_count else 0.0
        return {
            "code": _safe_str(item.get("SECURITY_CODE", "")),
            "end_date": _safe_str(item.get("END_DATE", ""))[:10],
            "holder_num": _safe_int(item.get("HOLDER_NUM", 0)),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        logger.warning(f"get_shareholder_count failed: {e}")
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
        return result
    except Exception as e:
        logger.warning(f"get_stock_news failed: {e}")
        return []


def get_global_news() -> list[dict]:
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
        items = data.get("data", {}).get("news_list", [])
        if not items:
            items = data.get("data", {}).get("list", [])
        result: list[dict] = []
        for item in items:
            result.append({
                "title": _safe_str(item.get("title", "")),
                "content": _safe_str(item.get("content", item.get("digest", "")))[:200],
                "source": _safe_str(item.get("source", item.get("mediaName", ""))),
                "publish_time": _safe_str(item.get("showTime", item.get("publishTime", ""))),
                "url": _safe_str(item.get("url", item.get("newsUrl", ""))),
            })
        return result
    except Exception as e:
        logger.warning(f"get_global_news failed: {e}")
        return []


def _financial_snapshot_mootdx(stock_code: str) -> dict:
    client = _mootdx_client()
    if client is None:
        return {}
    try:
        market = 1 if stock_code.startswith("6") or stock_code.startswith("9") else 0
        df = client.finance(symbol=stock_code, market=market)
        if df is None or df.empty:
            return {}
        row = df.iloc[0]
        return {
            "code": stock_code,
            "report_date": _safe_str(row.get("report_date", ""))[:10],
            "eps": _safe_float(row.get("basic_eps", 0)),
            "bvps": _safe_float(row.get("bvps", 0)),
            "roe": _safe_float(row.get("roe", 0)),
            "revenue": _safe_float(row.get("total_operating_revenue", 0)),
            "revenue_yoy": _safe_float(row.get("yysr_yoy", 0)),
            "profit": _safe_float(row.get("parent_net_profit", 0)),
            "profit_yoy": _safe_float(row.get("jlr_yoy", 0)),
            "gross_margin": _safe_float(row.get("gross_profit_ratio", 0)),
            "net_margin": _safe_float(row.get("net_profit_ratio", 0)),
        }
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
        return {
            "code": stock_code,
            "pe_ttm": _safe_float(data.get("f9", 0)),
            "pb": _safe_float(data.get("f23", 0)),
            "total_mv": _safe_float(data.get("f20", 0)),
            "circ_mv": _safe_float(data.get("f115", 0)),
            "roe": _safe_float(data.get("f162", 0)),
            "gross_margin": _safe_float(data.get("f186", 0)),
            "net_margin": _safe_float(data.get("f187", 0)),
            "revenue_yoy": _safe_float(data.get("f183", 0)),
            "profit_yoy": _safe_float(data.get("f185", 0)),
        }
    except Exception as e:
        logger.warning(f"_financial_snapshot_em failed: {e}")
        return {}


def get_financial_snapshot(stock_code: str) -> dict:
    result = _financial_snapshot_mootdx(stock_code)
    if result:
        return result
    return _financial_snapshot_em(stock_code)


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

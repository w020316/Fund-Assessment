"""东财 datacenter 报表契约回归测试

2026-11-15 实测发现东财悄悄下线/改名了多张报表, 导致融资融券、大宗交易、
股东户数、龙虎榜四个接口静默返回空列表。本测试锁定新的报表名、筛选列与
字段映射, 防止上游再次变更时无声退化。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core import data_source_v2 as ds2


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ok(rows, count=None):
    return _FakeResp({"success": True, "result": {"data": rows, "count": count if count is not None else len(rows)}})


def test_margin_trading_uses_current_report_and_fields(monkeypatch):
    """报表名/筛选列/字段映射锁定;并且**单位**必须对: RQMCL 是股, 不是元。

    本用例的单位/口径依据来自 2026-11-17 实测响应, 见
    docs/quality-review-2026-11-17-r6-stage1.md §5.4:
    实测 RPTA_WEB_RZRQ_GGMX(600519, 2026-11-16):
      RZYE=14,788,586,340(元) RQYE=322,111,545(元)
      RQYL=221,550(股) RQMCL=2,900(股) SPJ=1453.9(收盘价)
    且 RQYE/RQYL = 1453.9 = SPJ ⇒ RQYL/RQMCL 是**股**, RZYE/RQYE 是**元**;
    该报表**没有融券「额」字段**。
    """
    captured = {}

    def fake_em_get(url, params=None, **kwargs):
        captured.update(params or {})
        return _ok([{
            "SCODE": "600519", "DATE": "2026-11-16 00:00:00",
            # 融资买入额本次未核对上游原值, 只用于字段映射断言
            "RZMRE": 3.6e8,
            # 以下六项全部取 2026-11-16 实测原值, 保持三者(元/股/价)互相自洽
            "RZYE": 14788586340.0, "RQYE": 322111545.0, "RQYL": 221550,
            "RQMCL": 2900, "RZRQYE": 15110697885.0, "SPJ": 1453.9,
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_margin_trading("600519")

    assert captured["reportName"] == "RPTA_WEB_RZRQ_GGMX"
    assert 'SCODE="600519"' in captured["filter"]
    assert captured["sortColumns"] == "DATE"
    assert result == {
        "code": "600519",
        "trade_date": "2026-11-16",
        "margin_buy": 3.6e8,
        "margin_balance": 14788586340.0,
        # 金额语义不可得 → 诚实空值, 不把股数当元
        "short_sell": None,
        # 融券卖出量, 单位:股
        "short_sell_volume": 2900.0,
        "short_balance": 322111545.0,
        "total_balance": 15110697885.0,
    }
    # 单位自洽(准入要求): 融券余额(元) / 融券余量(股) == 当日收盘价(元/股)
    assert result["short_balance"] / 221550 == pytest.approx(1453.9, rel=1e-9)
    # total_balance == 融资余额 + 融券余额(元), 证明三者同为"元"
    assert result["total_balance"] == pytest.approx(
        result["margin_balance"] + result["short_balance"], rel=1e-9
    )


def test_margin_units_are_self_consistent(monkeypatch):
    """单位自洽等式(准入要求):融券余额(元) / 融券余量(股) == 当日收盘价(元/股)。"""

    def fake_em_get(url, params=None, **kwargs):
        return _ok([{
            "SCODE": "600519", "DATE": "2026-11-16 00:00:00",
            "RZMRE": 1.0e9, "RZYE": 14788586340.0, "RQMCL": 2900,
            "RQYL": 221550, "RQYE": 322111545.0, "RZRQYE": 15110697885.0,
            "SPJ": 1453.9,
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_margin_trading("600519")

    # 元 / 股 == 元/股(收盘价):证明 RQYE 是元、RQYL 是股
    assert result["short_balance"] / 221550 == pytest.approx(1453.9, rel=1e-6)
    assert result["short_sell_volume"] == 2900


def test_margin_missing_fields_are_none_not_zero(monkeypatch):
    """上游给空字段时 short_sell/short_sell_volume 必须是 None, 不得落成 0。"""

    def fake_em_get(url, params=None, **kwargs):
        return _ok([{
            "SCODE": "600519", "DATE": "2026-11-16 00:00:00",
            "RZMRE": 1.0e9, "RZYE": 1.0e10, "RQMCL": None,
            "RQYE": None, "RZRQYE": 1.1e10,
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_margin_trading("600519")

    assert result["short_sell"] is None
    assert result["short_sell_volume"] is None


def test_block_trades_uses_current_report_and_fields(monkeypatch):
    """报表字段 + 溢价率口径(上游 PREMIUM_RATIO 是小数, 基准=当日收盘价)。

    本用例的单位/口径依据来自 2026-11-17 实测响应, 见
    docs/quality-review-2026-11-17-r6-stage1.md §5.4:
    实测 RPT_DATA_BLOCKTRADE(300003, 2026-11-16):
      DEAL_PRICE=9.92, CLOSE_PRICE=12.02, PRE_CLOSE_PRICE=12.09
      PREMIUM_RATIO=-0.174708818636 == 9.92/12.02-1  (小数, 基准收盘价)
      DISCOUNT_RATIO=-17.948717948718 == 9.92/12.09-1 (百分数, 基准昨收)
      DEAL_AMT 13,137,100 / DEAL_PRICE 9.92 = 1,324,300 = DEAL_VOLUME(股)
    """
    captured = {}

    def fake_em_get(url, params=None, **kwargs):
        captured.update(params or {})
        return _ok([{
            "SECURITY_CODE": "300003", "SECURITY_NAME_ABBR": "乐普医疗",
            "TRADE_DATE": "2026-11-16 00:00:00", "DEAL_PRICE": 9.92,
            "DEAL_VOLUME": 1_324_300, "DEAL_AMT": 13_137_100.0,
            "PREMIUM_RATIO": -0.174708818636, "BUYER_NAME": "机构专用",
            "SELLER_NAME": "机构专用",
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_block_trades("300003")

    assert captured["reportName"] == "RPT_DATA_BLOCKTRADE"
    assert len(result) == 1
    item = result[0]
    assert item["volume"] == 1_324_300
    assert item["name"] == "乐普医疗"
    # 小数 → 百分数, 保留 2 位
    assert item["premium_pct"] == pytest.approx(-17.47, abs=0.01)
    # 基准是**收盘价**:与按昨收算的 -17.95 明确不同, 防止有人把两个字段互换
    assert item["premium_pct"] == pytest.approx(round((9.92 / 12.02 - 1) * 100, 2))
    assert abs(item["premium_pct"] - (9.92 / 12.09 - 1) * 100) > 0.4
    # 单位自洽(准入要求): 成交额(元) / 成交价(元/股) == 成交量(股)
    # 容忍 1e-5: 上游成交量按整百股取整, 13,137,100/9.92 = 1,324,300.4
    assert item["amount"] / item["price"] == pytest.approx(item["volume"], rel=1e-5)


def test_block_trades_missing_premium_is_none(monkeypatch):
    """PREMIUM_RATIO 缺失时 premium_pct 必须是 None, 不得当成 0% 溢价。"""

    def fake_em_get(url, params=None, **kwargs):
        return _ok([{
            "SECURITY_CODE": "300003", "SECURITY_NAME_ABBR": "乐普医疗",
            "TRADE_DATE": "2026-11-16 00:00:00", "DEAL_PRICE": 9.92,
            "DEAL_VOLUME": 1_000_000, "DEAL_AMT": 9_920_000.0,
            "PREMIUM_RATIO": None, "BUYER_NAME": "A", "SELLER_NAME": "B",
        }])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_block_trades("300003")

    assert result[0]["premium_pct"] is None


def test_shareholder_count_uses_renamed_fields(monkeypatch):
    def fake_em_get(url, params=None, **kwargs):
        return _ok([
            {"SECURITY_CODE": "600519", "END_DATE": "2026-09-30 00:00:00",
             "HOLDER_TOTAL_NUM": 278364, "TOTAL_NUM_RATIO": -6.0863},
            {"SECURITY_CODE": "600519", "END_DATE": "2026-06-30 00:00:00",
             "HOLDER_TOTAL_NUM": 296400, "TOTAL_NUM_RATIO": 1.2},
        ])

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    result = ds2.get_shareholder_count("600519")

    assert result == {
        "code": "600519",
        "end_date": "2026-09-30",
        "holder_num": 278364,
        "change_pct": -6.09,
    }


def test_dragon_tiger_single_request_and_latest_date(monkeypatch):
    """只发一次请求(避免 Render 上两次往返超时),并按返回集里的最近交易日截取。"""
    calls = []

    def fake_em_get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return _ok([
            {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
             "CLOSE_PRICE": 1435.26, "CHANGE_RATE": 1.77,
             "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
             "TOTAL_BUY": 1.2e8, "TOTAL_SELL": 9.1e7, "TOTAL_NET": 2.9e7,
             "TRADE_DATE": "2026-11-13 00:00:00"},
            {"SECURITY_CODE": "000858", "SECURITY_NAME_ABBR": "五粮液",
             "CLOSE_PRICE": 130.0, "CHANGE_RATE": 0.5,
             "EXPLANATION": "旧交易日数据",
             "TOTAL_BUY": 1.0, "TOTAL_SELL": 1.0, "TOTAL_NET": 0.0,
             "TRADE_DATE": "2026-11-12 00:00:00"},
        ])

    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", fake_em_get)

    result = ds2.get_dragon_tiger()

    assert len(calls) == 1
    assert calls[0]["reportName"] == "RPT_BILLBOARD_DAILYDETAILS"
    assert "filter" not in calls[0]
    assert len(result) == 1, "应只保留最近交易日(2026-11-13)的行"
    assert result == [{
        "code": "600519",
        "name": "贵州茅台",
        "price": 1435.26,
        "change_pct": 1.77,
        "reason": "日涨幅偏离值达到7%的前5只证券",
        "buy_amount": 1.2e8,
        "sell_amount": 9.1e7,
        "net_amount": 2.9e7,
        "trade_date": "2026-11-13",
    }]


def test_market_wide_stats_shareholder_uses_median(monkeypatch):
    def fake_em_get(url, params=None, **kwargs):
        report = (params or {}).get("reportName")
        if report == "RPT_DATA_BLOCKTRADE" and "filter" not in (params or {}):
            return _ok([{"TRADE_DATE": "2026-11-13 00:00:00"}])
        if report == "RPT_DATA_BLOCKTRADE":
            return _ok([{"TRADE_DATE": "2026-11-13 00:00:00"}], count=72)
        if report == "RPT_F10_EH_HOLDERNUM":
            return _ok([
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": -6.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": -2.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": 0.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": 2.0},
                {"HOLDER_TOTAL_NUM": 100000, "TOTAL_NUM_RATIO": 4.0},
                {"HOLDER_TOTAL_NUM": 19835, "TOTAL_NUM_RATIO": 46027.9},
            ])
        raise AssertionError(f"unexpected report {report}")

    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    monkeypatch.setattr(
        ds2, "_fetch_market_margin",
        lambda: {"trade_date": "2026-11-12", "margin_balance": 2.29e12},
    )

    result = ds2.get_market_wide_stats()

    assert result["block_trades_count"] == 72
    assert result["avg_shareholder_change_pct"] == 1.0
    assert result["margin_balance"] == 2.29e12
    assert result["margin_trade_date"] == "2026-11-12"


def test_market_wide_stats_margin_none_when_exchanges_unavailable(monkeypatch):
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _ok([]))
    monkeypatch.setattr(ds2, "_fetch_market_margin", lambda: None)

    result = ds2.get_market_wide_stats()

    assert result["margin_balance"] is None
    assert result["margin_trade_date"] is None


class _MarketMarginFakeGet:
    """按 URL 分派沪深交易所两融接口;szse_dates 之外的日期返回空数据。"""

    def __init__(self, sse_rows, szse_by_date):
        self.sse_rows = sse_rows
        self.szse_by_date = szse_by_date
        self.szse_queries: list[str] = []

    def __call__(self, url, params=None, **kwargs):
        if url == ds2._SSE_MARGIN_URL:
            return _FakeResp({"pageHelp": {"data": self.sse_rows}})
        if url == ds2._SZSE_MARGIN_URL:
            date = (params or {}).get("txtDate")
            self.szse_queries.append(date)
            row = self.szse_by_date.get(date)
            return _FakeResp([{"data": [row] if row else []}])
        raise AssertionError(f"unexpected url {url}")


def test_market_margin_aligns_latest_common_trade_date(monkeypatch):
    """深交所晚一天发布:必须退到两市都有数据的 11-12,而不是把 11-13 的沪市加 11-12 的深市。"""
    fake = _MarketMarginFakeGet(
        sse_rows=[
            {"opDate": "20261113", "rzrqjyzl": 1.0e12, "rzye": 9.9e11, "rqylje": 1.0e10},
            {"opDate": "20261112", "rzrqjyzl": 0.9e12, "rzye": 8.9e11, "rqylje": 1.0e10},
        ],
        szse_by_date={"2026-11-12": {"jrrzrjye": "10.00"}},
    )
    monkeypatch.setattr(ds2.requests, "get", fake)

    result = ds2._fetch_market_margin()

    assert fake.szse_queries == ["2026-11-13", "2026-11-12"]
    assert result == {"trade_date": "2026-11-12", "margin_balance": 901_000_000_000.0}


def test_market_margin_uses_same_day_when_szse_published(monkeypatch):
    fake = _MarketMarginFakeGet(
        sse_rows=[{"opDate": "20261113", "rzrqjyzl": 1.0e12}],
        szse_by_date={"2026-11-13": {"jrrzrjye": "5.00"}},
    )
    monkeypatch.setattr(ds2.requests, "get", fake)

    assert ds2._fetch_market_margin() == {
        "trade_date": "2026-11-13",
        "margin_balance": 1.0005e12,  # 5.00 亿元 = 5e8 元
    }


def test_market_margin_none_without_common_date(monkeypatch):
    fake = _MarketMarginFakeGet(
        sse_rows=[{"opDate": "20261113", "rzrqjyzl": 1.0e12},
                  {"opDate": "20261112", "rzrqjyzl": 0.9e12}],
        szse_by_date={},
    )
    monkeypatch.setattr(ds2.requests, "get", fake)

    assert ds2._fetch_market_margin() is None


def test_northbound_net_discontinued_returns_empty(monkeypatch):
    """报表只有成交额、净额字段为 None 时返回空,不得把 None 当 0 净流入卖出去。"""
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _FakeResp({"result": {"data": [{
        "TRADE_DATE": "2026-11-13 00:00:00", "DEAL_AMT": 111733.79,
        "NET_DEAL_AMT": None, "FUND_INFLOW": None,
        "BUY_AMT": None, "SELL_AMT": None,
    }]}}))

    assert ds2.get_northbound_flow_realtime() == {}


def test_northbound_resumes_with_total_only(monkeypatch):
    """上游若恢复净额但无沪深拆分, 返回合计并把 sh_sz_split_available 标 False。"""
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _FakeResp({"result": {"data": [{
        "TRADE_DATE": "2026-11-13 00:00:00", "DEAL_AMT": 111733.79,
        "NET_DEAL_AMT": 5.0e9, "FUND_INFLOW": None,
    }]}}))

    result = ds2.get_northbound_flow_realtime()

    assert result["total_net_inflow"] == 5.0e9
    assert result["date"] == "2026-11-13"
    assert result["sh_sz_split_available"] is False


def test_northbound_total_only_keeps_split_none(monkeypatch):
    """A-01(2026-11-17):只有合计净额时, sh/sz 必须是 None 而不是 0.0。

    数据源侧旧实现写死 0.0 + sh_sz_split_available=False, 下游只判 bool(northbound),
    于是 0.0 一路输出成"沪股通净流入 0 亿"。这里锁死"不可得 = None"。
    """
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _FakeResp({"result": {"data": [{
        "TRADE_DATE": "2026-11-16 00:00:00", "DEAL_AMT": 111733.79,
        "NET_DEAL_AMT": 5.0e9, "FUND_INFLOW": None,
    }]}}))

    result = ds2.get_northbound_flow_realtime()

    assert result["total_net_inflow"] == 5.0e9
    assert result["sh_net_inflow"] is None
    assert result["sz_net_inflow"] is None
    assert result["sh_sz_split_available"] is False


# ===== P1(2026-11-17):全市场涨跌家数必须来自聚合字段, 不能靠 clist 分页 =====
# 线上实测: /api/review/board 返回 rise=100 / fall=0 / flat=0 / ratio=1.0,
# 而同一时刻有 67 家跌停、创业板 -1.20%。根因是 clist 的 pz 有服务端上限
# (实测 pz=6000 仍只回 100 行)且 fid=f3 降序 → 那 100 行全是上涨股。
# 真实值(同一时刻直连东财 ulist.np 聚合字段):
#   沪市 f104=598 f105=1688 f106=69  / 深市 f104=638 f105=2237 f106=64
#   合计 涨=1236 跌=3925 平=133 (总 5294; clist total 5564, 覆盖率 95.1%)

_REAL_SH = {"f104": 598, "f105": 1688, "f106": 69}
_REAL_SZ = {"f104": 638, "f105": 2237, "f106": 64}
_REAL_TOTAL = 5564


class _SentimentResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _mock_em_for_breadth(monkeypatch, sh, sz, total, clist_diff_rows=None,
                         sh_raises=False, bj_total=366):
    """按 URL 分发: clist(沪深+北交所) → total / clist(北交所) → bj / ulist.np → 家数。"""
    calls = {"ulist": [], "clist": 0}

    def fake_em_get(url, params=None, **kwargs):
        params = params or {}
        if params.get("fs") == "m:0+t:81+s:2048":
            return _SentimentResp({"data": {"total": bj_total, "diff": []}})
        if "ulist.np" in url:
            secids = str(params.get("secids", ""))
            calls["ulist"].append(secids)
            if sh_raises and secids.startswith("1."):
                raise RuntimeError("Remote end closed connection without response")
            row = sh if secids.startswith("1.") else sz if secids.startswith("0.") else None
            if row is None:
                return _SentimentResp({"data": None})
            return _SentimentResp({"data": {"diff": [dict(row, f12=secids, f14="指数")]}})
        # clist: 只用于拿总家数; clist_diff_rows 用来模拟"只回 100 行且全上涨"
        calls["clist"] += 1
        if clist_diff_rows is not None:
            return _SentimentResp({"data": {"total": total, "diff": clist_diff_rows}})
        return _SentimentResp({"data": {"total": total, "diff": []}})

    monkeypatch.setattr(ds2, "em_get", fake_em_get)
    monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
    monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)
    return calls


class TestMarketBreadthCounts:
    def test_real_counts_from_aggregate_fields(self, monkeypatch):
        """真实值: 涨1236/跌3925/平133 → ratio 0.2335 → 偏空。"""
        _mock_em_for_breadth(monkeypatch, _REAL_SH, _REAL_SZ, _REAL_TOTAL)

        out = ds2.get_market_sentiment()

        assert out["rise_count"] == 1236
        assert out["fall_count"] == 3925
        assert out["flat_count"] == 133
        assert out["ratio"] == pytest.approx(0.2335, abs=1e-3)
        assert out["sentiment"] == "偏空"
        assert out["basis"] == "counts"
        assert out["counts_available"] is True
        assert out["counts_basis"] == "counts"

    def test_clist_capped_at_100_rows_does_not_produce_100_0_0(self, monkeypatch):
        """反向证明: 即便 clist 只回 100 行全上涨(真实上限), 也**不得**产出 100/0/0。

        这是本次修复的核心: 旧实现用 clist 分页统计 → rise=100, fall=0, flat=0,
        rise_ratio 恒 1.0; 现在家数来自聚合字段, clist 只提供分母。
        """
        hundred_up = [{"f3": 10.0 - i * 0.01} for i in range(100)]
        _mock_em_for_breadth(monkeypatch, _REAL_SH, _REAL_SZ, _REAL_TOTAL,
                             clist_diff_rows=hundred_up)

        out = ds2.get_market_sentiment()

        assert (out["rise_count"], out["fall_count"], out["flat_count"]) != (100, 0, 0)
        assert out["rise_count"] == 1236
        assert out["ratio"] != 1.0

    def test_coverage_out_of_range_fails_closed(self, monkeypatch):
        """只统计到半个市场(合计/total = 0.5) → 三个 count 一律 None, 不可当全市场。"""
        half_sh = {"f104": 300, "f105": 700, "f106": 30}
        half_sz = {"f104": 200, "f105": 900, "f106": 20}
        # 合计 2150 / total 4300 = 0.5
        _mock_em_for_breadth(monkeypatch, half_sh, half_sz, 4300)

        out = ds2.get_market_sentiment()

        assert out["rise_count"] is None
        assert out["fall_count"] is None
        assert out["flat_count"] is None
        assert out["counts_available"] is False
        # 家数状态: partial_sample;结论依据: 交给指数代理(basis=index_proxy)
        assert out["counts_basis"] == "partial_sample"
        assert out["basis"] != "counts"
        assert out["sentiment"] in ("--", "偏多", "偏空", "中性"), "交由指数代理兜底"

    def test_single_market_failure_is_not_treated_as_whole_market(self, monkeypatch):
        """沪市取不到 → **不得**拿深市数字当全市场;标 partial 并由指数代理出结论。"""
        calls = _mock_em_for_breadth(monkeypatch, _REAL_SH, _REAL_SZ, _REAL_TOTAL,
                                     sh_raises=True)
        monkeypatch.setattr(ds2.time, "sleep", lambda s: None)  # 跳过重试退避

        out = ds2.get_market_sentiment()

        # 沪市重试了 2 次(退避), 深市根本没去请求
        assert calls["ulist"] == ["1.000001", "1.000001"]
        assert out["counts_available"] is False
        assert out["counts_basis"] == "partial_sample"
        assert out["rise_count"] is None, "不能把深市的 638 当成全市场上涨家数"
        assert out["fall_count"] is None

    def test_clist_total_including_bj_is_not_mistaken_for_partial(self, monkeypatch):
        """clist total 含北交所(实测 5930), 而 ulist 只统计沪深(5294/5564=95.1%)。

        直接用 5930 当分母会算成 89.3% 而误判"只统计到一部分" —— 必须减去北交所家数。
        """
        _mock_em_for_breadth(monkeypatch, _REAL_SH, _REAL_SZ, 5930, bj_total=366)

        out = ds2.get_market_sentiment()

        assert out["counts_available"] is True, "分母未校正 → 误判 partial"
        assert out["rise_count"] == 1236
        assert out["sentiment"] == "偏空"

    def test_zero_rise_is_valid_not_failure(self, monkeypatch):
        """全市场下跌(rise=0)是合法实况, 不能被当成"数据缺失"。"""
        _mock_em_for_breadth(
            monkeypatch, {"f104": 0, "f105": 1200, "f106": 10},
            {"f104": 0, "f105": 1300, "f106": 12}, 2600,
        )

        out = ds2.get_market_sentiment()

        assert out["rise_count"] == 0
        assert out["counts_available"] is True
        assert out["sentiment"] == "偏空"
        assert out["ratio"] == 0.0

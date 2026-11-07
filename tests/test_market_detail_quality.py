"""U6 回归保护：个股详情的"数据可信度"与"响应时延预算"。

背景（2026-11-07）：
用户在阶段四体验评估里发现，个股详情页在数据全缺失时仍显示 `ROE 0.00%`
与"质量评分 100"，且首次打开要等 5.2 秒。逐层定位到四个独立缺陷：

1. `_prefix_code("510300")` 返回 `sz510300`（沪市场内基金被判成深市），
   腾讯行情返回 `v_pv_none_match`，于是 quote 整段为空 → 页面全是"—"。
2. `_financial_snapshot_mootdx` 用 `_safe_float(x, 0)` 把"缺失"变成 0.0，
   而字典非空 → 被当成有效快照直接返回 → 前端渲染 `ROE 0.00%`。
3. 详情接口命中缓存时用 `_build_meta(cached=True)` 重建 meta，
   `quality_score` 缺省 100 → "第一次 30 分、刷新一下变 100 分"。
4. `_parallel_fetch` 没有总预算，K 线回退链（mootdx→东财→新浪）
   单项 4.7s 直接把详情拖到 5.19s。

本文件锁死这四条的行为，防止回退。
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core import data_source_v2 as ds2
from web.routes import market


# ===== 1. 市场前缀 / secid =====


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("510300", "sh510300"),   # 沪市 ETF（本次修复的核心场景）
        ("512880", "sh512880"),   # 沪市 ETF
        ("159915", "sz159915"),   # 深市 ETF
        ("600519", "sh600519"),   # 沪市主板
        ("000001", "sz000001"),   # 深市主板
        ("688981", "sh688981"),   # 科创板
        ("920085", "bj920085"),   # 北交所（9 开头但属北交所，必须先判 92）
        ("430047", "bj430047"),   # 北交所
        ("sh000001", "sh000001"),  # 已带前缀原样返回
        ("SZ000001", "sz000001"),  # 大写前缀统一小写
    ],
)
def test_prefix_code_covers_funds_and_bse(code, expected):
    assert ds2._prefix_code(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("510300", "1.510300"),    # 修复前是 0.510300 → K线/财务全空
        ("600519", "1.600519"),
        ("000001", "0.000001"),
        ("920085", "0.920085"),
        ("sh000001", "1.000001"),  # 上证指数：显式前缀优先，不能按裸码判成深市
        ("sz399001", "0.399001"),
    ],
)
def test_em_secid_respects_explicit_prefix(code, expected):
    assert ds2._em_secid(code) == expected


def test_prefix_code_does_not_treat_bare_index_as_sz():
    """回归：sh000001 若先剥前缀再判市，会被错判成深市平安银行。"""
    assert ds2._em_secid("sh000001") == "1.000001"
    assert ds2._em_secid("sz000001") == "0.000001"


# ===== 2. 缺失值不得冒充 0 =====


def test_opt_float_distinguishes_missing_from_zero():
    assert ds2._opt_float(None) is None
    assert ds2._opt_float("") is None
    assert ds2._opt_float("--") is None
    assert ds2._opt_float("abc") is None
    assert ds2._opt_float(0) == 0.0          # 真实的 0 要保留
    assert ds2._opt_float("0") == 0.0
    assert ds2._opt_float(3.5) == 3.5


def _fake_mootdx_finance(row_values: dict):
    """构造一个"行为像 pandas.DataFrame"的假 finance 返回值。

    注意：iloc 必须是**实例属性**。若写成类属性（class iloc: ...），
    `df.iloc[0]` 会变成对类做下标访问（需要 __class_getitem__），
    测试会因 TypeError 落到 except 分支而"恰好"通过，属于假阳性。
    """

    class _Iloc:
        def __init__(self, row):
            self._row = row

        def __getitem__(self, _idx):
            return self._row

    class _FakeDf:
        empty = False

        def __init__(self, row):
            self.iloc = _Iloc(row)

    class _FakeClient:
        def finance(self, **_kwargs):
            return _FakeDf(dict(row_values))

    return _FakeClient()


def test_financial_snapshot_rejects_all_none_row():
    """mootdx 对 ETF 返回一行全 None，旧实现变成"全 0 快照"直接返回。"""
    fake = _fake_mootdx_finance({
        "report_date": None,
        "basic_eps": None,
        "bvps": None,
        "roe": None,
        "total_operating_revenue": None,
        "yysr_yoy": None,
        "parent_net_profit": None,
        "jlr_yoy": None,
        "gross_profit_ratio": None,
        "net_profit_ratio": None,
    })
    with patch.object(ds2, "_mootdx_client", return_value=fake):
        assert ds2._financial_snapshot_mootdx("510300") == {}


def test_financial_snapshot_keeps_real_zero_roe():
    """真实 ROE=0 是有效数据，不能被当成缺失丢掉。"""
    fake = _fake_mootdx_finance({"report_date": "2026-09-30", "roe": 0.0, "basic_eps": 1.2})
    with patch.object(ds2, "_mootdx_client", return_value=fake):
        snap = ds2._financial_snapshot_mootdx("600519")
    assert snap["roe"] == 0.0
    assert snap["report_date"] == "2026-09-30"
    assert snap["eps"] == 1.2
    # 缺失的字段必须是 None，不是 0
    assert snap["bvps"] is None
    assert snap["gross_margin"] is None


def test_tencent_snapshot_treats_zero_pe_as_not_applicable():
    """ETF 的 PE/PB 在腾讯侧是 0（不适用），必须转成 None。"""
    quote = {
        "code": "510300", "name": "沪深300ETF", "price": 4.1,
        "pe_ttm": 0.0, "pb": 0.0,
        "total_market_value": 1923.45, "circ_market_value": 1923.45,
    }
    with patch.object(ds2, "get_realtime_quote_tencent", return_value=[quote]):
        snap = ds2._financial_snapshot_tencent("510300")
    assert snap["pe_ttm"] is None
    assert snap["pb"] is None
    assert snap["total_mv"] == 1923.45


# ===== 3. 并行抓取的时间预算 =====


def test_parallel_fetch_returns_on_deadline_without_waiting_slowest():
    def _fast():
        return {"ok": True}

    def _slow():
        time.sleep(1.5)
        return {"ok": True}

    t0 = time.monotonic()
    out = ds2._parallel_fetch([("fast", _fast), ("slow", _slow)], deadline=0.4)
    elapsed = time.monotonic() - t0
    assert out["fast"] == {"ok": True}
    assert out["slow"] is None          # 超时记为缺失，而不是抛异常
    assert elapsed < 1.0                # 不能等慢任务跑完


def test_parallel_fetch_without_deadline_keeps_old_semantics():
    def _a():
        return 1

    def _b():
        return 2

    assert ds2._parallel_fetch([("a", _a), ("b", _b)]) == {"a": 1, "b": 2}


def test_parallel_fetch_deadline_marks_failure_as_none():
    def _boom():
        raise RuntimeError("upstream down")

    out = ds2._parallel_fetch([("boom", _boom)], deadline=1.0)
    assert out["boom"] is None


# ===== 4. 详情接口：完整度 / 质量分 / 缓存一致性 =====

FULL_DETAIL = {
    "code": "600519",
    "name": "贵州茅台",
    "quote": {
        "code": "600519", "name": "贵州茅台", "price": 1453.0, "prev_close": 1445.0,
        "change": 8.0, "change_pct": 0.55, "volume": 1e6, "turnover": 1.2,
        "pe_ttm": 22.11, "pb": 6.7,
        "total_market_value": 18163.69, "circ_market_value": 18163.69,
    },
    "financial": {"code": "600519", "pe_ttm": 22.11, "pb": 6.7, "roe": 28.5},
    "capital_flow": {"main_net_inflow": 1.2e8, "main_inflow_pct": 3.1},
    "kline_summary": {},
}

EMPTY_DETAIL = {
    "code": "510300", "name": "",
    "quote": {}, "financial": {}, "capital_flow": {}, "kline_summary": {},
}


@pytest.fixture
def client():
    from web.api import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_route_cache():
    market.cache.clear()
    yield
    market.cache.clear()


def test_detail_meta_exposes_sections_and_completeness(client):
    with patch.object(market.ds2, "get_stock_detail", return_value=FULL_DETAIL):
        body = client.get("/api/market/stock_detail", params={"code": "600519"}).json()
    meta = body["_meta"]
    assert meta["completeness"] == 100.0
    assert meta["sections"] == {
        "quote": True, "financial": True, "capital_flow": True, "kline_summary": False,
    }
    assert meta["degraded"] is False


def test_detail_quality_never_exceeds_completeness(client):
    """空数据时质量分必须是 0，不能出现"满页 — 但质量 100"。"""
    with patch.object(market.ds2, "get_stock_detail", return_value=EMPTY_DETAIL):
        body = client.get("/api/market/stock_detail", params={"code": "510300"}).json()
    meta = body["_meta"]
    assert meta["completeness"] == 0.0
    assert meta["quality_score"] == 0.0
    assert meta["degraded"] is True
    assert meta["sections"]["quote"] is False


def test_detail_cache_hit_keeps_quality_score(client):
    """回归：命中缓存时质量分曾被重置为 100。"""
    with patch.object(market.ds2, "get_stock_detail", return_value=EMPTY_DETAIL):
        first = client.get("/api/market/stock_detail", params={"code": "510300"}).json()
        second = client.get("/api/market/stock_detail", params={"code": "510300"}).json()
    assert first["_meta"]["quality_score"] == second["_meta"]["quality_score"] == 0.0
    assert second["_meta"]["cached"] is True
    assert second["_meta"]["completeness"] == first["_meta"]["completeness"]


def test_detail_exception_path_is_degraded(client):
    def _raise(_code):
        raise RuntimeError("boom")

    with patch.object(market.ds2, "get_stock_detail", _raise):
        body = client.get("/api/market/stock_detail", params={"code": "600519"}).json()
    assert body["_meta"]["degraded"] is True
    assert body["_meta"]["completeness"] == 0.0


# ===== 5. 区间摘要异步补拉端点 =====

SUMMARY = {
    "latest_date": "2026-11-06", "latest_close": 4.124,
    "high_30d": 4.658, "low_30d": 4.031,
    "avg_volume_30d": 16268214.73, "days": 30,
}


def test_kline_summary_endpoint_returns_data(client):
    with patch.object(market.ds2, "get_kline_summary", return_value=SUMMARY):
        body = client.get("/api/market/stock_kline_summary", params={"code": "510300"}).json()
    assert body["data"]["high_30d"] == 4.658
    assert body["_meta"]["quality_score"] == 100.0


def test_kline_summary_empty_is_not_cached(client):
    """空结果不写缓存：上游恢复后不能还回旧空值。"""
    with patch.object(market.ds2, "get_kline_summary", return_value={}):
        first = client.get("/api/market/stock_kline_summary", params={"code": "510300"}).json()
    assert first["data"] == {}
    assert first["_meta"]["degraded"] is True

    with patch.object(market.ds2, "get_kline_summary", return_value=SUMMARY):
        second = client.get("/api/market/stock_kline_summary", params={"code": "510300"}).json()
    assert second["data"]["latest_close"] == 4.124


def test_kline_summary_exception_is_handled(client):
    def _raise(*_a, **_kw):
        raise RuntimeError("kline down")

    with patch.object(market.ds2, "get_kline_summary", _raise):
        resp = client.get("/api/market/stock_kline_summary", params={"code": "510300"})
    assert resp.status_code == 200
    assert resp.json()["data"] == {}


# ===== 6. 校验器：kline_summary 不能再被误判为"缺K线" =====


def test_validator_accepts_kline_summary_as_kline_dimension():
    from src.core.data_validator import get_data_validator

    validator = get_data_validator()
    data = {
        "quote": {"code": "600519", "name": "贵州茅台", "price": 1453.0},
        "financial": {"roe": 28.5},
        "capital_flow": {"main_net_inflow": 1.0},
        "kline_summary": {"latest_close": 1453.0, "high_30d": 1500.0, "low_30d": 1400.0},
    }
    result = validator.validate_analysis_data(data)
    assert not any("K线" in msg for msg in result.warnings)

"""个股异动（砸盘 / 拉高）单元测试

覆盖三层：
1. 纯函数判定（compute_window_change / classify_anomaly）—— 边界值与缺数据；
2. 三个上游源的解析器（腾讯分时 / 东财 1 分钟 K 线 / 新浪 1 分钟 K 线）；
3. 编排与端点（候选池为空、分钟数据缺失、降级口径必须如实上报）。

关键回归点（都是「不撒谎」这条铁律的具体化）：
- 缺数据 → 返回 None / 计入 unavailable，**绝不填 0 当「无异动」**；
- 窗口端点缺失（如跨午休、上游丢零成交分钟）→ 判数据不足，**不用相邻分钟硬凑**
  （硬凑会把「5 分钟」静默变成别的跨度）；
- 候选池为空 / 口径降级 → `_meta.degraded=true` + `degraded_reason`，
  前端据此区分「查过但没有异动」与「数据源不可用」。

数据来源说明：test_real_measured_data 用的 8.97 / 10.46 是 2026-11-06 腾讯分时
300118（东方日升）09:45→09:50 的真实价格（当日 -1.2% 拉至 +15.2%），
用来锁住「自算窗口涨幅」的口径；不是构造的假数据。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core import data_source_v2 as ds2


@pytest.fixture()
def client():
    from web.api import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_caches():
    """清空两处缓存：路由层 DataCache 与 ds2 内部 _cache。

    异动编排结果在 ds2 内部缓存 30s、路由层缓存 60s，不清会让后一个用例
    读到前一个用例 mock 出来的结果（典型的「mock 被跳过」假绿）。
    """
    from web.routes import market

    market.cache.clear()
    ds2._cache.clear()
    yield
    market.cache.clear()
    ds2._cache.clear()


# ---------------------------------------------------------------------------
# 1. 纯函数：窗口涨幅
# ---------------------------------------------------------------------------

class TestComputeWindowChange:
    def test_normal_five_minute_gain(self):
        prices = {570: 10.0, 571: 10.0, 572: 10.1, 573: 10.2, 574: 10.2, 575: 10.3}
        change, start, end = ds2.compute_window_change(prices, 5)
        assert round(change, 4) == pytest.approx(3.0, abs=1e-6)
        assert (start, end) == (570, 575)

    def test_normal_five_minute_drop(self):
        prices = {570: 10.0, 575: 9.7}
        change, _, _ = ds2.compute_window_change(prices, 5)
        assert change == pytest.approx(-3.0, abs=1e-6)

    def test_missing_start_endpoint_returns_none(self):
        """跨午休/上游丢分钟时端点缺失 → None，绝不拿相邻分钟顶替。

        构造要点（第一版用例写成只有 2 个价格，朴素实现也返回 None，抓不到错）：
        必须给足 >= window+1 个价格，且窗口起点恰好缺失，
        这样「取最后 N 个价格」的朴素实现会算出 +5% 并误报拉高。
        """
        prices = {
            690: 10.0, 691: 10.1, 692: 10.2, 693: 10.3, 694: 10.4,  # 11:30 前后
            781: 10.5,                                              # 13:01，窗口起点 776 不存在
        }
        assert ds2.compute_window_change(prices, 5) is None

    def test_naive_last_n_would_falsely_trigger(self):
        """反证「取最后 6 个价格」会误报：10.0→10.5 = +5%，但真实跨度 91 分钟。

        这条与上一条互为守卫：上一条锁实现、这一条锁「为什么不能那样实现」。
        """
        prices = {690: 10.0, 691: 10.1, 692: 10.2, 693: 10.3, 694: 10.4, 781: 10.5}
        ordered = [prices[k] for k in sorted(prices)]
        naive = (ordered[-1] / ordered[-6] - 1) * 100
        assert naive == pytest.approx(5.0, abs=1e-6)          # 朴素算法会得出 +5%
        assert ds2.compute_window_change(prices, 5) is None    # 本实现拒绝

    def test_empty_and_invalid_inputs(self):
        assert ds2.compute_window_change({}, 5) is None
        assert ds2.compute_window_change({570: 10.0}, 0) is None
        assert ds2.compute_window_change({570: 10.0}, -3) is None

    def test_zero_start_price_guarded(self):
        assert ds2.compute_window_change({570: 0.0, 575: 10.0}, 5) is None

    def test_real_measured_data(self):
        """真实数据：300118 东方日升 2026-11-06 09:45=8.97 → 09:50=10.46。"""
        prices = {585: 8.97, 586: 9.35, 587: 9.91, 588: 10.23, 589: 10.14, 590: 10.46}
        change, start, end = ds2.compute_window_change(prices, 5)
        assert round(change, 2) == pytest.approx(16.61, abs=0.01)
        assert (start, end) == (585, 590)


# ---------------------------------------------------------------------------
# 2. 纯函数：判定
# ---------------------------------------------------------------------------

class TestClassifyAnomaly:
    def test_surge_boundary_is_inclusive(self):
        assert ds2.classify_anomaly(3.0) == "surge"

    def test_just_below_surge_not_triggered(self):
        assert ds2.classify_anomaly(2.99) is None

    def test_dump_boundary_is_inclusive(self):
        assert ds2.classify_anomaly(-3.0) == "dump"

    def test_just_above_dump_not_triggered(self):
        assert ds2.classify_anomaly(-2.99) is None

    def test_none_is_not_misjudged_as_no_anomaly(self):
        """字段缺失时返回 None（=不判定），而不是把缺失当成「无异动」再去比对阈值。

        这里断言的是「不抛异常也不产生误判」；「缺失要计入 unavailable」
        由编排层用例覆盖。
        """
        assert ds2.classify_anomaly(None) is None

    def test_custom_thresholds(self):
        assert ds2.classify_anomaly(1.5, surge_pct=1.0, dump_pct=-1.0) == "surge"
        assert ds2.classify_anomaly(-1.5, surge_pct=1.0, dump_pct=-1.0) == "dump"
        assert ds2.classify_anomaly(0.5, surge_pct=1.0, dump_pct=-1.0) is None


# ---------------------------------------------------------------------------
# 3. 解析器：三个上游源统一成 {分钟序号: 价格}
# ---------------------------------------------------------------------------

class TestParsers:
    def test_tencent_drops_post_close_duplicates(self):
        payload = [
            "1459 1451.00 42848 6199528289.00",
            "1500 1453.00 42864 6201893601.56",
            "1529 1453.00 42864 6201893601.56",  # 收盘后补点，必须剔除
            "1530 1453.00 42864 6201893601.56",
        ]
        prices, _ = ds2._parse_tx_minutes(payload)
        assert max(prices) == 15 * 60  # 15:00
        assert prices[15 * 60] == 1453.0
        assert len(prices) == 2

    def test_tencent_skips_malformed_and_zero_price(self):
        payload = ["", "0931", "0931 abc 1 1", "0931 0.00 1 1", "0931 10.5 1 1", "9999 10.0"]
        prices, _ = ds2._parse_tx_minutes(payload)
        assert prices == {9 * 60 + 31: 10.5}

    def test_em_parses_close_column(self):
        klines = [
            "2026-11-06 14:58,1451.00,1451.00,1451.00,1451.00,8,1160705.00",
            "2026-11-06 15:00,1451.00,1453.00,1453.00,1451.00,2826,410617808.00",
            "2026-11-06 15:30,1453.00,1453.00,1453.00,1453.00,0,0.00",  # >15:00 剔除
        ]
        prices, day = ds2._parse_em_minutes(klines)
        assert day == "2026-11-06"
        assert prices[14 * 60 + 58] == 1451.0
        assert prices[15 * 60] == 1453.0
        assert (15 * 60 + 30) not in prices

    def test_sina_tolerates_gaps_without_filling(self):
        """新浪丢零成交分钟（14:57 → 15:00 跳空）时，不能补 14:58/14:59。"""
        bars = [
            {"day": "2026-11-06 14:56:00", "close": "1451.880"},
            {"day": "2026-11-06 14:57:00", "close": "1451.000"},
            {"day": "2026-11-06 15:00:00", "close": "1453.000"},
            {"day": "bad", "close": "1"},
        ]
        prices, day = ds2._parse_sina_minutes(bars)
        assert day == "2026-11-06"
        assert sorted(prices) == [14 * 60 + 56, 14 * 60 + 57, 15 * 60]
        assert (14 * 60 + 58) not in prices  # 不插值


# ---------------------------------------------------------------------------
# 3b. 窗口贯穿取数层（A-07 回归：window>5 且腾讯失败回退时取点必须随 window 缩放）
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TestWindowThreading:
    def test_em_fetch_limit_scales_with_window(self, monkeypatch):
        """东财 lmt 必须 = window+4，而非硬编码常量 5+4（否则 window>5 取点不足）。"""
        captured = {}

        def fake_em_get(url, params=None, **kw):
            captured.update(params or {})
            return _FakeResp({"data": {"klines": []}})

        monkeypatch.setattr(ds2, "em_get", fake_em_get)
        ds2._minutes_from_em("300118", 30)
        assert captured["lmt"] == "34"
        ds2._minutes_from_em("300118", 5)
        assert captured["lmt"] == "9"

    def test_sina_fetch_limit_scales_with_window(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, **kw):
            captured.update(params or {})
            return _FakeResp([])

        monkeypatch.setattr(ds2.requests, "get", fake_get)
        ds2._minutes_from_sina("300118", 30)
        assert captured["datalen"] == "36"

    def test_minute_series_forwards_window_to_fetcher(self, monkeypatch):
        """get_stock_minute_series 必须把 window_minutes 透传给上游 fetcher。"""
        captured = {}

        def fake_tencent(code, window_minutes=ds2.ANOMALY_WINDOW_MINUTES):
            captured["window"] = window_minutes
            return ({i: 10.0 for i in range(40)}, "2026-11-08")

        monkeypatch.setattr(ds2, "_minutes_from_tencent", fake_tencent)
        prices, source, day = ds2.get_stock_minute_series("300118", 30)
        assert captured["window"] == 30
        assert source == "tencent"
        assert len(prices) == 40


class TestMinuteSeriesAcceptance:
    """A-03 回归：按「窗口端点可算」判定源可用，而非 crude len>window。"""

    def test_sparse_series_covering_window_is_accepted(self, monkeypatch):
        """仅 2 点但恰好覆盖窗口端点（新浪丢零成交分钟场景）应直接采纳，不回退。"""
        def fake_tencent(code, window_minutes=ds2.ANOMALY_WINDOW_MINUTES):
            return ({585: 10.0, 590: 11.0}, "2026-11-08")  # window=5: end=590, start=585

        def fail_em(code, window_minutes=ds2.ANOMALY_WINDOW_MINUTES):
            raise AssertionError("端点齐全不应回退到东财")

        monkeypatch.setattr(ds2, "_minutes_from_tencent", fake_tencent)
        monkeypatch.setattr(ds2, "_minutes_from_em", fail_em)
        prices, source, day = ds2.get_stock_minute_series("300118", 5)
        assert source == "tencent"
        assert len(prices) == 2  # 旧 len>5 门槛会误拒此可用序列

    def test_series_missing_window_start_falls_back(self, monkeypatch):
        """点数够但缺窗口起点（end-5 不存在）应回退下一源，而非采纳后恒空。"""
        def fake_tencent(code, window_minutes=ds2.ANOMALY_WINDOW_MINUTES):
            # 6 点但 end=590 的起点 585 缺失（580~584 + 590）
            return ({580: 10.0, 581: 10.0, 582: 10.0, 583: 10.0, 584: 10.0, 590: 10.0}, "2026-11-08")

        def fake_em(code, window_minutes=ds2.ANOMALY_WINDOW_MINUTES):
            return ({585: 10.0, 590: 11.0}, "2026-11-08")

        monkeypatch.setattr(ds2, "_minutes_from_tencent", fake_tencent)
        monkeypatch.setattr(ds2, "_minutes_from_em", fake_em)
        prices, source, day = ds2.get_stock_minute_series("300118", 5)
        assert source == "eastmoney"  # 腾讯缺起点 → 回退东财（端点齐全）
        assert prices[590] == 11.0


# ---------------------------------------------------------------------------
# 4. 编排：筛选 → 精算 → 判定
# ---------------------------------------------------------------------------

def _candidate(code="300118", name="东方日升", price=10.46, change_pct=15.26):
    return {
        "code": code, "name": name, "price": price, "change_pct": change_pct,
        "volume": 1.0, "amount": 2.0, "turnover_pct": 12.5, "volume_ratio": 2.1,
        "speed_1min_pct": 1.9,
    }


class TestOrchestration:
    def test_empty_candidate_pool(self, monkeypatch):
        monkeypatch.setattr(ds2, "_screen_candidates", lambda per_side=8: ([], "sina_changepercent"))
        res = ds2.get_stock_anomalies()
        assert res["items"] == []
        assert res["scanned"] == 0
        assert res["as_of"] == ""

    def test_missing_minute_data_counted_not_judged(self, monkeypatch):
        """取不到分钟数据的个股计入 unavailable，且**不产生**任何异动条目。"""
        monkeypatch.setattr(ds2, "_screen_candidates",
                            lambda per_side=8: ([_candidate()], "eastmoney_speed"))
        monkeypatch.setattr(ds2, "get_stock_minute_series", lambda code, window_minutes=5: ({}, "", ""))
        res = ds2.get_stock_anomalies()
        assert res["items"] == []
        assert res["scanned"] == 1
        assert res["unavailable"] == 1

    def test_surge_item_has_auditable_basis(self, monkeypatch):
        """命中拉高时，basis 必须带窗口起止分钟与具体涨幅（可逐条复核）。"""
        prices = {585: 8.97, 586: 9.35, 587: 9.91, 588: 10.23, 589: 10.14, 590: 10.46}
        monkeypatch.setattr(ds2, "_screen_candidates",
                            lambda per_side=8: ([_candidate()], "eastmoney_speed"))
        monkeypatch.setattr(ds2, "get_stock_minute_series",
                            lambda code, window_minutes=5: (prices, "tencent", "2026-11-06"))
        res = ds2.get_stock_anomalies()
        assert len(res["items"]) == 1
        item = res["items"][0]
        assert item["kind"] == "surge"
        basis = item["basis"]
        assert basis["window_minutes"] == 5
        assert basis["window_change_pct"] == pytest.approx(16.61, abs=0.01)
        assert basis["from_minute"] == "09:45"
        assert basis["to_minute"] == "09:50"
        assert basis["threshold_pct"] == ds2.ANOMALY_SURGE_PCT
        assert basis["minute_source"] == "tencent"
        assert res["as_of"] == "2026-11-06 09:50"

    def test_dump_detected(self, monkeypatch):
        prices = {585: 10.0, 586: 9.9, 587: 9.8, 588: 9.7, 589: 9.6, 590: 9.6}
        monkeypatch.setattr(ds2, "_screen_candidates",
                            lambda per_side=8: ([_candidate(code="600715", name="文投控股")], "eastmoney_speed"))
        monkeypatch.setattr(ds2, "get_stock_minute_series",
                            lambda code, window_minutes=5: (prices, "tencent", "2026-11-06"))
        res = ds2.get_stock_anomalies()
        assert len(res["items"]) == 1
        assert res["items"][0]["kind"] == "dump"

    def test_no_anomaly_when_below_threshold(self, monkeypatch):
        prices = {585: 10.0, 586: 10.01, 587: 10.02, 588: 10.01, 589: 10.0, 590: 10.02}
        monkeypatch.setattr(ds2, "_screen_candidates",
                            lambda per_side=8: ([_candidate()], "eastmoney_speed"))
        monkeypatch.setattr(ds2, "get_stock_minute_series",
                            lambda code, window_minutes=5: (prices, "tencent", "2026-11-06"))
        res = ds2.get_stock_anomalies()
        assert res["items"] == []
        # 查过了、确实没有异动 —— as_of 仍要有值，前端才能显示数据时间
        assert res["as_of"] == "2026-11-06 09:50"
        assert res["unavailable"] == 0


# ---------------------------------------------------------------------------
# 5. 端点：降级必须如实上报，不能 500、不能假装正常
# ---------------------------------------------------------------------------

class TestEndpoint:
    def test_degraded_when_screen_falls_back(self, client, monkeypatch):
        payload = {
            "items": [], "window_minutes": 5,
            "screen_source": "sina_changepercent", "kline_source": "tencent",
            "scanned": 16, "unavailable": 0, "as_of": "2026-11-06 15:00",
        }
        monkeypatch.setattr(ds2, "get_stock_anomalies", lambda window=5: payload)
        resp = client.get("/api/market/stock_anomaly")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        meta = body["_meta"]
        assert meta["degraded"] is True
        assert "涨速榜不可用" in meta["degraded_reason"]
        assert meta["screen_source"] == "sina_changepercent"
        assert meta["scanned"] == 16
        assert meta["market_closed"] is True  # as_of 不是今天
        assert meta["threshold_pct"] == {"surge": 3.0, "dump": -3.0}

    def test_no_degraded_when_native_screen_used(self, client, monkeypatch):
        payload = {
            "items": [{"code": "300118", "name": "东方日升", "price": 10.46,
                       "change_pct": 15.26, "kind": "surge",
                       "basis": {"window_minutes": 5, "window_change_pct": 16.61,
                                 "from_minute": "09:45", "to_minute": "09:50",
                                 "volume_ratio": 2.1, "turnover_pct": 12.5,
                                 "amount": 2.0, "threshold_pct": 3.0,
                                 "minute_source": "tencent"},
                       "trade_date": "2026-11-06"}],
            "window_minutes": 5, "screen_source": "eastmoney_speed",
            "kline_source": "tencent", "scanned": 16, "unavailable": 0,
            "as_of": "2026-11-06 09:50",
        }
        monkeypatch.setattr(ds2, "get_stock_anomalies", lambda window=5: payload)
        body = client.get("/api/market/stock_anomaly").json()
        assert len(body["data"]) == 1
        assert body["_meta"]["degraded"] is False
        assert "degraded_reason" not in body["_meta"]

    def test_upstream_exception_returns_structured_degraded_not_500(self, client, monkeypatch):
        def boom(window=5):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(ds2, "get_stock_anomalies", boom)
        resp = client.get("/api/market/stock_anomaly")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["_meta"]["degraded"] is True
        assert "编排失败" in body["_meta"]["degraded_reason"]

    def test_empty_pool_marks_degraded(self, client, monkeypatch):
        monkeypatch.setattr(ds2, "get_stock_anomalies", lambda window=5: {
            "items": [], "window_minutes": 5, "screen_source": "sina_changepercent",
            "kline_source": "", "scanned": 0, "unavailable": 0, "as_of": "",
        })
        body = client.get("/api/market/stock_anomaly").json()
        assert body["data"] == []
        assert body["_meta"]["degraded"] is True
        assert "候选池为空" in body["_meta"]["degraded_reason"]

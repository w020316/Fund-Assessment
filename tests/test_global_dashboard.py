"""全球市场面板单元测试（2026-11-17 新增）

覆盖:
- _em_scale: 东财 m:100 缩放规则（真实值 = 原始值 / 10^precision；缺失 → None）
- get_global_indices_world: 全球指数全景（区域分组 / 缺失 None / 上游失败 → []）
- get_global_sector_etfs: 行业板块 ETF 代理（腾讯 code 匹配 / 缺失 None / 固定 11 条）
- get_global_market_dashboard: 聚合（core + world + sectors + vix，任一失败不拖垮整体）
- /api/global/dashboard 路由: 200 与契约结构

口径约定（与 M-04/C-02 一致）: 缺失一律 None，严禁伪造 0。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core import data_source_v2 as ds2


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_route_cache():
    """清空 global_market 路由内存缓存，避免 mock 被跳过。"""
    from web.routes import global_market
    global_market.cache.clear()
    yield
    global_market.cache.clear()


def _em_resp(payload: dict):
    """构造 em_get 的最小返回对象（只需 .json()）。"""
    class _R:
        def json(self):
            return payload
    return _R()


class TestEmScale:
    """_em_scale 东财缩放规则"""

    def test_scales_by_precision(self):
        assert ds2._em_scale(17120502, 2) == pytest.approx(171205.02)
        assert ds2._em_scale(-134, 2) == pytest.approx(-1.34)
        assert ds2._em_scale(10361, 2) == pytest.approx(103.61)

    def test_precision_zero_no_scale(self):
        assert ds2._em_scale(1234, 0) == pytest.approx(1234.0)

    def test_missing_values_return_none(self):
        assert ds2._em_scale("-", 2) is None
        assert ds2._em_scale(None, 2) is None
        assert ds2._em_scale("", 2) is None
        assert ds2._em_scale("abc", 2) is None


class TestGlobalIndicesWorld:
    """get_global_indices_world 全球指数全景"""

    def _setup(self, monkeypatch, diff_rows, raise_exc=None):
        monkeypatch.setattr(ds2, "_cache", {})
        if raise_exc is not None:
            monkeypatch.setattr(ds2, "em_get", lambda *a, **k: (_ for _ in ()).throw(raise_exc))
        else:
            monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _em_resp({"data": {"diff": diff_rows}}))

    def test_scaling_and_region_mapping(self, monkeypatch):
        rows = [
            {"f1": 2, "f2": 5886043, "f3": -134, "f4": -80159, "f12": "N225", "f14": "日经225"},
            {"f1": 2, "f2": "-", "f3": "-", "f4": "-", "f12": "FTSE", "f14": "英国富时100"},
        ]
        self._setup(monkeypatch, rows)
        result = ds2.get_global_indices_world()
        codes = {r["code"]: r for r in result}
        # 固定 18 条精选清单
        assert len(result) == len(ds2._GLOBAL_WORLD_INDICES)
        n225 = codes["N225"]
        assert n225["price"] == pytest.approx(58860.43)
        assert n225["change_pct"] == pytest.approx(-1.34)
        assert n225["region"] == "asia"
        assert n225["region_label"] == "亚太"
        # 上游缺失 → None（不伪造 0）
        ftse = codes["FTSE"]
        assert ftse["price"] is None
        assert ftse["change_pct"] is None

    def test_partial_missing_rows_yield_none_fields(self, monkeypatch):
        # 精选 18 条中上游只返回部分 → 缺失行保留 None 字段（不伪造 0、不丢行）
        rows = [{"f1": 2, "f2": 10361, "f3": 2, "f4": 4, "f12": "UDI", "f14": "美元指数"}]
        self._setup(monkeypatch, rows)
        result = ds2.get_global_indices_world()
        assert len(result) == len(ds2._GLOBAL_WORLD_INDICES)
        udi = next(r for r in result if r["code"] == "UDI")
        assert udi["price"] is not None
        assert all(r["price"] is None for r in result if r["code"] != "UDI")

    def test_all_missing_fail_closed_to_empty(self, monkeypatch):
        """上游全部缺失 = 源不可用 → 返回 []（fail-closed，quality 0）。"""
        self._setup(monkeypatch, [])
        assert ds2.get_global_indices_world() == []

    def test_upstream_failure_returns_empty(self, monkeypatch):
        self._setup(monkeypatch, [], raise_exc=RuntimeError("blocked"))
        assert ds2.get_global_indices_world() == []

    def test_percent_is_not_doubled(self, monkeypatch):
        """涨跌幅 f3 除以 100 后即 %，不允许再乘 100（缩放自洽断言）。"""
        rows = [{"f1": 2, "f2": 10361, "f3": 2, "f4": 4, "f12": "UDI", "f14": "美元指数"}]
        self._setup(monkeypatch, rows)
        udi = next(r for r in ds2.get_global_indices_world() if r["code"] == "UDI")
        assert udi["price"] == pytest.approx(103.61)
        assert udi["change_pct"] == pytest.approx(0.02)
        assert udi["change"] == pytest.approx(0.04)
        # 自洽: change_pct ≈ change / prev...此处用 f4/f2 量级核对缩放一致
        assert abs(udi["change"]) < abs(udi["price"])  # 涨跌额不该大于价格本身


class TestGlobalSectorEtfs:
    """get_global_sector_etfs 行业板块 ETF"""

    def _quotes(self):
        quotes = []
        for code, sector in ds2._GLOBAL_SECTOR_ETFS:
            quotes.append({
                "code": code[2:] + ".AM", "name": f"{sector}ETF-SPDR",
                "price": 100.0, "prev_close": 99.0, "change": 1.0, "change_pct": 1.01,
            })
        return quotes

    def test_maps_tencent_codes_to_sectors(self, monkeypatch):
        monkeypatch.setattr(ds2, "_cache", {})
        with patch.object(ds2, "_tencent_global_quote", return_value=self._quotes()):
            result = ds2.get_global_sector_etfs()
        assert len(result) == 11
        by_sector = {r["sector"]: r for r in result}
        assert by_sector["科技"]["code"] == "XLK"
        assert by_sector["科技"]["price"] == pytest.approx(100.0)
        assert by_sector["通讯服务"]["code"] == "XLC"

    def test_empty_upstream_keeps_none_fields(self, monkeypatch):
        monkeypatch.setattr(ds2, "_cache", {})
        with patch.object(ds2, "_tencent_global_quote", return_value=[]):
            result = ds2.get_global_sector_etfs()
        assert len(result) == 11
        assert all(r["price"] is None and r["change_pct"] is None for r in result)


class TestGlobalMarketDashboard:
    """get_global_market_dashboard 聚合"""

    def test_aggregates_all_groups_and_vix(self, monkeypatch):
        monkeypatch.setattr(ds2, "_cache", {})
        fetched = {
            "core_indices": [{"code": ".DJI", "name": "道琼斯", "price": 1.0}],
            "world_indices": [{"code": "N225", "price": 2.0}],
            "sectors": [{"code": "XLK", "price": 3.0}],
        }
        with patch.object(ds2, "_parallel_fetch", return_value=fetched), \
             patch.object(ds2, "_tencent_global_quote",
                          return_value=[{"code": ".VIX", "name": "VIX", "price": 21.67, "change_pct": 0.35}]):
            result = ds2.get_global_market_dashboard()
        assert result["core_indices"] == fetched["core_indices"]
        assert result["world_indices"] == fetched["world_indices"]
        assert result["sectors"] == fetched["sectors"]
        assert result["vix"] == {"name": "标普500波动率指数(VIX)", "price": 21.67, "change_pct": 0.35}

    def test_groups_fail_independently(self, monkeypatch):
        """任一分组失败 → 空列表，vix 无报价 → None，不抛异常。"""
        monkeypatch.setattr(ds2, "_cache", {})
        with patch.object(ds2, "_parallel_fetch",
                          return_value={"core_indices": [], "world_indices": [], "sectors": []}), \
             patch.object(ds2, "_tencent_global_quote", return_value=[]):
            result = ds2.get_global_market_dashboard()
        assert result == {"core_indices": [], "world_indices": [], "sectors": [], "vix": None}


class TestDashboardRoute:
    """/api/global/dashboard 路由"""

    def test_route_returns_200_with_contract(self, client):
        with patch("web.routes.global_market.ds2.get_global_market_dashboard") as mock_dash, \
             patch("web.routes.global_market.cache") as mock_cache:
            mock_cache.get.return_value = None
            mock_dash.return_value = {
                "core_indices": [], "world_indices": [
                    {"code": "N225", "name": "日经225", "region": "asia", "region_label": "亚太",
                     "price": 58860.43, "change": -801.59, "change_pct": -1.34}],
                "sectors": [], "vix": None,
            }
            resp = client.get("/api/global/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["world_indices"][0]["price"] == pytest.approx(58860.43)
        assert body["_meta"]["data_source"] == "tencent+eastmoney"

    def test_route_upstream_failure_is_empty_not_500(self, client):
        with patch("web.routes.global_market.ds2.get_global_market_dashboard",
                   side_effect=RuntimeError("boom")), \
             patch("web.routes.global_market.cache") as mock_cache:
            mock_cache.get.return_value = None
            resp = client.get("/api/global/dashboard")
        assert resp.status_code == 200
        assert resp.json()["data"] == {}
        assert resp.json()["_meta"]["quality_score"] == 0.0

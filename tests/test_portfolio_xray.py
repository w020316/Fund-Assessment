"""持仓重叠 X-Ray 单元测试(阶段六批次 A,T-A3)。

验证:
- min-weight overlap 手算对拍
- 单股跨基金暴露聚合与排序
- 集中度预警阈值(单股 15% / 单基金 40%)
- 数据可得性降级:全部缺失 → insufficient_data + "数据不足"(非空结果);
  部分缺失 → missing_funds + degraded=True
- GET /api/portfolio/xray 路由(含 401 鉴权与降级路径)
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.analysis import portfolio_xray as pxray
from src.core.fund_nav_source import FundHoldings


def _h(code: str, holdings: list[tuple[str, str, float]], as_of: str = "2026Q3") -> FundHoldings:
    return FundHoldings(
        fund_code=code, as_of=as_of,
        holdings=[{"stock_code": s, "stock_name": n, "weight": w} for s, n, w in holdings],
    )


# ============ 纯计算 ============


class TestBuildXray:
    def test_overlap_matrix_manual(self):
        """手算对拍: A={X:10%,Y:5%}, B={X:6%,Z:9%} → overlap = min(10,6)=6%。"""
        hm = {
            "A": _h("A", [("600519", "贵州茅台", 10.0), ("000858", "五粮液", 5.0)]),
            "B": _h("B", [("600519", "贵州茅台", 6.0), ("300750", "宁德时代", 9.0)]),
        }
        r = pxray.build_xray(hm)
        assert r["status"] == "ok"
        assert r["funds"] == ["A", "B"]
        m = r["overlap_matrix"]
        assert m[0][0] == 1.0
        assert m[0][1] == pytest.approx(0.06, abs=1e-9)
        assert m[1][0] == pytest.approx(0.06, abs=1e-9)
        assert r["degraded"] is False

    def test_overlap_min_weight_semantics(self):
        """min-weight: A={X:10%}, B={X:3%} → 3%(取小者)。"""
        hm = {
            "A": _h("A", [("600519", "贵州茅台", 10.0)]),
            "B": _h("B", [("600519", "贵州茅台", 3.0)]),
        }
        r = pxray.build_xray(hm)
        assert r["overlap_matrix"][0][1] == pytest.approx(0.03, abs=1e-9)

    def test_stock_exposure_sorted_top(self):
        """单股暴露: X 由两只基金各持 10%/6% → total 16%,排第一。"""
        hm = {
            "A": _h("A", [("600519", "贵州茅台", 10.0)]),
            "B": _h("B", [("600519", "贵州茅台", 6.0), ("300308", "中际旭创", 8.0)]),
        }
        r = pxray.build_xray(hm)
        top = r["stock_exposure"][0]
        assert top["stock_code"] == "600519"
        assert top["fund_count"] == 2
        assert top["total_weight"] == pytest.approx(0.16, abs=1e-9)

    def test_concentration_warning_stock(self):
        """单股隐含权重 16% > 15% → 预警。"""
        hm = {
            "A": _h("A", [("600519", "贵州茅台", 10.0)]),
            "B": _h("B", [("600519", "贵州茅台", 6.0)]),
        }
        r = pxray.build_xray(hm)
        assert any("贵州茅台" in w for w in r["concentration_warnings"])

    def test_concentration_warning_fund_weight(self):
        """单基金市值占比 45% > 40% → 预警。"""
        hm = {"A": _h("A", [("600519", "贵州茅台", 5.0)])}
        r = pxray.build_xray(hm, fund_weights={"A": 0.45})
        assert any("市值占比" in w for w in r["concentration_warnings"])

    def test_all_missing_insufficient_data(self):
        """全部基金披露持仓拿不到 → insufficient_data + "数据不足"(非空结果)。"""
        r = pxray.build_xray({"A": None, "B": None})
        assert r["status"] == "insufficient_data"
        assert "数据不足" in r["message"]
        assert r["degraded"] is True
        assert r["missing_funds"] == ["A", "B"]

    def test_partial_missing_degraded(self):
        """部分缺失 → degraded=True + missing_funds,正常基金仍出矩阵。"""
        hm = {
            "A": _h("A", [("600519", "贵州茅台", 10.0)]),
            "B": None,
        }
        r = pxray.build_xray(hm)
        assert r["status"] == "ok"
        assert r["degraded"] is True
        assert r["missing_funds"] == ["B"]
        assert r["funds"] == ["A"]


# ============ 路由层 ============


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr("web.routes.fund._FUND_POSITIONS_FILE", tmp_path / "positions.json")
    from src.core.cache import DataCache
    monkeypatch.setattr(
        "web.routes.portfolio._portfolio_cache",
        DataCache(cache_dir=str(tmp_path / "cache"), default_ttl=60),
    )
    monkeypatch.setattr(
        "web.routes.portfolio.fns._holdings_cache",
        DataCache(cache_dir=str(tmp_path / "holdings"), default_ttl=86400),
    )
    yield


_POSITIONS = (
    '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0},'
    '{"fund_code":"008984","fund_name":"科技","shares":50.0,"cost_nav":1.5}]}'
)


class TestXrayRoute:
    def test_xray_requires_admin_token(self, client, monkeypatch):
        """铁律:401 鉴权用例。"""
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.get("/api/portfolio/xray")
        assert resp.status_code == 401

    def test_xray_empty_positions(self, client):
        resp = client.get("/api/portfolio/xray")
        assert resp.status_code == 200
        data = resp.json()
        assert data["funds"] == []
        assert "暂无持仓" in data["message"]

    def test_xray_success(self, client, tmp_path):
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        hm = {
            "110022": _h("110022", [("600519", "贵州茅台", 9.85)]),
            "008984": _h("008984", [("600519", "贵州茅台", 6.0), ("300308", "中际旭创", 10.2)]),
        }
        quotes = [
            {"code": "110022", "nav": 2.5}, {"code": "008984", "nav": 1.5},
        ]
        with patch("src.core.fund_nav_source.get_fund_holdings", side_effect=lambda c, top=10: hm.get(c)), \
             patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=quotes):
            resp = client.get("/api/portfolio/xray")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["funds"] == ["110022", "008984"]
        assert data["overlap_matrix"][0][1] == pytest.approx(0.06, abs=1e-9)
        # 市值占比: 110022 = 250/325 ≈ 76.9%(> 40% → 预警)
        assert any("市值占比" in w for w in data["concentration_warnings"])

    def test_xray_all_sources_fail_insufficient(self, client, tmp_path):
        """akshare 全失败 → insufficient_data,仍 200,且不缓存降级结果。"""
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        with patch("src.core.fund_nav_source.get_fund_holdings", return_value=None), \
             patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=[]):
            resp = client.get("/api/portfolio/xray")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "insufficient_data"
        assert "数据不足" in data["message"]
        assert data["degraded"] is True

    def test_xray_success_cached(self, client, tmp_path):
        """成功结果缓存 6h:第二次调用不再触达数据源(降级结果不缓存)。"""
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        hm = {
            "110022": _h("110022", [("600519", "贵州茅台", 9.85)]),
            "008984": _h("008984", [("300308", "中际旭创", 10.2)]),
        }
        calls = {"n": 0}

        def _fake(code, top=10):
            calls["n"] += 1
            return hm.get(code)

        with patch("src.core.fund_nav_source.get_fund_holdings", side_effect=_fake), \
             patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=[]):
            client.get("/api/portfolio/xray")
            client.get("/api/portfolio/xray")
        assert calls["n"] == 2  # 每基金各 1 次,第二次请求走缓存

    def test_xray_compute_exception_still_200(self, client, tmp_path, monkeypatch):
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        monkeypatch.setattr(
            "web.routes.portfolio._compute_xray",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        resp = client.get("/api/portfolio/xray")
        assert resp.status_code == 200
        assert resp.json()["status"] == "insufficient_data"

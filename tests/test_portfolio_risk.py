"""组合风险指标单元测试(阶段六批次 A,T-A2)。

验证:
- 计算口径与手算对拍(MDD/年化波动/夏普/Sortino/卡玛,rf=2.0%、244 交易日)
- 样本上限 250 点截断、样本不足 fail-closed(None 而非编数据)
- 组合静态加权合成的日期交集对齐
- GET /api/portfolio/risk 路由(含 401 鉴权用例与降级路径)
"""
from __future__ import annotations

import math
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.analysis import portfolio_risk as prisk


# ============ 纯计算:手算对拍 ============


class TestComputeRiskMetrics:
    def test_manual_case_mdd_and_total_return(self):
        """手算对拍: nav=[1.0, 1.1, 1.21, 0.968] → MDD=0.20,总收益=-3.2%。"""
        m = prisk.compute_risk_metrics([1.0, 1.1, 1.21, 0.968])
        assert m["nav_points"] == 4
        assert m["total_return"] == pytest.approx(-0.032, abs=1e-6)
        # MDD: 1 - 0.968/1.21 = 0.2
        assert m["max_drawdown"] == pytest.approx(0.20, abs=1e-6)
        # 卡玛 = 年化 / |MDD|
        assert m["calmar"] == pytest.approx(m["annual_return"] / 0.20, abs=1e-3)

    def test_manual_case_vol_and_sharpe(self):
        """波动/夏普对拍: 等比序列日收益率 {+10%, +10%, -20%}。"""
        m = prisk.compute_risk_metrics([1.0, 1.1, 1.21, 0.968])
        rets = [0.10, 0.10, -0.20]
        mean = sum(rets) / 3
        var = sum((r - mean) ** 2 for r in rets) / 2  # ddof=1
        vol = math.sqrt(var) * math.sqrt(244)
        assert m["annual_vol"] == pytest.approx(vol, abs=1e-4)
        # 年化收益 = (0.968)^(244/3) - 1
        ann = 0.968 ** (244 / 3) - 1
        assert m["annual_return"] == pytest.approx(ann, abs=1e-4)
        assert m["sharpe"] == pytest.approx((ann - 0.02) / vol, abs=1e-3)

    def test_sortino_downside_only(self):
        """Sortino 仅用负收益日: {+10%, +10%, -20%} → 下行 std 来自 -20%。"""
        m = prisk.compute_risk_metrics([1.0, 1.1, 1.21, 0.968])
        downside = [-0.20]
        # 负收益日仅 1 个(<2)→ 下行波动按 0 处理 → sortino None(fail-closed)
        assert m["sortino"] is None

    def test_sortino_with_two_downside_days(self):
        """两个负收益日 → sortino = (ann-rf)/下行波动。"""
        # 日收益率: -1%, +2%, -3% → nav = [1.0, 0.99, 1.0098, 0.979506]
        nav = [1.0, 0.99, 1.0098, 0.979506]
        m = prisk.compute_risk_metrics(nav)
        ann = (0.979506 / 1.0) ** (244 / 3) - 1
        assert ann < 0  # 负年化,保证分母有效
        dstd = [-0.01, -0.03]
        mean_d = sum(dstd) / 2
        var_d = sum((r - mean_d) ** 2 for r in dstd) / 1  # ddof=1
        dvol = math.sqrt(var_d) * math.sqrt(244)
        assert dvol > 0
        assert m["sortino"] == pytest.approx((ann - 0.02) / dvol, abs=1e-3)

    def test_constant_series_zero_vol(self):
        """恒定净值: 波动 0 → 夏普 None(fail-closed),MDD=0 → 卡玛 None。"""
        m = prisk.compute_risk_metrics([2.0, 2.0, 2.0, 2.0])
        assert m["annual_vol"] == 0.0
        assert m["sharpe"] is None
        assert m["max_drawdown"] == 0.0
        assert m["calmar"] is None
        assert m["total_return"] == 0.0

    def test_insufficient_samples_fail_closed(self):
        """样本 <2 点 → 各比率 None,nav_points 如实。"""
        for series in ([], [1.0]):
            m = prisk.compute_risk_metrics(series)
            assert m["nav_points"] == len(series)
            assert m["total_return"] is None
            assert m["sharpe"] is None

    def test_invalid_values_filtered(self):
        """NaN/非正值被剔除,不产生 NaN 结果。"""
        m = prisk.compute_risk_metrics([1.0, float("nan"), 0.0, -1.0, 1.05, 1.10])
        assert m["nav_points"] == 3
        assert m["total_return"] == pytest.approx(0.10, abs=1e-6)

    def test_series_capped_at_250_points(self):
        """>250 点 → 截取最近 250 点(内存硬预算)。"""
        nav = [1.0 + 0.001 * i for i in range(400)]
        m = prisk.compute_risk_metrics(nav)
        assert m["nav_points"] == 250

    def test_window_map(self):
        assert prisk.resolve_period("1m") == "1m"
        assert prisk.resolve_period("MAX") == "all"
        with pytest.raises(ValueError):
            prisk.resolve_period("10y")


# ============ 组合合成 ============


class TestCombinePortfolioNav:
    def test_weighted_sum_two_funds(self):
        """份额加权: A(100份) nav=2.0 + B(50份) nav=1.0 → 组合 250。"""
        series_map = {
            "A": [("2026-10-25", 2.0), ("2026-10-26", 2.1)],
            "B": [("2026-10-25", 1.0), ("2026-10-26", 0.9)],
        }
        out = prisk.combine_portfolio_nav(series_map, {"A": 100.0, "B": 50.0})
        assert out == [("2026-10-25", 250.0), ("2026-10-26", 255.0)]

    def test_date_intersection_alignment(self):
        """日期不对齐 → 取交集,不静默补 0。"""
        series_map = {
            "A": [("2026-10-24", 1.0), ("2026-10-25", 1.1), ("2026-10-26", 1.2)],
            "B": [("2026-10-25", 2.0), ("2026-10-26", 2.2)],
        }
        out = prisk.combine_portfolio_nav(series_map, {"A": 10.0, "B": 10.0})
        assert [d for d, _ in out] == ["2026-10-25", "2026-10-26"]
        assert out[0][1] == pytest.approx(11.0 + 20.0, abs=1e-6)

    def test_empty_and_zero_shares(self):
        assert prisk.combine_portfolio_nav({}, {}) == []
        out = prisk.combine_portfolio_nav(
            {"A": [("2026-10-25", 2.0)]}, {"A": 0.0}
        )
        assert out == []


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
        "web.routes.portfolio.fns._nav_history_cache",
        DataCache(cache_dir=str(tmp_path / "navhist"), default_ttl=3600),
    )
    yield


class TestRiskRoute:
    def test_risk_requires_admin_token(self, client, monkeypatch):
        """铁律:401 鉴权用例。"""
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 401

    def test_risk_empty_positions(self, client):
        resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["portfolio"]["nav_points"] == 0
        assert data["rf"] == 0.02
        assert data["trading_days"] == 244
        assert data["method"] == "static-shares"

    def test_risk_with_mock_history(self, client, tmp_path):
        """两基金合成: portfolio 指标可算,funds 各自带指标。"""
        (tmp_path / "positions.json").write_text(
            '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0},'
            '{"fund_code":"008984","fund_name":"科技","shares":50.0,"cost_nav":1.5}]}',
            encoding="utf-8",
        )
        nav_a = [1.0 + 0.01 * i for i in range(30)]
        nav_b = [1.0 - 0.005 * i for i in range(30)]
        series = {
            "110022": [{"date": f"d{i}", "nav": v, "acc_nav": v} for i, v in enumerate(nav_a)],
            "008984": [{"date": f"d{i}", "nav": v, "acc_nav": v} for i, v in enumerate(nav_b)],
        }

        def _fake_history(code, period):
            return series.get(code, [])

        with patch("src.core.fund_nav_source.get_fund_nav_history", side_effect=_fake_history):
            resp = client.get("/api/portfolio/risk", params={"window": "3m"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is False
        assert data["portfolio"]["nav_points"] == 30
        assert len(data["funds"]) == 2
        for f in data["funds"]:
            assert f["nav_points"] == 30
            assert f["degraded"] is False
            assert f["max_drawdown"] is not None

    def test_risk_single_point_degrades(self, client, tmp_path):
        """仅 1 个净值点 → 该基金显式 degraded,nav_points 如实为 1,指标 None。"""
        (tmp_path / "positions.json").write_text(
            '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0}]}',
            encoding="utf-8",
        )
        one = [{"date": "2026-10-27", "nav": 2.0, "acc_nav": 2.0}]
        with patch("src.core.fund_nav_source.get_fund_nav_history", return_value=one):
            resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        assert data["funds"][0]["degraded"] is True
        assert data["funds"][0]["nav_points"] == 1
        assert data["funds"][0]["sharpe"] is None
        assert data["portfolio"]["nav_points"] == 0

    def test_risk_history_missing_degrades(self, client, tmp_path):
        """历史缺失 → degraded=True,该基金指标 None,仍 200。"""
        (tmp_path / "positions.json").write_text(
            '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0}]}',
            encoding="utf-8",
        )
        with patch("src.core.fund_nav_source.get_fund_nav_history", return_value=[]):
            resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        assert data["funds"][0]["sharpe"] is None
        assert data["portfolio"]["nav_points"] == 0

    def test_risk_invalid_window(self, client):
        resp = client.get("/api/portfolio/risk", params={"window": "10y"})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["portfolio"] is None

    def test_risk_compute_exception_still_200(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.portfolio.fund_route._load_positions",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 200
        assert resp.json()["degraded"] is True

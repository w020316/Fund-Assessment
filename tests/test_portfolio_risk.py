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

    def test_two_point_series_produces_no_nan(self):
        """2 点序列只有 1 个日收益,ddof=1 样本标准差会得 NaN → 波动按 0,绝不输出 NaN。"""
        m = prisk.compute_risk_metrics([1.0, 1.1])
        assert m["annual_vol"] == 0.0
        assert m["sharpe"] is None
        assert not any(isinstance(v, float) and math.isnan(v) for v in m.values())

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

    def test_risk_fetches_history_concurrently(self, client, tmp_path):
        """多基金净值应并发拉取:用 Barrier 证明两个请求同时在途。"""
        import threading

        (tmp_path / "positions.json").write_text(
            '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0},'
            '{"fund_code":"008984","fund_name":"科技","shares":50.0,"cost_nav":1.5}]}',
            encoding="utf-8",
        )
        barrier = threading.Barrier(2, timeout=3)

        def _fake_history(code, period):
            barrier.wait()  # 串行执行时第二个请求永远不到,Barrier 超时抛错
            return [
                {"date": "2026-10-26", "nav": 1.0, "acc_nav": 1.0},
                {"date": "2026-10-27", "nav": 1.05, "acc_nav": 1.05},
                {"date": "2026-10-28", "nav": 1.1, "acc_nav": 1.1},
            ]

        with patch("src.core.fund_nav_source.get_fund_nav_history", side_effect=_fake_history):
            resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is False
        assert len(data["funds"]) == 2

    def test_risk_invalid_window_422(self, client):
        """非法窗口 → 422(REST 契约),不再 200 + error 字段。"""
        resp = client.get("/api/portfolio/risk", params={"window": "10y"})
        assert resp.status_code == 422
        assert "非法窗口" in resp.json()["detail"]

    def test_risk_compute_exception_still_200(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.portfolio.fund_route._load_positions",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        resp = client.get("/api/portfolio/risk")
        assert resp.status_code == 200
        assert resp.json()["degraded"] is True


# ============ 分散度诊断:纯计算(2026-11-12) ============


def _seq(dates_returns: list[tuple[str, float]]) -> list[tuple[str, float]]:
    return list(dates_returns)


def _navs_from_returns(rets: list[float], start: float = 1.0) -> list[float]:
    """由显式日收益序列构造净值(保证日收益有方差,避免常数收益的退化相关)。"""
    navs = [start]
    for r in rets:
        navs.append(navs[-1] * (1 + r))
    return navs


_RET_PATTERN = [0.01, -0.004, 0.008, 0.015, -0.002, 0.006, -0.009, 0.012]


def _pattern_returns(n: int) -> list[float]:
    return [_RET_PATTERN[i % len(_RET_PATTERN)] for i in range(n)]


class TestComputeCorrelation:
    def test_perfect_positive_and_negative(self):
        # b 与 a 日收益完全相同 → 1;c 完全取反 → -1(手算对拍)
        dates = [f"d{i:02d}" for i in range(25)]
        rets = _pattern_returns(24)
        a = _seq([(dates[i], v) for i, v in enumerate(_navs_from_returns(rets))])
        b = _seq([(dates[i], v) for i, v in enumerate(_navs_from_returns(rets))])
        c = _seq([(dates[i], v) for i, v in enumerate(_navs_from_returns([-r for r in rets]))])
        out = prisk.compute_correlation({"A": a, "B": b, "C": c})
        codes = out["codes"]
        ia, ib, ic = codes.index("A"), codes.index("B"), codes.index("C")
        assert out["matrix"][ia][ib] == pytest.approx(1.0, abs=1e-6)
        assert out["matrix"][ia][ic] == pytest.approx(-1.0, abs=1e-6)
        assert out["avg_correlation"] is not None
        assert out["valid_pairs"] == 3
        assert out["overlap_points"] == 24
        # 对角线恒 1,矩阵对称
        assert out["matrix"][ia][ia] == 1.0
        assert out["matrix"][ic][ib] == out["matrix"][ib][ic]

    def test_insufficient_overlap_returns_none(self):
        """交集日收益 < 21 点 → 相关对 None,不编相关系数。"""
        dates = [f"d{i}" for i in range(15)]  # 14 个日收益 < 21
        a = _seq([(dates[i], 1.0 + 0.01 * i) for i in range(15)])
        b = _seq([(dates[i], 1.0 - 0.005 * i) for i in range(15)])
        out = prisk.compute_correlation({"A": a, "B": b})
        assert out["valid_pairs"] == 0
        assert out["matrix"][0][1] is None
        assert out["avg_correlation"] is None

    def test_disjoint_dates_returns_none(self):
        a = _seq([(f"a{i}", 1.0 + 0.01 * i) for i in range(30)])
        b = _seq([(f"b{i}", 1.0 - 0.01 * i) for i in range(30)])
        out = prisk.compute_correlation({"A": a, "B": b})
        assert out["valid_pairs"] == 0

    def test_zero_variance_fund_excluded(self):
        """零方差(如货基)基金不参与相关对,但不影响其它对。"""
        dates = [f"d{i:02d}" for i in range(25)]
        a = _seq([(dates[i], 1.0 * (1.01 ** i)) for i in range(25)])
        flat = _seq([(dates[i], 1.0) for i in range(25)])
        c = _seq([(dates[i], 1.0 * (0.99 ** i)) for i in range(25)])
        out = prisk.compute_correlation({"A": a, "FLAT": flat, "C": c})
        iflat = out["codes"].index("FLAT")
        ia, ic = out["codes"].index("A"), out["codes"].index("C")
        assert out["matrix"][iflat][ia] is None
        assert out["matrix"][ia][ic] is not None
        assert out["valid_pairs"] == 1

    def test_single_fund_and_empty(self):
        one = _seq([(f"d{i}", 1.0 + 0.01 * i) for i in range(30)])
        out = prisk.compute_correlation({"A": one})
        assert out["codes"] == ["A"]
        assert out["avg_correlation"] is None
        assert prisk.compute_correlation({})["codes"] == []


class TestComputeConcentration:
    def test_two_funds_weights_hhi(self):
        # A 市值 100×2=200,B 50×2=100 → 权重 2/3、1/3
        out = prisk.compute_concentration({"A": 100.0, "B": 50.0}, {"A": 2.0, "B": 2.0})
        assert out["weights"]["A"] == pytest.approx(2 / 3, abs=1e-6)
        assert out["weights"]["B"] == pytest.approx(1 / 3, abs=1e-6)
        hhi = (2 / 3) ** 2 + (1 / 3) ** 2
        assert out["hhi"] == pytest.approx(hhi, abs=1e-4)
        assert out["effective_holdings"] == pytest.approx(1 / hhi, abs=1e-2)

    def test_equal_weights_effective_equals_n(self):
        out = prisk.compute_concentration({"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0},
                                          {c: 2.0 for c in "ABCD"})
        assert out["effective_holdings"] == pytest.approx(4.0, abs=1e-6)

    def test_missing_nav_fail_closed(self):
        out = prisk.compute_concentration({"A": 100.0}, {"A": 0.0})
        assert out["hhi"] is None and out["effective_holdings"] is None
        out2 = prisk.compute_concentration({}, {})
        assert out2["weights"] == {} and out2["hhi"] is None


# ============ 分散度诊断:路由(2026-11-12) ============


class TestDiversificationRoute:
    def test_requires_admin_token(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/portfolio/diversification").status_code == 401

    def test_invalid_window_422(self, client):
        resp = client.get("/api/portfolio/diversification", params={"window": "10y"})
        assert resp.status_code == 422
        assert "非法窗口" in resp.json()["detail"]

    def test_empty_positions(self, client):
        resp = client.get("/api/portfolio/diversification")
        assert resp.status_code == 200
        data = resp.json()
        assert data["funds"] == []
        assert data["note"] == "暂无持仓"
        assert data["effective_holdings"] is None

    def test_two_correlated_funds(self, client, tmp_path):
        """两基金日收益高度重叠 → 相关矩阵可算,note 含有效持仓。"""
        (tmp_path / "positions.json").write_text(
            '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0},'
            '{"fund_code":"008984","fund_name":"科技","shares":50.0,"cost_nav":1.5}]}',
            encoding="utf-8",
        )
        rets = _pattern_returns(27)
        navs_a = _navs_from_returns(rets)
        navs_b = _navs_from_returns([r * 0.8 for r in rets], start=2.0)  # 同向略异幅
        dates = [f"2026-10-{i:02d}" for i in range(1, 29)]
        series = {
            "110022": [{"date": d, "nav": v, "acc_nav": v} for d, v in zip(dates, navs_a)],
            "008984": [{"date": d, "nav": v, "acc_nav": v} for d, v in zip(dates, navs_b)],
        }

        def _fake_history(code, period):
            return series.get(code, [])

        with patch("src.core.fund_nav_source.get_fund_nav_history", side_effect=_fake_history):
            resp = client.get("/api/portfolio/diversification")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is False
        assert data["effective_holdings"] is not None
        corr = data["correlation"]
        assert corr["valid_pairs"] == 1
        assert corr["matrix"][0][1] is not None and corr["matrix"][0][1] > 0.9
        assert "有效持仓" in data["note"]

    def test_history_missing_degrades(self, client, tmp_path):
        (tmp_path / "positions.json").write_text(
            '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0}]}',
            encoding="utf-8",
        )
        with patch("src.core.fund_nav_source.get_fund_nav_history", return_value=[]):
            resp = client.get("/api/portfolio/diversification")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        assert data["funds"][0]["degraded"] is True
        assert data["effective_holdings"] is None

    def test_compute_exception_still_200(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.portfolio.fund_route._load_positions",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        resp = client.get("/api/portfolio/diversification")
        assert resp.status_code == 200
        assert resp.json()["degraded"] is True

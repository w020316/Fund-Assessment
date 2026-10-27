"""收益归因单元测试(阶段六批次 B,功能 #5)。

覆盖:
- MTD/YTD 期初点选择(窗口前最后一点)、MAX 自各基金历史起点
- 贡献额/贡献率/权重手算对拍;Σ贡献额 = 组合区间盈亏
- 历史不足/窗口不足 → None + missing_funds + partial,绝不编数据
- 路由:非法窗口、空持仓、鉴权 401、正常返回

铁律:持仓文件路径与净值源 100% mock,绝不触网/绝不碰真实持仓。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.analysis import portfolio_attribution as pa


# ============ 纯函数 ============


class TestComputeAttribution:
    def test_mtd_uses_last_point_before_month_start(self):
        """MTD 期初取 10-01 前最后一点(9-30),贡献额与区间收益手算对拍。"""
        series = {
            "A": [("2026-09-29", 1.0), ("2026-09-30", 1.1), ("2026-10-08", 1.2)],
            "B": [("2026-09-30", 2.0), ("2026-10-08", 1.9)],
        }
        out = pa.compute_attribution(
            series, {"A": 100.0, "B": 50.0}, {"A": "甲", "B": "乙"},
            "mtd", today=dt.date(2026, 10, 27),
        )
        funds = {f["fund_code"]: f for f in out["funds"]}
        # A: 100*(1.2-1.1)=+10; B: 50*(1.9-2.0)=-5
        assert funds["A"]["contribution"] == pytest.approx(10.0)
        assert funds["B"]["contribution"] == pytest.approx(-5.0)
        assert funds["A"]["start_date"] == "2026-09-30"
        assert funds["A"]["partial"] is False
        # 组合: 期初市值 110+100=210,盈亏 +5 → +2.381%
        assert out["portfolio"]["start_value"] == pytest.approx(210.0)
        assert out["portfolio"]["end_value"] == pytest.approx(215.0)
        assert out["portfolio"]["pnl"] == pytest.approx(5.0)
        assert out["portfolio"]["total_return"] == pytest.approx(5.0 / 210.0, abs=1e-6)
        # 贡献率: A=10/5=200%, B=-5/5=-100%;权重 A=110/210
        assert funds["A"]["contribution_rate"] == pytest.approx(2.0)
        assert funds["B"]["contribution_rate"] == pytest.approx(-1.0)
        assert funds["A"]["weight"] == pytest.approx(110.0 / 210.0, abs=1e-6)
        assert out["method"] == "static-shares"
        assert out["partial"] is False

    def test_contributions_sum_equals_portfolio_pnl(self):
        """铁律:Σ贡献额必须等于组合区间盈亏(合计=组合区间收益)。"""
        series = {
            "A": [("2026-01-02", 1.0), ("2026-10-08", 1.35)],
            "B": [("2026-01-02", 3.0), ("2026-10-08", 2.7)],
            "C": [("2026-01-02", 0.5), ("2026-10-08", 0.55)],
        }
        out = pa.compute_attribution(
            series, {"A": 100.0, "B": 200.0, "C": 300.0}, {}, "ytd",
            today=dt.date(2026, 10, 27),
        )
        total = sum(f["contribution"] for f in out["funds"])
        assert total == pytest.approx(out["portfolio"]["pnl"], abs=0.01)
        assert out["portfolio"]["end_value"] - out["portfolio"]["start_value"] == pytest.approx(
            out["portfolio"]["pnl"], abs=0.01
        )

    def test_ytd_uses_previous_year_end(self):
        """YTD 期初取 1-1 前最后一点(上一年末)。"""
        series = {"A": [("2025-12-31", 2.0), ("2026-01-02", 2.1), ("2026-10-08", 2.4)]}
        out = pa.compute_attribution(
            series, {"A": 50.0}, {}, "ytd", today=dt.date(2026, 10, 27),
        )
        f = out["funds"][0]
        assert f["start_date"] == "2025-12-31"
        assert f["contribution"] == pytest.approx(20.0)  # 50*(2.4-2.0)

    def test_max_uses_each_fund_first_point(self):
        """MAX:各基金自可得历史起点(起点可不同,按各自区间归因)。"""
        series = {
            "A": [("2024-01-02", 1.0), ("2026-10-08", 2.0)],
            "B": [("2025-06-02", 3.0), ("2026-10-08", 3.3)],
        }
        out = pa.compute_attribution(
            series, {"A": 10.0, "B": 10.0}, {}, "max", today=dt.date(2026, 10, 27),
        )
        funds = {f["fund_code"]: f for f in out["funds"]}
        assert funds["A"]["start_date"] == "2024-01-02"
        assert funds["A"]["contribution"] == pytest.approx(10.0)
        assert funds["B"]["start_date"] == "2025-06-02"
        assert funds["B"]["contribution"] == pytest.approx(3.0)

    def test_partial_when_history_starts_inside_window(self):
        """历史起点落在窗口内 → 按可得区间计算并标 partial(不假装是整月)。"""
        series = {"A": [("2026-10-08", 1.2), ("2026-10-09", 1.25)]}
        out = pa.compute_attribution(
            series, {"A": 100.0}, {}, "mtd", today=dt.date(2026, 10, 27),
        )
        f = out["funds"][0]
        assert f["start_date"] == "2026-10-08"
        assert f["partial"] is True
        assert out["partial"] is True
        assert "历史不足" in out["note"]

    def test_missing_history_is_not_fabricated(self):
        """历史 <2 点 → contribution=None + missing_funds + degraded。"""
        out = pa.compute_attribution(
            {"A": [("2026-10-08", 1.2)]}, {"A": 100.0}, {}, "mtd",
            today=dt.date(2026, 10, 27),
        )
        f = out["funds"][0]
        assert f["contribution"] is None
        assert f["reason"] == "history_insufficient"
        assert out["missing_funds"] == ["A"]
        assert out["degraded"] is True
        assert out["portfolio"]["pnl"] == 0.0

    def test_zero_total_pnl_has_null_rates(self):
        """组合盈亏为 0 → 贡献率/收益率为 None(避免除零编造)。"""
        series = {
            "A": [("2026-09-30", 1.0), ("2026-10-08", 1.2)],
            "B": [("2026-09-30", 1.0), ("2026-10-08", 0.8)],
        }
        out = pa.compute_attribution(
            series, {"A": 100.0, "B": 100.0}, {}, "mtd", today=dt.date(2026, 10, 27),
        )
        assert out["portfolio"]["pnl"] == pytest.approx(0.0)
        assert out["portfolio"]["total_return"] == pytest.approx(0.0)
        assert all(f["contribution_rate"] is None for f in out["funds"])

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            pa.compute_attribution({}, {}, {}, "1w", today=dt.date(2026, 10, 27))


# ============ 路由 ============


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
    yield


def _write_positions(tmp_path, positions: list[dict]):
    (tmp_path / "positions.json").write_text(
        json.dumps({"positions": positions}, ensure_ascii=False), encoding="utf-8"
    )


_POSITIONS = [
    {"fund_code": "110022", "fund_name": "易方达消费", "shares": 100.0, "cost_nav": 1.0},
    {"fund_code": "008984", "fund_name": "财通科技创新", "shares": 50.0, "cost_nav": 1.0},
]


class TestAttributionRoute:
    def test_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/portfolio/attribution").status_code == 401

    def test_invalid_window_returns_error_not_500(self, client):
        resp = client.get("/api/portfolio/attribution", params={"window": "1w"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]
        assert data["funds"] == []
        assert data["degraded"] is True

    def test_empty_positions(self, client):
        resp = client.get("/api/portfolio/attribution", params={"window": "mtd"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["funds"] == []
        assert data["portfolio"]["pnl"] == 0.0

    def test_route_mtd_matches_hand_calc(self, client, tmp_path):
        _write_positions(tmp_path, _POSITIONS)
        series = {
            "110022": [{"date": "2026-09-30", "acc_nav": 1.1}, {"date": "2026-10-09", "acc_nav": 1.2}],
            "008984": [{"date": "2026-09-30", "acc_nav": 2.0}, {"date": "2026-10-09", "acc_nav": 1.9}],
        }
        with patch("web.routes.portfolio._fetch_fund_nav_series", side_effect=lambda code, period: series[code]), \
             patch("web.routes.portfolio._today_date", return_value=dt.date(2026, 10, 27)):
            resp = client.get("/api/portfolio/attribution", params={"window": "mtd"})
        assert resp.status_code == 200
        data = resp.json()
        funds = {f["fund_code"]: f for f in data["funds"]}
        assert funds["110022"]["contribution"] == pytest.approx(10.0)
        assert funds["008984"]["contribution"] == pytest.approx(-5.0)
        assert data["portfolio"]["pnl"] == pytest.approx(5.0)
        assert data["window_start"] == "2026-09-30"
        assert data["generated_at"]

    def test_route_missing_series_degrades(self, client, tmp_path):
        _write_positions(tmp_path, _POSITIONS)
        with patch("web.routes.portfolio._fetch_fund_nav_series", return_value=[]), \
             patch("web.routes.portfolio._today_date", return_value=dt.date(2026, 10, 27)):
            resp = client.get("/api/portfolio/attribution", params={"window": "ytd"})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["missing_funds"]) == {"110022", "008984"}
        assert data["degraded"] is True
        assert data["portfolio"]["pnl"] == 0.0

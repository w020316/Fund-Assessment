"""组合路由单元测试(阶段六批次 A)。

覆盖:
- GET /api/portfolio/watchboard(主源/备源/降级/置信度)
- require_admin 401 用例(铁律:每个新端点必须有鉴权测试)

铁律:
- 持仓文件路径一律 monkeypatch 到 tmp_path,绝不触碰 web/user_fund_positions.json
- 数据源 100% mock,无真实网络请求
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    """默认开发模式放行(与 test_monitor_routes 同范式),避免鉴权干扰。"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """隔离:持仓文件 → tmp;进程内缓存与 last-known 清空。"""
    monkeypatch.setattr("web.routes.fund._FUND_POSITIONS_FILE", tmp_path / "positions.json")
    monkeypatch.setattr("web.routes.portfolio._last_known", {})
    from src.core.cache import DataCache
    monkeypatch.setattr(
        "web.routes.portfolio._portfolio_cache",
        DataCache(cache_dir=str(tmp_path / "cache"), default_ttl=60),
    )
    # 数据源缓存目录也隔离(避免读写真实 data/cache)
    monkeypatch.setattr(
        "web.routes.portfolio.fns._holdings_cache",
        DataCache(cache_dir=str(tmp_path / "holdings"), default_ttl=86400),
    )
    yield


_POSITIONS = {
    "positions": [
        {"fund_code": "110022", "fund_name": "易方达消费行业", "shares": 100.0, "cost_nav": 2.0},
        {"fund_code": "002891", "fund_name": "华夏移动互联混合(QDII)", "shares": 50.0, "cost_nav": 3.0},
    ]
}

_QUOTES = [
    {
        "code": "110022", "name": "易方达消费行业",
        "nav": 2.500, "estimated_nav": 2.550, "change": 0.05,
        "change_pct": 2.0, "update_time": "2026-10-26 14:30",
    },
    {
        "code": "002891", "name": "华夏移动互联混合(QDII)",
        "nav": 2.000, "estimated_nav": 2.060, "change": 0.06,
        "change_pct": 3.0, "update_time": "2026-10-26 14:30",
    },
]


def _write_positions(tmp_path, positions: dict):
    (tmp_path / "positions.json").write_text(
        __import__("json").dumps(positions, ensure_ascii=False), encoding="utf-8"
    )


# ============ 鉴权(铁律:每个新端点 401 用例) ============


class TestWatchboardAuth:
    def test_watchboard_requires_admin_token(self, client, monkeypatch):
        """配置了 ADMIN_TOKEN 后,无 Authorization 头 → 401。"""
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 401

    def test_watchboard_accepts_valid_token(self, client, monkeypatch):
        """携带正确 Bearer 令牌 → 200(无持仓,空数据)。"""
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.get(
            "/api/portfolio/watchboard",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200


# ============ watchboard 业务逻辑 ============


class TestWatchboard:
    def test_empty_positions_zero_summary(self, client, tmp_path):
        """无持仓 → 空列表 + 零汇总,非 degraded。"""
        resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["positions"] == []
        assert data["summary"]["count"] == 0
        assert data["degraded"] is False

    def test_main_source_tencent_high_confidence(self, client, tmp_path):
        """主源直连:QDII 标 low,普通基金标 high;估算盈亏手算对拍。"""
        _write_positions(tmp_path, _POSITIONS)
        import datetime as dt
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=_QUOTES), \
             patch("web.routes.portfolio.fund_route._today", return_value=dt.date(2026, 10, 26)):
            resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 200
        data = resp.json()
        items = {p["fund_code"]: p for p in data["positions"]}
        # 普通基金: high 置信度,est_pct 直传
        assert items["110022"]["confidence"] == "high"
        assert items["110022"]["source"] == "tencent"
        assert items["110022"]["est_pct"] == 2.0
        assert items["110022"]["est_nav"] == 2.55
        # QDII: low 置信度 + 原因说明(主理人拍板)
        assert items["002891"]["confidence"] == "low"
        assert "QDII" in items["002891"]["confidence_reason"]
        # 盈亏手算: 110022 shares=100, nav=2.5, est_nav=2.55, pct=2 → prev=2.5/1.02
        # pnl = 100 * (2.55 - 2.5/1.02) ≈ 100 * (2.55 - 2.45098) ≈ 9.90
        assert items["110022"]["est_day_pnl"] == pytest.approx(9.90, abs=0.05)

    def test_nav_lagged_low_confidence(self, client, tmp_path):
        """净值滞后 ≥1 天 → low 置信度(阶段5 规则沿用)。"""
        _write_positions(tmp_path, {"positions": [_POSITIONS["positions"][0]]})
        lagged = [dict(_QUOTES[0], update_time="2026-10-20 15:00")]
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=lagged), \
             patch("web.routes.portfolio.fund_route._today") as fake_today:
            import datetime as dt
            fake_today.return_value = dt.date(2026, 10, 26)
            resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 200
        item = resp.json()["positions"][0]
        assert item["confidence"] == "low"
        assert "滞后" in item["confidence_reason"]

    def test_fallback_fitted_medium_confidence(self, client, tmp_path):
        """主源缺数据 → 备源披露持仓拟合,medium 置信度。"""
        _write_positions(tmp_path, {"positions": [_POSITIONS["positions"][0]]})
        fitted_quote = [{
            "code": "110022", "name": "易方达消费行业",
            "nav": 2.500, "estimated_nav": 0, "change": 0, "change_pct": 0,
            "update_time": "2026-10-26 14:30",
        }]
        from src.core.fund_nav_source import FundHoldings
        holdings = FundHoldings(fund_code="110022", as_of="2026Q3", holdings=[
            {"stock_code": "600519", "stock_name": "贵州茅台", "weight": 50.0},
        ])
        stock_quotes = [{"code": "600519", "name": "贵州茅台", "change_pct": 2.0}]

        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=fitted_quote), \
             patch("src.core.fund_nav_source.get_fund_holdings", return_value=holdings), \
             patch("src.core.data_source_v2.get_realtime_quote_tencent", return_value=stock_quotes), \
             patch("src.core.data_source_v2.get_index_realtime", return_value=[]):
            resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 200
        item = resp.json()["positions"][0]
        assert item["source"] == "fitted"
        assert item["confidence"] == "medium"
        assert item["est_pct"] == pytest.approx(1.0, abs=0.01)  # 0.5*2 + 0.5*0

    def test_source_failure_degrades_to_last_known(self, client, tmp_path):
        """主源异常 → 降级返回 last-known + degraded 标识,状态码仍 200(绝不 500)。"""
        _write_positions(tmp_path, _POSITIONS)
        # 先成功一次,填 last-known(进程内字典经 isolated_state 已隔离)
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=_QUOTES):
            client.get("/api/portfolio/watchboard")
        # 再模拟接口异常
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", side_effect=RuntimeError("network down")):
            resp = client.get("/api/portfolio/watchboard", params={"force": "true"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        items = {p["fund_code"]: p for p in data["positions"]}
        assert items["110022"]["stale"] is True
        assert items["110022"]["est_pct"] == 2.0  # last-known 值

    def test_source_failure_without_last_known(self, client, tmp_path):
        """从未成功过 + 接口异常 → low 置信度空数据,仍 200 不 500。"""
        _write_positions(tmp_path, _POSITIONS)
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", side_effect=RuntimeError("down")):
            resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        assert all(p["confidence"] == "low" for p in data["positions"])
        assert all(p["est_pct"] is None for p in data["positions"])

    def test_cache_hit(self, client, tmp_path):
        """60s 缓存命中:第二次调用不再触达数据源。"""
        _write_positions(tmp_path, {"positions": [_POSITIONS["positions"][0]]})
        calls = {"n": 0}

        def _fake(codes):
            calls["n"] += 1
            return [_QUOTES[0]]

        with patch("src.core.data_source_v2.get_fund_realtime_tencent", side_effect=_fake):
            client.get("/api/portfolio/watchboard")
            client.get("/api/portfolio/watchboard")
        assert calls["n"] == 1

    def test_compute_exception_still_200(self, client, tmp_path, monkeypatch):
        """聚合逻辑内部异常 → 降级响应而非 500。"""
        _write_positions(tmp_path, _POSITIONS)
        with patch("web.routes.portfolio._compute_watchboard", side_effect=RuntimeError("boom")):
            resp = client.get("/api/portfolio/watchboard")
        assert resp.status_code == 200
        assert resp.json()["degraded"] is True

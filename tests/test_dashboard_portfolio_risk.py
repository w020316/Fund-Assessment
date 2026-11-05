"""GET /api/dashboard/portfolio_risk 覆盖率补测(阶段二)。

覆盖组合风险分析的 7 条分支:
1. 60s 缓存命中
2. 无持仓 → insufficient
3. 持仓数量为 0(无有效市值)→ insufficient
4. 行情失败 + K 线全异常 → degraded
5. K 线不足 2 根 → degraded
6. 交易日 < 5 → insufficient
7. 完整计算 → normal(权重/波动/夏普/Sortino/回撤/Calmar)

全部 mock ds2 行情与 K 线,不发起网络请求,不读写真实持仓文件。
"""
from __future__ import annotations

import math
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from web.routes import dashboard


QUOTES = [
    {"code": "600519", "name": "贵州茅台", "price": 100.0, "change_pct": 1.0, "prev_close": 99.0},
]


def _klines(closes: list[float]) -> list[dict]:
    return [{"date": f"2026-10-{i + 1:02d}", "close": c} for i, c in enumerate(closes)]


@pytest.fixture
def client():
    from web.api import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "_USER_POSITIONS_FILE", tmp_path / "positions.json")
    monkeypatch.setattr(dashboard, "_portfolio_risk_cache", {"ts": 0.0, "data": None})
    monkeypatch.setattr(dashboard.ds2, "get_realtime_quote_tencent", lambda symbols: QUOTES)
    yield


def _write_positions(path, positions):
    import json

    path.write_text(json.dumps({"positions": positions}), encoding="utf-8")


POSITION = {
    "symbol": "600519",
    "name": "贵州茅台",
    "quantity": 10.0,
    "available_quantity": 10.0,
    "cost_price": 90.0,
}


class TestCalcMaxDrawdown:
    def test_empty_curve(self):
        assert dashboard._calc_max_drawdown([]) == 0.0

    def test_monotonic_up_no_drawdown(self):
        assert dashboard._calc_max_drawdown([1.0, 1.1, 1.2]) == 0.0

    def test_drawdown_percent(self):
        # 峰值 1.2 → 谷值 0.9,回撤 = 25%
        assert dashboard._calc_max_drawdown([1.0, 1.2, 0.9]) == pytest.approx(25.0)


class TestPortfolioRiskRoute:
    def test_empty_positions_insufficient(self, client):
        resp = client.get("/api/dashboard/portfolio_risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_quality"] == "insufficient"
        assert data["position_count"] == 0
        assert data["weights"] == []

    def test_zero_quantity_insufficient(self, client, tmp_path):
        _write_positions(
            tmp_path / "positions.json", [dict(POSITION, quantity=0.0, available_quantity=0.0)]
        )
        data = client.get("/api/dashboard/portfolio_risk").json()
        assert data["data_quality"] == "insufficient"
        # 持仓存在但无有效市值 → position_count 如实为持仓条数
        assert data["position_count"] == 1

    def test_kline_all_failed_degraded(self, client, tmp_path, monkeypatch):
        _write_positions(tmp_path / "positions.json", [POSITION])

        def _boom(*_a, **_kw):
            raise RuntimeError("kline down")

        monkeypatch.setattr(dashboard.ds2, "get_kline_mootdx", _boom)
        data = client.get("/api/dashboard/portfolio_risk").json()
        assert data["data_quality"] == "degraded"
        assert data["trading_days"] == 0

    def test_kline_too_short_degraded(self, client, tmp_path, monkeypatch):
        _write_positions(tmp_path / "positions.json", [POSITION])
        monkeypatch.setattr(dashboard.ds2, "get_kline_mootdx", lambda code, period, count: _klines([100.0]))
        data = client.get("/api/dashboard/portfolio_risk").json()
        assert data["data_quality"] == "degraded"

    def test_insufficient_trading_days(self, client, tmp_path, monkeypatch):
        _write_positions(tmp_path / "positions.json", [POSITION])
        # 3 根 K 线 → 2 个日收益 < 5
        monkeypatch.setattr(dashboard.ds2, "get_kline_mootdx", lambda code, period, count: _klines([100, 101, 102]))
        data = client.get("/api/dashboard/portfolio_risk").json()
        assert data["data_quality"] == "insufficient"
        assert data["trading_days"] == 2

    def test_full_calculation_normal(self, client, tmp_path, monkeypatch):
        _write_positions(tmp_path / "positions.json", [POSITION])
        closes = [100.0 + i * 0.5 for i in range(10)]
        monkeypatch.setattr(dashboard.ds2, "get_kline_mootdx", lambda code, period, count: _klines(closes))

        data = client.get("/api/dashboard/portfolio_risk").json()
        assert data["data_quality"] == "normal"
        assert data["trading_days"] == 9
        assert data["position_count"] == 1
        assert len(data["weights"]) == 1
        assert data["weights"][0]["weight"] == pytest.approx(100.0)
        assert data["annual_volatility"] > 0
        assert len(data["weighted_returns"]) == 9
        # 均匀上涨 → 无回撤,Calmar 按 0 处理
        assert data["max_drawdown"] == 0.0
        assert data["calmar_ratio"] == 0.0
        for value in data.values():
            assert not (isinstance(value, float) and math.isnan(value))

    def test_two_positions_weighted(self, client, tmp_path, monkeypatch):
        _write_positions(
            tmp_path / "positions.json",
            [
                POSITION,
                dict(POSITION, symbol="000001", name="平安银行", quantity=10.0, cost_price=10.0),
            ],
        )
        monkeypatch.setattr(
            dashboard.ds2,
            "get_realtime_quote_tencent",
            lambda symbols: [
                {"code": "600519", "name": "贵州茅台", "price": 100.0, "change_pct": 1.0, "prev_close": 99.0},
                {"code": "000001", "name": "平安银行", "price": 10.0, "change_pct": 1.0, "prev_close": 9.9},
            ],
        )
        monkeypatch.setattr(
            dashboard.ds2,
            "get_kline_mootdx",
            lambda code, period, count: _klines([100.0 + i for i in range(6)]),
        )
        data = client.get("/api/dashboard/portfolio_risk").json()
        assert data["data_quality"] == "normal"
        total_weight = sum(w["weight"] for w in data["weights"])
        assert total_weight == pytest.approx(100.0)
        # 两只股票市值比 1000 : 100 → 权重约 90.91% / 9.09%
        assert data["weights"][0]["weight"] == pytest.approx(90.91, abs=0.02)

    def test_cache_hit_returns_same_payload(self, client):
        cached = dashboard.PortfolioRiskResponse(
            position_count=1, trading_days=30, annual_volatility=12.0, sharpe_ratio=0.8,
            sortino_ratio=1.0, max_drawdown=5.0, calmar_ratio=0.6,
            weighted_returns=[0.1], weights=[{"symbol": "600519", "name": "贵州茅台", "weight": 100.0}],
            data_quality="normal",
        )
        dashboard._portfolio_risk_cache["data"] = cached
        dashboard._portfolio_risk_cache["ts"] = time.monotonic()

        with patch.object(dashboard.ds2, "get_kline_mootdx") as mocked:
            data = client.get("/api/dashboard/portfolio_risk").json()
        assert mocked.assert_not_called() is None
        assert data["trading_days"] == 30

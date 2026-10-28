"""再平衡建议单(阶段七,T-A3)测试。

覆盖:
- 存储层:默认值、白名单校验(代码/权重/合计/阈值)、roundtrip、损坏回退
- 计算层:偏离触发、阈值边界、未纳管持仓剔除、目标未持有买入、缺净值降级
- 路由:401、GET/PUT 契约、非法配置整包拒绝、preview 端到端(净值打桩)

铁律:配置与持仓一律 monkeypatch 到 tmp_path / 内存,绝不触碰真实文件。
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.analysis import rebalance as reb
from src.core import portfolio_store as pstore


POSITIONS = [
    {"fund_code": "110022", "fund_name": "易方达消费", "shares": 100.0, "cost_nav": 1.0},
    {"fund_code": "002891", "fund_name": "华夏移动互联", "shares": 100.0, "cost_nav": 1.0},
]


class TestRebalanceStore:
    def test_load_defaults_when_missing(self, tmp_path):
        cfg = pstore.load_rebalance_config(tmp_path / "rebalance.json")
        assert cfg == {"schema_version": 1, "drift_threshold_pct": 5.0, "targets": {}}

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "rebalance.json"
        cfg = {"drift_threshold_pct": 3.5, "targets": {"110022": 60.0, "002891": 40.0}}
        assert pstore.save_rebalance_config(cfg, path) is True
        assert pstore.load_rebalance_config(path) == {
            "schema_version": 1, "drift_threshold_pct": 3.5,
            "targets": {"110022": 60.0, "002891": 40.0},
        }

    def test_corrupt_file_falls_back_to_default(self, tmp_path):
        path = tmp_path / "rebalance.json"
        path.write_text("{ not-json", encoding="utf-8")
        assert pstore.load_rebalance_config(path)["targets"] == {}

    def test_normalize_rejects_bad_code_and_weight(self):
        errors, _ = pstore.normalize_rebalance_config({
            "drift_threshold_pct": 5,
            "targets": {"abc123": 10, "110022": 0, "002891": 101},
        })
        assert len(errors) == 3

    def test_normalize_rejects_sum_over_100(self):
        errors, _ = pstore.normalize_rebalance_config({
            "drift_threshold_pct": 5,
            "targets": {"110022": 60, "002891": 50},
        })
        assert any("超过 100%" in e for e in errors)

    @pytest.mark.parametrize("threshold", [0, -1, 101, float("inf"), "abc", True])
    def test_normalize_rejects_bad_threshold(self, threshold):
        errors, _ = pstore.normalize_rebalance_config(
            {"drift_threshold_pct": threshold, "targets": {"110022": 50}}
        )
        assert any("drift_threshold_pct" in e for e in errors)

    def test_normalize_allows_sum_below_100(self):
        errors, cleaned = pstore.normalize_rebalance_config({
            "drift_threshold_pct": 5,
            "targets": {"110022": 40, "002891": 30},
        })
        assert errors == []
        assert cleaned["targets"] == {"110022": 40.0, "002891": 30.0}

    def test_save_rejects_invalid_config(self, tmp_path):
        path = tmp_path / "rebalance.json"
        assert pstore.save_rebalance_config({"targets": {"x": 10}}, path) is False
        assert not path.exists()


class TestBuildPreview:
    def test_empty_positions(self):
        out = reb.build_preview([], {}, {"110022": 100}, 5.0)
        assert out["suggestions"] == []
        assert out["managed_value"] == 0.0

    def test_drift_generates_buy_and_sell(self):
        navs = {"110022": 1.0, "002891": 1.0}
        out = reb.build_preview(POSITIONS, navs, {"110022": 60, "002891": 40}, 5.0)
        assert out["managed_value"] == 200.0
        by_code = {s["code"]: s for s in out["suggestions"]}
        assert by_code["110022"]["action"] == "buy"
        assert by_code["110022"]["amount"] == 20.0
        assert by_code["110022"]["shares"] == 20.0
        assert by_code["002891"]["action"] == "sell"
        assert out["total_buy"] == 20.0
        assert out["total_sell"] == 20.0
        assert out["degraded"] is False

    def test_drift_equal_to_threshold_is_hold(self):
        # 55/45 vs 50/50 → 偏离恰好 5.0pp,不大于阈值 → 不出建议
        out = reb.build_preview(POSITIONS, {"110022": 1.0, "002891": 1.0},
                                {"110022": 50, "002891": 50}, 5.0)
        assert out["suggestions"] == []
        assert all(p["status"] == "hold" for p in out["positions"])
        assert any("未超阈值" in s["reason"] for s in out["skipped"])

    def test_unmanaged_position_excluded_from_denominator(self):
        positions = POSITIONS + [
            {"fund_code": "161725", "fund_name": "招商中证白酒", "shares": 100.0},
        ]
        out = reb.build_preview(positions, {"110022": 1.0, "002891": 1.0, "161725": 1.0},
                                {"110022": 50, "002891": 50}, 5.0)
        # 161725 不参与,分母仍为 200
        assert out["managed_value"] == 200.0
        assert any(s["code"] == "161725" and "未配置目标权重" in s["reason"]
                   for s in out["skipped"])

    def test_target_not_held_generates_buy_from_zero(self):
        navs = {"110022": 1.0, "002891": 1.0, "161725": 2.0}
        out = reb.build_preview(POSITIONS, navs,
                                {"110022": 40, "002891": 40, "161725": 20}, 5.0)
        buy = [s for s in out["suggestions"] if s["code"] == "161725"][0]
        assert buy["action"] == "buy"
        assert buy["amount"] == 40.0  # 20% × 200
        assert buy["shares"] == 20.0  # 40 / 2.0

    def test_missing_nav_marks_degraded_and_skips(self):
        out = reb.build_preview(POSITIONS, {"110022": 1.2},
                                {"110022": 50, "002891": 50}, 5.0)
        assert out["degraded"] is True
        assert out["managed_value"] == 120.0
        assert any(s["code"] == "002891" and "缺少最新净值" in s["reason"]
                   for s in out["skipped"])
        assert all(p["code"] != "002891" for p in out["positions"])

    def test_zero_or_negative_shares_ignored(self):
        positions = [
            {"fund_code": "110022", "shares": 0},
            {"fund_code": "002891", "shares": -5},
        ]
        out = reb.build_preview(positions, {"110022": 1.0, "002891": 1.0},
                                {"110022": 50, "002891": 50}, 5.0)
        assert out["managed_value"] == 0.0
        assert out["suggestions"] == []

    def test_suggestions_sorted_by_abs_drift(self):
        navs = {"110022": 1.0, "002891": 1.0}
        out = reb.build_preview(POSITIONS, navs, {"110022": 70, "002891": 30}, 5.0)
        drifts = [abs(s["drift_pct"]) for s in out["suggestions"]]
        assert drifts == sorted(drifts, reverse=True)


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(pstore, "REBALANCE_CONFIG_FILE", tmp_path / "rebalance.json")
    monkeypatch.setattr("web.routes.fund._load_positions", lambda: list(POSITIONS))
    monkeypatch.setattr(
        "src.core.data_source_v2.get_fund_realtime_tencent",
        lambda codes: [{"code": c, "nav": 1.0} for c in codes],
    )
    yield


class TestRebalanceRoutes:
    def test_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/portfolio/rebalance/config").status_code == 401
        assert client.put("/api/portfolio/rebalance/config",
                          json={"targets": {}}).status_code == 401
        assert client.get("/api/portfolio/rebalance/preview").status_code == 401

    def test_get_default_config(self, client):
        resp = client.get("/api/portfolio/rebalance/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["targets"] == {}
        assert data["drift_threshold_pct"] == 5.0

    def test_put_rejects_invalid_whole_payload(self, client, tmp_path):
        resp = client.put("/api/portfolio/rebalance/config", json={
            "drift_threshold_pct": 5,
            "targets": {"110022": 60, "002891": 50},
        })
        data = resp.json()
        assert data["success"] is False
        assert any("超过 100%" in e for e in data["errors"])
        assert not (tmp_path / "rebalance.json").exists()

    def test_put_and_get_roundtrip(self, client):
        put = client.put("/api/portfolio/rebalance/config", json={
            "drift_threshold_pct": 3,
            "targets": {"110022": 60, "002891": 40},
        })
        assert put.json()["success"] is True
        got = client.get("/api/portfolio/rebalance/config").json()
        assert got["targets"] == {"110022": 60.0, "002891": 40.0}
        assert got["drift_threshold_pct"] == 3.0

    def test_preview_end_to_end(self, client):
        client.put("/api/portfolio/rebalance/config", json={
            "drift_threshold_pct": 5,
            "targets": {"110022": 60, "002891": 40},
        })
        resp = client.get("/api/portfolio/rebalance/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["managed_value"] == 200.0
        assert data["targets"] == {"110022": 60.0, "002891": 40.0}
        actions = {s["code"]: s["action"] for s in data["suggestions"]}
        assert actions == {"110022": "buy", "002891": "sell"}

    def test_preview_degrades_when_quotes_fail(self, client, monkeypatch):
        client.put("/api/portfolio/rebalance/config", json={
            "drift_threshold_pct": 5, "targets": {"110022": 100},
        })

        def _boom(codes):
            raise RuntimeError("upstream down")

        monkeypatch.setattr("src.core.data_source_v2.get_fund_realtime_tencent", _boom)
        resp = client.get("/api/portfolio/rebalance/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        assert data["suggestions"] == []
        assert data["managed_value"] == 0.0

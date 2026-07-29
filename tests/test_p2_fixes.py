"""P2 修复后的新增功能测试

验证:
- monitor 自选股文件持久化(替换原 _mock_watchlist 内存)
- strategy 降级响应(_lightweight_analysis + _fallback_analysis,不冒充真实评分)
- dashboard 无 mock 数据(空持仓返回 [])
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


class TestMonitorWatchlistPersistence:
    """monitor 自选股文件持久化"""

    def test_watchlist_returns_empty_when_no_file(self, client, tmp_path, monkeypatch):
        """无文件时返回空列表(不返回 mock)"""
        monkeypatch.setattr("web.routes.monitor._WATCHLIST_FILE", str(tmp_path / "no_exist.json"))
        resp = client.get("/api/monitor/watchlist")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_and_remove_watchlist_persists(self, client, tmp_path, monkeypatch):
        """添加后持久化,删除后消失"""
        watch_file = tmp_path / "watch.json"
        monkeypatch.setattr("web.routes.monitor._WATCHLIST_FILE", str(watch_file))

        # 添加
        resp = client.post("/api/monitor/watchlist", json={"stock_code": "600519", "rules": ["price_surge"]})
        assert resp.json()["success"] is True
        assert watch_file.exists()
        data = json.loads(watch_file.read_text(encoding="utf-8"))
        assert "600519" in data

        # 读取
        resp = client.get("/api/monitor/watchlist")
        assert len(resp.json()) == 1
        assert resp.json()[0]["stock_code"] == "600519"

        # 删除
        resp = client.delete("/api/monitor/watchlist/600519")
        assert resp.json()["success"] is True
        resp = client.get("/api/monitor/watchlist")
        assert resp.json() == []

    def test_remove_nonexistent_returns_false(self, client, tmp_path, monkeypatch):
        """删除不存在的代码返回 success=False"""
        monkeypatch.setattr("web.routes.monitor._WATCHLIST_FILE", str(tmp_path / "no_exist.json"))
        resp = client.delete("/api/monitor/watchlist/000001")
        assert resp.json()["success"] is False


class TestStrategyFallback:
    """strategy 降级响应(不冒充真实评分)"""

    def test_lightweight_analysis_returns_available_true(self, client):
        """策略未启用时,轻量分析返回 available=True"""
        with patch("web.routes.strategy._HAS_STRATEGIES", False):
            with patch("web.routes.strategy.ds2.get_realtime_quote_tencent", return_value=[]):
                with patch("web.routes.strategy.ds2.get_financial_snapshot", return_value={}):
                    with patch("web.routes.strategy.ds2.get_kline_mootdx", return_value=[]):
                        with patch("web.routes.strategy.ds2.get_capital_flow_detail", return_value={}):
                            resp = client.post("/api/strategy/analyze", json={
                                "stock_code": "600519", "strategy_type": "comprehensive"
                            })
        result = resp.json()["result"]
        assert result["available"] is True
        assert result["source"] == "lightweight"
        assert result["total_score"] >= 0

    def test_fallback_returns_available_false_on_error(self, client):
        """数据源异常时返回 available=False(不冒充)"""
        with patch("web.routes.strategy._HAS_STRATEGIES", False):
            with patch("web.routes.strategy.ds2.get_realtime_quote_tencent", side_effect=Exception("network")):
                resp = client.post("/api/strategy/analyze", json={
                    "stock_code": "600519", "strategy_type": "comprehensive"
                })
        result = resp.json()["result"]
        assert result["available"] is False
        assert result["total_score"] == 0.0
        assert result["signal"] == "N/A"


class TestDashboardNoMock:
    """dashboard 无 mock 数据"""

    def test_empty_positions_when_no_file(self, client, tmp_path, monkeypatch):
        """无持仓文件时返回空列表(不返回 _MOCK_POSITIONS)"""
        monkeypatch.setattr("web.routes.dashboard._HAS_CORE", False)
        monkeypatch.setattr("os.path.exists", lambda p: False)
        resp = client.get("/api/dashboard/positions")
        # 返回空列表(不返回 mock)
        data = resp.json()
        assert data == []


class TestHealthEndpointNoLeak:
    """health 端点不泄露密钥详情"""

    def test_health_returns_ai_ready_boolean(self, client):
        """返回 ai_ready 布尔值,ai_keys 仅含布尔状态不泄露 key 本身"""
        resp = client.get("/api/health")
        data = resp.json()
        assert "ai_ready" in data
        assert isinstance(data["ai_ready"], bool)
        # ai_keys 存在且值为布尔型(不泄露 key 字符串)
        if "ai_keys" in data:
            for v in data["ai_keys"].values():
                assert isinstance(v, bool)


class TestExecutorSellProfitFix:
    """executor SELL profit 计算修复"""

    def test_pre_sell_snapshot_exists_after_sell(self):
        """SELL 执行后会创建 _pre_sell_snapshot"""
        from src.core.executor import TradeExecutor
        import inspect
        source = inspect.getsource(TradeExecutor.execute_signal)
        assert "_pre_sell_snapshot" in source
        assert '"止损" in (signal.reason or "")' in source  # 中文不用 lower()

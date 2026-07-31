"""监控路由单元测试

验证 web/routes/monitor.py 的端点:
- GET    /api/monitor/alerts                 告警列表(默认 + 指定个股)
- GET    /api/monitor/watchlist              自选股列表(需鉴权)
- POST   /api/monitor/watchlist              添加自选股(需鉴权)
- DELETE /api/monitor/watchlist/{stock_code} 移除自选股(需鉴权)
- GET    /api/monitor/capital_flow           资金流向(主力/大单/北向)
- GET    /api/monitor/northbound             北向资金实时

通过 mock src.core.data_source_v2 与 monitor 模块的函数,
并用 monkeypatch 将 _WATCHLIST_FILE 指向 tmp_path,避免真实网络与文件副作用。
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    """确保 ADMIN_TOKEN 未配置(开发模式放行),避免鉴权干扰

    conftest.py 已全局 setdefault APP_ENV=dev,此处仅需清除 ADMIN_TOKEN。
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


@pytest.fixture
def isolated_watchlist(tmp_path, monkeypatch):
    """将 monitor 的 watchlist 文件路径指向 tmp_path,隔离测试副作用

    每个测试用例获得独立的 watchlist 文件,避免污染仓库的 web/user_watchlist.json。
    """
    watch_file = tmp_path / "watchlist.json"
    monkeypatch.setattr("web.routes.monitor._WATCHLIST_FILE", str(watch_file))
    return watch_file


# 模拟行情数据(默认 alerts 路径用)
_MOCK_QUOTES = [
    {"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": 6.5},
    {"code": "000858", "name": "五粮液", "price": 168.0, "change_pct": 1.2},
]

# 模拟资金流向明细
_MOCK_CAPITAL_FLOW = {
    "main_net_inflow": 50000000,
    "large_net_inflow": 20000000,
    "super_large_net_inflow": 30000000,
    "medium_net_inflow": -15000000,
    "small_net_inflow": -35000000,
}

# 模拟北向资金
_MOCK_NORTHBOUND = {
    "total_net_inflow": 8800000000,
    "sh_net_inflow": 5000000000,
    "sz_net_inflow": 3800000000,
    "top_stocks": [{"code": "600519", "name": "贵州茅台", "net_inflow": 500000000}],
}


class TestAlerts:
    """GET /api/monitor/alerts"""

    def test_alerts_default_empty_watchlist(self, client, isolated_watchlist):
        """无自选股时返回"暂无自选股"提示告警"""
        resp = client.get("/api/monitor/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["alert_type"] == "market_status"
        assert "暂无自选股" in data[0]["message"]

    def test_alerts_default_with_watchlist_surge(self, client, isolated_watchlist):
        """自选股涨跌幅>=5% 时返回 price_surge 告警(严重程度 critical/warning)"""
        # 先添加自选股
        client.post("/api/monitor/watchlist", json={"stock_code": "600519", "rules": ["price_surge"]})
        client.post("/api/monitor/watchlist", json={"stock_code": "000858", "rules": ["price_surge"]})
        # mock 行情:600519 涨 6.5%(>=5 -> price_surge),000858 涨 1.2%(无告警)
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent", return_value=_MOCK_QUOTES), \
             patch("web.routes.monitor.ds2.get_index_realtime", return_value=[]):
            resp = client.get("/api/monitor/alerts")
        assert resp.status_code == 200
        data = resp.json()
        # 至少包含 600519 的 price_surge 告警
        surge_alerts = [a for a in data if a["stock_code"] == "600519"]
        assert len(surge_alerts) == 1
        assert surge_alerts[0]["alert_type"] == "price_surge"
        assert surge_alerts[0]["severity"] == "warning"  # 6.5% < 8% -> warning

    def test_alerts_with_stock_code_no_monitor(self, client):
        """指定 stock_code 且 _HAS_MONITOR=False 时返回"暂无监控数据"告警"""
        with patch("web.routes.monitor._HAS_MONITOR", False):
            resp = client.get("/api/monitor/alerts", params={"stock_code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_code"] == "600519"
        assert "暂无监控数据" in data[0]["message"]

    def test_alerts_with_stock_code_monitor_error(self, client):
        """_HAS_MONITOR=True 但 check_alerts 抛异常时返回 system_error 告警"""
        with patch("web.routes.monitor._HAS_MONITOR", True), \
             patch("web.routes.monitor.StockMonitor") as mock_cls:
            mock_cls.return_value.check_alerts.side_effect = RuntimeError("db down")
            resp = client.get("/api/monitor/alerts", params={"stock_code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["alert_type"] == "system_error"
        assert data[0]["severity"] == "warning"
        assert "告警检查失败" in data[0]["message"]


class TestWatchlistCrud:
    """自选股 CRUD(需鉴权,dev 模式放行)"""

    def test_watchlist_empty_when_no_file(self, client, isolated_watchlist):
        """无文件时返回空列表"""
        resp = client.get("/api/monitor/watchlist")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_watchlist_persists_and_lists(self, client, isolated_watchlist):
        """添加自选股后持久化,GET 能读到"""
        resp = client.post("/api/monitor/watchlist", json={
            "stock_code": "600519", "rules": ["price_surge"]
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "600519" in resp.json()["message"]

        # 文件应已写入
        assert isolated_watchlist.exists()
        # GET 应返回 1 条
        resp = client.get("/api/monitor/watchlist")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_code"] == "600519"
        assert data[0]["rules"] == ["price_surge"]

    def test_add_watchlist_default_rules_when_none(self, client, isolated_watchlist):
        """未传 rules 时默认 ["price_surge"]"""
        resp = client.post("/api/monitor/watchlist", json={"stock_code": "000858"})
        assert resp.status_code == 200
        # 通过 GET 验证默认 rules
        data = client.get("/api/monitor/watchlist").json()
        assert data[0]["rules"] == ["price_surge"]

    def test_remove_watchlist_existing(self, client, isolated_watchlist):
        """删除已存在的自选股返回 success=True"""
        client.post("/api/monitor/watchlist", json={"stock_code": "600519", "rules": ["price_surge"]})
        resp = client.delete("/api/monitor/watchlist/600519")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # GET 应为空
        assert client.get("/api/monitor/watchlist").json() == []

    def test_remove_watchlist_nonexistent(self, client, isolated_watchlist):
        """删除不存在的自选股返回 success=False"""
        resp = client.delete("/api/monitor/watchlist/999999")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "不在自选列表" in resp.json()["message"]


class TestCapitalFlow:
    """GET /api/monitor/capital_flow"""

    def test_capital_flow_no_stock_code_returns_zeros(self, client):
        """无 stock_code 时返回 0 + 北向资金(单位:亿元)"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   return_value=_MOCK_NORTHBOUND):
            resp = client.get("/api/monitor/capital_flow")
        assert resp.status_code == 200
        data = resp.json()
        assert data["main_net_inflow"] == 0
        assert data["large_order_ratio"] == 0
        # 北向 88 亿 / 1e8 = 88.0
        assert data["northbound_change"] == 88.0
        assert data["data_quality"] == "normal"

    def test_capital_flow_with_stock_code(self, client):
        """指定 stock_code 时返回解析后的主力/大单/中单/小单比例"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   return_value=_MOCK_NORTHBOUND), \
             patch("web.routes.monitor.ds2.get_capital_flow_detail",
                   return_value=_MOCK_CAPITAL_FLOW):
            resp = client.get("/api/monitor/capital_flow", params={"stock_code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["main_net_inflow"] == 50000000
        # 比例之和应接近 100(大单+中单+小单)
        total_ratio = data["large_order_ratio"] + data["medium_order_ratio"] + data["small_order_ratio"]
        assert 99.0 <= total_ratio <= 101.0
        assert data["data_quality"] == "normal"

    def test_capital_flow_degraded_on_northbound_failure(self, client):
        """北向资金接口异常时 data_quality=degraded"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   side_effect=RuntimeError("network error")):
            resp = client.get("/api/monitor/capital_flow")
        assert resp.status_code == 200
        assert resp.json()["data_quality"] == "degraded"
        assert resp.json()["northbound_change"] == 0

    def test_capital_flow_degraded_on_detail_failure(self, client):
        """资金明细接口异常时 data_quality=degraded"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   return_value=_MOCK_NORTHBOUND), \
             patch("web.routes.monitor.ds2.get_capital_flow_detail",
                   side_effect=RuntimeError("timeout")):
            resp = client.get("/api/monitor/capital_flow", params={"stock_code": "600519"})
        assert resp.status_code == 200
        assert resp.json()["data_quality"] == "degraded"


class TestNorthbound:
    """GET /api/monitor/northbound"""

    def test_northbound_normal(self, client):
        """正常返回北向资金数据 + top_stocks"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   return_value=_MOCK_NORTHBOUND):
            resp = client.get("/api/monitor/northbound")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_net_inflow"] == 8800000000
        assert data["sh_net_inflow"] == 5000000000
        assert data["sz_net_inflow"] == 3800000000
        assert len(data["top_stocks"]) == 1
        assert data["data_quality"] == "normal"

    def test_northbound_degraded_on_exception(self, client):
        """接口异常 + _HAS_MONITOR=False 时返回全零 + degraded"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   side_effect=RuntimeError("api down")), \
             patch("web.routes.monitor._HAS_MONITOR", False):
            resp = client.get("/api/monitor/northbound")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_net_inflow"] == 0
        assert data["top_stocks"] == []
        assert data["data_quality"] == "degraded"

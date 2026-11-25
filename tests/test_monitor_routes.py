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


class TestAlertsAuth:
    """GET /api/monitor/alerts 鉴权（2026-10-26 安全修复回归）

    缺陷：该端点无鉴权，而不带 stock_code 时会走 _generate_default_alerts()，
    该函数读取**自选股列表**——等于匿名即可间接拿到 /api/monitor/watchlist
    （同名数据、已挂鉴权）的内容，属"同一份私有数据两条路径、一条敞开"。
    """

    def test_alerts_requires_token_in_production(self, client):
        """生产环境未带令牌 → 401"""
        with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_TOKEN": "unit-test-token"}, clear=False):
            resp = client.get("/api/monitor/alerts")
        assert resp.status_code == 401

    def test_alerts_rejects_wrong_token(self, client):
        with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_TOKEN": "unit-test-token"}, clear=False):
            resp = client.get("/api/monitor/alerts", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_alerts_allows_valid_token(self, client):
        """携带正确令牌 → 200（返回值结构不变）"""
        with patch.dict(os.environ, {"APP_ENV": "production", "ADMIN_TOKEN": "unit-test-token"}, clear=False), \
             patch("web.routes.monitor._generate_default_alerts", return_value=[]):
            resp = client.get("/api/monitor/alerts",
                              headers={"Authorization": "Bearer unit-test-token"})
        assert resp.status_code == 200
        assert resp.json() == []


# ===== r9-S2 覆盖率补强: 告警生成分支 / 写失败 / 北向回退链 =====

class TestDefaultAlertsBranches:
    """_generate_default_alerts 各分支(需 isolated_watchlist 隔离文件)"""

    def _add_watch(self, client, code):
        client.post("/api/monitor/watchlist", json={"stock_code": code, "rules": ["price_surge"]})

    def test_price_alert_band_3_to_5pct(self, client, isolated_watchlist):
        """涨跌幅 3%~5% → price_alert(info)，不升级为 price_surge"""
        self._add_watch(client, "600519")
        quotes = [{"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": 4.2}]
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent", return_value=quotes), \
             patch("web.routes.monitor.ds2.get_index_realtime", return_value=[]):
            data = client.get("/api/monitor/alerts").json()
        surge = [a for a in data if a["stock_code"] == "600519"]
        assert len(surge) == 1
        assert surge[0]["alert_type"] == "price_alert"
        assert surge[0]["severity"] == "info"

    def test_critical_surge_at_8pct(self, client, isolated_watchlist):
        """涨跌幅 >=8% → severity=critical"""
        self._add_watch(client, "600519")
        quotes = [{"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": 8.5}]
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent", return_value=quotes), \
             patch("web.routes.monitor.ds2.get_index_realtime", return_value=[]):
            data = client.get("/api/monitor/alerts").json()
        surge = [a for a in data if a["stock_code"] == "600519"]
        assert surge[0]["severity"] == "critical"

    def test_quotes_failure_degrades_to_calm(self, client, isolated_watchlist):
        """行情接口异常 → 降级为空行情, 无自选股级告警, 最终给"平稳"提示"""
        self._add_watch(client, "600519")
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent",
                   side_effect=RuntimeError("network")), \
             patch("web.routes.monitor.ds2.get_index_realtime", return_value=[]):
            data = client.get("/api/monitor/alerts").json()
        assert len(data) == 1
        assert data[0]["alert_type"] == "market_status"
        assert "平稳" in data[0]["message"]

    def test_change_pct_non_numeric_parsed_as_zero(self, client, isolated_watchlist):
        """change_pct 为脏数据(非数值)时解析告警降级为 0, 不抛 500"""
        self._add_watch(client, "600519")
        quotes = [{"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": "abc"}]
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent", return_value=quotes), \
             patch("web.routes.monitor.ds2.get_index_realtime", return_value=[]):
            data = client.get("/api/monitor/alerts").json()
        assert len(data) == 1  # 仅"平稳"，无个股告警
        assert data[0]["alert_type"] == "market_status"

    def test_index_alert_market_alert(self, client, isolated_watchlist):
        """指数涨跌幅 >=2% → market_alert(warning)"""
        self._add_watch(client, "600519")
        quotes = [{"code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 0.1}]
        idx = [{"code": "sh000001", "name": "上证指数", "change_pct": 2.5}]
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent", return_value=quotes), \
             patch("web.routes.monitor.ds2.get_index_realtime", return_value=idx):
            data = client.get("/api/monitor/alerts").json()
        market = [a for a in data if a["alert_type"] == "market_alert"]
        assert len(market) == 1
        assert market[0]["stock_code"] == "sh000001"

    def test_index_fetch_failure_ignored(self, client, isolated_watchlist):
        """指数接口异常 → 跳过指数告警, 不影响其余告警生成"""
        self._add_watch(client, "600519")
        quotes = [{"code": "600519", "name": "贵州茅台", "price": 1.0, "change_pct": 6.0}]
        with patch("web.routes.monitor.ds2.get_realtime_quote_tencent", return_value=quotes), \
             patch("web.routes.monitor.ds2.get_index_realtime", side_effect=RuntimeError("idx down")):
            data = client.get("/api/monitor/alerts").json()
        assert [a["alert_type"] for a in data if a["stock_code"] == "600519"] == ["price_surge"]
        assert all(a["alert_type"] != "market_alert" for a in data)


class TestWatchlistCorruptAndSaveFailure:
    """watchlist 文件损坏回退与写失败提示"""

    def test_watchlist_non_dict_file_returns_empty(self, client, isolated_watchlist):
        """watchlist 文件内容为非 dict(JSON 数组/标量)时按空处理, 不抛 500"""
        isolated_watchlist.write_text('["not", "a", "dict"]', encoding="utf-8")
        resp = client.get("/api/monitor/watchlist")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_watchlist_save_failure_returns_failure(self, client, isolated_watchlist):
        """写文件异常 → success=False, 不抛 500"""
        with patch("web.routes.monitor.atomic_write_json", side_effect=OSError("disk full")):
            resp = client.post("/api/monitor/watchlist", json={"stock_code": "600519"})
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "添加失败" in resp.json()["message"]

    def test_remove_watchlist_save_failure_returns_failure(self, client, isolated_watchlist):
        client.post("/api/monitor/watchlist", json={"stock_code": "600519"})
        with patch("web.routes.monitor.atomic_write_json", side_effect=OSError("disk full")):
            resp = client.delete("/api/monitor/watchlist/600519")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "移除失败" in resp.json()["message"]


class TestAlertsWithMonitorHappyPath:
    """_HAS_MONITOR=True 时 alerts 走 StockMonitor 成功路径"""

    def test_alerts_monitor_success(self, client):
        with patch("web.routes.monitor._HAS_MONITOR", True), \
             patch("web.routes.monitor.StockMonitor") as mock_cls:
            mock_cls.return_value.check_alerts.return_value = [{
                "stock_code": "600519", "alert_type": "price_surge",
                "severity": "critical", "message": "急涨", "detail": {"change_pct": 9.1},
            }]
            resp = client.get("/api/monitor/alerts", params={"stock_code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["alert_type"] == "price_surge"
        assert data[0]["detail"]["change_pct"] == 9.1


class TestCapitalFlowEmptyBranches:
    """capital_flow: 北向空数据降级 + 明细空数据兜底"""

    def test_northbound_empty_data_degrades(self, client):
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime", return_value=None):
            resp = client.get("/api/monitor/capital_flow")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_quality"] == "degraded"
        assert data["northbound_change"] == 0

    def test_capital_flow_detail_empty_returns_default_degraded(self, client):
        """明细接口返回空 dict 时返回全零 + degraded, 不产生无返回值 500"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   return_value=_MOCK_NORTHBOUND), \
             patch("web.routes.monitor.ds2.get_capital_flow_detail", return_value={}):
            resp = client.get("/api/monitor/capital_flow", params={"stock_code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["main_net_inflow"] == 0
        assert data["data_quality"] == "degraded"


class TestNorthboundQuantFallback:
    """northbound: ds2 失败后 _HAS_MONITOR=True 时走 TradingQuant 回退链"""

    def test_northbound_quant_fallback_success(self, client):
        quant_result = {
            "total_net_inflow": 12000000000,
            "sh_net_inflow": 7000000000,
            "sz_net_inflow": 5000000000,
            "top_stocks": [{"code": "600519", "name": "贵州茅台", "net_inflow": 6e8}],
        }
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   side_effect=RuntimeError("ds2 down")), \
             patch("web.routes.monitor._HAS_MONITOR", True), \
             patch("src.strategies.trading_quant.TradingQuant") as mock_cls:
            mock_cls.return_value.northbound_flow.return_value = quant_result
            resp = client.get("/api/monitor/northbound")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_net_inflow"] == 12000000000
        assert data["top_stocks"][0]["code"] == "600519"

    def test_northbound_quant_failure_returns_degraded(self, client):
        """回退链也失败 → 全零 + degraded, 不抛 500"""
        with patch("web.routes.monitor.ds2.get_northbound_flow_realtime",
                   side_effect=RuntimeError("ds2 down")), \
             patch("web.routes.monitor._HAS_MONITOR", True), \
             patch("src.strategies.trading_quant.TradingQuant",
                   side_effect=RuntimeError("quant down")):
            resp = client.get("/api/monitor/northbound")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_net_inflow"] == 0
        assert data["data_quality"] == "degraded"

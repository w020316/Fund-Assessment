"""Dashboard 路由测试

覆盖 web/routes/dashboard.py:
- GET /api/dashboard/overview
- GET /api/dashboard/positions
- GET /api/dashboard/trades
- GET /api/dashboard/risk
- _load_user_positions / _load_user_cash 工具函数
- _enrich_positions_with_realtime 异步行情增强
- RiskResponse 风险等级计算
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端(强制 _HAS_CORE=False 走文件读取分支)"""
    # patch _HAS_CORE 为 False,走 _load_user_positions 文件分支
    with patch("web.routes.dashboard._HAS_CORE", False):
        from web.api import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def sample_positions():
    """示例持仓数据"""
    return [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "quantity": 100,
            "available_quantity": 100,
            "cost_price": 1500.0,
        },
        {
            "symbol": "000001",
            "name": "平安银行",
            "quantity": 1000,
            "available_quantity": 1000,
            "cost_price": 10.0,
        },
    ]


@pytest.fixture
def sample_quotes():
    """示例行情返回(腾讯接口格式,list)"""
    return [
        {
            "code": "600519",
            "name": "贵州茅台",
            "price": 1688.0,
            "change_pct": 1.11,
            "prev_close": 1669.5,
        },
        {
            "code": "000001",
            "name": "平安银行",
            "price": 11.0,
            "change_pct": 0.5,
            "prev_close": 10.95,
        },
    ]


def _make_pos_file_mock(content=None, exists=True):
    """构造 mock os.path.exists + open 的 patcher 组合

    content: None 表示文件不存在;否则为 JSON 字符串内容
    返回 (exists_patcher, open_patcher)
    """
    def exists_side_effect(path):
        if str(path).endswith("user_positions.json"):
            return exists and content is not None
        return False

    def open_side_effect(file, mode="r", *args, **kwargs):
        if str(file).endswith("user_positions.json"):
            return io.StringIO(content)
        raise FileNotFoundError(file)

    return patch("os.path.exists", side_effect=exists_side_effect), \
           patch("builtins.open", side_effect=open_side_effect)


def _patch_pos_file(content=None, exists=True):
    """上下文管理器:同时 patch exists 和 open"""
    p1, p2 = _make_pos_file_mock(content=content, exists=exists)
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(p1)
    stack.enter_context(p2)
    return stack


class TestLoadUserPositions:
    """_load_user_positions 工具函数测试"""

    def test_returns_empty_list_when_file_missing(self):
        """文件缺失时返回空列表"""
        from web.routes import dashboard
        with _patch_pos_file(content=None, exists=False):
            result = dashboard._load_user_positions()
        assert result == []

    def test_returns_positions_when_file_exists(self):
        """文件存在时返回 positions 字段"""
        from web.routes import dashboard
        content = json.dumps({
            "positions": [{"symbol": "600519", "name": "贵州茅台"}],
            "available_cash": 50000.0,
        })
        with _patch_pos_file(content=content, exists=True):
            result = dashboard._load_user_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "600519"

    def test_returns_empty_when_json_corrupted(self):
        """JSON 损坏时返回空列表(不抛异常)"""
        from web.routes import dashboard
        with _patch_pos_file(content="not-a-json", exists=True):
            result = dashboard._load_user_positions()
        assert result == []

    def test_returns_empty_when_no_positions_key(self):
        """JSON 缺 positions 键时返回空列表"""
        from web.routes import dashboard
        content = json.dumps({"available_cash": 1000.0})
        with _patch_pos_file(content=content, exists=True):
            result = dashboard._load_user_positions()
        assert result == []

    def test_returns_positions_list_when_present(self):
        """positions 是列表时返回该列表"""
        from web.routes import dashboard
        content = json.dumps({"positions": [{"symbol": "A"}, {"symbol": "B"}]})
        with _patch_pos_file(content=content, exists=True):
            result = dashboard._load_user_positions()
        assert len(result) == 2


class TestLoadUserCash:
    """_load_user_cash 工具函数测试"""

    def test_returns_zero_when_file_missing(self):
        from web.routes import dashboard
        with _patch_pos_file(content=None, exists=False):
            assert dashboard._load_user_cash() == 0.0

    def test_returns_cash_when_file_exists(self):
        from web.routes import dashboard
        content = json.dumps({
            "positions": [],
            "available_cash": 88888.0,
        })
        with _patch_pos_file(content=content, exists=True):
            assert dashboard._load_user_cash() == 88888.0

    def test_returns_zero_when_json_corrupted(self):
        from web.routes import dashboard
        with _patch_pos_file(content="bad json", exists=True):
            assert dashboard._load_user_cash() == 0.0

    def test_returns_zero_when_no_cash_key(self):
        from web.routes import dashboard
        content = json.dumps({"positions": []})
        with _patch_pos_file(content=content, exists=True):
            assert dashboard._load_user_cash() == 0.0

    def test_returns_zero_when_cash_not_numeric(self):
        """cash 非数字时返回 0(容错,float() 抛 ValueError 被 except 捕获)"""
        from web.routes import dashboard
        content = json.dumps({"available_cash": "not-a-number"})
        with _patch_pos_file(content=content, exists=True):
            assert dashboard._load_user_cash() == 0.0

    def test_returns_negative_cash(self):
        """cash 为负数时应正确返回"""
        from web.routes import dashboard
        content = json.dumps({"available_cash": -1000.5})
        with _patch_pos_file(content=content, exists=True):
            assert dashboard._load_user_cash() == -1000.5


class TestEnrichPositions:
    """_enrich_positions_with_realtime 异步函数测试"""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_positions(self):
        from web.routes.dashboard import _enrich_positions_with_realtime
        result = await _enrich_positions_with_realtime([])
        assert result == []

    @pytest.mark.asyncio
    async def test_enriches_with_quotes(self, sample_positions, sample_quotes):
        from web.routes.dashboard import _enrich_positions_with_realtime
        with patch("web.routes.dashboard.ds2.get_realtime_quote_tencent",
                   return_value=sample_quotes):
            result = await _enrich_positions_with_realtime([dict(p) for p in sample_positions])
        # 第一只:成本 1500,现价 1688
        assert result[0]["current_price"] == 1688.0
        assert result[0]["market_value"] == round(1688.0 * 100, 2)
        assert result[0]["profit"] == round(1688.0 * 100 - 1500.0 * 100, 2)
        assert result[0]["profit_pct"] == round((1688.0 / 1500.0 - 1) * 100, 2)
        assert result[0]["_change_pct"] == 1.11
        assert result[0]["_prev_close"] == 1669.5
        assert result[0]["name"] == "贵州茅台"

    @pytest.mark.asyncio
    async def test_uses_cost_when_quote_missing(self, sample_positions):
        """行情缺失时回退到成本价"""
        from web.routes.dashboard import _enrich_positions_with_realtime
        with patch("web.routes.dashboard.ds2.get_realtime_quote_tencent",
                   return_value=[]):
            result = await _enrich_positions_with_realtime([dict(sample_positions[0])])
        # 无行情,current_price 回退到 cost_price
        assert result[0]["current_price"] == 1500.0
        assert result[0]["market_value"] == round(1500.0 * 100, 2)
        assert result[0]["profit"] == 0.0
        assert result[0]["profit_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_handles_zero_cost_price(self):
        """成本价为 0 时 profit_pct 应为 0(避免除零)"""
        from web.routes.dashboard import _enrich_positions_with_realtime
        positions = [{"symbol": "000001", "name": "X", "quantity": 100, "cost_price": 0}]
        with patch("web.routes.dashboard.ds2.get_realtime_quote_tencent",
                   return_value=[{"code": "000001", "price": 10.0}]):
            result = await _enrich_positions_with_realtime(positions)
        assert result[0]["profit_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_handles_quote_exception(self, sample_positions):
        """行情接口异常时,回退到成本价"""
        from web.routes.dashboard import _enrich_positions_with_realtime
        with patch("web.routes.dashboard.ds2.get_realtime_quote_tencent",
                   side_effect=Exception("network down")):
            result = await _enrich_positions_with_realtime([dict(sample_positions[0])])
        # 异常被捕获,quotes=[],回退成本价
        assert result[0]["current_price"] == 1500.0

    @pytest.mark.asyncio
    async def test_updates_name_from_quote(self):
        """行情返回的 name 应覆盖持仓的 name"""
        from web.routes.dashboard import _enrich_positions_with_realtime
        positions = [{"symbol": "600519", "name": "旧名称", "quantity": 100, "cost_price": 1500}]
        quotes = [{"code": "600519", "name": "贵州茅台", "price": 1688.0}]
        with patch("web.routes.dashboard.ds2.get_realtime_quote_tencent",
                   return_value=quotes):
            result = await _enrich_positions_with_realtime(positions)
        assert result[0]["name"] == "贵州茅台"

    @pytest.mark.asyncio
    async def test_partial_match_only_updates_matched(self, sample_positions, sample_quotes):
        """部分匹配:仅匹配的持仓被更新"""
        from web.routes.dashboard import _enrich_positions_with_realtime
        # 只返回第一只的行情
        with patch("web.routes.dashboard.ds2.get_realtime_quote_tencent",
                   return_value=[sample_quotes[0]]):
            result = await _enrich_positions_with_realtime([dict(p) for p in sample_positions])
        # 第一只被更新
        assert result[0]["current_price"] == 1688.0
        # 第二只回退成本价
        assert result[1]["current_price"] == 10.0


class TestOverviewEndpoint:
    """GET /api/dashboard/overview 测试"""

    def test_overview_returns_200(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "available_cash" in data
        assert "total_assets" in data
        assert "market_value" in data
        assert "daily_pnl" in data
        assert "daily_pnl_pct" in data
        assert "position_count" in data
        assert "risk_level" in data
        assert "risk_message" in data

    def test_overview_empty_positions(self, client):
        """无持仓时 market_value=0, position_count=0"""
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/overview")
        data = resp.json()
        assert data["market_value"] == 0
        assert data["position_count"] == 0
        assert data["available_cash"] == 0

    def test_overview_with_positions(self, client, sample_positions, sample_quotes):
        with patch("web.routes.dashboard._load_user_positions", return_value=sample_positions), \
             patch("web.routes.dashboard._load_user_cash", return_value=100000.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=sample_quotes):
            resp = client.get("/api/dashboard/overview")
        data = resp.json()
        assert data["position_count"] == 2
        assert data["available_cash"] == 100000.0
        # market_value = 1688*100 + 11*1000 = 168800 + 11000 = 179800
        assert data["market_value"] == 179800.0
        # total = 100000 + 179800 = 279800
        assert data["total_assets"] == 279800.0

    def test_overview_calculates_daily_pnl(self, client, sample_positions, sample_quotes):
        """daily_pnl 应基于 prev_close 和 change_pct 计算"""
        with patch("web.routes.dashboard._load_user_positions", return_value=sample_positions), \
             patch("web.routes.dashboard._load_user_cash", return_value=100000.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=sample_quotes):
            resp = client.get("/api/dashboard/overview")
        data = resp.json()
        # daily_pnl = sum(quantity * prev_close * change_pct / 100)
        # = 100 * 1669.5 * 1.11/100 + 1000 * 10.95 * 0.5/100
        # = 1853.0 + 54.75 = 1907.75
        assert data["daily_pnl"] > 0

    def test_overview_normal_risk_level(self, client):
        """无持仓时风险等级 NORMAL"""
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/overview")
        assert resp.json()["risk_level"] == "NORMAL"


class TestPositionsEndpoint:
    """GET /api/dashboard/positions 测试"""

    def test_positions_returns_200(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/positions")
        assert resp.status_code == 200

    def test_positions_returns_list(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/positions")
        assert isinstance(resp.json(), list)

    def test_positions_empty(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/positions")
        assert resp.json() == []

    def test_positions_with_data(self, client, sample_positions, sample_quotes):
        with patch("web.routes.dashboard._load_user_positions", return_value=sample_positions), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=sample_quotes):
            resp = client.get("/api/dashboard/positions")
        data = resp.json()
        assert len(data) == 2
        assert data[0]["symbol"] == "600519"
        assert data[0]["name"] == "贵州茅台"
        assert data[0]["current_price"] == 1688.0
        assert data[0]["market_value"] == 168800.0
        assert data[0]["profit"] == 18800.0
        assert "available_quantity" in data[0]
        assert "cost_price" in data[0]
        assert "profit_pct" in data[0]


class TestTradesEndpoint:
    """GET /api/dashboard/trades 测试"""

    def test_trades_returns_200(self, client):
        resp = client.get("/api/dashboard/trades")
        assert resp.status_code == 200

    def test_trades_returns_list(self, client):
        resp = client.get("/api/dashboard/trades")
        assert isinstance(resp.json(), list)

    def test_trades_empty_without_core(self, client):
        """无 core 模块时返回空列表"""
        resp = client.get("/api/dashboard/trades")
        # _HAS_CORE 为 False 时直接返回 []
        assert resp.json() == []

    def test_trades_accepts_limit_param(self, client):
        """应接受 limit 参数"""
        resp = client.get("/api/dashboard/trades", params={"limit": 5})
        assert resp.status_code == 200


class TestRiskEndpoint:
    """GET /api/dashboard/risk 测试"""

    def test_risk_returns_200(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/risk")
        assert resp.status_code == 200

    def test_risk_response_fields(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/risk")
        data = resp.json()
        expected_keys = {
            "level", "total_assets", "peak_assets", "drawdown_pct",
            "daily_pnl", "daily_pnl_pct", "consecutive_stop_losses",
            "is_paused", "pause_until", "is_emergency_stopped",
            "no_new_positions", "position_reduction", "message",
        }
        assert expected_keys.issubset(set(data.keys()))

    def test_risk_normal_when_no_drawdown(self, client):
        """无亏损时 level=NORMAL"""
        positions = [{"symbol": "600519", "name": "X", "quantity": 100, "cost_price": 1500}]
        quotes = [{"code": "600519", "price": 1688.0}]  # 盈利
        with patch("web.routes.dashboard._load_user_positions", return_value=positions), \
             patch("web.routes.dashboard._load_user_cash", return_value=100000.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=quotes):
            resp = client.get("/api/dashboard/risk")
        assert resp.json()["level"] == "NORMAL"
        assert resp.json()["drawdown_pct"] == 0.0

    def test_risk_warning_when_drawdown_5_to_10(self, client):
        """回撤 5%-10% 时 level=WARNING"""
        # 成本 1500,现价 1425(亏 5%)
        positions = [{"symbol": "600519", "name": "X", "quantity": 100, "cost_price": 1500}]
        quotes = [{"code": "600519", "price": 1425.0}]
        with patch("web.routes.dashboard._load_user_positions", return_value=positions), \
             patch("web.routes.dashboard._load_user_cash", return_value=100000.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=quotes):
            resp = client.get("/api/dashboard/risk")
        assert resp.json()["level"] == "WARNING"

    def test_risk_danger_when_drawdown_10_to_15(self, client):
        """回撤 10%-15% 时 level=DANGER"""
        # 成本 1500,现价 1350(亏 10%)
        positions = [{"symbol": "600519", "name": "X", "quantity": 100, "cost_price": 1500}]
        quotes = [{"code": "600519", "price": 1350.0}]
        with patch("web.routes.dashboard._load_user_positions", return_value=positions), \
             patch("web.routes.dashboard._load_user_cash", return_value=100000.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=quotes):
            resp = client.get("/api/dashboard/risk")
        assert resp.json()["level"] == "DANGER"

    def test_risk_critical_when_drawdown_over_15(self, client):
        """回撤 >15% 时 level=CRITICAL"""
        # 成本 1500,现价 1200(亏 20%)
        positions = [{"symbol": "600519", "name": "X", "quantity": 100, "cost_price": 1500}]
        quotes = [{"code": "600519", "price": 1200.0}]
        with patch("web.routes.dashboard._load_user_positions", return_value=positions), \
             patch("web.routes.dashboard._load_user_cash", return_value=100000.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=quotes):
            resp = client.get("/api/dashboard/risk")
        assert resp.json()["level"] == "CRITICAL"

    def test_risk_paused_false_when_normal(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/risk")
        data = resp.json()
        assert data["is_paused"] is False
        assert data["is_emergency_stopped"] is False
        assert data["no_new_positions"] is False
        assert data["position_reduction"] == 1.0
        assert data["consecutive_stop_losses"] == 0
        assert data["pause_until"] is None

    def test_risk_message_normal(self, client):
        with patch("web.routes.dashboard._load_user_positions", return_value=[]), \
             patch("web.routes.dashboard._load_user_cash", return_value=0.0), \
             patch("web.routes.dashboard.ds2.get_realtime_quote_tencent", return_value=[]):
            resp = client.get("/api/dashboard/risk")
        assert "正常" in resp.json()["message"] or resp.json()["level"] == "NORMAL"


class TestPydanticModels:
    """Pydantic 响应模型测试"""

    def test_overview_response_model(self):
        from web.routes.dashboard import OverviewResponse
        m = OverviewResponse(
            available_cash=1000.0,
            total_assets=2000.0,
            market_value=1000.0,
            daily_pnl=50.0,
            daily_pnl_pct=2.5,
            position_count=1,
            risk_level="NORMAL",
            risk_message="ok",
        )
        assert m.available_cash == 1000.0
        assert m.risk_level == "NORMAL"

    def test_position_item_model(self):
        from web.routes.dashboard import PositionItem
        m = PositionItem(
            symbol="600519", name="贵州茅台", quantity=100,
            available_quantity=100, cost_price=1500.0,
            current_price=1688.0, market_value=168800.0,
            profit=18800.0, profit_pct=12.53,
        )
        assert m.symbol == "600519"
        assert m.profit == 18800.0

    def test_trade_item_model(self):
        from web.routes.dashboard import TradeItem
        m = TradeItem(
            trade_id="t1", order_id="o1", symbol="600519",
            side="BUY", price=1500.0, quantity=100, amount=150000.0,
            commission=50.0, stamp_tax=150.0, net_amount=150200.0,
            strategy="test", reason="reason", created_at="2026-07-29",
        )
        assert m.side == "BUY"
        assert m.net_amount == 150200.0

    def test_risk_response_model(self):
        from web.routes.dashboard import RiskResponse
        m = RiskResponse(
            level="NORMAL", total_assets=100000.0, peak_assets=100000.0,
            drawdown_pct=0.0, daily_pnl=0.0, daily_pnl_pct=0.0,
            consecutive_stop_losses=0, is_paused=False, pause_until=None,
            is_emergency_stopped=False, no_new_positions=False,
            position_reduction=1.0, message="ok",
        )
        assert m.level == "NORMAL"
        assert m.pause_until is None

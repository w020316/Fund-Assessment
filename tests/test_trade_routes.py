"""交易管理路由单元测试

验证 web/routes/trade.py 的端点:
- POST /api/trade/buy     买入(需鉴权)
- POST /api/trade/sell    卖出(需鉴权)
- POST /api/trade/cancel  撤单(需鉴权)
- GET  /api/trade/orders  订单列表
- GET  /api/trade/history 成交历史

通过 mock _HAS_EXECUTOR 标志与 app_state 中的 executor/broker,避免真实交易引擎依赖。
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    """确保 ADMIN_TOKEN 未配置(开发模式放行),避免鉴权干扰"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


# 模拟 Order 对象(对应 src.core.executor.Order)
def _make_mock_order(
    order_id="ORD001", symbol="600519", side="buy", price=1688.0,
    quantity=100, order_type="limit", status="filled",
    filled_price=1688.0, filled_quantity=100,
):
    order = MagicMock()
    order.order_id = order_id
    order.symbol = symbol
    order.side = MagicMock(value=side)
    order.price = price
    order.quantity = quantity
    order.order_type = MagicMock(value=order_type)
    order.status = MagicMock(value=status)
    order.filled_price = filled_price
    order.filled_quantity = filled_quantity
    return order


class TestOrders:
    """GET /api/trade/orders"""

    def test_orders_returns_list_when_enabled(self, client):
        """executor 启用时返回订单列表"""
        mock_broker = MagicMock()
        mock_broker.get_orders.return_value = [_make_mock_order()]
        mock_state = {"broker": mock_broker}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.get("/api/trade/orders")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["order_id"] == "ORD001"
        assert data[0]["symbol"] == "600519"

    def test_orders_empty_when_no_broker(self, client):
        """broker 为 None 时返回空列表"""
        mock_state = {"broker": None}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.get("/api/trade/orders")
        assert resp.status_code == 200
        assert resp.json() == []


class TestHistory:
    """GET /api/trade/history"""

    def test_history_returns_records(self, client):
        """返回成交历史记录"""
        mock_executor = MagicMock()
        mock_executor.get_trade_history.return_value = [
            {"trade_id": "T001", "order_id": "ORD001", "symbol": "600519",
             "side": "buy", "price": 1688.0, "quantity": 100, "amount": 168800.0,
             "commission": 5.0, "stamp_tax": 0.0, "net_amount": 168805.0,
             "strategy": "均线突破", "reason": "MACD金叉", "created_at": "2026-07-29 10:00:00"},
        ]
        mock_state = {"executor": mock_executor}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.get("/api/trade/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trade_id"] == "T001"
        assert data[0]["symbol"] == "600519"
        assert data[0]["amount"] == 168800.0


class TestBuy:
    """POST /api/trade/buy"""

    def test_buy_disabled_returns_503(self, client):
        """executor 未启用时返回 503"""
        with patch("web.routes.trade._HAS_EXECUTOR", False):
            resp = client.post("/api/trade/buy", json={
                "stock_code": "600519", "amount": 100, "price": 1688.0,
            })
        assert resp.status_code == 503
        assert "交易功能未启用" in resp.json()["error"]

    def test_buy_with_executor_returns_order(self, client):
        """executor 启用时返回订单响应"""
        mock_order = _make_mock_order(order_id="ORD002", side="buy", status="filled")
        mock_executor = MagicMock()
        mock_executor.execute_signal.return_value = mock_order
        mock_state = {"executor": mock_executor}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.post("/api/trade/buy", json={
                "stock_code": "600519", "amount": 100, "price": 1688.0, "strategy": "突破",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["order_id"] == "ORD002"
        assert data["symbol"] == "600519"
        assert data["side"] == "buy"
        assert data["status"] == "filled"
        # executor.execute_signal 应被调用一次
        mock_executor.execute_signal.assert_called_once()

    def test_buy_rejected_returns_empty_order(self, client):
        """订单被拒(executor 返回 None)时返回 rejected 状态"""
        mock_executor = MagicMock()
        mock_executor.execute_signal.return_value = None
        mock_state = {"executor": mock_executor}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.post("/api/trade/buy", json={
                "stock_code": "600519", "amount": 100, "price": 0.0,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["order_id"] == ""


class TestSell:
    """POST /api/trade/sell"""

    def test_sell_disabled_returns_503(self, client):
        """executor 未启用时返回 503"""
        with patch("web.routes.trade._HAS_EXECUTOR", False):
            resp = client.post("/api/trade/sell", json={
                "stock_code": "600519", "amount": 100, "price": 1700.0,
            })
        assert resp.status_code == 503


class TestCancelEndpointRemoved:
    """撤单端点已下线（2026-10-26 功能移除，非测试规避）

    移除原因：SimulatedBroker.buy/sell 在同一次加锁内完成"建单→判定→置终态"，
    订单从不进入 PENDING，cancel_order 恒返回 False —— 撤单是与实现不符的 dead path，
    前端按钮点了必然失败。与其保留永远失败的按钮，选择下线端点与 UI 入口。
    用例随功能移除而改写为"端点确已下线"的断言，数量不减（3 → 3）。
    """

    def test_cancel_route_no_longer_exists(self, client):
        """POST /api/trade/cancel 应返回 404（路由已移除）"""
        with patch("web.routes.trade._HAS_EXECUTOR", True):
            resp = client.post("/api/trade/cancel", json={"order_id": "ORD001"})
        assert resp.status_code == 404

    def test_cancel_not_declared_in_openapi(self, client):
        """OpenAPI 文档中不应再声明撤单端点"""
        spec = client.get("/openapi.json").json()
        assert "/api/trade/cancel" not in spec["paths"]
        assert "/api/trade/buy" in spec["paths"], "删除撤单不得误伤买入端点"

    def test_orders_endpoint_unaffected(self, client):
        """回归保护：/orders 仍可用（仅返回终态订单）"""
        mock_order = _make_mock_order(order_id="ORD_TERM", side="buy", status="filled")
        mock_broker = MagicMock()
        mock_broker.get_orders.return_value = [mock_order]
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value={"broker": mock_broker}):
            resp = client.get("/api/trade/orders")
        assert resp.status_code == 200
        assert resp.json()[0]["status"] == "filled"


class TestMarketOrderPriceResolution:
    """市价单价格解析（2026-10-26 P0 修复回归）

    缺陷：前端「委托价格留空」提交 price=0 表示市价单，后端此前把 0 原样下传，
    broker 以 0 元成交。修复后市价单先解析最新价；解析不到则保持 0，
    交由风控/券商层拒绝，绝不产生 0 价成交。
    """

    def test_market_order_resolves_latest_price(self, client):
        """price=0 时应以实时最新价构造 Signal"""
        mock_order = _make_mock_order(order_id="ORD_MKT", side="buy", status="filled")
        mock_executor = MagicMock()
        mock_executor.execute_signal.return_value = mock_order
        mock_ds = MagicMock()
        mock_ds.get_realtime_quote.return_value = MagicMock(price=1700.0)
        mock_state = {"executor": mock_executor, "data_source": mock_ds}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.post("/api/trade/buy", json={
                "stock_code": "600519", "amount": 100, "price": 0.0,
            })
        assert resp.status_code == 200
        signal = mock_executor.execute_signal.call_args[0][0]
        assert signal.price == 1700.0, "市价单必须解析为最新价，而不是以 0 价下单"
        assert signal.order_type.value == "market"

    def test_market_order_without_price_source_stays_zero(self, client):
        """数据源不可用时不得编造价格，保持 0 交由风控拒绝"""
        mock_executor = MagicMock()
        mock_executor.execute_signal.return_value = None
        mock_state = {"executor": mock_executor, "data_source": None}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.post("/api/trade/buy", json={
                "stock_code": "600519", "amount": 100, "price": 0.0,
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        signal = mock_executor.execute_signal.call_args[0][0]
        assert signal.price == 0.0

    def test_market_order_rejects_absurd_price_from_source(self, client):
        """数据源返回异常价格(0/inf)时不得采信"""
        mock_executor = MagicMock()
        mock_executor.execute_signal.return_value = None
        mock_ds = MagicMock()
        mock_ds.get_realtime_quote.return_value = MagicMock(price=0.0)
        mock_state = {"executor": mock_executor, "data_source": mock_ds}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            client.post("/api/trade/buy", json={"stock_code": "600519", "amount": 100, "price": 0.0})
        assert mock_executor.execute_signal.call_args[0][0].price == 0.0

    def test_limit_order_price_not_overridden(self, client):
        """限价单价格不得被实时价覆盖"""
        mock_order = _make_mock_order(order_id="ORD_LMT", side="buy", status="filled")
        mock_executor = MagicMock()
        mock_executor.execute_signal.return_value = mock_order
        mock_ds = MagicMock()
        mock_ds.get_realtime_quote.return_value = MagicMock(price=9999.0)
        mock_state = {"executor": mock_executor, "data_source": mock_ds}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            client.post("/api/trade/buy", json={"stock_code": "600519", "amount": 100, "price": 1688.0})
        signal = mock_executor.execute_signal.call_args[0][0]
        assert signal.price == 1688.0
        assert signal.order_type.value == "limit"

    def test_price_above_upper_bound_rejected_by_validation(self, client):
        """超过价格上界的请求应被 422 拒绝（挡 inf/NaN 与异常报价）"""
        with patch("web.routes.trade._HAS_EXECUTOR", True):
            resp = client.post("/api/trade/buy", json={
                "stock_code": "600519", "amount": 100, "price": 1000001.0,
            })
        assert resp.status_code == 422


class TestHistoryLimitBounds:
    """成交历史分页边界（2026-10-26 加固）

    缺陷：`limit: int = 50` 无上下界，?limit=10000000 会把整表读进内存并整段序列化。
    """

    @pytest.mark.parametrize("bad_limit", [0, -1, 1001, 10_000_000])
    def test_rejects_out_of_range_limit(self, client, bad_limit):
        with patch("web.routes.trade._HAS_EXECUTOR", True):
            resp = client.get("/api/trade/history", params={"limit": bad_limit})
        assert resp.status_code == 422

    def test_accepts_limit_within_range(self, client):
        mock_executor = MagicMock()
        mock_executor.get_trade_history.return_value = []
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value={"executor": mock_executor}):
            resp = client.get("/api/trade/history", params={"limit": 1000})
        assert resp.status_code == 200

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


class TestCancel:
    """POST /api/trade/cancel"""

    def test_cancel_disabled_returns_failure(self, client):
        """executor 未启用时返回 success=False"""
        with patch("web.routes.trade._HAS_EXECUTOR", False):
            resp = client.post("/api/trade/cancel", json={"order_id": "ORD001"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "交易功能未启用" in data["message"]

    def test_cancel_success(self, client):
        """撤单成功返回 success=True"""
        mock_broker = MagicMock()
        mock_broker.cancel_order.return_value = True
        mock_state = {"broker": mock_broker}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.post("/api/trade/cancel", json={"order_id": "ORD001"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "撤单成功" in resp.json()["message"]
        mock_broker.cancel_order.assert_called_once_with("ORD001")

    def test_cancel_not_found(self, client):
        """订单不存在时返回 success=False"""
        mock_broker = MagicMock()
        mock_broker.cancel_order.return_value = False
        mock_state = {"broker": mock_broker}
        with patch("web.routes.trade._HAS_EXECUTOR", True), \
             patch("web.routes.trade._get_state", return_value=mock_state):
            resp = client.post("/api/trade/cancel", json={"order_id": "ORD999"})
        assert resp.status_code == 200
        assert resp.json()["success"] is False

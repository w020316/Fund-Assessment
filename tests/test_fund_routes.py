"""基金模块路由单元测试

验证 web/routes/fund.py 的端点:
- GET    /api/fund/positions          基金持仓(含实时净值与盈亏)
- POST   /api/fund/positions          保存基金持仓(全量覆盖)
- DELETE /api/fund/positions/{code}   删除指定基金持仓
- GET    /api/fund/advice             基金建议(规则引擎)
- GET    /api/fund/search             基金搜索(东方财富)
- GET    /api/fund/realtime           基金实时行情聚合
- GET    /api/fund/history            基金历史净值

通过 mock 文件 IO、data_source_v2 与 fund_advisor,避免真实网络与文件副作用。
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


# 模拟持仓数据
_MOCK_POSITIONS = [
    {"fund_code": "110022", "fund_name": "易方达消费", "shares": 1000.0,
     "cost_nav": 2.5, "buy_date": "2026-01-15", "note": "消费主题"},
]

_MOCK_QUOTES = [
    {"code": "110022", "name": "易方达消费行业股票", "nav": 2.724,
     "change_pct": 1.5, "update_time": "2026-07-29 15:00"},
]

_MOCK_ADVICE = {
    "positions": [
        {"fund_code": "110022", "fund_name": "易方达消费",
         "advice": {"signal": "hold", "action": "继续持有"}},
    ],
    "summary": "组合整体稳健",
}

_MOCK_HISTORY = [
    {"date": "2026-07-28", "nav": 2.71, "acc_nav": 2.71, "change_pct": 0.5},
    {"date": "2026-07-29", "nav": 2.724, "acc_nav": 2.724, "change_pct": 0.52},
]


class TestGetPositions:
    """GET /api/fund/positions"""

    def test_get_positions_with_quotes(self, client):
        """返回持仓并含实时净值与盈亏"""
        with patch("web.routes.fund._load_positions", return_value=_MOCK_POSITIONS), \
             patch("web.routes.fund.get_fund_realtime_tencent", return_value=_MOCK_QUOTES):
            resp = client.get("/api/fund/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["fund_code"] == "110022"
        assert pos["current_nav"] == 2.724
        assert pos["cost_nav"] == 2.5
        # pnl_pct = (2.724 - 2.5) / 2.5 * 100 = 8.96
        assert pos["pnl_pct"] == 8.96
        assert pos["market_value"] == 2724.0
        assert data["summary"]["count"] == 1

    def test_get_positions_empty(self, client):
        """无持仓时返回空列表与零值汇总"""
        with patch("web.routes.fund._load_positions", return_value=[]):
            resp = client.get("/api/fund/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["positions"] == []
        assert data["summary"]["count"] == 0
        assert data["summary"]["total_market_value"] == 0.0


class TestSavePositions:
    """POST /api/fund/positions"""

    def test_save_positions_calls_save(self, client):
        """保存持仓调用 _save_positions 并返回成功"""
        # P1 修复(2026-07-30):_save_positions 现返回 bool,mock 需显式 return_value=True
        with patch("web.routes.fund._save_positions", return_value=True) as mock_save:
            resp = client.post("/api/fund/positions", json={
                "positions": [
                    {"fund_code": "161725", "fund_name": "招商白酒",
                     "shares": 5000.0, "cost_nav": 0.85, "buy_date": "2026-02-20", "note": ""}
                ]
            })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "已保存 1 只基金持仓" in resp.json()["message"]
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert saved[0]["fund_code"] == "161725"


class TestDeletePosition:
    """DELETE /api/fund/positions/{fund_code}"""

    def test_delete_existing_position(self, client):
        """删除已存在的持仓返回成功"""
        # P1 修复(2026-07-30):_save_positions 现返回 bool,mock 需显式 return_value=True
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_POSITIONS)), \
             patch("web.routes.fund._save_positions", return_value=True) as mock_save:
            resp = client.delete("/api/fund/positions/110022")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_save.assert_called_once()
        # 保存的列表不应包含被删除的代码
        saved = mock_save.call_args[0][0]
        assert all(p["fund_code"] != "110022" for p in saved)

    def test_delete_nonexistent_position(self, client):
        """删除不存在的持仓返回 success=False"""
        with patch("web.routes.fund._load_positions", return_value=list(_MOCK_POSITIONS)), \
             patch("web.routes.fund._save_positions"):
            resp = client.delete("/api/fund/positions/999999")
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_save_positions_failure_returns_false(self, client):
        """P1 回归:_save_positions 失败时返回 success=False(避免静默吞异常误导用户)"""
        with patch("web.routes.fund._save_positions", return_value=False):
            resp = client.post("/api/fund/positions", json={
                "positions": [
                    {"fund_code": "161725", "fund_name": "招商白酒",
                     "shares": 5000.0, "cost_nav": 0.85, "buy_date": "2026-02-20", "note": ""}
                ]
            })
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "保存失败" in resp.json()["message"]


class TestFundAdvice:
    """GET /api/fund/advice"""

    def test_advice_returns_result(self, client):
        """基金建议返回规则引擎结果"""
        with patch("web.routes.fund._load_positions", return_value=_MOCK_POSITIONS), \
             patch("src.analysis.fund_advisor.generate_fund_advice", return_value=_MOCK_ADVICE):
            resp = client.get("/api/fund/advice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "组合整体稳健"
        assert data["positions"][0]["advice"]["signal"] == "hold"


class TestFundRealtime:
    """GET /api/fund/realtime"""

    def test_realtime_returns_quotes(self, client):
        """返回基金实时行情列表"""
        with patch("web.routes.fund.get_fund_realtime_tencent", return_value=_MOCK_QUOTES):
            resp = client.get("/api/fund/realtime", params={"codes": "110022"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "110022"
        assert data["data"][0]["nav"] == 2.724

    def test_realtime_empty_codes_returns_empty(self, client):
        """空 codes 返回空列表"""
        resp = client.get("/api/fund/realtime", params={"codes": ""})
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestFundHistory:
    """GET /api/fund/history"""

    def test_history_returns_data(self, client):
        """返回历史净值数据"""
        with patch("web.routes.fund.get_fund_history_tencent", return_value=_MOCK_HISTORY):
            resp = client.get("/api/fund/history", params={"code": "110022", "period": "1y"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "110022"
        assert data["period"] == "1y"
        assert len(data["data"]) == 2
        assert data["data"][-1]["nav"] == 2.724


class TestFundSearch:
    """GET /api/fund/search"""

    def test_search_returns_results(self, client):
        """基金搜索解析 JSONP 返回结果"""
        mock_response = MagicMock()
        mock_response.text = 'jQuery({"Datas": [{"CODE": "110022", "NAME": "易方达消费", "FundBaseInfo": {"FTYPE": "股票型"}}]})'
        with patch("web.routes.fund.requests.get", return_value=mock_response):
            resp = client.get("/api/fund/search", params={"q": "易方达"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "易方达"
        assert len(data["data"]) == 1
        assert data["data"][0]["code"] == "110022"
        assert data["data"][0]["name"] == "易方达消费"
        assert data["data"][0]["type"] == "股票型"

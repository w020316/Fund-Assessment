"""策略路由单元测试

验证 web/routes/strategy.py 的端点:
- GET  /api/strategy/list          策略列表(策略信息)
- POST /api/strategy/analyze       策略详情分析(含雷达图五维评分)
- GET  /api/strategy/scan/new_high 新高扫描
- GET  /api/strategy/scan/limit_up 涨停板扫描
- GET  /api/strategy/scan/cb       可转债扫描
- POST /api/strategy/backtest      回测

通过 mock src.core.data_source_v2 与策略类,避免真实网络与重型计算。
策略分析返回的 result.scores 包含 technical/capital/fundamental/news/sentiment 五维,
对应前端雷达图数据,本测试单独验证其完整性。
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
    """确保 ADMIN_TOKEN 未配置(开发模式放行),避免鉴权干扰

    conftest.py 已全局 setdefault APP_ENV=dev,此处仅需清除 ADMIN_TOKEN。
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


# 模拟轻量分析依赖的行情/财务/K线/资金流数据
_MOCK_QUOTE = [{"code": "600519", "name": "贵州茅台", "price": 1688.0, "change_pct": 1.5}]
_MOCK_FINANCIAL = {"roe": 31.2, "pe_ttm": 28.5}
_MOCK_KLINE = [{"close": 1680.0}, {"close": 1685.0}, {"close": 1688.0},
               {"close": 1690.0}, {"close": 1695.0}]  # 5 根,触发 trend 计算
_MOCK_FLOW = {"main_inflow_pct": 5.2}


def _patch_lightweight_ds2():
    """统一 patch 轻量分析所依赖的 ds2 函数(返回上下文管理器元组)"""
    return [
        patch("web.routes.strategy.ds2.get_realtime_quote_tencent", return_value=_MOCK_QUOTE),
        patch("web.routes.strategy.ds2.get_financial_snapshot", return_value=_MOCK_FINANCIAL),
        patch("web.routes.strategy.ds2.get_kline_mootdx", return_value=_MOCK_KLINE),
        patch("web.routes.strategy.ds2.get_capital_flow_detail", return_value=_MOCK_FLOW),
    ]


class TestStrategyList:
    """GET /api/strategy/list 策略列表"""

    def test_list_returns_six_strategies(self, client):
        """返回 6 个策略(comprehensive/trading_quant/bspro_quant/limit_up/cb_t0/new_high)"""
        resp = client.get("/api/strategy/list")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 6
        names = [s["name"] for s in data]
        assert "comprehensive" in names
        assert "trading_quant" in names
        assert "bspro_quant" in names
        assert "limit_up" in names
        assert "cb_t0" in names
        assert "new_high" in names

    def test_list_item_structure(self, client):
        """每个策略包含 name/display_name/description/enabled 字段"""
        resp = client.get("/api/strategy/list")
        for s in resp.json():
            assert "name" in s and isinstance(s["name"], str) and s["name"]
            assert "display_name" in s and isinstance(s["display_name"], str)
            assert "description" in s and isinstance(s["description"], str)
            assert s["enabled"] is True


class TestStrategyAnalyze:
    """POST /api/strategy/analyze 策略详情 + 雷达图数据"""

    def test_analyze_lightweight_returns_radar_scores(self, client):
        """轻量分析返回五维雷达图评分(technical/capital/fundamental/news/sentiment)"""
        patches = _patch_lightweight_ds2()
        for p in patches:
            p.start()
        try:
            with patch("web.routes.strategy._HAS_STRATEGIES", False):
                resp = client.post("/api/strategy/analyze", json={
                    "stock_code": "600519", "strategy_type": "comprehensive"
                })
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200
        result = resp.json()["result"]
        # 雷达图五维评分必须齐全
        scores = result["scores"]
        expected_dims = {"technical", "capital", "fundamental", "news", "sentiment"}
        assert set(scores.keys()) == expected_dims
        # 每个评分应为 0-100 之间的数值
        for dim, val in scores.items():
            assert isinstance(val, (int, float)), f"{dim} 评分应为数值"
            assert 0 <= val <= 100, f"{dim} 评分 {val} 超出 [0,100]"
        # total_score 应为五维平均
        expected_total = sum(scores.values()) / len(scores)
        assert abs(result["total_score"] - round(expected_total, 1)) < 0.5
        assert result["available"] is True
        assert result["source"] == "lightweight"

    def test_analyze_lightweight_signal_thresholds(self, client):
        """轻量分析根据 total_score 给出 BUY/WATCH/HOLD/SELL 信号"""
        patches = _patch_lightweight_ds2()
        for p in patches:
            p.start()
        try:
            with patch("web.routes.strategy._HAS_STRATEGIES", False):
                resp = client.post("/api/strategy/analyze", json={
                    "stock_code": "600519", "strategy_type": "comprehensive"
                })
        finally:
            for p in patches:
                p.stop()
        signal = resp.json()["result"]["signal"]
        assert signal in {"BUY", "WATCH", "HOLD", "SELL"}

    def test_analyze_fallback_on_ds2_error(self, client):
        """数据源异常时返回 available=False 的降级响应(不冒充真实评分)"""
        with patch("web.routes.strategy._HAS_STRATEGIES", False), \
             patch("web.routes.strategy.ds2.get_realtime_quote_tencent",
                   side_effect=RuntimeError("network down")):
            resp = client.post("/api/strategy/analyze", json={
                "stock_code": "600519", "strategy_type": "comprehensive"
            })
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["available"] is False
        assert result["total_score"] == 0.0
        assert result["signal"] == "N/A"
        # 五维评分均为 0(显式空值)
        for dim in ("technical", "capital", "fundamental", "news", "sentiment"):
            assert result["scores"][dim] == 0.0

    def test_analyze_normalize_path_with_strategies_enabled(self, client):
        """_HAS_STRATEGIES=True 时调用策略类并通过 _normalize_analysis 归一化

        模拟 AStockAnalyst.comprehensive_analysis 返回 composite_score 结构,
        验证 _normalize_analysis 将其转换为雷达图 scores 结构。
        """
        raw_result = {
            "stock_code": "600519",
            "strategy_type": "comprehensive",
            "composite_score": 75,
            "fundamental": {"score": 80, "roe": 31.2},
            "technical": {"score": 70, "trend": "up"},
            "industry": {"score": 65},
        }
        mock_analyst = MagicMock()
        mock_analyst.comprehensive_analysis.return_value = raw_result
        with patch("web.routes.strategy._HAS_STRATEGIES", True), \
             patch("web.routes.strategy.AStockAnalyst", return_value=mock_analyst):
            resp = client.post("/api/strategy/analyze", json={
                "stock_code": "600519", "strategy_type": "comprehensive"
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock_code"] == "600519"
        assert data["strategy_type"] == "comprehensive"
        scores = data["result"]["scores"]
        # 归一化后应包含五维评分
        assert set(scores.keys()) == {"technical", "capital", "fundamental", "news", "sentiment"}
        assert scores["fundamental"] == 80
        assert scores["technical"] == 70
        assert scores["capital"] == 65
        # total_score >= 70 -> BUY
        assert data["result"]["signal"] == "BUY"

    def test_analyze_strategy_exception_returns_fallback(self, client):
        """策略类抛异常时返回 available=False 的降级响应"""
        mock_analyst = MagicMock()
        mock_analyst.comprehensive_analysis.side_effect = RuntimeError("strategy crashed")
        with patch("web.routes.strategy._HAS_STRATEGIES", True), \
             patch("web.routes.strategy.AStockAnalyst", return_value=mock_analyst):
            resp = client.post("/api/strategy/analyze", json={
                "stock_code": "600519", "strategy_type": "comprehensive"
            })
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["available"] is False
        assert result["total_score"] == 0.0


class TestScanEndpoints:
    """扫描类端点"""

    def test_scan_new_high_lightweight_filters_by_change_pct(self, client):
        """_HAS_STRATEGIES=False 时走轻量路径,仅返回 change_pct>=3 的标的"""
        mock_ranking = [
            {"code": "600519", "name": "贵州茅台", "change_pct": 5.5,
             "volume": 1000000, "amount": 50000000},
            {"code": "000858", "name": "五粮液", "change_pct": 2.0,
             "volume": 800000, "amount": 30000000},  # 不满足 >=3,应被过滤
        ]
        with patch("web.routes.strategy._HAS_STRATEGIES", False), \
             patch("web.routes.strategy.ds2.get_stock_ranking_em",
                   return_value=mock_ranking):
            resp = client.get("/api/strategy/scan/new_high")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_code"] == "600519"
        assert data[0]["change_pct"] == 5.5

    def test_scan_limit_up_disabled_returns_empty(self, client):
        """_HAS_STRATEGIES=False 时涨停扫描返回空列表"""
        with patch("web.routes.strategy._HAS_STRATEGIES", False):
            resp = client.get("/api/strategy/scan/limit_up")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_scan_cb_disabled_returns_empty(self, client):
        """_HAS_STRATEGIES=False 时可转债扫描返回空列表"""
        with patch("web.routes.strategy._HAS_STRATEGIES", False):
            resp = client.get("/api/strategy/scan/cb")
        assert resp.status_code == 200
        assert resp.json() == []


class TestBacktest:
    """POST /api/strategy/backtest 回测(需鉴权)"""

    def test_backtest_disabled_returns_zeros(self, client):
        """_HAS_STRATEGIES=False 时回测返回全零结果"""
        with patch("web.routes.strategy._HAS_STRATEGIES", False):
            resp = client.post("/api/strategy/backtest", json={
                "strategy": "bspro_quant", "stock_code": ""
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy"] == "bspro_quant"
        assert data["total_return"] == 0
        assert data["sharpe_ratio"] == 0
        assert data["win_rate"] == 0
        assert data["trades"] == 0

    def test_backtest_enabled_returns_parsed_result(self, client):
        """_HAS_STRATEGIES=True 时回测返回 BSProQuant 解析后的结果"""
        mock_quant = MagicMock()
        mock_quant.backtest_strategy.return_value = {
            "total_return": 0.25,
            "annualized_return": 0.15,
            "max_drawdown": -0.08,
            "sharpe_ratio": 1.2,
            "win_rate": 0.6,
            "trades": 42,
        }
        with patch("web.routes.strategy._HAS_STRATEGIES", True), \
             patch("web.routes.strategy.BSProQuant", return_value=mock_quant):
            resp = client.post("/api/strategy/backtest", json={
                "strategy": "bspro_quant", "stock_code": ""
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_return"] == 0.25
        assert data["sharpe_ratio"] == 1.2
        assert data["win_rate"] == 0.6
        assert data["trades"] == 42

    def test_backtest_exception_returns_503(self, client):
        """_HAS_STRATEGIES=True 但回测异常时返回 503(不静默吞异常)"""
        mock_quant = MagicMock()
        mock_quant.backtest_strategy.side_effect = RuntimeError("data missing")
        with patch("web.routes.strategy._HAS_STRATEGIES", True), \
             patch("web.routes.strategy.BSProQuant", return_value=mock_quant):
            resp = client.post("/api/strategy/backtest", json={
                "strategy": "bspro_quant", "stock_code": ""
            })
        assert resp.status_code == 503
        assert "回测执行失败" in resp.json()["detail"]


class TestAuth:
    """鉴权场景(analyze 与 backtest 端点依赖 require_admin)"""

    def test_analyze_rejected_in_production_without_token(self, client, monkeypatch):
        """生产环境未配置 ADMIN_TOKEN 时 analyze 返回 500(fail-closed)"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        resp = client.post("/api/strategy/analyze", json={
            "stock_code": "600519", "strategy_type": "comprehensive"
        })
        assert resp.status_code == 500

    def test_analyze_rejected_with_wrong_token(self, client, monkeypatch):
        """配置 ADMIN_TOKEN 但请求携带错误 token 时返回 401"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ADMIN_TOKEN", "correct-secret-token")
        resp = client.post(
            "/api/strategy/analyze",
            json={"stock_code": "600519", "strategy_type": "comprehensive"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_analyze_passes_with_correct_token(self, client, monkeypatch):
        """配置 ADMIN_TOKEN 且请求携带正确 token 时放行"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ADMIN_TOKEN", "correct-secret-token")
        patches = _patch_lightweight_ds2()
        for p in patches:
            p.start()
        try:
            with patch("web.routes.strategy._HAS_STRATEGIES", False):
                resp = client.post(
                    "/api/strategy/analyze",
                    json={"stock_code": "600519", "strategy_type": "comprehensive"},
                    headers={"Authorization": "Bearer correct-secret-token"},
                )
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200

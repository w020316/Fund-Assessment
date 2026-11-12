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

    def test_backtest_disabled_returns_unavailable(self, client):
        with patch("web.routes.strategy._HAS_STRATEGIES", False):
            resp = client.post("/api/strategy/backtest", json={
                "strategy": "bspro_quant", "stock_code": ""
            })
        assert resp.status_code == 503
        assert "未执行回测" in resp.json()["detail"]

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
        assert "未来函数偏差" in data["warning"]
        assert data["partial"] is False

    def test_backtest_preserves_partial_warning(self, client):
        mock_quant = MagicMock()
        mock_quant.backtest_strategy.return_value = {
            "total_return": 1, "annualized_return": 2, "max_drawdown": -3,
            "sharpe_ratio": 0.4, "win_rate": 50, "trades": 2,
            "warning": "假设性回测：部分股票因子未完成", "partial": True,
        }
        with patch("web.routes.strategy._HAS_STRATEGIES", True), \
             patch("web.routes.strategy.BSProQuant", return_value=mock_quant):
            resp = client.post("/api/strategy/backtest", json={})
        assert resp.status_code == 200
        assert resp.json()["partial"] is True
        assert resp.json()["warning"] == "假设性回测：部分股票因子未完成"

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


class TestFactorResearch:
    endpoint = "/api/strategy/factor-research"

    def test_forwards_parameters_and_preserves_report(self, client):
        report = {"available": False, "message": "样本不足", "notes": ["未扣成本"],
                  "summary": {"test": {"rank_ic_mean": None}}}
        with patch("src.core.factor_research.research_alpha144", return_value=report) as compute:
            resp = client.post(self.endpoint, json={"csv_data": "csv", "direction": -1})
        assert resp.status_code == 200
        assert resp.json() == {"data": report}
        assert compute.call_args.kwargs == {
            "csv_data": "csv", "window": 20, "horizon": 20, "groups": 5,
            "min_listing_days": 120, "min_average_amount": 5_000_000,
            "direction": -1, "train_ratio": 0.7,
        }

    @pytest.mark.parametrize("fields", [
        {}, {"csv_data": ""}, {"window": 1}, {"window": 121}, {"window": True},
        {"horizon": 0}, {"groups": 11}, {"direction": 0}, {"train_ratio": 0.95},
        {"min_average_amount": -1}, {"min_listing_days": 1001},
        {"csv_data": "x" * 2_000_001},
    ])
    def test_rejects_invalid_request(self, client, fields):
        payload = {"csv_data": "csv", **fields} if fields else {}
        with patch("src.core.factor_research.research_alpha144") as compute:
            resp = client.post(self.endpoint, json=payload)
        assert resp.status_code == 422
        compute.assert_not_called()

    def test_csv_error_is_actionable(self, client):
        with patch("src.core.factor_research.research_alpha144", side_effect=ValueError("缺少必需字段 amount")):
            resp = client.post(self.endpoint, json={"csv_data": "date,stock_code"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "缺少必需字段 amount"

    def test_timeout_returns_503(self, client, monkeypatch):
        """F-2(2026-11-12): 计算挂起时 wait_for 兜底 503, 不无限占用单飞锁。"""
        from web.routes import strategy as strategy_module

        def _hang(*_a, **_kw):
            import time
            time.sleep(5)

        monkeypatch.setattr(strategy_module, "BACKTEST_TIMEOUT_SECONDS", 0.05)
        with patch("src.core.factor_research.research_alpha144", side_effect=_hang):
            resp = client.post(self.endpoint, json={"csv_data": "csv"})
        assert resp.status_code == 503
        assert "超时" in resp.json()["detail"]

    def test_rate_limit(self, client):
        with patch("src.core.factor_research.research_alpha144", return_value={"available": False}):
            responses = [client.post(self.endpoint, json={"csv_data": "csv"}) for _ in range(4)]
        assert [resp.status_code for resp in responses] == [200, 200, 200, 429]

    def test_requires_auth_and_accepts_valid_token(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ADMIN_TOKEN", "factor-research-test-token")
        monkeypatch.delenv("ADMIN_TOKENS", raising=False)
        with patch("src.core.factor_research.research_alpha144", return_value={"available": False}) as compute:
            assert client.post(self.endpoint, json={"csv_data": "csv"}).status_code == 401
            compute.assert_not_called()
            resp = client.post(self.endpoint, json={"csv_data": "csv"},
                               headers={"Authorization": "Bearer factor-research-test-token"})
        assert resp.status_code == 200
        compute.assert_called_once()

    def test_production_without_token_fails_closed(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        with patch("src.utils.auth._get_admin_tokens", return_value=[]), \
             patch("src.core.factor_research.research_alpha144") as compute:
            resp = client.post(self.endpoint, json={"csv_data": "csv"})
        assert resp.status_code == 503
        compute.assert_not_called()

    def test_shares_backtest_guard(self, client):
        from web.routes.strategy import _single_backtest_guard
        guard = _single_backtest_guard()
        next(guard)
        try:
            with patch("src.core.factor_research.research_alpha144") as compute:
                resp = client.post(self.endpoint, json={"csv_data": "csv"})
            assert resp.status_code == 503
            compute.assert_not_called()
        finally:
            guard.close()

    def test_research_page_is_served(self, client):
        resp = client.get("/static/factor-research.html")
        assert resp.status_code == 200
        assert "因子研究实验室" in resp.text
        assert "不内置或自动填充模拟行情" in resp.text
        js = client.get("/static/js/factor-research.js")
        assert js.status_code == 200
        assert "innerHTML" not in js.text
        assert "localStorage.setItem" not in js.text


class TestBacktestGuard:
    """阶段五 R1:回测单飞守卫(并发时快速拒绝,而不是排队打满实例)"""

    def test_second_concurrent_backtest_rejected(self):
        from fastapi import HTTPException
        from web.routes import strategy as mod

        first = mod._single_backtest_guard()
        next(first)
        try:
            second = mod._single_backtest_guard()
            with pytest.raises(HTTPException) as exc:
                next(second)
            assert exc.value.status_code == 503
            assert "回测仍在进行" in exc.value.detail
        finally:
            first.close()

    def test_guard_released_after_first_finishes(self):
        from web.routes import strategy as mod

        first = mod._single_backtest_guard()
        next(first)
        first.close()  # 依赖退出时释放
        second = mod._single_backtest_guard()
        next(second)
        second.close()


class TestAuth:
    """鉴权场景(analyze 与 backtest 端点依赖 require_admin)"""

    def test_analyze_rejected_in_production_without_token(self, client, monkeypatch):
        """生产环境未配置 ADMIN_TOKEN 时 analyze 返回 503(fail-closed)"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        resp = client.post("/api/strategy/analyze", json={
            "stock_code": "600519", "strategy_type": "comprehensive"
        })
        assert resp.status_code == 503

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

    def test_validate_requires_admin_token(self, client, monkeypatch):
        """validate 与 analyze/backtest 同口径: 生产缺 token 返回 503, 错 token 401(2026-11-05 R-13)"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        resp = client.post("/api/strategy/validate", json={"returns": [0.01] * 120})
        assert resp.status_code == 503

        monkeypatch.setenv("ADMIN_TOKEN", "correct-secret-token")
        resp = client.post(
            "/api/strategy/validate",
            json={"returns": [0.01] * 120},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_validate_passes_with_correct_token(self, client, monkeypatch):
        """携带正确 token 时 validate 正常返回"""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ADMIN_TOKEN", "correct-secret-token")
        resp = client.post(
            "/api/strategy/validate",
            json={"returns": [0.005] * 120, "runs": 100},
            headers={"Authorization": "Bearer correct-secret-token"},
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_backtest_times_out_with_503(self, client, monkeypatch):
        """回测挂起超过上限时返回 503 超时提示, 不得无限等待(2026-11-05 R-03)"""
        import time

        import web.routes.strategy as strategy_mod

        class _SlowQuant:
            def backtest_strategy(self, factor_combo, period):
                time.sleep(1.0)
                return {}

        monkeypatch.setattr(strategy_mod, "BSProQuant", _SlowQuant)
        monkeypatch.setattr(strategy_mod, "BACKTEST_TIMEOUT_SECONDS", 0.05)
        resp = client.post("/api/strategy/backtest", json={"strategy": "bspro_quant", "stock_code": "600519"})
        assert resp.status_code == 503
        assert "超时" in resp.json()["detail"]


class TestOptimizeMaCross:
    """POST /api/strategy/optimize-ma-cross — MA 金叉参数网格优化(视频链路 STEP 15)"""

    endpoint = "/api/strategy/optimize-ma-cross"

    @pytest.fixture(autouse=True)
    def _reset_rate_limiter(self):
        """限流桶为本进程内存态,先清空避免与其它测试类的请求串桶"""
        from web.rate_limiter import limiter
        storage = getattr(limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()
        yield
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()

    @staticmethod
    def _canned_result():
        return {
            "stock_code": "600519", "start_date": "2025-01-01", "end_date": "2025-06-30",
            "grid": [
                {"short_window": 5, "long_window": 20, "total_return": 0.12, "annual_return": 0.25,
                 "max_drawdown": 0.08, "sharpe_ratio": 1.2, "information_ratio": 0.9, "trade_count": 6},
                {"short_window": 3, "long_window": 10, "total_return": 0.05, "annual_return": 0.10,
                 "max_drawdown": 0.10, "sharpe_ratio": 0.6, "information_ratio": 0.3, "trade_count": 9},
            ],
            "best": {"short_window": 5, "long_window": 20, "total_return": 0.12, "annual_return": 0.25,
                     "max_drawdown": 0.08, "sharpe_ratio": 1.2, "information_ratio": 0.9, "trade_count": 6},
        }

    def test_returns_grid_and_warning(self, client):
        canned = self._canned_result()
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            engine_cls.return_value.optimize_ma_cross.return_value = canned
            resp = client.post(self.endpoint, json={
                "stock_code": "600519", "start_date": "2025-01-01", "end_date": "2025-06-30",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == canned
        assert "历史回测不代表未来收益" in body["warning"]
        engine_cls.return_value.optimize_ma_cross.assert_called_once_with("600519", "2025-01-01", "2025-06-30")

    def test_default_dates_filled(self, client):
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            engine_cls.return_value.optimize_ma_cross.return_value = self._canned_result()
            resp = client.post(self.endpoint, json={"stock_code": "000001"})
        assert resp.status_code == 200
        args = engine_cls.return_value.optimize_ma_cross.call_args.args
        assert args[0] == "000001"
        # 默认区间:开始早于结束,约一年跨度
        assert args[1] < args[2]

    @pytest.mark.parametrize("fields", [
        {}, {"stock_code": ""}, {"stock_code": "x" * 13},
        {"stock_code": "ABC123"}, {"stock_code": "60051"}, {"stock_code": "6005191"},
        {"stock_code": "600519", "start_date": "2025/01/01"},
        {"stock_code": "600519", "end_date": "not-a-date"},
        {"stock_code": "600519", "start_date": "2025-06-01", "end_date": "2025-01-01"},
        {"stock_code": "600519", "start_date": "2025-01-01", "end_date": "2025-01-01"},
    ])
    def test_rejects_invalid_request(self, client, fields):
        payload = {"stock_code": "600519", **fields} if fields else {}
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            resp = client.post(self.endpoint, json=payload)
        assert resp.status_code == 422
        engine_cls.assert_not_called()

    def test_engine_error_is_503(self, client):
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            engine_cls.return_value.optimize_ma_cross.side_effect = RuntimeError("upstream down")
            resp = client.post(self.endpoint, json={"stock_code": "600519"})
        assert resp.status_code == 503
        assert "参数优化执行失败" in resp.json()["detail"]

    def test_custom_windows_passed_through(self, client):
        """立项 2 收口: 自定义窗口数组去重排序后透传引擎"""
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            engine_cls.return_value.optimize_ma_cross.return_value = self._canned_result()
            resp = client.post(self.endpoint, json={
                "stock_code": "600519", "short_windows": [5, 3, 5], "long_windows": [20, 10],
            })
        assert resp.status_code == 200
        kwargs = engine_cls.return_value.optimize_ma_cross.call_args.kwargs
        assert kwargs["short_windows"] == [3, 5]
        assert kwargs["long_windows"] == [10, 20]

    def test_custom_windows_empty_pair_rejected(self, client):
        """短窗口 ≥ 长窗口导致零有效组合 → 422, 引擎不被调用"""
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            resp = client.post(self.endpoint, json={
                "stock_code": "600519", "short_windows": [20], "long_windows": [10],
            })
        assert resp.status_code == 422
        assert "MA_short 必须小于 MA_long" in resp.json()["detail"]
        engine_cls.assert_not_called()

    def test_custom_windows_combo_cap_rejected(self, client):
        """有效组合数超过 2000 → 422 明确报上限"""
        shorts = list(range(2, 62))
        longs = list(range(4, 122, 2))
        assert max(longs) <= 120 and sum(1 for s in shorts for lw in longs if s < lw) > 2000
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            resp = client.post(self.endpoint, json={
                "stock_code": "600519", "short_windows": shorts, "long_windows": longs,
            })
        assert resp.status_code == 422
        assert "上限" in resp.json()["detail"]
        engine_cls.assert_not_called()

    @pytest.mark.parametrize("fields", [
        {"short_windows": [1]},
        {"short_windows": [121]},
        {"short_windows": list(range(2, 63))},
        {"long_windows": [0]},
        {"long_windows": ["a"]},
        {"short_windows": [3], "long_windows": [10, 20], "start_date": "bad-date"},
    ])
    def test_custom_windows_invalid_rejected(self, client, fields):
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            resp = client.post(self.endpoint, json={"stock_code": "600519", **fields})
        assert resp.status_code == 422
        engine_cls.assert_not_called()

    def test_rate_limit(self, client):
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            engine_cls.return_value.optimize_ma_cross.return_value = self._canned_result()
            responses = [client.post(self.endpoint, json={"stock_code": "600519"}) for _ in range(4)]
        assert [resp.status_code for resp in responses] == [200, 200, 200, 429]

    def test_requires_auth_and_accepts_valid_token(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ADMIN_TOKEN", "optimize-ma-test-token")
        monkeypatch.delenv("ADMIN_TOKENS", raising=False)
        with patch("src.core.backtest.BacktestEngine") as engine_cls:
            assert client.post(self.endpoint, json={"stock_code": "600519"}).status_code == 401
            engine_cls.assert_not_called()
            resp = client.post(self.endpoint, json={"stock_code": "600519"},
                               headers={"Authorization": "Bearer optimize-ma-test-token"})
        assert resp.status_code == 200
        engine_cls.return_value.optimize_ma_cross.assert_called_once()

    def test_shares_backtest_guard(self, client):
        from web.routes.strategy import _single_backtest_guard
        guard = _single_backtest_guard()
        next(guard)
        try:
            with patch("src.core.backtest.BacktestEngine") as engine_cls:
                resp = client.post(self.endpoint, json={"stock_code": "600519"})
            assert resp.status_code == 503
            engine_cls.assert_not_called()
        finally:
            guard.close()

    def test_optimizer_page_is_served(self, client):
        resp = client.get("/static/ma-optimizer.html")
        assert resp.status_code == 200
        assert "MA 金叉参数优化热力图" in resp.text
        assert "历史回测不代表未来收益" in resp.text
        js = client.get("/static/js/ma-optimizer.js")
        assert js.status_code == 200
        assert "innerHTML" not in js.text
        assert "localStorage.setItem" not in js.text

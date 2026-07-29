"""多智能体分析路由单元测试

验证 web/routes/agent.py 的端点:
- POST /analyze           个股深度分析
- GET  /opinions          快速多智能体观点
- GET  /debate            多空辩论
- GET  /history           决策历史
- POST /quick_analysis    快速分析
- POST /multi_analyze     多模式分析
- POST /portfolio_advice  持仓组合建议
- GET  /market_outlook    市场展望
- POST /fund_analyze      基金多智能体分析

通过 mock src.core.ai_service 与 src.analysis.multi_agent_fund 的函数,避免真实 LLM 调用。
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_decision_history(monkeypatch):
    """每个测试前清空 agent 路由的决策历史,避免状态泄漏

    P1 修复(2026-07-29)配套:agent.py 5 个 LLM 端点已加 Depends(require_admin),
    生产环境 fail-closed(未配置 ADMIN_TOKEN 时抛 500),测试环境设为 dev 模式放行。
    """
    # P1 配套:测试环境设为 dev 模式,放行未配置 ADMIN_TOKEN 的鉴权
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from web.routes import agent
    agent._decision_history.clear()
    yield
    agent._decision_history.clear()


# 模拟 AI 分析返回结果
_MOCK_ANALYZE_RESULT = {
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "decision": "BUY",
    "confidence": 0.85,
    "debate_result": {
        "topic": "600519多空辩论",
        "bull_arguments": ["品牌护城河深", "现金流强劲"],
        "bear_arguments": ["估值偏高"],
        "bull_score": 75,
        "bear_score": 25,
        "consensus": "BULLISH",
        "confidence": 0.75,
    },
    "agent_opinions": [
        {"agent": "技术分析师", "opinion": "MACD金叉,短期看涨"},
        {"agent": "基本面分析师", "opinion": "ROE优秀"},
    ],
}

_MOCK_QUICK_RESULT = {
    "stock_code": "600519",
    "agent_opinions": [
        {"agent": "技术分析师", "opinion": "短期超买"},
    ],
}

_MOCK_PORTFOLIO_RESULT = {
    "summary": "组合整体稳健",
    "suggestions": [{"symbol": "600519", "action": "hold"}],
}

_MOCK_OUTLOOK_RESULT = {
    "market_trend": "震荡上行",
    "risk_level": "中等",
    "suggestions": ["关注消费板块"],
}

_MOCK_FUND_ANALYZE_RESULT = {
    "fund_code": "110022",
    "fund_name": "易方达消费",
    "decision": "HOLD",
    "agents": {"news": "中性", "fund": "稳健"},
}


class TestAnalyze:
    """POST /api/agent/analyze"""

    def test_analyze_returns_result(self, client):
        """深度分析返回完整结果"""
        with patch("web.routes.agent.analyze_stock", return_value=_MOCK_ANALYZE_RESULT):
            resp = client.post("/api/agent/analyze", json={"stock_code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock_code"] == "600519"
        assert data["decision"] == "BUY"
        assert data["confidence"] == 0.85

    def test_analyze_appends_to_history(self, client):
        """分析结果会写入决策历史"""
        with patch("web.routes.agent.analyze_stock", return_value=_MOCK_ANALYZE_RESULT):
            client.post("/api/agent/analyze", json={"stock_code": "600519"})
        # 通过 history 端点验证历史记录
        resp = client.get("/api/agent/history")
        assert resp.json()["count"] == 1


class TestOpinions:
    """GET /api/agent/opinions"""

    def test_opinions_returns_agent_opinions(self, client):
        """返回多智能体观点列表"""
        with patch("web.routes.agent.ai_quick_analysis", return_value=_MOCK_QUICK_RESULT):
            resp = client.get("/api/agent/opinions", params={"code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock_code"] == "600519"
        assert len(data["opinions"]) == 1
        assert data["opinions"][0]["agent"] == "技术分析师"

    def test_opinions_empty_when_no_agent_opinions(self, client):
        """无 agent_opinions 字段时返回空列表"""
        with patch("web.routes.agent.ai_quick_analysis", return_value={"stock_code": "600519"}):
            resp = client.get("/api/agent/opinions", params={"code": "600519"})
        assert resp.json()["opinions"] == []


class TestDebate:
    """GET /api/agent/debate"""

    def test_debate_returns_debate_result(self, client):
        """返回多空辩论结果"""
        with patch("web.routes.agent.analyze_stock", return_value=_MOCK_ANALYZE_RESULT):
            resp = client.get("/api/agent/debate", params={"code": "600519"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["consensus"] == "BULLISH"
        assert data["bull_score"] == 75
        assert len(data["bull_arguments"]) == 2

    def test_debate_default_when_missing(self, client):
        """分析结果不含 debate_result 时返回默认中性结构"""
        with patch("web.routes.agent.analyze_stock", return_value={"stock_code": "600519"}):
            resp = client.get("/api/agent/debate", params={"code": "600519"})
        data = resp.json()
        assert data["consensus"] == "NEUTRAL"
        assert data["bull_score"] == 50
        assert data["bear_score"] == 50
        assert data["confidence"] == 0.1


class TestHistory:
    """GET /api/agent/history"""

    def test_history_empty_initially(self, client):
        """初始状态历史为空"""
        resp = client.get("/api/agent/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["history"] == []


class TestQuickAnalysis:
    """POST /api/agent/quick_analysis"""

    def test_quick_analysis_returns_result(self, client):
        """快速分析返回结果"""
        with patch("web.routes.agent.ai_quick_analysis", return_value=_MOCK_QUICK_RESULT):
            resp = client.post("/api/agent/quick_analysis", json={"stock_code": "600519"})
        assert resp.status_code == 200
        assert resp.json()["stock_code"] == "600519"


class TestMultiAnalyze:
    """POST /api/agent/multi_analyze"""

    def test_multi_analyze_adds_selected_agents(self, client):
        """返回结果中携带 selected_agents"""
        mock_result = {"stock_code": "600519", "decision": "BUY"}
        with patch("web.routes.agent.ai_multi_analyze", return_value=mock_result):
            resp = client.post("/api/agent/multi_analyze", json={
                "stock_code": "600519",
                "mode": "deep",
                "agents": ["技术", "基本面"],
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "BUY"
        assert data["selected_agents"] == ["技术", "基本面"]

    def test_multi_analyze_appends_history(self, client):
        """多模式分析结果写入历史"""
        with patch("web.routes.agent.ai_multi_analyze", return_value={"stock_code": "600519"}):
            client.post("/api/agent/multi_analyze", json={"stock_code": "600519"})
        assert client.get("/api/agent/history").json()["count"] == 1


class TestPortfolioAdvice:
    """POST /api/agent/portfolio_advice"""

    def test_portfolio_advice_returns_result(self, client):
        """持仓组合建议返回结果"""
        with patch("web.routes.agent.ai_analyze_portfolio", return_value=_MOCK_PORTFOLIO_RESULT):
            resp = client.post("/api/agent/portfolio_advice", json={
                "positions": [{"symbol": "600519", "shares": 100}]
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "组合整体稳健"
        assert len(data["suggestions"]) == 1


class TestMarketOutlook:
    """GET /api/agent/market_outlook"""

    def test_market_outlook_returns_result(self, client):
        """市场展望返回结果"""
        with patch("web.routes.agent.ai_get_market_outlook", return_value=_MOCK_OUTLOOK_RESULT):
            resp = client.get("/api/agent/market_outlook")
        assert resp.status_code == 200
        data = resp.json()
        assert data["market_trend"] == "震荡上行"
        assert data["risk_level"] == "中等"


class TestFundAnalyze:
    """POST /api/agent/fund_analyze"""

    def test_fund_analyze_returns_result(self, client):
        """基金多智能体分析返回结果"""
        with patch("src.analysis.multi_agent_fund.analyze_fund_with_agents",
                   new=AsyncMock(return_value=_MOCK_FUND_ANALYZE_RESULT)):
            resp = client.post("/api/agent/fund_analyze", json={
                "fund_code": "110022",
                "fund_name": "易方达消费",
                "cost_nav": 2.5,
                "shares": 1000,
                "mode": "deep",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["fund_code"] == "110022"
        assert data["decision"] == "HOLD"

    def test_fund_analyze_default_mode(self, client):
        """未指定 mode 时默认 deep"""
        with patch("src.analysis.multi_agent_fund.analyze_fund_with_agents",
                   new=AsyncMock(return_value=_MOCK_FUND_ANALYZE_RESULT)) as mock_fn:
            resp = client.post("/api/agent/fund_analyze", json={"fund_code": "110022"})
        assert resp.status_code == 200
        # 验证 mode 参数默认为 deep
        _, kwargs = mock_fn.call_args
        assert kwargs["mode"] == "deep"


class TestFundAnalyzeSteps:
    """GET /api/agent/fund_analyze_steps

    验证多智能体分析进度步骤定义端点(借鉴 TradingAgents-CN 进度展示)
    """

    def test_returns_seven_steps(self, client):
        """应返回 7 个分析步骤"""
        resp = client.get("/api/agent/fund_analyze_steps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_steps"] == 7
        assert len(data["steps"]) == 7

    def test_step_structure(self, client):
        """每个步骤应包含 step/key/name/desc 字段"""
        resp = client.get("/api/agent/fund_analyze_steps")
        data = resp.json()
        for i, s in enumerate(data["steps"], 1):
            assert s["step"] == i
            assert "key" in s and isinstance(s["key"], str) and len(s["key"]) > 0
            assert "name" in s and isinstance(s["name"], str) and len(s["name"]) > 0
            assert "desc" in s and isinstance(s["desc"], str)

    def test_step_keys_unique(self, client):
        """所有步骤的 key 应唯一"""
        resp = client.get("/api/agent/fund_analyze_steps")
        data = resp.json()
        keys = [s["key"] for s in data["steps"]]
        assert len(set(keys)) == len(keys), f"步骤key有重复: {keys}"

    def test_estimated_seconds_positive(self, client):
        """预估时间应为正数"""
        resp = client.get("/api/agent/fund_analyze_steps")
        data = resp.json()
        assert data["estimated_seconds"] > 0

    def test_first_step_is_data_fetch(self, client):
        """第一步应是数据抓取"""
        resp = client.get("/api/agent/fund_analyze_steps")
        data = resp.json()
        assert data["steps"][0]["key"] == "data"
        assert "抓取" in data["steps"][0]["name"] or "数据" in data["steps"][0]["name"]

    def test_last_step_is_debate(self, client):
        """最后一步应是多空辩论"""
        resp = client.get("/api/agent/fund_analyze_steps")
        data = resp.json()
        assert data["steps"][-1]["key"] == "debate"
        assert "辩论" in data["steps"][-1]["name"]

"""R-05 回归:组合级 AI 建议入口与失败兜底。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.api import app

client = TestClient(app)
INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def test_portfolio_advice_route_returns_analysis():
    fake = {
        "positions": [{"symbol": "600519", "name": "贵州茅台", "action": "HOLD", "confidence": 0.6, "risk_level": "MEDIUM"}],
        "position_weights": {"600519": 1.0},
        "total_value": 100000.0,
        "concentration_risk_level": "HIGH",
        "portfolio_analysis": {
            "concentration_risk": {"level": "HIGH", "max_weight": 1.0, "suggestion": "降低单一持仓"},
            "sector_exposure": {"suggestion": "板块集中在消费"},
            "rebalancing": [{"symbol": "600519", "action": "REDUCE", "reason": "权重过高", "target_weight": 0.8}],
            "overall_risk_score": {"level": "MEDIUM", "score": 62},
        },
        "degraded": False,
    }
    with patch("web.routes.agent.ai_analyze_portfolio", return_value=fake):
        resp = client.post(
            "/api/agent/portfolio_advice",
            json={"positions": [{"symbol": "600519", "quantity": 100, "cost_price": 1500}]},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_analysis"]["concentration_risk"]["level"] == "HIGH"
    assert data["degraded"] is False


def test_frontend_has_portfolio_advice_entry_and_fallback():
    text = INDEX.read_text(encoding="utf-8")
    assert 'id="portfolioAdviceCard"' in text
    assert "async function loadPortfolioAdvice" in text
    assert "function renderPortfolioAdvice" in text
    assert "改用组合规则建议" in text
    assert "loadFundAdviceV2()" in text

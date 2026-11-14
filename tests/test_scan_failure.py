"""R-13 回归:策略扫描失败必须 503, 前端不得再用默认假标的。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.api import app

client = TestClient(app)
INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def test_new_high_all_sources_failed_returns_503():
    with patch("web.routes.strategy._HAS_STRATEGIES", False), \
         patch("web.routes.strategy.ds2.get_stock_ranking_em", side_effect=RuntimeError("em")), \
         patch("web.routes.strategy.ds2._get_stock_ranking_sina", side_effect=RuntimeError("sina")):
        resp = client.get("/api/strategy/scan/new_high")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "SCAN_DATA_UNAVAILABLE"


def test_limit_up_failure_returns_503():
    class BoomAnalyzer:
        def scan_limit_up(self):
            raise RuntimeError("boom")

    with patch("web.routes.strategy._HAS_STRATEGIES", True), \
         patch("web.routes.strategy.LimitUpAnalyzer", BoomAnalyzer):
        resp = client.get("/api/strategy/scan/limit_up")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "SCAN_DATA_UNAVAILABLE"


def test_cb_failure_returns_503():
    class BoomSniper:
        def scan_cb_opportunities(self):
            raise RuntimeError("boom")

    with patch("web.routes.strategy._HAS_STRATEGIES", True), \
         patch("web.routes.strategy.CBT0Sniper", BoomSniper):
        resp = client.get("/api/strategy/scan/cb")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "SCAN_DATA_UNAVAILABLE"


def test_scan_upstream_hang_returns_503():
    """上游(akshare)无超时挂起时, 扫描必须在 _SCAN_TIMEOUT_S 后 503, 不能无限阻塞。"""
    import time

    class SlowAnalyzer:
        def scan_limit_up(self):
            time.sleep(0.3)
            return []

    with patch("web.routes.strategy._HAS_STRATEGIES", True), \
         patch("web.routes.strategy.LimitUpAnalyzer", SlowAnalyzer), \
         patch("web.routes.strategy._SCAN_TIMEOUT_S", 0.05):
        resp = client.get("/api/strategy/scan/limit_up")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "SCAN_DATA_UNAVAILABLE"


def test_frontend_has_no_default_scan_results():
    text = INDEX.read_text(encoding="utf-8")
    assert "const defaultResults" not in text
    assert "暂无扫描结果" in text


def test_frontend_scan_error_does_not_render_empty_state():
    """api() 对 HTTP 错误返回 null(不抛异常), 扫描失败时不得继续渲染“暂无扫描结果”。"""
    text = INDEX.read_text(encoding="utf-8")
    assert "if (!data) return;" in text
    # 客户端超时必须长于后端 _SCAN_TIMEOUT_S(15s), 否则用户只能看到笼统的超时提示
    assert "url.startsWith('/api/strategy/scan/')) return 25000" in text

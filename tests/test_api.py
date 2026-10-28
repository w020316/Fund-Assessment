"""API端点测试"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端"""
    from web.api import app
    return TestClient(app)


class TestHealthEndpoint:
    """健康检查端点测试"""

    def test_health_returns_ok(self, client):
        """测试健康检查返回ok"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "akshare" in data
        assert "core_modules" in data

    def test_health_has_ai_ready(self, client):
        """测试健康检查返回 AI 就绪状态(不泄露具体 key)"""
        resp = client.get("/api/health")
        data = resp.json()
        assert "ai_ready" in data
        assert isinstance(data["ai_ready"], bool)


class TestMarketEndpoints:
    """行情端点测试"""

    def test_index_realtime(self, client):
        """测试指数实时行情"""
        resp = client.get("/api/market/index_realtime")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "_meta" in data

    def test_stock_realtime(self, client):
        """测试个股实时行情"""
        resp = client.get("/api/market/stock_realtime", params={"codes": "600519"})
        assert resp.status_code == 200

    def test_stock_kline(self, client):
        """测试K线数据"""
        resp = client.get("/api/market/stock_kline", params={"code": "600519", "period": "daily", "count": 30})
        assert resp.status_code == 200

    def test_data_quality(self, client):
        """测试数据质量检查"""
        resp = client.get("/api/market/data-quality/600519")
        assert resp.status_code == 200
        data = resp.json()
        assert "quality_score" in data
        assert "is_valid" in data


class TestConfigEndpoints:
    """配置端点测试"""

    def test_get_config(self, client):
        """测试获取配置"""
        resp = client.get("/api/config/settings")
        assert resp.status_code == 200

    def test_config_masks_sensitive(self, client):
        """测试配置脱敏"""
        resp = client.get("/api/config/settings")
        data = resp.json()
        # 敏感字段应被脱敏
        config_str = str(data)
        assert "tvly-dev-" not in config_str
        assert "sk-tinyfish-" not in config_str


class TestDashboardEndpoints:
    """仪表盘端点测试"""

    def test_overview(self, client):
        """测试仪表盘概览"""
        resp = client.get("/api/dashboard/overview")
        assert resp.status_code == 200


class TestStaticFiles:
    """静态文件测试"""

    def test_root_redirects(self, client):
        """测试根路径重定向"""
        resp = client.get("/")
        assert resp.status_code in (200, 307)

    def test_head_root_probe_returns_200(self, client):
        """HEAD / 探活返回 200 空 body(R2-20:Uptime 类监控默认用 HEAD)。"""
        resp = client.head("/")
        assert resp.status_code == 200
        assert resp.content == b""

    def test_cors_preflight_rejects_admin_token_header(self, client):
        """R2-22:CORS 白名单不再放行未被鉴权读取的 X-Admin-Token。"""
        base = {
            # 默认白名单内的 Origin,确保 400 来自请求头被拒而不是 Origin 被拒
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        }
        ok = client.options("/api/health", headers={**base, "Access-Control-Request-Headers": "content-type"})
        assert ok.status_code == 200
        resp = client.options("/api/health", headers={**base, "Access-Control-Request-Headers": "x-admin-token"})
        assert resp.status_code == 400
        allowed = (resp.headers.get("access-control-allow-headers") or "").lower()
        assert "x-admin-token" not in allowed

    def test_index_html_accessible(self, client):
        """测试index.html可访问"""
        resp = client.get("/static/index.html")
        assert resp.status_code == 200
        assert "QuantFlow Pro" in resp.text

    def test_static_cache_headers(self, client):
        """阶段 5:S5-08 静态资源补 Cache-Control;HTML 短缓存,API 不被缓存。"""
        html = client.get("/static/index.html")
        assert "max-age=60" in (html.headers.get("cache-control") or "")
        css = client.get("/static/css/stage6.css")
        assert css.status_code == 200
        assert "max-age=300" in (css.headers.get("cache-control") or "")
        api = client.get("/api/health")
        assert "cache-control" not in {k.lower() for k in api.headers.keys()}

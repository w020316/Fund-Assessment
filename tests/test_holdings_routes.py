"""基金重仓股板块分析路由单元测试

验证 web/routes/holdings.py 的端点:
- GET /api/holdings/{fund_code}            基金重仓股分析(持仓/板块/集中度/净值影响)
- GET /api/holdings/sector-rotation/overview  板块轮动总览

通过 mock src.analysis.fund_holdings 的函数 + 清文件缓存,避免真实网络请求与缓存干扰。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_holdings_cache():
    """每个测试前清空 holdings 路由的文件缓存 + slowapi 限流计数器

    限流计数器必须清理的原因:
    - TestClient 共享同一客户端 IP(127.0.0.1)
    - slowapi 的 3/minute 限流会在连续测试中累积,导致第4个测试起被 429 拒绝
    - 通过 limiter._storage.reset() 清除计数,确保每个测试独立
    """
    from web.routes import holdings
    holdings.cache.clear()
    # 清除 slowapi 限流存储,避免跨测试影响
    from web.rate_limiter import limiter
    try:
        limiter._storage.reset()
    except Exception:
        pass
    yield
    holdings.cache.clear()
    try:
        limiter._storage.reset()
    except Exception:
        pass


# 模拟重仓股分析结果
_MOCK_HOLDINGS = {
    "fund_code": "110022",
    "fund_name": "易方达消费",
    "holdings": [
        {"code": "600519", "name": "贵州茅台", "weight": 9.5, "change_pct": 1.2},
        {"code": "000858", "name": "五粮液", "weight": 8.3, "change_pct": 0.8},
    ],
    "sector_exposure": {"白酒": 35.0, "食品": 15.0},
    "concentration": {"top5": 42.0, "top10": 65.0, "hhi": 850},
    "nav_impact": 0.45,
    "sector_rotation": {"signal": "白酒走强", "strength": "中"},
}

_MOCK_SECTOR_ROTATION = {
    "sectors": [
        {"name": "白酒", "change_pct": 2.5, "trend": "up", "main_net_inflow": 50000000},
        {"name": "半导体", "change_pct": -1.2, "trend": "down", "main_net_inflow": -30000000},
    ],
    "rotation_signal": "白酒板块资金流入,半导体流出",
    "top_sectors": ["白酒", "新能源"],
}


class TestFundHoldings:
    """GET /api/holdings/{fund_code}"""

    def test_returns_holdings_analysis(self, client):
        """返回重仓股分析结果"""
        with patch("web.routes.holdings.analyze_fund_holdings",
                   new=AsyncMock(return_value=_MOCK_HOLDINGS)):
            resp = client.get("/api/holdings/110022")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["fund_code"] == "110022"
        assert len(data["data"]["holdings"]) == 2
        assert data["data"]["concentration"]["top5"] == 42.0
        assert data["_meta"]["data_source"] == "em_fund+akshare"
        assert data["_meta"]["cached"] is False

    def test_response_has_quality_score(self, client):
        """未缓存时质量评分为 80"""
        with patch("web.routes.holdings.analyze_fund_holdings",
                   new=AsyncMock(return_value=_MOCK_HOLDINGS)):
            resp = client.get("/api/holdings/110022")
        assert resp.json()["_meta"]["quality_score"] == 80.0

    def test_cache_second_call_is_cached(self, client):
        """第二次调用命中缓存,质量评分 100"""
        mock_fn = AsyncMock(return_value=_MOCK_HOLDINGS)
        with patch("web.routes.holdings.analyze_fund_holdings", new=mock_fn):
            first = client.get("/api/holdings/110022")
            second = client.get("/api/holdings/110022")
        assert first.json()["_meta"]["cached"] is False
        assert second.json()["_meta"]["cached"] is True
        assert second.json()["_meta"]["quality_score"] == 100.0
        # mock 只被调用一次(第二次命中缓存)
        assert mock_fn.call_count == 1

    def test_refresh_bypasses_cache(self, client):
        """refresh=True 时忽略缓存并重新获取"""
        mock_fn = AsyncMock(return_value=_MOCK_HOLDINGS)
        with patch("web.routes.holdings.analyze_fund_holdings", new=mock_fn):
            client.get("/api/holdings/110022")
            client.get("/api/holdings/110022", params={"refresh": True})
        # refresh 强制刷新,mock 应被调用两次
        assert mock_fn.call_count == 2


class TestSectorRotation:
    """GET /api/holdings/sector-rotation/overview"""

    def test_returns_sector_rotation(self, client):
        """返回板块轮动总览"""
        with patch("web.routes.holdings.get_sector_rotation",
                   new=AsyncMock(return_value=_MOCK_SECTOR_ROTATION)):
            resp = client.get("/api/holdings/sector-rotation/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["sectors"]) == 2
        assert data["data"]["rotation_signal"] == "白酒板块资金流入,半导体流出"
        assert data["_meta"]["data_source"] == "em_sector_ranking"

    def test_sector_rotation_cached(self, client):
        """板块轮动总览也走缓存"""
        mock_fn = AsyncMock(return_value=_MOCK_SECTOR_ROTATION)
        with patch("web.routes.holdings.get_sector_rotation", new=mock_fn):
            client.get("/api/holdings/sector-rotation/overview")
            client.get("/api/holdings/sector-rotation/overview")
        assert mock_fn.call_count == 1


class TestUploadHoldingsFile:
    """POST /api/holdings/upload 上传文件/图片识别持仓"""

    @pytest.fixture
    def auth_headers(self, monkeypatch):
        """管理员鉴权 header"""
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token-123")
        return {"Authorization": "Bearer test-admin-token-123"}

    def test_csv_upload_english_headers(self, client, auth_headers):
        """CSV 上传(英文表头)解析正确"""
        csv_content = b"code,name,weight\n161725,\xe6\x8b\x9b\xe5\x95\x86\xe4\xb8\xad\xe8\xaf\x81\xe7\x99\xbd\xe9\x85\x92,35.5\n110022,\xe6\x98\x93\xe6\x96\xb9\xe8\xbe\xbe\xe6\xb6\x88\xe8\xb4\xb9,20.3"
        resp = client.post(
            "/api/holdings/upload",
            files={"file": ("holdings.csv", csv_content, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "csv"
        assert data["count"] == 2
        assert data["holdings"][0]["code"] == "161725"
        assert data["holdings"][0]["weight"] == 35.5

    def test_csv_upload_chinese_headers(self, client, auth_headers):
        """CSV 上传(中文表头:基金代码/基金名称/持仓比例)且含%符号"""
        csv_content = "\xe5\x9f\xba\xe9\x87\x91\xe4\xbb\xa3\xe7\xa0\x81,\xe5\x9f\xba\xe9\x87\x91\xe5\x90\x8d\xe7\xa7\xb0,\xe6\x8c\x81\xe4\xbb\x93\xe6\xaf\x94\xe4\xbe\x8b\n161725,\xe6\x8b\x9b\xe5\x95\x86\xe7\x99\xbd\xe9\x85\x92,35.5%".encode("utf-8")
        resp = client.post(
            "/api/holdings/upload",
            files={"file": ("holdings.csv", csv_content, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["holdings"][0]["code"] == "161725"
        # % 符号应被清理
        assert data["holdings"][0]["weight"] == 35.5

    def test_csv_empty_no_matching_columns(self, client, auth_headers):
        """CSV 无匹配列名时返回 count=0"""
        csv_content = b"foo,bar\n1,2"
        resp = client.post(
            "/api/holdings/upload",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_image_upload_mock_vision(self, client, auth_headers):
        """图片上传调用 agnes vision(mock)"""
        mock_result = [{"code": "161725", "name": "招商白酒", "weight": 35.5}]
        with patch("src.core.ai_service.recognize_holdings_from_image",
                   return_value=mock_result):
            resp = client.post(
                "/api/holdings/upload",
                files={"file": ("screenshot.png", b"fake-png-bytes", "image/png")},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "image"
        assert data["count"] == 1
        assert data["holdings"][0]["code"] == "161725"

    def test_unsupported_file_type(self, client, auth_headers):
        """不支持的文件类型返回 400"""
        resp = client.post(
            "/api/holdings/upload",
            files={"file": ("test.txt", b"hello", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_no_auth_rejected(self, client, monkeypatch):
        """无鉴权 token 生产环境返回 401"""
        monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.post(
            "/api/holdings/upload",
            files={"file": ("test.csv", b"code,name\n1,a", "text/csv")},
        )
        assert resp.status_code == 401

    def test_csv_code_decimal_cleanup(self, client, auth_headers):
        """CSV 数字 code 被读为 '161725.0' 时清理为 '161725'"""
        csv_content = b"code,name,weight\n161725.0,white_wine,35.5"
        resp = client.post(
            "/api/holdings/upload",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["holdings"][0]["code"] == "161725"

    def test_rate_limit_triggered_after_3_requests(self, client, auth_headers):
        """P1 回归:验证 SlowAPIMiddleware 注册后限流真实生效

        背景(2026-07-30 端到端测试发现):
        - web/api.py 创建了 limiter 并 add_exception_handler,
          但未 add_middleware(SlowAPIMiddleware)
        - 导致所有 @limiter.limit 装饰器(含 /api/agent/fund_analyze、/api/holdings/upload)
          静默失效,LLM 高成本端点可被无限调用
        - 修复:补充 app.add_middleware(SlowAPIMiddleware)
        本用例连续 4 次调用,第 4 次应被 429 拒绝
        """
        csv_content = b"code,name,weight\n161725,fund_a,35.5"
        files = {"file": ("test.csv", csv_content, "text/csv")}
        # 前 3 次应成功(3/minute 限流)
        for i in range(3):
            resp = client.post("/api/holdings/upload", files=files, headers=auth_headers)
            assert resp.status_code == 200, f"请求 {i+1} 应成功,实际 {resp.status_code}"
        # 第 4 次应被限流拒绝(slowapi 抛 RateLimitExceeded → 429)
        resp4 = client.post("/api/holdings/upload", files=files, headers=auth_headers)
        assert resp4.status_code == 429, f"第4次应被限流(429),实际 {resp4.status_code}"


class TestRecognizeHoldingsImage:
    """recognize_holdings_from_image 单元测试(mock agnes vision)"""

    def test_no_api_key_returns_empty(self, monkeypatch):
        """未配置 AGNES_API_KEY 返回空列表"""
        from src.core import ai_service
        with patch.object(ai_service, "_get_agnes_api_key", return_value=""):
            result = ai_service.recognize_holdings_from_image(b"fake", "image/png")
        assert result == []

    def test_unsupported_mime_returns_empty(self, monkeypatch):
        """不支持的图片类型返回空"""
        from src.core import ai_service
        monkeypatch.setenv("AGNES_API_KEY", "sk-test")
        result = ai_service.recognize_holdings_from_image(b"fake", "image/bmp")
        assert result == []

    def test_valid_response_parses_holdings(self, monkeypatch):
        """正常 agnes 响应解析持仓"""
        from src.core import ai_service
        monkeypatch.setenv("AGNES_API_KEY", "sk-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"holdings": [{"code":"161725","name":"招商白酒","weight":35.5}]}'}}]
        }
        mock_resp.raise_for_status.return_value = None
        with patch.object(ai_service.requests, "post", return_value=mock_resp):
            result = ai_service.recognize_holdings_from_image(b"fake-png", "image/png")
        assert len(result) == 1
        assert result[0]["code"] == "161725"
        assert result[0]["weight"] == 35.5

    def test_markdown_wrapped_response(self, monkeypatch):
        """模型返回 ```json 包裹的内容时正确解析"""
        from src.core import ai_service
        monkeypatch.setenv("AGNES_API_KEY", "sk-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '```json\n{"holdings": [{"code":"110022","name":"易方达","weight":20}]}\n```'}}]
        }
        mock_resp.raise_for_status.return_value = None
        with patch.object(ai_service.requests, "post", return_value=mock_resp):
            result = ai_service.recognize_holdings_from_image(b"fake", "image/png")
        assert len(result) == 1
        assert result[0]["code"] == "110022"

    def test_invalid_json_returns_empty(self, monkeypatch):
        """模型返回非 JSON 返回空列表"""
        from src.core import ai_service
        monkeypatch.setenv("AGNES_API_KEY", "sk-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "这不是JSON"}}]
        }
        mock_resp.raise_for_status.return_value = None
        with patch.object(ai_service.requests, "post", return_value=mock_resp):
            result = ai_service.recognize_holdings_from_image(b"fake", "image/png")
        assert result == []

    def test_empty_holdings_response(self, monkeypatch):
        """模型返回空 holdings 列表"""
        from src.core import ai_service
        monkeypatch.setenv("AGNES_API_KEY", "sk-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"holdings": []}'}}]
        }
        mock_resp.raise_for_status.return_value = None
        with patch.object(ai_service.requests, "post", return_value=mock_resp):
            result = ai_service.recognize_holdings_from_image(b"fake", "image/png")
        assert result == []

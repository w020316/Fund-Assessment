"""话术库路由单元测试

验证 web/routes/scripts.py 的 7 个端点:
- GET /list 列表(可过滤)
- GET /categories 分类结构
- GET /{script_id} 单个模板
- POST /generate 生成话术
- POST /match/fund 基金建议匹配
- POST /match/stock 个股数据匹配
- POST /ai-generate P3 AI建议生成(LLM生成,失败降级)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


class TestListScripts:
    """GET /api/scripts/list"""

    def test_list_all_returns_24_scripts(self, client):
        """无过滤返回全部 24 条"""
        resp = client.get("/api/scripts/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "_meta" in data
        assert len(data["data"]) == 24

    def test_filter_by_stock_category(self, client):
        """按 category=stock 过滤返回 12 条"""
        resp = client.get("/api/scripts/list", params={"category": "stock"})
        data = resp.json()
        assert len(data["data"]) == 12
        for item in data["data"]:
            assert item["category"] == "stock"

    def test_filter_by_fund_category(self, client):
        """按 category=fund 过滤返回 12 条"""
        resp = client.get("/api/scripts/list", params={"category": "fund"})
        data = resp.json()
        assert len(data["data"]) == 12

    def test_filter_by_scene_buy(self, client):
        """按 scene=buy 过滤"""
        resp = client.get("/api/scripts/list", params={"scene": "buy"})
        data = resp.json()
        for item in data["data"]:
            assert item["scene"] == "buy"

    def test_filter_by_category_and_scene(self, client):
        """组合过滤:fund + take_profit"""
        resp = client.get("/api/scripts/list", params={"category": "fund", "scene": "take_profit"})
        data = resp.json()
        assert len(data["data"]) == 3
        for item in data["data"]:
            assert item["category"] == "fund"
            assert item["scene"] == "take_profit"

    def test_meta_has_quality_score(self, client):
        """_meta 包含数据质量评分"""
        resp = client.get("/api/scripts/list")
        meta = resp.json()["_meta"]
        assert meta["quality_score"] == 100.0
        assert meta["data_source"] == "template"


class TestCategories:
    """GET /api/scripts/categories"""

    def test_returns_stock_and_fund(self, client):
        """返回 stock 与 fund 两个分类"""
        resp = client.get("/api/scripts/categories")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "stock" in data
        assert "fund" in data
        assert data["total"] == 24

    def test_stock_has_5_scenes(self, client):
        """stock 场景列表"""
        data = client.get("/api/scripts/categories").json()["data"]
        assert data["stock"]["count"] == 12
        assert "buy" in data["stock"]["scenes"]
        assert "sell" in data["stock"]["scenes"]

    def test_fund_has_5_scenes(self, client):
        """fund 场景列表"""
        data = client.get("/api/scripts/categories").json()["data"]
        assert data["fund"]["count"] == 12
        assert "take_profit" in data["fund"]["scenes"]


class TestGetScript:
    """GET /api/scripts/{script_id}"""

    def test_get_existing_script(self, client):
        """获取存在的模板"""
        resp = client.get("/api/scripts/stock_buy_breakthrough")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "stock_buy_breakthrough"
        assert data["category"] == "stock"
        assert "template" in data
        assert "variables" in data

    def test_get_nonexistent_returns_error(self, client):
        """不存在的 ID 返回 error"""
        resp = client.get("/api/scripts/not_exists")
        data = resp.json()
        assert data["data"] is None
        assert data["error"] == "script not found"


class TestGenerateScript:
    """POST /api/scripts/generate"""

    def test_generate_with_full_variables(self, client):
        """完整变量生成话术"""
        resp = client.post("/api/scripts/generate", json={
            "script_id": "stock_buy_breakthrough",
            "variables": {
                "stock_name": "贵州茅台",
                "resistance_price": 1800,
                "volume_ratio": 2.5,
                "stop_loss_price": 1700,
            }
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["content"] == "贵州茅台突破关键阻力位 1800,成交量放大至 2.50 倍,MACD 金叉,短期动能强劲,可考虑分批建仓,止损位 1700。"
        assert data["variables"]["stock_name"] == "贵州茅台"

    def test_generate_with_missing_variables_uses_dash(self, client):
        """缺失变量用「—」占位"""
        resp = client.post("/api/scripts/generate", json={
            "script_id": "stock_buy_breakthrough",
            "variables": {"stock_name": "贵州茅台"}
        })
        content = resp.json()["data"]["content"]
        assert "—" in content
        # 缺失变量不应出现原始占位符
        assert "{resistance_price}" not in content

    def test_generate_nonexistent_script(self, client):
        """不存在的模板 ID"""
        resp = client.post("/api/scripts/generate", json={
            "script_id": "not_exists",
            "variables": {}
        })
        data = resp.json()
        assert data["data"] is None
        assert data["error"] == "script not found"


class TestMatchFund:
    """POST /api/scripts/match/fund"""

    def test_take_profit_signal_matches_3_scripts(self, client):
        """take_profit 信号匹配 3 条止盈话术"""
        resp = client.post("/api/scripts/match/fund", json={
            "fund_advice": {
                "fund_code": "110022",
                "fund_name": "易方达消费",
                "current_nav": 2.724,
                "cost_nav": 2.5,
                "pnl_pct": 8.96,
                "advice": {"signal": "take_profit", "action": "建议止盈"},
                "market_signal": {"index_name": "上证指数", "change_pct": 0.5}
            }
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        for item in data["data"]:
            assert item["scene"] == "take_profit"
            assert "易方达消费" in item["content"]

    def test_hold_signal_matches_hold_scripts(self, client):
        """hold 信号匹配持有话术"""
        resp = client.post("/api/scripts/match/fund", json={
            "fund_advice": {
                "fund_name": "测试基金",
                "advice": {"signal": "hold"},
            }
        })
        data = resp.json()
        assert data["count"] >= 1
        for item in data["data"]:
            assert item["scene"] == "hold"

    def test_watch_signal_maps_to_wait(self, client):
        """watch 信号映射到 wait 场景"""
        resp = client.post("/api/scripts/match/fund", json={
            "fund_advice": {
                "fund_name": "测试基金",
                "advice": {"signal": "watch"},
            }
        })
        data = resp.json()
        for item in data["data"]:
            assert item["scene"] == "wait"


class TestMatchStock:
    """POST /api/scripts/match/stock"""

    def test_big_gain_matches_sell(self, client):
        """大涨(>=5%)匹配卖出话术"""
        resp = client.post("/api/scripts/match/stock", json={
            "stock_data": {
                "code": "600519",
                "name": "贵州茅台",
                "change_pct": 6.5,
                "pnl_pct": 0,
            }
        })
        data = resp.json()
        for item in data["data"]:
            assert item["scene"] == "sell"

    def test_big_drop_matches_wait(self, client):
        """大跌(<=-5%)匹配观望话术"""
        resp = client.post("/api/scripts/match/stock", json={
            "stock_data": {
                "code": "600519",
                "name": "贵州茅台",
                "change_pct": -6.0,
                "pnl_pct": 0,
            }
        })
        data = resp.json()
        for item in data["data"]:
            assert item["scene"] == "wait"

    def test_normal_range_matches_hold(self, client):
        """正常波动匹配持有话术"""
        resp = client.post("/api/scripts/match/stock", json={
            "stock_data": {
                "code": "600519",
                "name": "贵州茅台",
                "change_pct": 2.0,
                "pnl_pct": 5.0,
            }
        })
        data = resp.json()
        for item in data["data"]:
            assert item["scene"] == "hold"

    def test_high_pnl_triggers_sell(self, client):
        """浮盈>=20% 触发卖出"""
        resp = client.post("/api/scripts/match/stock", json={
            "stock_data": {
                "name": "贵州茅台",
                "change_pct": 1.0,
                "pnl_pct": 25.0,
            }
        })
        data = resp.json()
        for item in data["data"]:
            assert item["scene"] == "sell"


class TestAIGenerate:
    """POST /api/scripts/ai-generate

    P3 AI建议生成:基于基金/个股数据用免费 LLM 生成个性化投资建议话术
    LLM 不可用时自动降级为模板话术
    """

    def test_ai_generate_fund_success(self, client):
        """基金 AI 生成 - LLM 可用时返回 AI 生成内容"""
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.content = "建议持有该基金,当前净值稳定,长期趋势向好。"
        with patch("src.core.llm_router.get_llm_router") as mock_router:
            mock_router.return_value.chat.return_value = mock_response
            resp = client.post("/api/scripts/ai-generate", json={
                "target_type": "fund",
                "target_data": {
                    "fund_code": "110022",
                    "fund_name": "易方达消费",
                    "current_nav": 2.5,
                    "change_pct": 1.5,
                    "pnl_pct": 8.0,
                    "decision": "HOLD",
                },
                "scene": "持有",
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "ai"
        assert "持有" in data["content"]
        assert data["target_type"] == "fund"
        assert data["target_name"] == "易方达消费"

    def test_ai_generate_stock_success(self, client):
        """个股 AI 生成 - LLM 可用时返回 AI 生成内容"""
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.content = "建议观望,短期波动较大,等待回调机会。"
        with patch("src.core.llm_router.get_llm_router") as mock_router:
            mock_router.return_value.chat.return_value = mock_response
            resp = client.post("/api/scripts/ai-generate", json={
                "target_type": "stock",
                "target_data": {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "current_price": 1800.0,
                    "change_pct": -2.0,
                    "pnl_pct": -5.0,
                },
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "ai"
        assert "观望" in data["content"]
        assert data["target_type"] == "stock"

    def test_ai_generate_fallback_on_llm_failure(self, client):
        """LLM 失败时降级为模板话术"""
        from unittest.mock import patch
        with patch("src.core.llm_router.get_llm_router") as mock_router:
            mock_router.return_value.chat.side_effect = RuntimeError("LLM 不可用")
            resp = client.post("/api/scripts/ai-generate", json={
                "target_type": "fund",
                "target_data": {
                    "fund_code": "110022",
                    "fund_name": "易方达消费",
                    "current_nav": 2.5,
                    "change_pct": 1.5,
                    "pnl_pct": 8.0,
                },
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "fallback"
        assert "content" in data
        assert "error" in data

    def test_ai_generate_invalid_target_type(self, client):
        """无效 target_type 返回错误"""
        resp = client.post("/api/scripts/ai-generate", json={
            "target_type": "invalid",
            "target_data": {},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] is None
        assert "target_type" in data["error"]

    def test_ai_generate_empty_response_falls_back(self, client):
        """LLM 返回空内容时降级"""
        from unittest.mock import MagicMock, patch
        mock_response = MagicMock()
        mock_response.content = ""
        with patch("src.core.llm_router.get_llm_router") as mock_router:
            mock_router.return_value.chat.return_value = mock_response
            resp = client.post("/api/scripts/ai-generate", json={
                "target_type": "stock",
                "target_data": {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "current_price": 1800.0,
                    "change_pct": 1.0,
                },
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "fallback"

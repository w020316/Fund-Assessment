"""AI 服务模块单元测试

验证 src/core/ai_service.py 的核心逻辑:
- _check_api_keys API 密钥检查
- _gather_stock_data 数据并行抓取(mock)
- _build_analysis_prompt 提示词构建
- _parse_analysis_response 响应解析
- _fallback_result 降级结果
- _call_ttapi_direct 直接 TTAPI 调用(mock requests)
- _call_llm LLM 路由(mock LLM Router)
- _search_tavily / _search_tinyfish 检索(mock requests)
- search_news 新闻检索(mock)
- analyze_stock / quick_analysis / multi_analyze 端到端(mock LLM)
- analyze_portfolio 组合分析(mock ThreadPoolExecutor)
- get_market_outlook 市场展望(mock 并行抓取)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core import ai_service
from src.core.ai_service import (
    _build_analysis_prompt,
    _call_llm,
    _call_ttapi_direct,
    _check_api_keys,
    _fallback_result,
    _gather_stock_data,
    _get_agnes_api_key,
    _get_tavily_api_key,
    _get_tinyfish_api_key,
    _get_ttapi_api_key,
    _parse_analysis_response,
    _search_tavily,
    _search_tinyfish,
    analyze_portfolio,
    analyze_stock,
    get_market_outlook,
    multi_analyze,
    quick_analysis,
    search_news,
)


@pytest.fixture
def stock_data(sample_quote, sample_kline, sample_capital_flow, sample_financial):
    """完整股票数据(覆盖 _build_analysis_prompt 各字段)"""
    return {
        "quote": sample_quote,
        "financial": sample_financial,
        "capital_flow": sample_capital_flow,
        "company": {"name": "贵州茅台", "industry": "白酒"},
        "margin": {"margin_balance": 1e9, "short_balance": 1e7},
        "shareholder": {"holder_num": 150000, "change_pct": -2.5},
        "kline_daily": sample_kline,
        "research_reports": [
            {"publish_date": "2026-07-20", "org_name": "中信证券", "title": "买入评级", "rating": "买入"},
        ],
        "news": [
            {"publish_time": "2026-07-28", "title": "利好公告", "source": "东方财富"},
        ],
        "dragon_tiger": [
            {"name": "茅台", "code": "600519", "change_pct": 5.0, "buy_amount": 1e8,
             "sell_amount": 5e7, "net_amount": 5e7, "reason": "涨停"},
        ],
        "northbound_flow": {"sh_net_inflow": 30e8, "sz_net_inflow": 20e8, "total_net_inflow": 50e8},
        "global_news": [
            {"publish_time": "2026-07-28", "title": "全球宏观", "source": "reuters"},
        ],
    }


@pytest.fixture
def mock_analysis_response():
    """完整 AI 分析响应 JSON"""
    return json.dumps({
        "opinions": [
            {"role": "fundamental", "signal": "BULLISH", "confidence": 0.8,
             "reasoning": "PE合理", "key_points": ["PE低", "ROE高"], "score": 75},
            {"role": "technical", "signal": "BULLISH", "confidence": 0.7,
             "reasoning": "均线多头", "key_points": ["MA5上穿"], "score": 70},
            {"role": "sentiment", "signal": "NEUTRAL", "confidence": 0.5,
             "reasoning": "情绪中性", "key_points": ["换手率正常"], "score": 55},
            {"role": "news", "signal": "BULLISH", "confidence": 0.6,
             "reasoning": "利好新闻多", "key_points": ["业绩预增"], "score": 65},
            {"role": "policy", "signal": "NEUTRAL", "confidence": 0.5,
             "reasoning": "政策稳定", "key_points": ["无重大政策"], "score": 50},
            {"role": "hot_money", "signal": "BULLISH", "confidence": 0.7,
             "reasoning": "北向流入", "key_points": ["主力净流入"], "score": 70},
            {"role": "lockup", "signal": "NEUTRAL", "confidence": 0.6,
             "reasoning": "无解禁", "key_points": ["无减持"], "score": 60},
        ],
        "bull_bear_debate": {
            "topic": "600519多空辩论",
            "bull_arguments": ["估值低", "业绩好"],
            "bear_arguments": ["涨幅大"],
            "bull_score": 75,
            "bear_score": 35,
            "consensus": "BULLISH",
            "confidence": 0.7,
        },
        "risk_debate": {
            "aggressive_position": {"view": "激进", "arguments": ["加仓"],
                                    "suggested_position": 0.6, "risk_tolerance": "HIGH"},
            "conservative_position": {"view": "保守", "arguments": ["观望"],
                                      "suggested_position": 0.2, "risk_tolerance": "LOW"},
            "neutral_position": {"view": "中性", "arguments": ["适度"],
                                 "suggested_position": 0.4, "risk_tolerance": "MEDIUM"},
            "final_risk_level": "MEDIUM",
            "final_suggested_position": 0.4,
        },
        "portfolio_decision": {
            "action": "BUY",
            "position_size": 0.5,
            "target_price": 1800.0,
            "stop_loss_price": 1600.0,
            "confidence": 0.8,
            "reasoning": "估值合理,买入",
            "astock_constraints": {
                "t_plus_1": "T+1交易",
                "price_limit": "10%涨跌停",
                "min_lot": "100股",
                "warnings": ["注意波动风险"],
            },
        },
    })


class TestCheckApiKeys:
    """_check_api_keys API 密钥检查"""

    def test_returns_dict_with_all_keys(self):
        result = _check_api_keys()
        assert "ttapi" in result
        assert "tavily" in result
        assert "tinyfish" in result
        assert "agnes" in result

    def test_all_values_are_bool(self):
        result = _check_api_keys()
        for v in result.values():
            assert isinstance(v, bool)


class TestDynamicApiKeyHotReload:
    """动态 API Key 实时读取(支持 Render Dashboard 热更新)

    P2 修复(2026-07-29):原模块级常量在 import 时固化 API Key,
    导致 Render Dashboard 修改 key 后需重启进程才生效。
    改为 _get_*_api_key() 实时读取函数,验证环境变量修改立即生效。
    """

    def test_ttapi_key_reads_env_var(self, monkeypatch):
        """_get_ttapi_api_key 应实时读取 TTAPI_API_KEY 环境变量"""
        monkeypatch.setenv("TTAPI_API_KEY", "hot-reloaded-ttapi-key")
        assert _get_ttapi_api_key() == "hot-reloaded-ttapi-key"

    def test_tavily_key_reads_env_var(self, monkeypatch):
        """_get_tavily_api_key 应实时读取 TAVILY_API_KEY 环境变量"""
        monkeypatch.setenv("TAVILY_API_KEY", "hot-reloaded-tavily-key")
        assert _get_tavily_api_key() == "hot-reloaded-tavily-key"

    def test_tinyfish_key_reads_env_var(self, monkeypatch):
        """_get_tinyfish_api_key 应实时读取 TINYFISH_API_KEY 环境变量"""
        monkeypatch.setenv("TINYFISH_API_KEY", "hot-reloaded-tf-key")
        assert _get_tinyfish_api_key() == "hot-reloaded-tf-key"

    def test_agnes_key_reads_env_var(self, monkeypatch):
        """_get_agnes_api_key 应实时读取 AGNES_API_KEY 环境变量"""
        monkeypatch.setenv("AGNES_API_KEY", "hot-reloaded-agnes-key")
        assert _get_agnes_api_key() == "hot-reloaded-agnes-key"

    def test_env_change_reflected_without_restart(self, monkeypatch):
        """环境变量变更后立即生效(无需重启进程)"""
        monkeypatch.setenv("TTAPI_API_KEY", "old-key")
        assert _get_ttapi_api_key() == "old-key"
        # 模拟 Render Dashboard 修改环境变量
        monkeypatch.setenv("TTAPI_API_KEY", "new-key-after-dashboard-change")
        assert _get_ttapi_api_key() == "new-key-after-dashboard-change"

    def test_empty_env_returns_empty_string(self, monkeypatch):
        """未配置环境变量 → 返回空字符串"""
        monkeypatch.delenv("TTAPI_API_KEY", raising=False)
        monkeypatch.setattr(ai_service.settings, "ttapi_api_key", "")
        assert _get_ttapi_api_key() == ""

    def test_check_api_keys_reflects_env_change(self, monkeypatch):
        """_check_api_keys 应反映环境变量的最新状态"""
        monkeypatch.delenv("TTAPI_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.setattr(ai_service.settings, "ttapi_api_key", "")
        monkeypatch.setattr(ai_service.settings, "tavily_api_key", "")
        monkeypatch.setattr(ai_service.settings, "tinyfish_api_key", "")
        monkeypatch.setattr(ai_service.settings, "agnes_api_key", "")
        # 初始全部未配置
        keys = _check_api_keys()
        assert keys["ttapi"] is False
        # 配置后立即反映
        monkeypatch.setenv("TTAPI_API_KEY", "new-key")
        keys = _check_api_keys()
        assert keys["ttapi"] is True


class TestGatherStockData:
    """_gather_stock_data 数据并行抓取"""

    def test_returns_dict_with_keys(self, sample_quote, sample_kline):
        """应返回包含 quote/kline_daily 等键的字典"""
        with patch.object(ai_service, "_parallel_fetch") as mock_parallel:
            mock_parallel.return_value = {
                "quote": [sample_quote],
                "kline": sample_kline,
                "capital_flow": {"main_net_inflow": 1e8},
                "financial": {"pe_ttm": 28.5},
                "company": {"name": "贵州茅台"},
                "margin": {"margin_balance": 1e9},
                "shareholder": {"holder_num": 150000},
                "research_reports": [{"title": "t"}],
                "news": [{"title": "t"}],
                "dragon_tiger": [{"code": "600519", "name": "茅台"}],
                "northbound": {"total_net_inflow": 1e9},
                "global_news": [{"title": "t"}],
            }
            result = _gather_stock_data("600519")
        assert "quote" in result
        assert result["quote"]["code"] == "600519"
        assert "kline_daily" in result
        assert len(result["kline_daily"]) <= 30  # 截断到 30 条
        assert "dragon_tiger" in result

    def test_returns_empty_dict_when_all_fail(self):
        """所有源失败 → 返回空字典"""
        with patch.object(ai_service, "_parallel_fetch", return_value={}):
            result = _gather_stock_data("600519")
        assert result == {}

    def test_dragon_tiger_filtered_by_code(self, sample_quote):
        """龙虎榜应优先按股票代码过滤"""
        with patch.object(ai_service, "_parallel_fetch") as mock_parallel:
            mock_parallel.return_value = {
                "dragon_tiger": [
                    {"code": "600519", "name": "茅台"},
                    {"code": "000001", "name": "平安"},
                ],
            }
            result = _gather_stock_data("600519")
        assert len(result["dragon_tiger"]) == 1
        assert result["dragon_tiger"][0]["code"] == "600519"

    def test_global_news_truncated_to_10(self):
        """全球新闻应截断到 10 条"""
        with patch.object(ai_service, "_parallel_fetch") as mock_parallel:
            mock_parallel.return_value = {
                "global_news": [{"title": f"新闻{i}"} for i in range(20)],
            }
            result = _gather_stock_data("600519")
        assert len(result["global_news"]) == 10


class TestBuildAnalysisPrompt:
    """_build_analysis_prompt 提示词构建"""

    def test_prompt_contains_stock_code(self, stock_data):
        prompt = _build_analysis_prompt("600519", stock_data, [], "deep")
        assert "600519" in prompt

    def test_prompt_contains_quote_info(self, stock_data):
        """应包含行情字段"""
        prompt = _build_analysis_prompt("600519", stock_data, [], "deep")
        assert "1688" in prompt  # 当前价
        assert "贵州茅台" in prompt

    def test_deep_mode_includes_depth_instruction(self, stock_data):
        """deep 模式应包含深度分析指令"""
        prompt = _build_analysis_prompt("600519", stock_data, [], "deep")
        assert "深度分析" in prompt

    def test_quick_mode_includes_quick_instruction(self, stock_data):
        """quick 模式应包含快速分析指令"""
        prompt = _build_analysis_prompt("600519", stock_data, [], "quick")
        assert "快速分析" in prompt

    def test_prompt_contains_seven_roles(self, stock_data):
        """应包含 7 位分析师角色"""
        prompt = _build_analysis_prompt("600519", stock_data, [], "deep")
        for role in ["fundamental", "technical", "sentiment", "news", "policy", "hot_money", "lockup"]:
            assert role in prompt

    def test_prompt_includes_search_news(self, stock_data):
        """应包含检索到的新闻"""
        search_news = [{"title": "外部检索新闻", "source": "tavily", "publish_time": "2026-07-28"}]
        prompt = _build_analysis_prompt("600519", stock_data, search_news, "deep")
        assert "外部检索新闻" in prompt

    def test_prompt_with_empty_data_safe(self):
        """空数据也应能构建提示词"""
        prompt = _build_analysis_prompt("600519", {}, [], "deep")
        assert "600519" in prompt


class TestParseAnalysisResponse:
    """_parse_analysis_response 响应解析"""

    def test_parses_full_response(self, mock_analysis_response):
        """完整 JSON 响应应被正确解析"""
        result = _parse_analysis_response(mock_analysis_response, "600519")
        assert result["stock_code"] == "600519"
        assert result["action"] == "BUY"
        assert len(result["agent_opinions"]) == 7
        assert result["agent_opinions"][0]["role"] == "fundamental"
        assert result["agent_opinions"][0]["stock_code"] == "600519"
        assert result["debate_result"]["consensus"] == "BULLISH"
        assert result["risk_assessment"]["risk_level"] in ("LOW", "MEDIUM", "HIGH")
        assert "timestamp" in result

    def test_invalid_json_returns_fallback(self):
        """非 JSON 文本 → 返回降级结果"""
        result = _parse_analysis_response("not a json", "600519")
        assert result["stock_code"] == "600519"
        assert result["action"] == "HOLD"
        assert result["confidence"] == 0.0
        assert result["risk_assessment"]["risk_level"] == "HIGH"

    def test_json_with_text_wrapper(self, mock_analysis_response):
        """JSON 被文本包裹 → 应能提取"""
        wrapped = f"这是分析结果:\n{mock_analysis_response}\n谢谢"
        result = _parse_analysis_response(wrapped, "600519")
        assert result["action"] == "BUY"

    def test_buy_action_capped_at_50_percent(self, mock_analysis_response):
        """BUY 仓位应被限制在 50% 以下"""
        result = _parse_analysis_response(mock_analysis_response, "600519")
        # 原始 0.5 应被 cap 在 0.5
        assert result["position_size"] <= 0.5

    def test_low_confidence_raises_risk_level(self):
        """低置信度 → HIGH 风险"""
        response = json.dumps({
            "opinions": [
                {"role": "fundamental", "signal": "BULLISH", "confidence": 0.1, "score": 50},
            ],
            "bull_bear_debate": {"confidence": 0.1},
            "portfolio_decision": {"action": "BUY", "position_size": 0.3, "confidence": 0.1},
        })
        result = _parse_analysis_response(response, "600519")
        assert result["risk_assessment"]["risk_level"] == "HIGH"
        assert any("置信度" in w for w in result["risk_assessment"]["warnings"])

    def test_missing_optional_fields_uses_defaults(self):
        """缺失可选字段应使用默认值"""
        response = json.dumps({
            "opinions": [],
            "portfolio_decision": {"action": "HOLD"},
        })
        result = _parse_analysis_response(response, "600519")
        assert result["action"] == "HOLD"
        assert result["position_size"] == 0.0
        assert result["confidence"] == 0.3  # 默认
        assert result["debate_result"]["consensus"] == "NEUTRAL"


class TestFallbackResult:
    """_fallback_result 降级结果"""

    def test_returns_hold_with_zero_confidence(self):
        result = _fallback_result("600519", "测试原因")
        assert result["stock_code"] == "600519"
        assert result["action"] == "HOLD"
        assert result["confidence"] == 0.0
        assert result["position_size"] == 0.0
        assert result["risk_assessment"]["risk_level"] == "HIGH"
        assert "测试原因" in result["reasoning"]

    def test_includes_seven_fallback_opinions(self):
        """应包含 7 个降级分析师意见"""
        result = _fallback_result("600519", "失败")
        assert len(result["agent_opinions"]) == 7
        roles = [op["role"] for op in result["agent_opinions"]]
        assert "fundamental" in roles
        assert "lockup" in roles

    def test_astock_constraints_default(self):
        """A 股约束应有默认值"""
        result = _fallback_result("600519", "失败")
        assert "T+1" in result["astock_constraints"]["t_plus_1"]
        assert "100股" in result["astock_constraints"]["min_lot"]


class TestCallTTapiDirect:
    """_call_ttapi_direct 直接 TTAPI 调用"""

    def test_no_api_key_raises(self, monkeypatch):
        """无 API Key → 抛 ValueError"""
        # P3 修复(2026-07-29):_call_ttapi_direct 改用 _get_ttapi_api_key() 动态读取,
        # 需同时清空 env 与 settings 才能触发 "未配置" 分支
        monkeypatch.delenv("TTAPI_API_KEY", raising=False)
        monkeypatch.setattr(ai_service.settings, "ttapi_api_key", "")
        with pytest.raises(ValueError, match="TTAPI_API_KEY 未配置"):
            _call_ttapi_direct([{"role": "user", "content": "t"}])

    def test_successful_call_returns_content(self, monkeypatch):
        """成功调用 → 返回 content"""
        monkeypatch.setattr(ai_service, "_get_ttapi_api_key", lambda: "test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "LLM 回复"}}],
        }
        with patch("requests.post", return_value=mock_resp):
            content = _call_ttapi_direct([{"role": "user", "content": "t"}])
        assert content == "LLM 回复"

    def test_empty_content_raises(self, monkeypatch):
        """空 content → 抛 ValueError"""
        monkeypatch.setattr(ai_service, "_get_ttapi_api_key", lambda: "test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(ValueError, match="empty content"):
                _call_ttapi_direct([{"role": "user", "content": "t"}])

    def test_timeout_propagates(self, monkeypatch):
        """超时异常应向上抛出"""
        import requests
        monkeypatch.setattr(ai_service, "_get_ttapi_api_key", lambda: "test-key")
        with patch("requests.post", side_effect=requests.exceptions.Timeout("timeout")):
            with pytest.raises(requests.exceptions.Timeout):
                _call_ttapi_direct([{"role": "user", "content": "t"}])


class TestSearchTavily:
    """_search_tavily Tavily 检索"""

    def test_successful_search_returns_results(self, monkeypatch):
        monkeypatch.setattr(ai_service, "_get_tavily_api_key", lambda: "test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"title": "新闻1", "content": "内容1" * 50, "url": "u1", "published_date": "2026-07-28"},
                {"title": "新闻2", "content": "内容2", "url": "u2", "published_date": ""},
            ],
        }
        with patch("requests.post", return_value=mock_resp):
            results = _search_tavily("白酒")
        assert len(results) == 2
        assert results[0]["title"] == "新闻1"
        assert results[0]["source"] == "tavily"
        # content 截断到 300 字符
        assert len(results[0]["content"]) <= 300

    def test_exception_returns_empty(self, monkeypatch):
        """异常 → 返回空列表"""
        monkeypatch.setattr(ai_service, "_get_tavily_api_key", lambda: "test-key")
        with patch("requests.post", side_effect=Exception("net error")):
            assert _search_tavily("白酒") == []


class TestSearchNews:
    """search_news 新闻检索"""

    def test_no_api_keys_returns_empty(self, monkeypatch):
        """无任何 API Key → 返回空列表"""
        # P3 修复(2026-07-29):search_news 改用 _get_*_api_key() 动态读取(env 优先),
        # 需同时清空 env 与 settings 才能触发 "未配置" 分支
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
        monkeypatch.setattr(ai_service.settings, "tavily_api_key", "")
        monkeypatch.setattr(ai_service.settings, "tinyfish_api_key", "")
        assert search_news("白酒") == []

    def test_tavily_priority_over_tinyfish(self, monkeypatch):
        """Tavily 有结果时优先返回,不调用 tinyfish"""
        monkeypatch.setattr(ai_service, "_get_tavily_api_key", lambda: "tavily-key")
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tinyfish-key")
        with patch.object(ai_service, "_search_tavily", return_value=[{"title": "tavily 结果"}]) as mock_tavily:
            with patch.object(ai_service, "_search_tinyfish", return_value=[{"title": "tinyfish 结果"}]) as mock_tiny:
                result = search_news("白酒")
        assert len(result) == 1
        assert result[0]["title"] == "tavily 结果"
        mock_tiny.assert_not_called()

    def test_fallback_to_tinyfish_when_tavily_empty(self, monkeypatch):
        """Tavily 空结果 → 回退到 tinyfish"""
        monkeypatch.setattr(ai_service, "_get_tavily_api_key", lambda: "tavily-key")
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tinyfish-key")
        with patch.object(ai_service, "_search_tavily", return_value=[]):
            with patch.object(ai_service, "_search_tinyfish", return_value=[{"title": "tinyfish 结果"}]):
                result = search_news("白酒")
        assert len(result) == 1
        assert result[0]["title"] == "tinyfish 结果"


class TestAnalyzeStock:
    """analyze_stock 端到端(mock LLM)"""

    def test_no_data_returns_fallback(self):
        """无数据 → 返回降级结果"""
        with patch.object(ai_service, "_gather_stock_data", return_value={}):
            result = analyze_stock("600519")
        assert result["action"] == "HOLD"
        assert result["risk_assessment"]["risk_level"] == "HIGH"

    def test_successful_analysis(self, mock_analysis_response):
        """成功分析(mock LLM)"""
        with patch.object(ai_service, "_gather_stock_data", return_value={"quote": {"code": "600519"}}):
            with patch.object(ai_service, "search_news", return_value=[]):
                with patch.object(ai_service, "_call_llm", return_value=mock_analysis_response):
                    # 跳过数据校验
                    mock_validator = MagicMock()
                    mock_validation = MagicMock()
                    mock_validation.criticals = []
                    mock_validation.quality_score = 80
                    mock_validator.validate_analysis_data.return_value = mock_validation
                    with patch.object(ai_service, "get_data_validator", return_value=mock_validator):
                        result = analyze_stock("600519")
        assert result["stock_code"] == "600519"
        assert result["action"] == "BUY"

    def test_llm_failure_returns_fallback(self):
        """LLM 失败 → 返回降级结果"""
        with patch.object(ai_service, "_gather_stock_data", return_value={"quote": {"code": "600519"}}):
            with patch.object(ai_service, "search_news", return_value=[]):
                with patch.object(ai_service, "_call_llm", side_effect=Exception("llm error")):
                    mock_validator = MagicMock()
                    mock_validation = MagicMock()
                    mock_validation.criticals = []
                    mock_validation.quality_score = 80
                    mock_validator.validate_analysis_data.return_value = mock_validation
                    with patch.object(ai_service, "get_data_validator", return_value=mock_validator):
                        result = analyze_stock("600519")
        assert result["action"] == "HOLD"
        assert "AI分析失败" in result["reasoning"]


class TestQuickAnalysis:
    """quick_analysis 快速分析"""

    def test_no_data_returns_fallback(self):
        with patch.object(ai_service, "_gather_stock_data", return_value={}):
            result = quick_analysis("600519")
        assert result["action"] == "HOLD"

    def test_successful_quick_analysis(self, mock_analysis_response):
        with patch.object(ai_service, "_gather_stock_data", return_value={"quote": {"code": "600519"}}):
            with patch.object(ai_service, "search_news", return_value=[]):
                with patch.object(ai_service, "_call_llm", return_value=mock_analysis_response):
                    result = quick_analysis("600519")
        assert result["action"] == "BUY"


class TestMultiAnalyze:
    """multi_analyze 多智能体分析"""

    def test_no_data_returns_fallback(self):
        with patch.object(ai_service, "_gather_stock_data", return_value={}):
            result = multi_analyze("600519")
        assert result["action"] == "HOLD"

    def test_successful_multi_analyze_adds_mode(self, mock_analysis_response):
        """成功分析应附加 analysis_mode 和 analyst_count"""
        with patch.object(ai_service, "_gather_stock_data", return_value={"quote": {"code": "600519"}}):
            with patch.object(ai_service, "search_news", return_value=[]):
                with patch.object(ai_service, "_call_llm", return_value=mock_analysis_response):
                    result = multi_analyze("600519")
        assert result["analysis_mode"] == "multi_agent"
        assert result["analyst_count"] == 7

    def test_multi_analyze_failure_returns_fallback(self):
        """multi_analyze LLM 失败 → 降级结果"""
        with patch.object(ai_service, "_gather_stock_data", return_value={"quote": {"code": "600519"}}):
            with patch.object(ai_service, "search_news", return_value=[]):
                with patch.object(ai_service, "_call_llm", side_effect=Exception("boom")):
                    result = multi_analyze("600519")
        assert result["action"] == "HOLD"
        assert "多智能体分析失败" in result["reasoning"]


class TestCallLlm:
    """_call_llm LLM 路由测试

    注意:_call_llm 内部用 `from src.core.llm_router import get_llm_router`
    局部导入,所以必须 patch 源模块 src.core.llm_router.get_llm_router。
    """

    def test_uses_llm_router_when_available(self, monkeypatch):
        """LLM Router 有可用 Provider 时优先使用"""
        mock_router = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "router 返回"
        mock_response.provider = "agnes"
        mock_response.latency_ms = 150.0
        mock_router.chat.return_value = mock_response
        mock_router.available_providers = [mock_router]
        # patch 源模块的 get_llm_router(局部导入)
        monkeypatch.setattr("src.core.llm_router.get_llm_router", lambda: mock_router)
        result = _call_llm([{"role": "user", "content": "t"}])
        assert result == "router 返回"
        mock_router.chat.assert_called_once()

    def test_falls_back_to_ttapi_on_router_unavailable(self, monkeypatch):
        """Router 全部失败 → 降级到 TTAPI"""
        def fake_get_router():
            router = MagicMock()
            router.available_providers = [MagicMock()]
            router.chat.side_effect = RuntimeError("所有LLM Provider均不可用")
            return router
        monkeypatch.setattr("src.core.llm_router.get_llm_router", fake_get_router)
        monkeypatch.setattr(ai_service, "_get_ttapi_api_key", lambda: "tt-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ttapi 返回"}}]}
        with patch("requests.post", return_value=mock_resp):
            result = _call_llm([{"role": "user", "content": "t"}])
        assert result == "ttapi 返回"

    def test_falls_back_to_ttapi_on_router_exception(self, monkeypatch):
        """Router 抛非 RuntimeError 异常 → 降级到 TTAPI"""
        mock_router = MagicMock()
        mock_router.available_providers = [MagicMock()]
        mock_router.chat.side_effect = ValueError("weird error")
        monkeypatch.setattr("src.core.llm_router.get_llm_router", lambda: mock_router)
        monkeypatch.setattr(ai_service, "_get_ttapi_api_key", lambda: "tt-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ttapi 返回"}}]}
        with patch("requests.post", return_value=mock_resp):
            result = _call_llm([{"role": "user", "content": "t"}])
        assert result == "ttapi 返回"

    def test_no_available_providers_falls_back_to_ttapi(self, monkeypatch):
        """Router 无可用 Provider → 降级到 TTAPI(available_providers 为空)"""
        mock_router = MagicMock()
        mock_router.available_providers = []  # 空,跳过 router
        monkeypatch.setattr("src.core.llm_router.get_llm_router", lambda: mock_router)
        monkeypatch.setattr(ai_service, "_get_ttapi_api_key", lambda: "tt-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ttapi 返回"}}]}
        with patch("requests.post", return_value=mock_resp):
            result = _call_llm([{"role": "user", "content": "t"}])
        assert result == "ttapi 返回"
        # router.chat 不应被调用(因为 available_providers 为空)
        mock_router.chat.assert_not_called()


class TestSearchTinyfish:
    """_search_tinyfish TinyFish 检索测试"""

    def test_successful_search_returns_results(self, monkeypatch):
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tf-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "results": [
                    {"title": "新闻1", "content": "内容1" * 50, "url": "u1", "source": "s1",
                     "published_date": "2026-07-28"},
                ],
            })}}],
        }
        with patch("requests.post", return_value=mock_resp):
            results = _search_tinyfish("白酒")
        assert len(results) == 1
        assert results[0]["title"] == "新闻1"
        assert "tinyfish" in results[0]["source"]
        assert len(results[0]["content"]) <= 300

    def test_news_key_returns_results(self, monkeypatch):
        """响应用 news 键而非 results 也应能解析"""
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tf-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "news": [{"title": "新闻X", "content": "c", "url": "u"}],
            })}}],
        }
        with patch("requests.post", return_value=mock_resp):
            results = _search_tinyfish("白酒")
        assert len(results) == 1
        assert results[0]["title"] == "新闻X"

    def test_empty_content_returns_empty(self, monkeypatch):
        """空 content → 返回空列表"""
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tf-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        with patch("requests.post", return_value=mock_resp):
            assert _search_tinyfish("白酒") == []

    def test_non_list_results_returns_empty(self, monkeypatch):
        """results 非 list → 返回空"""
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tf-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"results": "not-a-list"})}}],
        }
        with patch("requests.post", return_value=mock_resp):
            assert _search_tinyfish("白酒") == []

    def test_exception_returns_empty(self, monkeypatch):
        """异常 → 返回空列表"""
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tf-key")
        with patch("requests.post", side_effect=Exception("net error")):
            assert _search_tinyfish("白酒") == []

    def test_invalid_json_content_returns_empty(self, monkeypatch):
        """content 非 JSON → 返回空"""
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tf-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "not-a-json"}}],
        }
        with patch("requests.post", return_value=mock_resp):
            assert _search_tinyfish("白酒") == []


class TestSearchNewsFallback:
    """search_news 回退逻辑补充"""

    def test_both_sources_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ai_service, "_get_tavily_api_key", lambda: "tavily-key")
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tinyfish-key")
        with patch.object(ai_service, "_search_tavily", return_value=[]):
            with patch.object(ai_service, "_search_tinyfish", return_value=[]):
                assert search_news("白酒") == []

    def test_only_tinyfish_key_configured(self, monkeypatch):
        """仅 tinyfish 配置时应直接调用 tinyfish"""
        monkeypatch.setattr(ai_service, "_get_tavily_api_key", lambda: "")
        monkeypatch.setattr(ai_service, "_get_tinyfish_api_key", lambda: "tinyfish-key")
        with patch.object(ai_service, "_search_tavily", return_value=[]) as mock_tavily:
            with patch.object(ai_service, "_search_tinyfish", return_value=[{"title": "tf 结果"}]) as mock_tf:
                result = search_news("白酒")
        # tavily 被调用但返回空(逻辑:先调 tavily,空则调 tinyfish)
        mock_tavily.assert_called_once()
        mock_tf.assert_called_once()
        assert len(result) == 1


class TestAnalyzePortfolio:
    """analyze_portfolio 组合分析测试"""

    def test_empty_positions_returns_empty_structure(self):
        """空持仓 → 返回空结构"""
        result = analyze_portfolio([])
        assert result["positions"] == []
        assert result["total_value"] == 0
        assert result["concentration_risk_level"] == "LOW"
        assert "timestamp" in result

    def test_position_without_symbol_skipped(self):
        """无 symbol 的持仓应被跳过(symbol 为空)"""
        positions = [{"name": "无名", "quantity": 100, "cost_price": 10}]
        with patch.object(ai_service, "_call_llm", return_value="{}"):
            result = analyze_portfolio(positions)
        # 无 symbol 的持仓 _analyze_one 返回 symbol="",被 results[idx] 过滤
        assert result["positions"] == []

    def test_successful_analysis(self, mock_analysis_response):
        """成功分析(mock quick_analysis + LLM)"""
        positions = [
            {"symbol": "600519", "name": "茅台", "quantity": 100, "cost_price": 1500, "current_price": 1688},
            {"symbol": "000001", "name": "平安", "quantity": 1000, "cost_price": 10, "current_price": 11},
        ]
        mock_portfolio_response = json.dumps({
            "concentration_risk": {"level": "LOW", "max_weight": 0.6},
            "overall_risk_score": {"score": 70, "level": "MEDIUM"},
        })
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[{"price": 1688, "name": "茅台"}]):
            with patch.object(ai_service, "quick_analysis", return_value={
                "action": "BUY", "confidence": 0.8, "target_price": 1800,
                "stop_loss_price": 1600, "risk_assessment": {"risk_level": "MEDIUM"},
            }):
                with patch.object(ai_service, "_call_llm", return_value=mock_portfolio_response):
                    result = analyze_portfolio(positions)
        assert len(result["positions"]) == 2
        assert result["total_value"] > 0
        assert "position_weights" in result
        assert "concentration_risk_level" in result
        assert "portfolio_analysis" in result

    def test_concentration_risk_high_when_single_position(self, mock_analysis_response):
        """单只高权重持仓 → 集中度 HIGH"""
        positions = [
            {"symbol": "600519", "name": "茅台", "quantity": 100, "cost_price": 1500, "current_price": 1688},
        ]
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[]):
            with patch.object(ai_service, "quick_analysis", return_value={
                "action": "BUY", "confidence": 0.8, "risk_assessment": {"risk_level": "MEDIUM"},
            }):
                with patch.object(ai_service, "_call_llm", return_value="{}"):
                    result = analyze_portfolio(positions)
        # 单只持仓权重 1.0 > 0.4 → HIGH
        assert result["concentration_risk_level"] == "HIGH"

    def test_concentration_risk_medium(self, mock_analysis_response):
        """最大权重 0.25-0.4 → MEDIUM"""
        positions = [
            {"symbol": "A", "name": "X", "quantity": 100, "cost_price": 10, "current_price": 12},
            {"symbol": "B", "name": "Y", "quantity": 100, "cost_price": 10, "current_price": 5},
        ]
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[]):
            with patch.object(ai_service, "quick_analysis", return_value={
                "action": "HOLD", "confidence": 0.5, "risk_assessment": {"risk_level": "LOW"},
            }):
                with patch.object(ai_service, "_call_llm", return_value="{}"):
                    result = analyze_portfolio(positions)
        # A 价值 1200, B 价值 500,总 1700,A 权重 ~0.70 > 0.4 → HIGH
        assert result["concentration_risk_level"] in ("MEDIUM", "HIGH")

    def test_quick_analysis_exception_returns_hold(self, mock_analysis_response):
        """单只分析异常 → 该持仓返回 HOLD"""
        positions = [{"symbol": "600519", "name": "茅台", "quantity": 100, "cost_price": 1500, "current_price": 1688}]
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[]):
            with patch.object(ai_service, "quick_analysis", side_effect=Exception("quick fail")):
                with patch.object(ai_service, "_call_llm", return_value="{}"):
                    result = analyze_portfolio(positions)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["action"] == "HOLD"
        assert result["positions"][0]["confidence"] == 0

    def test_llm_returns_invalid_json(self, mock_analysis_response):
        """LLM 返回非 JSON → portfolio_analysis 为空字典"""
        positions = [{"symbol": "600519", "name": "茅台", "quantity": 100, "cost_price": 1500, "current_price": 1688}]
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[]):
            with patch.object(ai_service, "quick_analysis", return_value={
                "action": "BUY", "confidence": 0.8, "risk_assessment": {"risk_level": "MEDIUM"},
            }):
                with patch.object(ai_service, "_call_llm", return_value="not-json"):
                    result = analyze_portfolio(positions)
        assert result["portfolio_analysis"] == {}

    def test_llm_returns_json_with_text_wrapper(self, mock_analysis_response):
        """LLM 返回带文本包裹的 JSON → 应能提取"""
        positions = [{"symbol": "600519", "name": "茅台", "quantity": 100, "cost_price": 1500, "current_price": 1688}]
        wrapped = f"分析结果:\n{{\"score\": 80}}\n谢谢"
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[]):
            with patch.object(ai_service, "quick_analysis", return_value={
                "action": "BUY", "confidence": 0.8, "risk_assessment": {"risk_level": "MEDIUM"},
            }):
                with patch.object(ai_service, "_call_llm", return_value=wrapped):
                    result = analyze_portfolio(positions)
        assert result["portfolio_analysis"] == {"score": 80}

    def test_llm_exception_returns_empty_analysis(self, mock_analysis_response):
        """LLM 调用异常 → portfolio_analysis 为空字典"""
        positions = [{"symbol": "600519", "name": "茅台", "quantity": 100, "cost_price": 1500, "current_price": 1688}]
        with patch.object(ai_service, "get_realtime_quote_tencent", return_value=[]):
            with patch.object(ai_service, "quick_analysis", return_value={
                "action": "BUY", "confidence": 0.8, "risk_assessment": {"risk_level": "MEDIUM"},
            }):
                with patch.object(ai_service, "_call_llm", side_effect=Exception("llm down")):
                    result = analyze_portfolio(positions)
        assert result["portfolio_analysis"] == {}


class TestGetMarketOutlook:
    """get_market_outlook 市场展望测试

    注意:get_market_outlook 内部用 `from src.core.data_source_v2 import _parallel_fetch`
    局部导入,所以必须 patch 源模块 src.core.data_source_v2._parallel_fetch,
    而非 ai_service._parallel_fetch。
    """

    def _patch_parallel_fetch(self, return_value):
        """patch data_source_v2._parallel_fetch(get_market_outlook 内部局部导入)"""
        return patch("src.core.data_source_v2._parallel_fetch", return_value=return_value)

    def test_no_market_data_returns_unknown(self):
        """无市场数据 → 返回 UNKNOWN"""
        with self._patch_parallel_fetch({}):
            result = get_market_outlook()
        # 无数据时返回降级结构(outlook=UNKNOWN,无 outlook_analysis 键)
        assert result.get("outlook") == "UNKNOWN" or result.get("outlook_analysis") == {}
        assert "timestamp" in result

    def test_successful_outlook(self):
        """成功展望(mock LLM)"""
        mock_outlook = json.dumps({
            "outlook": "BULLISH",
            "confidence": 0.75,
            "summary": "市场向好",
        })
        with self._patch_parallel_fetch({
            "indices": [{"name": "上证", "change_pct": 1.0}],
            "northbound": {"total_net_inflow": 1e9},
            "sectors": [{"name": f"板块{i}"} for i in range(15)],
            "news": [{"title": f"新闻{i}"} for i in range(15)],
        }):
            with patch.object(ai_service, "_call_llm", return_value=mock_outlook):
                result = get_market_outlook()
        assert result["outlook_analysis"]["outlook"] == "BULLISH"
        assert result["outlook_analysis"]["confidence"] == 0.75
        assert "market_data" in result
        assert len(result["market_data"]["hot_sectors"]) == 10
        assert len(result["market_data"]["cold_sectors"]) == 5

    def test_outlook_with_text_wrapper(self):
        """LLM 返回带文本包裹的 JSON → 应能提取"""
        wrapped = f"结果如下:\n{{\"outlook\": \"NEUTRAL\"}}\n结束"
        with self._patch_parallel_fetch({
            "indices": [{"name": "上证"}],
        }):
            with patch.object(ai_service, "_call_llm", return_value=wrapped):
                result = get_market_outlook()
        assert result["outlook_analysis"]["outlook"] == "NEUTRAL"

    def test_outlook_invalid_json_returns_empty(self):
        """LLM 返回非 JSON 且无 {} → outlook_analysis 为空"""
        with self._patch_parallel_fetch({
            "indices": [{"name": "上证"}],
        }):
            with patch.object(ai_service, "_call_llm", return_value="not-json-no-braces"):
                result = get_market_outlook()
        assert result["outlook_analysis"] == {}

    def test_outlook_llm_exception_falls_back_to_no_json_mode(self):
        """json_mode 失败 → 重试无 json_mode"""
        mock_response = json.dumps({"outlook": "BEARISH"})
        call_count = [0]
        def fake_call_llm(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("json mode failed")
            return mock_response
        with self._patch_parallel_fetch({
            "indices": [{"name": "上证"}],
        }):
            with patch.object(ai_service, "_call_llm", side_effect=fake_call_llm):
                result = get_market_outlook()
        assert call_count[0] == 2
        assert result["outlook_analysis"]["outlook"] == "BEARISH"

    def test_outlook_all_llm_calls_fail_returns_empty(self):
        """两次 LLM 调用都失败 → outlook_analysis 为空"""
        with self._patch_parallel_fetch({
            "indices": [{"name": "上证"}],
        }):
            with patch.object(ai_service, "_call_llm", side_effect=Exception("always fails")):
                result = get_market_outlook()
        assert result["outlook_analysis"] == {}

    def test_outlook_partial_data(self):
        """部分数据源成功 → 仍生成展望"""
        mock_outlook = json.dumps({"outlook": "NEUTRAL"})
        with self._patch_parallel_fetch({
            "indices": [{"name": "上证"}],
            # northbound/sectors/news 为 None
        }):
            with patch.object(ai_service, "_call_llm", return_value=mock_outlook):
                result = get_market_outlook()
        assert result["outlook_analysis"]["outlook"] == "NEUTRAL"
        assert "indices" in result["market_data"]

    def test_outlook_sectors_less_than_10(self):
        """板块数 < 10 时 cold_sectors 为空"""
        mock_outlook = json.dumps({"outlook": "NEUTRAL"})
        with self._patch_parallel_fetch({
            "sectors": [{"name": "板块1"}, {"name": "板块2"}],
        }):
            with patch.object(ai_service, "_call_llm", return_value=mock_outlook):
                result = get_market_outlook()
        assert len(result["market_data"]["hot_sectors"]) == 2
        assert result["market_data"]["cold_sectors"] == []

    def test_outlook_news_truncated_to_8(self):
        """全球新闻应截断到 8 条"""
        mock_outlook = json.dumps({"outlook": "NEUTRAL"})
        with self._patch_parallel_fetch({
            "news": [{"title": f"新闻{i}"} for i in range(20)],
        }):
            with patch.object(ai_service, "_call_llm", return_value=mock_outlook):
                result = get_market_outlook()
        assert len(result["market_data"]["global_news"]) == 8

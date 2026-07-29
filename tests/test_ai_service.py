"""AI 服务模块单元测试

验证 src/core/ai_service.py 的核心逻辑:
- _check_api_keys API 密钥检查
- _gather_stock_data 数据并行抓取(mock)
- _build_analysis_prompt 提示词构建
- _parse_analysis_response 响应解析
- _fallback_result 降级结果
- _call_ttapi_direct 直接 TTAPI 调用(mock requests)
- _search_tavily Tavily 检索(mock requests)
- search_news 新闻检索(mock)
- analyze_stock / quick_analysis / multi_analyze 端到端(mock LLM)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core import ai_service
from src.core.ai_service import (
    _build_analysis_prompt,
    _call_ttapi_direct,
    _check_api_keys,
    _fallback_result,
    _gather_stock_data,
    _parse_analysis_response,
    _search_tavily,
    analyze_stock,
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
        monkeypatch.setattr(ai_service, "_TTAPI_API_KEY", "")
        with pytest.raises(ValueError, match="TTAPI_API_KEY 未配置"):
            _call_ttapi_direct([{"role": "user", "content": "t"}])

    def test_successful_call_returns_content(self, monkeypatch):
        """成功调用 → 返回 content"""
        monkeypatch.setattr(ai_service, "_TTAPI_API_KEY", "test-key")
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
        monkeypatch.setattr(ai_service, "_TTAPI_API_KEY", "test-key")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(ValueError, match="empty content"):
                _call_ttapi_direct([{"role": "user", "content": "t"}])

    def test_timeout_propagates(self, monkeypatch):
        """超时异常应向上抛出"""
        import requests
        monkeypatch.setattr(ai_service, "_TTAPI_API_KEY", "test-key")
        with patch("requests.post", side_effect=requests.exceptions.Timeout("timeout")):
            with pytest.raises(requests.exceptions.Timeout):
                _call_ttapi_direct([{"role": "user", "content": "t"}])


class TestSearchTavily:
    """_search_tavily Tavily 检索"""

    def test_successful_search_returns_results(self, monkeypatch):
        monkeypatch.setattr(ai_service, "_TAVILY_API_KEY", "test-key")
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
        monkeypatch.setattr(ai_service, "_TAVILY_API_KEY", "test-key")
        with patch("requests.post", side_effect=Exception("net error")):
            assert _search_tavily("白酒") == []


class TestSearchNews:
    """search_news 新闻检索"""

    def test_no_api_keys_returns_empty(self, monkeypatch):
        """无任何 API Key → 返回空列表"""
        monkeypatch.setattr(ai_service, "_TAVILY_API_KEY", "")
        monkeypatch.setattr(ai_service, "_TINYFISH_API_KEY", "")
        assert search_news("白酒") == []

    def test_tavily_priority_over_tinyfish(self, monkeypatch):
        """Tavily 有结果时优先返回,不调用 tinyfish"""
        monkeypatch.setattr(ai_service, "_TAVILY_API_KEY", "tavily-key")
        monkeypatch.setattr(ai_service, "_TINYFISH_API_KEY", "tinyfish-key")
        with patch.object(ai_service, "_search_tavily", return_value=[{"title": "tavily 结果"}]) as mock_tavily:
            with patch.object(ai_service, "_search_tinyfish", return_value=[{"title": "tinyfish 结果"}]) as mock_tiny:
                result = search_news("白酒")
        assert len(result) == 1
        assert result[0]["title"] == "tavily 结果"
        mock_tiny.assert_not_called()

    def test_fallback_to_tinyfish_when_tavily_empty(self, monkeypatch):
        """Tavily 空结果 → 回退到 tinyfish"""
        monkeypatch.setattr(ai_service, "_TAVILY_API_KEY", "tavily-key")
        monkeypatch.setattr(ai_service, "_TINYFISH_API_KEY", "tinyfish-key")
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

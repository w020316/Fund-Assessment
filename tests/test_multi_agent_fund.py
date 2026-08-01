"""多智能体基金分析测试(P1-5)

验证 src/analysis/multi_agent_fund.py 的核心逻辑:
- ANALYST_ROLES 定义完整性
- _build_fund_analysis_prompt 构建
- _parse_fund_analysis_response 解析
- _fallback_fund_result 兜底
- 端到端 analyze_fund_with_agents(mock LLM)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.analysis import multi_agent_fund
from src.analysis.multi_agent_fund import (
    ANALYST_ROLES,
    _build_fund_analysis_prompt,
    _fallback_fund_result,
    _parse_fund_analysis_response,
    analyze_fund_with_agents,
)


class TestAnalystRoles:
    """7角色定义完整性"""

    def test_seven_roles_defined(self):
        assert len(ANALYST_ROLES) == 7

    def test_roles_cover_required_dimensions(self):
        roles = [r["role"] for r in ANALYST_ROLES]
        expected = {"news", "fund", "sector", "technical", "fundamental", "risk", "macro"}
        assert set(roles) == expected

    def test_each_role_has_required_fields(self):
        for role in ANALYST_ROLES:
            assert "role" in role
            assert "name" in role
            assert "responsibility" in role
            assert "data_needed" in role
            assert isinstance(role["data_needed"], list)


class TestBuildFundAnalysisPrompt:
    """Prompt 构建"""

    def test_prompt_contains_fund_code(self):
        context = {
            "fund_quote": {"nav": 1.5, "change_pct": 1.2},
            "nav_history": [{"date": "2026-07-28", "nav": 1.5, "change_pct": 1.2}],
            "news_data": {"sentiment_index": 65, "total_count": 10, "hot_events": []},
            "holdings_data": {"holdings": [], "concentration": {}, "nav_impact": {}, "sector_rotation": []},
            "stock_quotes": [],
            "thermometer": {"score": 60, "level": "偏热", "action": "谨慎"},
            "five_signals": {"final_score": 65, "direction": "bullish", "signal": "hold", "action": "持有"},
        }
        prompt = _build_fund_analysis_prompt("110022", "测试基金", context, 1.0, 1000, "deep")
        assert "110022" in prompt
        assert "测试基金" in prompt
        assert "7位" in prompt or "7 位" in prompt

    def test_prompt_contains_all_roles(self):
        context = {}
        prompt = _build_fund_analysis_prompt("110022", "测试基金", context, 1.0, 1000, "deep")
        for role in ["news", "fund", "sector", "technical", "fundamental", "risk", "macro"]:
            assert role in prompt

    def test_prompt_contains_a_share_constraints(self):
        """A股特有约束"""
        context = {}
        prompt = _build_fund_analysis_prompt("110022", "测试基金", context, 1.0, 1000, "deep")
        assert "涨跌停" in prompt or "T+1" in prompt
        assert "北向资金" in prompt

    def test_quick_mode_shorter_instruction(self):
        context = {}
        quick_prompt = _build_fund_analysis_prompt("110022", "测试", context, 1.0, 1000, "quick")
        deep_prompt = _build_fund_analysis_prompt("110022", "测试", context, 1.0, 1000, "deep")
        # quick 模式的指令应更简短
        assert "快速分析" in quick_prompt
        assert "深度分析" in deep_prompt


class TestParseFundAnalysisResponse:
    """响应解析"""

    def test_valid_json_response(self):
        response = '''
{
  "opinions": [
    {"role": "news", "signal": "BULLISH", "confidence": 0.8, "reasoning": "消息面利好", "key_points": ["a"], "score": 75}
  ],
  "bull_bear_debate": {"topic": "测试", "bull_arguments": ["a"], "bear_arguments": ["b"], "bull_score": 70, "bear_score": 30, "consensus": "BULLISH", "confidence": 0.7},
  "risk_debate": {"top_risks": ["r1"], "mitigations": ["m1"], "risk_level": "LOW", "risk_score": 20},
  "portfolio_manager_decision": {"action": "BUY", "confidence": 0.8, "target_price": 2.0, "stop_loss_price": 1.5, "position_sizing": "60%", "reasoning": "看多", "key_factors": ["f1"]},
  "final_recommendation": {"action": "加仓", "confidence": 0.8, "time_horizon": "中期", "expected_return": "10%", "key_risks": ["r1"]}
}
'''
        result = _parse_fund_analysis_response(response, "110022")
        assert result["fund_code"] == "110022"
        assert len(result["agent_opinions"]) == 1
        assert result["action"] == "BUY"
        assert result["confidence"] == 0.8
        assert result["analysis_mode"] == "multi_agent_fund"
        assert result["analyst_count"] == 1

    def test_markdown_code_block_response(self):
        """带 markdown 代码块的响应"""
        response = '''```json
{"opinions": [], "bull_bear_debate": {}, "risk_debate": {}, "portfolio_manager_decision": {"action": "HOLD", "confidence": 0.5}, "final_recommendation": {"action": "持有"}}
```'''
        result = _parse_fund_analysis_response(response, "110022")
        assert result["action"] == "HOLD"

    def test_invalid_json_returns_fallback(self):
        result = _parse_fund_analysis_response("not a json", "110022")
        assert result["action"] == "HOLD"
        assert result["final_recommendation"]["action"] == "持有"

    def test_empty_response_returns_fallback(self):
        result = _parse_fund_analysis_response("", "110022")
        assert result["action"] == "HOLD"
        assert result["final_recommendation"]["action"] == "持有"


class TestFallbackResult:
    """兜底结果"""

    def test_fallback_structure(self):
        result = _fallback_fund_result("110022", "测试失败")
        assert result["fund_code"] == "110022"
        assert result["action"] == "HOLD"
        assert result["confidence"] == 0.0
        assert result["analyst_count"] == 0
        assert "测试失败" in result["final_recommendation"]["reason"]
        assert result["final_recommendation"]["action"] == "持有"


class TestAnalyzeFundWithAgents:
    """端到端测试(mock LLM)"""

    @pytest.mark.asyncio
    async def test_analyze_with_mock_llm(self):
        """完整流程(mock 所有外部依赖)"""
        mock_nav_history = [{"date": f"2026-07-{i:02d}", "nav": 1.0 + i * 0.01, "change_pct": 1.0} for i in range(1, 11)]
        mock_quotes = [{"code": "110022", "nav": 1.5, "name": "测试基金", "change_pct": 1.5}]
        mock_holdings = {
            "holdings": [{"code": "600519", "name": "贵州茅台", "weight": 9.85}],
            "concentration": {"top5_weight": 40, "hhi": 1500, "level": "中等集中"},
            "nav_impact": {"estimated_change_pct": 0.3},
            "sector_rotation": [{"sector": "白酒", "weight": 9.85, "change_pct": 2.0, "signal": "强势"}],
        }
        mock_news = {"sentiment_index": 70, "total_count": 15, "hot_events": [{"title": "利好消息", "sentiment": "利好"}]}
        mock_thermometer = {"score": 65, "level": "偏热", "action": "谨慎乐观", "components": {}}
        mock_stock_quotes = [{"code": "600519", "pe_ttm": 25, "pb": 8.0}]
        mock_five_signals = {"five_signals": {"final_score": 70, "direction": "bullish", "signal": "hold", "action": "持有"}}

        # Mock LLM 响应
        mock_llm_response = MagicMock()
        mock_llm_response.content = '{"opinions": [{"role": "news", "signal": "BULLISH", "confidence": 0.8, "reasoning": "ok", "key_points": ["a"], "score": 75}], "bull_bear_debate": {"consensus": "BULLISH"}, "risk_debate": {"risk_level": "LOW"}, "portfolio_manager_decision": {"action": "BUY", "confidence": 0.8, "target_price": 2.0, "stop_loss_price": 1.4}, "final_recommendation": {"action": "加仓", "confidence": 0.8}}'

        mock_router = MagicMock()
        mock_router.chat.return_value = mock_llm_response

        with patch.object(multi_agent_fund.ds2, "get_fund_history_tencent", return_value=mock_nav_history):
            with patch.object(multi_agent_fund.ds2, "get_fund_realtime_tencent", return_value=mock_quotes):
                with patch.object(multi_agent_fund.ds2, "get_realtime_quote_tencent", return_value=mock_stock_quotes):
                    with patch("src.analysis.fund_holdings.analyze_fund_holdings", return_value=mock_holdings):
                        with patch("src.analysis.news_aggregator.get_news_feed", return_value=mock_news):
                            with patch("src.analysis.market_assessment.get_market_thermometer", return_value=mock_thermometer):
                                with patch("src.analysis.fund_advisor_v2.analyze_fund_five_signals", return_value=mock_five_signals):
                                    with patch("src.core.llm_router.get_llm_router", return_value=mock_router):
                                        result = await analyze_fund_with_agents(
                                            fund_code="110022",
                                            fund_name="测试基金",
                                            cost_nav=1.0,
                                            shares=1000,
                                            mode="deep",
                                        )

        assert result["fund_code"] == "110022"
        assert result["action"] == "BUY"
        assert result["analyst_count"] == 1
        assert result["analysis_mode"] == "multi_agent_fund"
        assert "data_snapshot" in result
        assert result["data_snapshot"]["current_nav"] == 1.5
        assert result["data_snapshot"]["holdings_count"] == 1
        assert result["data_snapshot"]["news_count"] == 15

    @pytest.mark.asyncio
    async def test_analyze_llm_failure_returns_fallback(self):
        """LLM 调用失败 → 返回兜底结果"""
        mock_router = MagicMock()
        mock_router.chat.side_effect = RuntimeError("LLM 不可用")

        with patch.object(multi_agent_fund.ds2, "get_fund_history_tencent", return_value=[]):
            with patch.object(multi_agent_fund.ds2, "get_fund_realtime_tencent", return_value=[]):
                with patch.object(multi_agent_fund.ds2, "get_realtime_quote_tencent", return_value=[]):
                    with patch("src.analysis.fund_holdings.analyze_fund_holdings", return_value={}):
                        with patch("src.analysis.news_aggregator.get_news_feed", return_value={}):
                            with patch("src.analysis.market_assessment.get_market_thermometer", return_value={}):
                                with patch("src.analysis.fund_advisor_v2.analyze_fund_five_signals", return_value={}):
                                    with patch("src.core.llm_router.get_llm_router", return_value=mock_router):
                                        result = await analyze_fund_with_agents(
                                            fund_code="110022",
                                            fund_name="测试基金",
                                        )

        assert result["action"] == "HOLD"
        assert result["final_recommendation"]["action"] == "持有"
        assert "多智能体分析失败" in result["final_recommendation"]["reason"]

    @pytest.mark.asyncio
    async def test_data_source_failures_isolated(self):
        """数据源失败不影响整体流程"""
        mock_router = MagicMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = '{"opinions": [], "portfolio_manager_decision": {"action": "HOLD", "confidence": 0.5}, "final_recommendation": {"action": "持有"}}'
        mock_router.chat.return_value = mock_llm_response

        with patch.object(multi_agent_fund.ds2, "get_fund_history_tencent", side_effect=Exception("nav fail")):
            with patch.object(multi_agent_fund.ds2, "get_fund_realtime_tencent", side_effect=Exception("quote fail")):
                with patch.object(multi_agent_fund.ds2, "get_realtime_quote_tencent", side_effect=Exception("stock fail")):
                    with patch("src.analysis.fund_holdings.analyze_fund_holdings", side_effect=Exception("holdings fail")):
                        with patch("src.analysis.news_aggregator.get_news_feed", side_effect=Exception("news fail")):
                            with patch("src.analysis.market_assessment.get_market_thermometer", side_effect=Exception("market fail")):
                                with patch("src.analysis.fund_advisor_v2.analyze_fund_five_signals", side_effect=Exception("signals fail")):
                                    with patch("src.core.llm_router.get_llm_router", return_value=mock_router):
                                        result = await analyze_fund_with_agents(
                                            fund_code="110022",
                                            fund_name="测试基金",
                                        )

        # 即使数据源全部失败,LLM 仍然能基于空数据返回结果
        assert result["fund_code"] == "110022"
        assert result["action"] == "HOLD"
        # 数据快照应该是默认值
        assert result["data_snapshot"]["current_nav"] == 0
        assert result["data_snapshot"]["holdings_count"] == 0

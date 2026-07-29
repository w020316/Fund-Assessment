"""消息面聚合引擎单元测试

验证 src/analysis/news_aggregator.py 的核心逻辑:
- _classify_sentiment 关键词情绪分类
- _deduplicate 标题相似度去重
- _build_news_item 统一新闻项结构
- _calc_sentiment_index 情绪指数计算
- _fetch_finance_news/_fetch_research_reports/_fetch_sentiment_hot/_fetch_stock_news(mock)
- get_news_feed 端到端聚合(多源 mock)
- get_hot_events / get_sentiment_index / ai_search
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from src.analysis import news_aggregator
from src.analysis.news_aggregator import (
    _build_news_item,
    _calc_sentiment_index,
    _classify_sentiment,
    _deduplicate,
    _fetch_finance_news,
    _fetch_research_reports,
    _fetch_sentiment_hot,
    _fetch_stock_news,
    ai_search,
    get_hot_events,
    get_news_feed,
    get_sentiment_index,
)


class TestClassifySentiment:
    """_classify_sentiment 关键词情绪分类"""

    def test_positive_classification(self):
        """利好关键词多于利空 → 利好"""
        assert _classify_sentiment("公司发布利好公告 净利润增长") == "利好"

    def test_negative_classification(self):
        """利空关键词多于利好 → 利空"""
        assert _classify_sentiment("公司遭遇利空 净利润亏损下滑") == "利空"

    def test_neutral_classification(self):
        """无关键词或平衡 → 中性"""
        assert _classify_sentiment("公司今日召开股东大会") == "中性"

    def test_balanced_returns_neutral(self):
        """利好利空各一 → 中性(不大于)"""
        assert _classify_sentiment("利好 下跌") == "中性"

    def test_content_participates(self):
        """content 也参与分类"""
        assert _classify_sentiment("公告", "公司利空亏损") == "利空"

    def test_empty_text_returns_neutral(self):
        assert _classify_sentiment("", "") == "中性"


class TestDeduplicate:
    """_deduplicate 标题相似度去重"""

    def test_empty_list_returns_empty(self):
        assert _deduplicate([]) == []

    def test_unique_titles_preserved(self):
        """完全不同标题 → 全部保留"""
        items = [
            {"title": "贵州茅台发布年报"},
            {"title": "招商银行推出新产品"},
            {"title": "中芯国际产能扩张"},
        ]
        result = _deduplicate(items)
        assert len(result) == 3

    def test_near_duplicates_removed(self):
        """高度相似标题 → 仅保留最新一条"""
        items = [
            {"title": "贵州茅台发布2026年一季报"},
            {"title": "贵州茅台发布2026年一季报。"},  # 仅多一个句号
        ]
        result = _deduplicate(items, similarity_threshold=0.8)
        assert len(result) == 1

    def test_custom_threshold(self):
        """自定义相似度阈值"""
        items = [
            {"title": "茅台一季报"},
            {"title": "茅台半年报"},
        ]
        # 相似度约 0.6,阈值 0.8 不去重
        result_high = _deduplicate(items, similarity_threshold=0.8)
        assert len(result_high) == 2
        # 阈值 0.5 应去重
        result_low = _deduplicate(items, similarity_threshold=0.5)
        assert len(result_low) == 1

    def test_preserves_first_occurrence(self):
        """重复时保留先出现的那条"""
        items = [
            {"title": "贵州茅台发布年报", "source": "第一来源"},
            {"title": "贵州茅台发布年报", "source": "第二来源"},
        ]
        result = _deduplicate(items, similarity_threshold=0.8)
        assert len(result) == 1
        assert result[0]["source"] == "第一来源"


class TestBuildNewsItem:
    """_build_news_item 统一新闻项结构"""

    def test_builds_all_fields(self):
        raw = {
            "title": "利好公告",
            "content": "净利润增长" * 50,  # 超过 200 字
            "source": "东方财富",
            "publish_time": "2026-07-28",
            "category": "财经新闻",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "url": "https://example.com",
        }
        item = _build_news_item(raw)
        assert item["title"] == "利好公告"
        assert item["source"] == "东方财富"
        assert item["category"] == "财经新闻"
        assert item["stock_code"] == "600519"
        assert item["stock_name"] == "贵州茅台"
        assert item["url"] == "https://example.com"
        assert item["sentiment"] == "利好"
        # content 截断至 200 字符
        assert len(item["content"]) <= 200

    def test_missing_fields_default_empty(self):
        """缺失字段应默认空字符串"""
        item = _build_news_item({})
        assert item["title"] == ""
        assert item["content"] == ""
        assert item["source"] == ""
        assert item["sentiment"] == "中性"

    def test_empty_content_safe(self):
        """content 为空时不报错"""
        item = _build_news_item({"title": "t", "content": ""})
        assert item["content"] == ""


class TestCalcSentimentIndex:
    """_calc_sentiment_index 情绪指数计算"""

    def test_empty_list_returns_50(self):
        """无新闻 → 50(中性)"""
        assert _calc_sentiment_index([]) == 50

    def test_all_positive_high_index(self):
        """全部利好 → 100"""
        items = [{"sentiment": "利好"}] * 5
        assert _calc_sentiment_index(items) == 100

    def test_all_negative_low_index(self):
        """全部利空 → 0"""
        items = [{"sentiment": "利空"}] * 5
        assert _calc_sentiment_index(items) == 0

    def test_balanced_returns_50(self):
        """利好利空各半 → 50"""
        items = [{"sentiment": "利好"}, {"sentiment": "利空"}]
        assert _calc_sentiment_index(items) == 50

    def test_index_bounded_0_100(self):
        """指数应在 [0, 100] 范围内"""
        items = [{"sentiment": "利好"}, {"sentiment": "中性"}, {"sentiment": "利空"}]
        idx = _calc_sentiment_index(items)
        assert 0 <= idx <= 100


class TestFetchers:
    """_fetch_* 各数据源抓取"""

    def test_fetch_finance_news_adds_category(self):
        """财经新闻应附加 category='财经新闻'"""
        mock_data = [{"title": "t1", "content": "c1"}]
        with patch.object(news_aggregator.ds2, "get_global_news", return_value=mock_data):
            result = _fetch_finance_news(limit=5)
        assert len(result) == 1
        assert result[0]["category"] == "财经新闻"

    def test_fetch_finance_news_exception_returns_empty(self):
        """抓取异常 → 返回空列表"""
        with patch.object(news_aggregator.ds2, "get_global_news", side_effect=Exception("net")):
            assert _fetch_finance_news() == []

    def test_fetch_research_reports_adds_category(self):
        """研报应附加 category='研报'"""
        mock_data = [{"title": "t1", "content": "c1"}]
        with patch.object(news_aggregator.ds2, "get_research_reports", return_value=mock_data):
            result = _fetch_research_reports(limit=5)
        assert result[0]["category"] == "研报"

    def test_fetch_research_reports_exception_returns_empty(self):
        with patch.object(news_aggregator.ds2, "get_research_reports", side_effect=Exception("net")):
            assert _fetch_research_reports() == []

    def test_fetch_stock_news_adds_code_and_category(self):
        """个股新闻应附加 stock_code 与 category"""
        mock_data = [{"title": "t1", "content": "c1"}]
        with patch.object(news_aggregator.ds2, "get_stock_news", return_value=mock_data):
            result = _fetch_stock_news("600519", limit=5)
        assert result[0]["stock_code"] == "600519"
        assert result[0]["category"] == "个股新闻"

    def test_fetch_sentiment_hot_with_akshare(self):
        """akshare 可用时返回人气榜数据"""
        df = pd.DataFrame([
            {"股票代码": "600519", "股票名称": "贵州茅台", "关注度": "100w"},
        ])
        with patch.object(news_aggregator.ak, "stock_hot_follow_em", return_value=df, create=True):
            result = _fetch_sentiment_hot(limit=5)
        assert len(result) == 1
        assert result[0]["stock_code"] == "600519"
        assert result[0]["category"] == "舆情热度"

    def test_fetch_sentiment_hot_exception_returns_empty(self):
        """akshare 抓取异常 → 返回空列表"""
        with patch.object(news_aggregator.ak, "stock_hot_follow_em", side_effect=Exception("net"), create=True):
            assert _fetch_sentiment_hot() == []


class TestGetNewsFeed:
    """get_news_feed 端到端聚合"""

    @pytest.mark.asyncio
    async def test_returns_required_structure(self):
        """应返回完整结构(hot_events/sentiment_index/key_news/total_count/query)"""
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=[]):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=[]):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=[]):
                    result = await get_news_feed()
        assert "hot_events" in result
        assert "sentiment_index" in result
        assert "key_news" in result
        assert "total_count" in result
        assert "query" in result
        assert result["sentiment_index"] == 50  # 无新闻

    @pytest.mark.asyncio
    async def test_aggregates_multiple_sources(self):
        """应聚合多源新闻"""
        finance = [{"title": "财经新闻1", "content": "c", "source": "s", "publish_time": "t"}]
        reports = [{"title": "研报1", "content": "c", "source": "s", "publish_time": "t"}]
        hot = [{"title": "人气股1", "content": "c", "source": "s", "publish_time": "t",
                "stock_code": "600519", "stock_name": "贵州茅台"}]
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=finance):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=reports):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=hot):
                    result = await get_news_feed()
        assert result["total_count"] == 3
        assert len(result["hot_events"]) == 3

    @pytest.mark.asyncio
    async def test_sector_filter(self):
        """板块过滤应只返回相关新闻"""
        finance = [
            {"title": "白酒板块大涨", "content": "白酒行业景气", "source": "s", "publish_time": "t"},
            {"title": "半导体新闻", "content": "芯片涨价", "source": "s", "publish_time": "t"},
        ]
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=finance):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=[]):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=[]):
                    result = await get_news_feed(sector="白酒")
        assert result["total_count"] == 1
        assert "白酒" in result["hot_events"][0]["title"]

    @pytest.mark.asyncio
    async def test_deduplication_applied(self):
        """重复标题应被去重"""
        finance = [
            {"title": "贵州茅台发布年报", "content": "c1", "source": "s", "publish_time": "t"},
            {"title": "贵州茅台发布年报", "content": "c2", "source": "s", "publish_time": "t"},
        ]
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=finance):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=[]):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=[]):
                    result = await get_news_feed()
        assert result["total_count"] == 1


class TestGetHotEvents:
    """get_hot_events 热点事件"""

    @pytest.mark.asyncio
    async def test_returns_list_limited(self):
        """应返回限定数量的热点事件"""
        finance = [{"title": f"新闻{i}", "content": "c", "source": "s", "publish_time": "t"}
                    for i in range(15)]
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=finance):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=[]):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=[]):
                    events = await get_hot_events(limit=5)
        assert len(events) <= 5
        assert isinstance(events, list)


class TestGetSentimentIndex:
    """get_sentiment_index 情绪指数"""

    @pytest.mark.asyncio
    async def test_returns_required_structure(self):
        """应返回 index/level/positive_count/negative_count/neutral_count/timestamp"""
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=[]):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=[]):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=[]):
                    result = await get_sentiment_index()
        assert "index" in result
        assert "level" in result
        assert "positive_count" in result
        assert "negative_count" in result
        assert "neutral_count" in result
        assert "timestamp" in result
        # 空新闻 → 50 → level='中性'
        assert result["index"] == 50
        assert result["level"] == "中性"

    @pytest.mark.asyncio
    async def test_bullish_level_when_high(self):
        """指数 > 60 → 偏多"""
        finance = [{"title": "利好增长盈利", "content": "突破创新高", "source": "s", "publish_time": "t"}
                   for _ in range(5)]
        with patch.object(news_aggregator, "_fetch_finance_news", return_value=finance):
            with patch.object(news_aggregator, "_fetch_research_reports", return_value=[]):
                with patch.object(news_aggregator, "_fetch_sentiment_hot", return_value=[]):
                    result = await get_sentiment_index()
        assert result["index"] > 60
        assert result["level"] == "偏多"


class TestAISearch:
    """ai_search AI 检索"""

    @pytest.mark.asyncio
    async def test_empty_results_returns_default_summary(self):
        """无检索结果 → 返回默认摘要"""
        with patch.object(news_aggregator, "_ai_search", return_value=[]):
            result = await ai_search("白酒板块")
        assert result["query"] == "白酒板块"
        assert result["results"] == []
        assert result["summary"] == "未检索到相关消息"
        assert result["sentiment"] == "中性"

    @pytest.mark.asyncio
    async def test_results_returned_with_summary(self):
        """有检索结果 → 应统一结构并附摘要"""
        mock_results = [
            {"title": "利好新闻", "content": "增长", "url": "u", "source": "tavily", "publish_time": "t"},
        ]
        with patch.object(news_aggregator, "_ai_search", return_value=mock_results):
            # 同时 mock LLM 路由,避免真实调用
            mock_router = MagicMock()
            mock_response = MagicMock()
            mock_response.content = "这是 LLM 总结"
            mock_router.chat.return_value = mock_response
            with patch("src.core.llm_router.get_llm_router", return_value=mock_router):
                result = await ai_search("白酒")
        assert len(result["results"]) == 1
        assert result["results"][0]["title"] == "利好新闻"
        assert result["sentiment"] == "利好"
        assert result["summary"] == "这是 LLM 总结"

    @pytest.mark.asyncio
    async def test_llm_failure_fallback_summary(self):
        """LLM 失败 → 回退到简单聚合摘要"""
        mock_results = [
            {"title": "利好新闻", "content": "增长", "url": "u", "source": "tavily", "publish_time": "t"},
        ]
        with patch.object(news_aggregator, "_ai_search", return_value=mock_results):
            with patch("src.core.llm_router.get_llm_router", side_effect=Exception("llm error")):
                result = await ai_search("白酒")
        assert "共检索到1条相关消息" in result["summary"]

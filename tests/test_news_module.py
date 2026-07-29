"""新闻情绪分析模块单元测试

验证 src/analysis/news.py 的核心逻辑:
- NewsItem 数据类
- fetch_news 数据抓取(mock akshare)
- _count_keywords 关键词计数
- analyze_news_sentiment 情绪评分
- _score_positive_count 利好条数评分
- _score_sentiment_value 情绪值评分
- _score_news_heat 新闻热度评分
- score_news 综合评分
- 异常降级场景
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import patch

from src.analysis import news
from src.analysis.news import (
    NewsItem,
    _count_keywords,
    _score_news_heat,
    _score_positive_count,
    _score_sentiment_value,
    analyze_news_sentiment,
    fetch_news,
    score_news,
)


class TestNewsItem:
    """NewsItem 数据类"""

    def test_creation_with_all_fields(self):
        item = NewsItem(
            title="贵州茅台发布利好公告",
            content="公司一季度净利润同比增长20%",
            source="东方财富",
            time="2026-07-28 10:30",
        )
        assert item.title == "贵州茅台发布利好公告"
        assert item.source == "东方财富"

    def test_default_no_optional_fields(self):
        """NewsItem 必填字段"""
        item = NewsItem(title="t", content="c", source="s", time="2026")
        assert item.title == "t"


class TestCountKeywords:
    """_count_keywords 关键词计数"""

    def test_single_match(self):
        assert _count_keywords("公司发布利好公告", ["利好"]) == 1

    def test_multiple_match_same_keyword(self):
        """同一关键词多次出现"""
        assert _count_keywords("利好利好利好", ["利好"]) == 3

    def test_multiple_keywords(self):
        assert _count_keywords("利好增长突破", ["利好", "增长", "突破"]) == 3

    def test_no_match(self):
        assert _count_keywords("平平无奇的内容", ["利好", "增长"]) == 0

    def test_empty_text(self):
        assert _count_keywords("", ["利好"]) == 0


class TestAnalyzeNewsSentiment:
    """analyze_news_sentiment 情绪评分"""

    def test_empty_list_returns_zero(self):
        assert analyze_news_sentiment([]) == 0.0

    def test_all_positive_returns_high(self):
        """全部利好新闻 → 接近 1.0"""
        news_list = [
            {"title": "公司发布利好", "content": "净利润大幅增长"},
            {"title": "业绩超预期", "content": "订单突破历史新高"},
        ]
        score = analyze_news_sentiment(news_list)
        assert score > 0.5

    def test_all_negative_returns_low(self):
        """全部利空新闻 → 接近 -1.0"""
        news_list = [
            {"title": "公司利空", "content": "净利润下滑"},
            {"title": "违规处罚", "content": "业绩预警"},
        ]
        score = analyze_news_sentiment(news_list)
        assert score < -0.5

    def test_balanced_news_returns_near_zero(self):
        """利好利空均衡 → 接近 0"""
        news_list = [
            {"title": "公司利好增长", "content": "营收上升"},
            {"title": "公司利空下跌", "content": "利润下降"},
        ]
        score = analyze_news_sentiment(news_list)
        assert abs(score) < 0.1

    def test_empty_text_news_skipped(self):
        """空文本新闻应被跳过(不计入 valid_count)"""
        news_list = [
            {"title": "", "content": ""},
            {"title": "利好", "content": "增长"},
        ]
        score = analyze_news_sentiment(news_list)
        # 只算第二条,纯利好 → 1.0
        assert score == pytest.approx(1.0)

    def test_all_empty_text_returns_zero(self):
        """全部空文本 → valid_count=0,返回 0"""
        news_list = [
            {"title": "", "content": ""},
            {"title": "   ", "content": "   "},
        ]
        assert analyze_news_sentiment(news_list) == 0.0

    def test_no_keyword_news_returns_zero(self):
        """无关键词命中 → 每条 score=0,平均 0"""
        news_list = [
            {"title": "公司公告", "content": "今日召开股东大会"},
        ]
        assert analyze_news_sentiment(news_list) == 0.0

    def test_score_bounded_minus1_to_1(self):
        """评分应在 [-1, 1] 范围内"""
        news_list = [
            {"title": "利好" * 50, "content": "增长" * 50},
        ]
        score = analyze_news_sentiment(news_list)
        assert -1.0 <= score <= 1.0


class TestScorePositiveCount:
    """_score_positive_count 利好条数评分"""

    def test_empty_list_returns_default(self):
        assert _score_positive_count([]) == 10.0

    def test_high_ratio_top_score(self):
        """利好占比 > 70% → 40"""
        news_list = [
            {"title": "利好增长", "content": ""},
            {"title": "突破创新高", "content": ""},
            {"title": "盈利超预期", "content": ""},
        ]
        assert _score_positive_count(news_list) == 40.0

    def test_no_positive_low_score(self):
        """无利好新闻 → 8"""
        news_list = [
            {"title": "利空下跌", "content": ""},
            {"title": "亏损减持", "content": ""},
        ]
        assert _score_positive_count(news_list) == 8.0


class TestScoreSentimentValue:
    """_score_sentiment_value 情绪值评分"""

    def test_high_positive_top_score(self):
        """sentiment > 0.5 → 40"""
        assert _score_sentiment_value(0.6) == 40.0

    def test_strong_negative_bottom_score(self):
        """sentiment <= -0.5 → 4"""
        assert _score_sentiment_value(-0.6) == 4.0

    def test_zero_returns_18(self):
        """sentiment=0 → 18(中性区间 -0.2 到 0)"""
        assert _score_sentiment_value(0.0) == 18.0


class TestScoreNewsHeat:
    """_score_news_heat 新闻热度评分"""

    def test_empty_list_returns_min(self):
        assert _score_news_heat([]) == 2.0

    def test_fifteen_plus_top_score(self):
        """≥15 条 → 20"""
        news_list = [{"title": "t", "content": "c"}] * 15
        assert _score_news_heat(news_list) == 20.0

    def test_ten_to_fourteen(self):
        """10-14 条 → 16"""
        news_list = [{"title": "t", "content": "c"}] * 10
        assert _score_news_heat(news_list) == 16.0

    def test_five_to_nine(self):
        """5-9 条 → 12"""
        news_list = [{"title": "t", "content": "c"}] * 5
        assert _score_news_heat(news_list) == 12.0

    def test_three_to_four(self):
        """3-4 条 → 8"""
        news_list = [{"title": "t", "content": "c"}] * 3
        assert _score_news_heat(news_list) == 8.0

    def test_one_to_two(self):
        """1-2 条 → 4"""
        news_list = [{"title": "t", "content": "c"}]
        assert _score_news_heat(news_list) == 4.0


class TestFetchNews:
    """fetch_news 数据抓取"""

    def test_normal_fetch_returns_list(self):
        """正常抓取应返回最多 20 条新闻(主列名)"""
        df = pd.DataFrame([
            {
                "新闻标题": "贵州茅台发布年报",
                "新闻内容": "净利润同比增长20%",
                "文章来源": "东方财富",
                "发布时间": "2026-07-28 10:30",
            },
            {
                "新闻标题": "茅台股价创新高",
                "新闻内容": "突破历史新高",
                "文章来源": "新浪财经",
                "发布时间": "2026-07-28 11:00",
            },
        ])
        with patch.object(news.ak, "stock_news_em", return_value=df):
            result = fetch_news("600519")
        assert len(result) == 2
        assert result[0]["title"] == "贵州茅台发布年报"
        assert result[0]["source"] == "东方财富"
        assert result[1]["title"] == "茅台股价创新高"

    def test_fetch_with_alt_column_names(self):
        """备选列名(标题/内容/来源/时间)应被识别"""
        df = pd.DataFrame([
            {
                "标题": "茅台股价创新高",
                "内容": "突破历史新高",
                "来源": "新浪财经",
                "时间": "2026-07-28 11:00",
            },
        ])
        with patch.object(news.ak, "stock_news_em", return_value=df):
            result = fetch_news("600519")
        assert len(result) == 1
        assert result[0]["title"] == "茅台股价创新高"
        assert result[0]["source"] == "新浪财经"

    def test_fetch_exception_returns_empty(self):
        """抓取异常 → 返回空列表,不抛出"""
        with patch.object(news.ak, "stock_news_em", side_effect=Exception("net error")):
            result = fetch_news("600519")
        assert result == []

    def test_empty_df_returns_empty(self):
        """空 DataFrame → 返回空列表"""
        with patch.object(news.ak, "stock_news_em", return_value=pd.DataFrame()):
            result = fetch_news("600519")
        assert result == []

    def test_fetch_limits_to_20(self):
        """抓取超过 20 条时只返回前 20(head 限制)"""
        rows = [{"新闻标题": f"新闻{i}", "新闻内容": "c", "文章来源": "s", "发布时间": "t"} for i in range(30)]
        df = pd.DataFrame(rows)
        with patch.object(news.ak, "stock_news_em", return_value=df):
            result = fetch_news("600519")
        assert len(result) == 20


class TestScoreNews:
    """score_news 综合评分"""

    def test_returns_float_in_range(self):
        """综合评分应在 [0, 100] 范围内"""
        df = pd.DataFrame([
            {"新闻标题": "利好增长", "新闻内容": "盈利超预期", "文章来源": "s", "发布时间": "t"},
        ])
        with patch.object(news.ak, "stock_news_em", return_value=df):
            score = score_news("600519")
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_no_news_returns_low_score(self):
        """无新闻 → positive(10) + sentiment(18) + heat(2) = 30"""
        with patch.object(news.ak, "stock_news_em", return_value=pd.DataFrame()):
            score = score_news("600519")
        assert score == pytest.approx(30.0)

    def test_strong_positive_news_high_score(self):
        """强利好新闻 → 评分较高"""
        rows = [{"新闻标题": "利好增长盈利超预期", "新闻内容": "突破创新高增持回购", "文章来源": "s", "发布时间": "t"}] * 15
        df = pd.DataFrame(rows)
        with patch.object(news.ak, "stock_news_em", return_value=df):
            score = score_news("600519")
        # positive(40) + sentiment(40) + heat(20) = 100
        assert score >= 80

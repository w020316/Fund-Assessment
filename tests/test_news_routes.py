"""消息面聚合路由单元测试

验证 web/routes/news.py 的端点:
- GET  /api/news/feed        消息面流(支持 sector/fund_code 过滤)
- GET  /api/news/hot         热点事件 Top N
- GET  /api/news/sentiment   情绪指数
- POST /api/news/search      AI 检索(Tavily+LLM 总结)

通过 mock src.analysis.news_aggregator 的函数 + 清文件缓存,避免真实网络请求与缓存干扰。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """测试客户端"""
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_news_cache():
    """每个测试前清空 news 路由的文件缓存,避免 mock 被跳过"""
    from web.routes import news
    news.cache.clear()
    yield
    news.cache.clear()


# 模拟消息面数据
_MOCK_FEED = {
    "items": [
        {"title": "央行降准0.5个百分点", "source": "新华社",
         "publish_time": "2026-07-29 10:00", "sentiment": "positive", "sector": "宏观"},
        {"title": "新能源汽车销量创新高", "source": "证券时报",
         "publish_time": "2026-07-29 09:30", "sentiment": "positive", "sector": "新能源"},
    ],
    "total": 2,
}

_MOCK_HOT = [
    {"title": "美联储维持利率不变", "heat": 95, "sentiment": "neutral"},
    {"title": "半导体板块大涨", "heat": 88, "sentiment": "positive"},
]

_MOCK_SENTIMENT = {"score": 65, "label": "乐观", "trend": "上升", "sample_count": 120}

_MOCK_SEARCH = {
    "summary": "近期利好消息为主,关注稳增长政策",
    "key_points": ["央行降准", "财政发力", "消费回暖"],
    "sources": ["新华社", "证券时报"],
}


class TestNewsFeed:
    """GET /api/news/feed"""

    def test_feed_returns_items(self, client):
        """返回消息面流"""
        with patch("web.routes.news.get_news_feed", new=AsyncMock(return_value=_MOCK_FEED)):
            resp = client.get("/api/news/feed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]["items"]) == 2
        assert data["data"]["items"][0]["title"] == "央行降准0.5个百分点"
        assert data["_meta"]["data_source"] == "aggregator"
        assert data["_meta"]["cached"] is False

    def test_feed_with_sector_filter(self, client):
        """sector 过滤参数透传给底层函数"""
        mock_fn = AsyncMock(return_value=_MOCK_FEED)
        with patch("web.routes.news.get_news_feed", new=mock_fn):
            client.get("/api/news/feed", params={"sector": "新能源"})
        mock_fn.assert_called_once_with(sector="新能源", fund_code="")

    def test_feed_with_fund_code_filter(self, client):
        """fund_code 过滤参数透传给底层函数"""
        mock_fn = AsyncMock(return_value=_MOCK_FEED)
        with patch("web.routes.news.get_news_feed", new=mock_fn):
            client.get("/api/news/feed", params={"fund_code": "110022"})
        mock_fn.assert_called_once_with(sector="", fund_code="110022")

    def test_feed_cached_on_second_call(self, client):
        """第二次调用命中缓存"""
        mock_fn = AsyncMock(return_value=_MOCK_FEED)
        with patch("web.routes.news.get_news_feed", new=mock_fn):
            first = client.get("/api/news/feed")
            second = client.get("/api/news/feed")
        assert first.json()["_meta"]["cached"] is False
        assert second.json()["_meta"]["cached"] is True
        assert mock_fn.call_count == 1


class TestNewsHot:
    """GET /api/news/hot"""

    def test_hot_returns_events(self, client):
        """返回热点事件列表"""
        with patch("web.routes.news.get_hot_events", new=AsyncMock(return_value=_MOCK_HOT)):
            resp = client.get("/api/news/hot")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["heat"] == 95

    def test_hot_limit_param_passed(self, client):
        """limit 参数透传给底层函数"""
        mock_fn = AsyncMock(return_value=_MOCK_HOT)
        with patch("web.routes.news.get_hot_events", new=mock_fn):
            client.get("/api/news/hot", params={"limit": 5})
        mock_fn.assert_called_once_with(limit=5)


class TestNewsSentiment:
    """GET /api/news/sentiment"""

    def test_sentiment_returns_index(self, client):
        """返回情绪指数"""
        with patch("web.routes.news.get_sentiment_index", new=AsyncMock(return_value=_MOCK_SENTIMENT)):
            resp = client.get("/api/news/sentiment")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["score"] == 65
        assert data["data"]["label"] == "乐观"

    def test_sentiment_cached(self, client):
        """情绪指数走缓存"""
        mock_fn = AsyncMock(return_value=_MOCK_SENTIMENT)
        with patch("web.routes.news.get_sentiment_index", new=mock_fn):
            client.get("/api/news/sentiment")
            client.get("/api/news/sentiment")
        assert mock_fn.call_count == 1


class TestNewsSearch:
    """POST /api/news/search"""

    def test_search_returns_data(self, client):
        """AI 检索返回结果"""
        with patch("web.routes.news.ai_search", new=AsyncMock(return_value=_MOCK_SEARCH)):
            resp = client.post("/api/news/search", json={"query": "央行降准影响"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["summary"] == "近期利好消息为主,关注稳增长政策"
        assert len(data["data"]["key_points"]) == 3
        assert data["_meta"]["data_source"] == "tavily+llm"

    def test_search_not_cached(self, client):
        """AI 检索不缓存,每次都调用底层函数"""
        mock_fn = AsyncMock(return_value=_MOCK_SEARCH)
        with patch("web.routes.news.ai_search", new=mock_fn):
            client.post("/api/news/search", json={"query": "降准"})
            client.post("/api/news/search", json={"query": "降准"})
        assert mock_fn.call_count == 2


@pytest.fixture
def tolerant_client():
    """异常路径专用客户端:不重抛服务端异常,以便断言真实 HTTP 状态码。

    Starlette 默认 raise_server_exceptions=True 会把服务端异常直接抛出到测试,
    此时断言到的是异常而非响应码(500 与"异常被抛出"无法区分)。
    降级分支用例必须关闭该行为,否则测不出真实线上表现。
    """
    from web.api import app
    return TestClient(app, raise_server_exceptions=False)


class TestNewsDegradation:
    """异常(except)分支回归 —— 防止"优雅降级自己崩"缺陷复发。

    本组用例覆盖 except 分支;补丁前该分支因 `_build_meta()` 不接受
    `quality_score` 而抛 TypeError → HTTP 500。

    历史缺陷(2026-11-07):`web/routes/news.py` 的 `_build_meta` 签名为
    `(data_source, cached=False)`,但 feed/hot/sentiment/search 四个降级
    分支均以 `quality_score=0.0` 调用,导致异常处理器自身抛 TypeError。
    后果:新闻源抖动时 3 个**公开无鉴权** GET 端点返回 500,而非设计意图的
    "空列表 + quality_score=0.0"。

    该缺陷在 2305 条全绿测试下长期潜伏,原因是此前所有用例只 mock 成功路径,
    异常分支覆盖为零。故本组用例的断言重点是:
      1. HTTP 200(而非 500);
      2. `_meta.quality_score == 0.0`(降级必须在 meta 上可见,
         不能继续宣称默认的 80.0 健康度)。
    """

    def test_feed_degrades_on_upstream_error(self, tolerant_client):
        """feed 上游异常时应优雅降级为 200 + 空列表 + quality_score=0.0"""
        with patch(
            "web.routes.news.get_news_feed",
            new=AsyncMock(side_effect=RuntimeError("upstream news feed down")),
        ):
            resp = tolerant_client.get("/api/news/feed")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["_meta"]["quality_score"] == 0.0

    def test_hot_degrades_on_upstream_error(self, tolerant_client):
        """hot 上游异常时应优雅降级为 200 + 空列表 + quality_score=0.0"""
        with patch(
            "web.routes.news.get_hot_events",
            new=AsyncMock(side_effect=RuntimeError("upstream hot events down")),
        ):
            resp = tolerant_client.get("/api/news/hot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["_meta"]["quality_score"] == 0.0

    def test_sentiment_degrades_on_upstream_error(self, tolerant_client):
        """sentiment 上游异常时应降级为中性 50 + quality_score=0.0"""
        with patch(
            "web.routes.news.get_sentiment_index",
            new=AsyncMock(side_effect=RuntimeError("upstream sentiment down")),
        ):
            resp = tolerant_client.get("/api/news/sentiment")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["score"] == 50
        assert body["data"]["level"] == "neutral"
        assert body["_meta"]["quality_score"] == 0.0

    def test_search_degrades_on_upstream_error(self, tolerant_client):
        """search 检索异常时应降级为 200 + 空结果提示 + quality_score=0.0"""
        with patch(
            "web.routes.news.ai_search",
            new=AsyncMock(side_effect=RuntimeError("tavily down")),
        ):
            resp = tolerant_client.post("/api/news/search", json={"query": "降准"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["results"] == []
        assert body["_meta"]["quality_score"] == 0.0

import re
from dataclasses import dataclass

import akshare as ak


_POSITIVE_KEYWORDS: list[str] = [
    "利好", "增长", "突破", "创新高", "上涨", "盈利", "超预期", "增持",
    "回购", "分红", "签约", "中标", "获批", "合作", "业绩预增", "扭亏",
    "订单", "投产", "扩产", "景气", "复苏", "反弹", "强势", "领涨",
]

_NEGATIVE_KEYWORDS: list[str] = [
    "利空", "下跌", "亏损", "减持", "违规", "处罚", "退市", "风险",
    "暴跌", "下滑", "下降", "预警", "诉讼", "爆雷", "违约", "质押",
    "冻结", "调查", "警示", "跌停", "破发", "破净", "债务", "缩水",
]


@dataclass
class NewsItem:
    title: str
    content: str
    source: str
    time: str


def fetch_news(stock_code: str) -> list[dict]:
    news_list: list[dict] = []

    try:
        df = ak.stock_news_em(symbol=stock_code)
        if df is not None and not df.empty:
            df.columns = [c.strip() for c in df.columns]
            for _, row in df.head(20).iterrows():
                item: dict = {
                    "title": str(row.get("新闻标题", row.get("标题", ""))),
                    "content": str(row.get("新闻内容", row.get("内容", ""))),
                    "source": str(row.get("文章来源", row.get("来源", ""))),
                    "time": str(row.get("发布时间", row.get("时间", ""))),
                }
                news_list.append(item)
    except Exception:
        pass

    return news_list


def _count_keywords(text: str, keywords: list[str]) -> int:
    count = 0
    for kw in keywords:
        count += len(re.findall(kw, text))
    return count


def analyze_news_sentiment(news_list: list[dict]) -> float:
    if not news_list:
        return 0.0

    total_score = 0.0
    valid_count = 0

    for news in news_list:
        text = f"{news.get('title', '')} {news.get('content', '')}"
        if not text.strip():
            continue

        pos_count = _count_keywords(text, _POSITIVE_KEYWORDS)
        neg_count = _count_keywords(text, _NEGATIVE_KEYWORDS)
        total = pos_count + neg_count

        if total > 0:
            score = (pos_count - neg_count) / total
        else:
            score = 0.0

        total_score += score
        valid_count += 1

    if valid_count == 0:
        return 0.0

    avg_score = total_score / valid_count
    return round(max(-1.0, min(1.0, avg_score)), 4)


def _score_positive_count(news_list: list[dict]) -> float:
    if not news_list:
        return 10.0

    positive_count = 0
    for news in news_list:
        text = f"{news.get('title', '')} {news.get('content', '')}"
        pos = _count_keywords(text, _POSITIVE_KEYWORDS)
        neg = _count_keywords(text, _NEGATIVE_KEYWORDS)
        if pos > neg:
            positive_count += 1

    ratio = positive_count / len(news_list)

    if ratio > 0.7:
        return 40.0
    elif ratio > 0.5:
        return 32.0
    elif ratio > 0.3:
        return 24.0
    elif ratio > 0.1:
        return 16.0
    else:
        return 8.0


def _score_sentiment_value(sentiment: float) -> float:
    mapped = (sentiment + 1) / 2 * 40

    if sentiment > 0.5:
        return 40.0
    elif sentiment > 0.2:
        return 34.0
    elif sentiment > 0:
        return 26.0
    elif sentiment > -0.2:
        return 18.0
    elif sentiment > -0.5:
        return 10.0
    else:
        return 4.0


def _score_news_heat(news_list: list[dict]) -> float:
    count = len(news_list)

    if count >= 15:
        return 20.0
    elif count >= 10:
        return 16.0
    elif count >= 5:
        return 12.0
    elif count >= 3:
        return 8.0
    elif count >= 1:
        return 4.0
    else:
        return 2.0


def score_news(stock_code: str) -> float:
    news_list = fetch_news(stock_code)
    sentiment = analyze_news_sentiment(news_list)

    positive_score = _score_positive_count(news_list)
    sentiment_score = _score_sentiment_value(sentiment)
    heat_score = _score_news_heat(news_list)

    total = positive_score + sentiment_score + heat_score
    return round(total, 2)

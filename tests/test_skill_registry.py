"""金融技能库与一句话调度测试"""
from __future__ import annotations

from src.agents.dispatcher import dispatch
from src.agents.skill_registry import (
    CATEGORY_ORDER,
    SKILLS,
    category_summary,
    get_skill,
    list_skills,
    registry_stats,
)


def test_registry_has_unique_ids_and_real_apis():
    ids = [s.id for s in SKILLS]
    assert len(ids) == len(set(ids))
    assert all(s.api.startswith("/api/") for s in SKILLS)
    assert all(s.category in CATEGORY_ORDER for s in SKILLS)


def test_registry_stats():
    stats = registry_stats()
    assert stats["total"] == len(SKILLS)
    assert stats["categories"] == len(CATEGORY_ORDER)
    assert stats["read_only"] + stats["write"] == stats["total"]


def test_list_skills_filter_by_category_and_keyword():
    review = list_skills(category="市场复盘")
    assert review
    assert all(item["category"] == "市场复盘" for item in review)

    hit = list_skills(keyword="炸板")
    assert any(item["id"] == "daily_review" for item in hit)

    assert len(list_skills(category="不存在的分类")) == 0


def test_category_summary_covers_all_categories():
    summary = category_summary()
    assert len(summary) == len(CATEGORY_ORDER)
    assert sum(item["count"] for item in summary) == len(SKILLS)


def test_get_skill():
    assert get_skill("daily_review") is not None
    assert get_skill("not_exists") is None


def test_dispatch_market_review_intent():
    result = dispatch("帮我复盘今天大盘")
    assert result["intent"]["skill_id"] == "daily_review"
    assert result["plan"][0]["api"] == "/api/review/board"
    assert result["execution"] == "plan_only"


def test_dispatch_fund_intent():
    result = dispatch("我的基金要不要加仓")
    assert result["intent"]["category"] == "基金组合"
    assert any(step["category"] == "基金组合" for step in result["plan"])


def test_dispatch_validation_intent():
    result = dispatch("这个策略回测是不是过拟合")
    assert result["intent"]["category"] == "回测验证"


def test_dispatch_extracts_stock_code():
    result = dispatch("600519 值不值得买")
    assert result["stock_code"] == "600519"
    assert result["plan"][0]["params_hint"].get("stock_code") == "600519"
    assert result["intent"]["confidence"] in {"high", "medium", "low"}


def test_dispatch_empty_query_falls_back():
    result = dispatch("")
    assert result["plan"]
    assert result["intent"]["confidence"] == "low"
    assert any("默认" in note for note in result["notes"])


def test_dispatch_write_skill_requires_confirmation():
    result = dispatch("帮我买入600519")
    write_steps = [s for s in result["plan"] if not s["read_only"]]
    if write_steps:
        assert any("人工确认" in note for note in result["notes"])

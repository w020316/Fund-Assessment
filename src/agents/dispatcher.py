"""一句话调度器

参考 Vibe-Trading "一句话 → 选技能 → 出方案" 的交互方式,做一个确定性
(非 LLM)的意图路由:把自然语言映射到技能库中真实存在的 API,输出可执行的
调度方案。

设计取舍:
- 不使用 LLM 做路由,避免额外成本与随机性;关键词命中 + 上下文加权即可覆盖
  项目的高频场景(复盘/个股/基金/风险/回测/资讯)
- 只生成方案,不自动执行写操作;交易类技能一律要求人工确认
"""
from __future__ import annotations

import re

from src.agents.skill_registry import SKILLS, Skill, get_skill


_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# 场景词 → 类别加权
_CATEGORY_BOOSTS: tuple[tuple[tuple[str, ...], str, float], ...] = (
    (("基金", "加仓", "减仓", "止盈", "净值", "组合"), "基金组合", 6.0),
    (("风险", "止损", "仓位", "回撤", "风控"), "风险监控", 5.0),
    (("回测", "验证", "过拟合", "蒙特卡洛", "walk"), "回测验证", 5.0),
    (("复盘", "涨停", "炸板", "连板", "情绪", "首板"), "市场复盘", 6.0),
    (("新闻", "资讯", "研报", "公告", "消息"), "资讯话术", 4.0),
    (("策略", "选股", "因子", "信号"), "策略选股", 4.0),
    (("持仓", "盯盘", "交易", "买入", "卖出"), "持仓交易", 4.0),
)


def _score_skill(skill: Skill, query: str, *, has_code: bool, category_boosts: dict[str, float]) -> float:
    score = 0.0
    for keyword in skill.keywords:
        if keyword and keyword.lower() in query:
            # 长关键词更具体,给更高权重
            score += 1.0 + min(len(keyword), 8) / 4.0
    if skill.category in category_boosts and score > 0:
        score += category_boosts[skill.category]
    if has_code and skill.category in ("个股研究", "行情数据", "策略选股", "持仓交易"):
        score += 2.0
    return score


def dispatch(query: str) -> dict:
    """把一句话解析成技能调度方案"""
    raw = (query or "").strip()
    normalized = raw.lower()
    code_match = _CODE_RE.search(raw)
    stock_code = code_match.group(1) if code_match else ""

    category_boosts: dict[str, float] = {}
    for words, category, boost in _CATEGORY_BOOSTS:
        hits = sum(1 for w in words if w in normalized)
        if hits:
            category_boosts[category] = max(category_boosts.get(category, 0.0), boost + (hits - 1) * 1.5)

    scored: list[tuple[float, Skill]] = [
        (score, skill)
        for skill in SKILLS
        if (score := _score_skill(skill, normalized, has_code=bool(stock_code), category_boosts=category_boosts)) > 0
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    selected = [skill for _, skill in scored[:3]]
    if not selected:
        # 兜底:任何问题先看盘面,再给指数
        selected = [s for s in (get_skill("daily_review"), get_skill("index_quote")) if s]

    notes: list[str] = []
    has_write = any(not s.read_only for s in selected)
    if has_write:
        notes.append("所选技能包含写操作(下单),系统只生成方案,必须人工确认后执行")
    if any(s.auth for s in selected):
        notes.append("部分技能需要 ADMIN_TOKEN 鉴权,请先在系统配置中完成令牌设置")
    if any(s.category in ("个股研究", "策略选股", "持仓交易") for s in selected) and not stock_code:
        notes.append("未识别到 6 位股票代码,个股类技能需要补充代码后再执行")
    if not scored:
        notes.append("未命中明确技能关键词,已按默认顺序给出盘面与指数技能")

    plan = []
    for index, skill in enumerate(selected, start=1):
        params_hint: dict = {}
        if stock_code and skill.category in ("个股研究", "行情数据", "策略选股", "持仓交易"):
            params_hint["stock_code"] = stock_code
        if skill.id == "sentiment_cycle":
            params_hint["days"] = 5
        if skill.id == "backtest":
            params_hint["period"] = 60
        plan.append({
            "step": index,
            "skill_id": skill.id,
            "skill": skill.name,
            "category": skill.category,
            "description": skill.description,
            "method": skill.method,
            "api": skill.api,
            "read_only": skill.read_only,
            "auth": skill.auth,
            "params_hint": params_hint,
        })

    top_score = scored[0][0] if scored else 0.0
    confidence = "high" if top_score >= 7 else ("medium" if top_score >= 3 else "low")

    return {
        "query": raw,
        "stock_code": stock_code,
        "intent": {
            "category": selected[0].category,
            "skill_id": selected[0].id,
            "name": selected[0].name,
            "confidence": confidence,
            "score": round(top_score, 2),
        },
        "plan": plan,
        "matched_count": len(scored),
        "notes": notes,
        "execution": "plan_only",
    }

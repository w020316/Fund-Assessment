"""R-02 回归:LLM/外部文本进入 innerHTML 前必须转义。

无 JS 测试运行器,这里对静态源码做契约断言:
- 4 个历史注入点必须调用 escapeHtml();
- 不允许再出现裸插值(如 ${d.reason} / ${p}</div> / <td>${c}</td>)。
"""
from __future__ import annotations

from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def _text() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_agent_points_are_escaped():
    text = _text()
    assert "opData.points.map(p => `<div style=\"padding:3px 0;\">• ${escapeHtml(p)}</div>`)" in text
    assert "• ${p}</div>" not in text


def test_debate_points_are_escaped():
    text = _text()
    assert "escapeHtml(p)" in text
    assert "typeof p === 'string' ? p :" not in text


def test_final_decision_reason_is_escaped():
    text = _text()
    assert "${escapeHtml(d.reason)}" in text
    assert "${d.reason}</div>" not in text


def test_data_table_string_cells_are_escaped():
    text = _text()
    assert "return `<td>${escapeHtml(c)}</td>`;" in text
    assert "return `<td>${c}</td>`;" not in text


def test_multi_agent_report_is_escaped():
    """F-01/QF3-09 回归(2026-11-08):基金多智能体报告的 6 类 sink 必须转义。

    该处为 R-02 的"第 5 处"遗漏:分析师 name/view/stance/score、多空论据、
    风险条目、决策 reasoning、目标价/止损位/仓位建议/预期回报/时间维度,
    全部来自 LLM 输出,直接拼进 innerHTML 可执行 <img onerror>。
    断言限定在 multiAgentAnalyze 函数体内,避免与其他函数的同名变量误判。
    """
    text = _text()
    start = text.index("async function multiAgentAnalyze(")
    end = text.index("async function loadFundScripts(")
    body = text[start:end]

    # 必须已转义
    for expr in (
        "${escapeHtml(reasoning)}",
        "${escapeHtml(positionAdvice)}",
        "${escapeHtml(targetPrice)}",
        "${escapeHtml(stopLoss)}",
        "${escapeHtml(name)}",
        "${escapeHtml(view)}",
        "${escapeHtml(stance)}",
        "${escapeHtml(score)}",
    ):
        assert expr in body, f"multiAgentAnalyze 缺少转义: {expr}"
    # 不允许再出现裸插值
    for bare in (
        "${reasoning}",
        "${positionAdvice}",
        "${targetPrice}",
        "${stopLoss}",
        "${name}",
        "${view}",
        "${stance}",
    ):
        assert bare not in body, f"multiAgentAnalyze 仍存在裸插值: {bare}"


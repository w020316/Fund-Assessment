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

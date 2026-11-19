# -*- coding: utf-8 -*-
"""基金页前端契约: 后端百分比字段不得二次 ×100; 大额负数必须用同一货币格式。

2026-11-19 实走发现:
- 重仓股分析弹窗把 7.96%(后端百分比)乘 100 渲染成 796.00%, 集中度 35.85%→3585%;
- 数据中心净买入为负时未走 fmtMoney, 同列混用 "5.84亿" 与 "-28,564,015.67"。
"""
from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "web/static/index.html"


def _function_body(src: str, name: str) -> str:
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", src)
    assert m, f"未找到函数 {name}()"
    start = src.find("{", m.end())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"{name}() 花括号不配对")


def test_view_holdings_uses_percent_fields_without_rescaling():
    src = INDEX.read_text(encoding="utf-8", errors="replace")
    body = _function_body(src, "viewHoldings")
    # 后端 weight/top5_weight 已是百分数, 不得再 ×100
    assert "top5_weight || 0).toFixed(2)}%" in body
    assert "top5_weight || 0) * 100" not in body
    assert "h.weight || 0) * 100" not in body
    # 净值影响字段名必须与后端一致
    assert "estimated_change_pct" in body
    # 板块分布用后端字段名
    assert "total_weight" in body
    # 未披露涨跌幅不得兜成 0.00%
    assert "Number.isFinite(h.change)" in body


def test_large_negative_amounts_use_same_money_format():
    src = INDEX.read_text(encoding="utf-8", errors="replace")
    assert "Math.abs(c) > 10000" in src, "负的大额金额会绕过 fmtMoney, 同列混用两种格式"

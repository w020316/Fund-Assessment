"""通用类型转换工具, 消除多文件重复定义的 _safe_float/_safe_str/_safe_int。

各路由文件原本各自重复实现, 现统一在此提供。为兼容现有调用,
各路由文件可通过 `from src.utils.convert import safe_float as _safe_float`
零改动接入。
"""
from __future__ import annotations

import math
from typing import Any


def safe_float(val: Any, default: float = 0.0) -> float:
    """安全转 float, 处理 None/NaN/Inf/非法字符串。"""
    if val is None:
        return default
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    try:
        result = float(val)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def opt_float(val: Any) -> float | None:
    """诚实变体: 只在值确实存在且为有效数字时返回 float, 其余一律 None。

    与 safe_float 的区别(R-08 B1, 2026-11-09): safe_float 缺字段兜 0.0,
    使「数据缺失」与「真实值 0」无法区分, 缺失被渲染成 0.00/0.00% 冒充真值,
    违反「缺失必须显示缺失(—), 不得填 0」红线。语义与 data_source_v2._opt_float
    完全一致, 供路由层渲染契约 float→Optional 迁移复用; 前端 None→—。
    """
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        if val in ("", "-", "--", "—", "None", "null", "nan"):
            return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    try:
        result = float(val)
    except (ValueError, TypeError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def safe_str(val: Any, default: str = "") -> str:
    """安全转 str, 处理 None 与 NaN。"""
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return str(val)


def safe_int(val: Any, default: int = 0) -> int:
    """安全转 int, 处理 None/字符串/浮点。"""
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

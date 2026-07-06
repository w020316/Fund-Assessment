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

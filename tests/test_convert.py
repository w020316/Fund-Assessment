"""通用类型转换工具单元测试

验证 src/utils/convert.py 的 safe_float / safe_str / safe_int:
- None / NaN / Inf / 非法字符串均回退到默认值
- 各路由文件原 _safe_float/_safe_str 行为已统一到此模块
"""
from __future__ import annotations

import math

from src.utils.convert import safe_float, safe_int, safe_str


class TestSafeFloat:
    def test_none_returns_default(self):
        assert safe_float(None) == 0.0
        assert safe_float(None, -1.0) == -1.0

    def test_nan_returns_default(self):
        assert safe_float(float("nan")) == 0.0
        assert safe_float(float("nan"), -1.0) == -1.0

    def test_inf_returns_default(self):
        assert safe_float(float("inf")) == 0.0
        assert safe_float(float("-inf"), -1.0) == -1.0

    def test_invalid_string_returns_default(self):
        assert safe_float("abc") == 0.0
        assert safe_float("abc", 99.0) == 99.0

    def test_valid_string_returns_float(self):
        assert safe_float("3.14") == 3.14
        assert safe_float("  2.5  ") == 2.5

    def test_int_returns_float(self):
        assert safe_float(42) == 42.0

    def test_float_returns_float(self):
        assert safe_float(3.14) == 3.14

    def test_negative_zero(self):
        assert safe_float(-0.0) == 0.0


class TestSafeStr:
    def test_none_returns_default(self):
        assert safe_str(None) == ""
        assert safe_str(None, "fallback") == "fallback"

    def test_nan_returns_default(self):
        assert safe_str(float("nan")) == ""
        assert safe_str(float("nan"), "na") == "na"

    def test_int_returns_str(self):
        assert safe_str(42) == "42"

    def test_float_returns_str(self):
        assert safe_str(3.14) == "3.14"

    def test_empty_string_returns_empty(self):
        # 空字符串不是 None/NaN, 应原样返回(不触发 default)
        assert safe_str("") == ""


class TestSafeInt:
    def test_none_returns_default(self):
        assert safe_int(None) == 0
        assert safe_int(None, 99) == 99

    def test_float_returns_int(self):
        assert safe_int(3.7) == 3
        assert safe_int(-2.9) == -2

    def test_string_float_returns_int(self):
        assert safe_int("3.7") == 3
        assert safe_int("42") == 42

    def test_invalid_string_returns_default(self):
        assert safe_int("abc") == 0
        assert safe_int("abc", 99) == 99

    def test_negative_float_string(self):
        assert safe_int("-5.9") == -5

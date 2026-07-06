"""话术库单元测试

验证 src/analysis/script_library.py:
- list_scripts: 列表/过滤
- get_script: 按 ID 获取
- _fill_template: 变量填充(含缺失变量兜底)
- generate_script: 完整生成
- match_fund_scripts: 基金建议→话术匹配
- match_stock_scripts: 个股数据→话术匹配
- get_script_categories: 分类结构
"""
from __future__ import annotations

import pytest

from src.analysis.script_library import (
    _fill_template,
    generate_script,
    get_script,
    get_script_categories,
    list_scripts,
    match_fund_scripts,
    match_stock_scripts,
)


class TestListScripts:
    """list_scripts 列表与过滤"""

    def test_list_all_returns_all_scripts(self):
        """无参数返回全部话术"""
        result = list_scripts()
        assert len(result) >= 24  # 12 股市 + 12 基金

    def test_filter_by_category_stock(self):
        """按 category=stock 过滤"""
        result = list_scripts(category="stock")
        assert len(result) >= 12
        assert all(s["category"] == "stock" for s in result)

    def test_filter_by_category_fund(self):
        """按 category=fund 过滤"""
        result = list_scripts(category="fund")
        assert len(result) >= 12
        assert all(s["category"] == "fund" for s in result)

    def test_filter_by_scene(self):
        """按 scene 过滤"""
        result = list_scripts(scene="buy")
        assert all(s["scene"] == "buy" for s in result)
        assert len(result) >= 1

    def test_filter_by_category_and_scene(self):
        """同时按 category + scene 过滤"""
        result = list_scripts(category="fund", scene="take_profit")
        assert all(s["category"] == "fund" and s["scene"] == "take_profit" for s in result)
        assert len(result) >= 1


class TestGetScript:
    """get_script 按 ID 获取"""

    def test_get_existing_script(self):
        s = get_script("stock_buy_breakthrough")
        assert s is not None
        assert s["title"] == "突破买入"
        assert "template" in s
        assert "variables" in s

    def test_get_nonexistent_returns_none(self):
        assert get_script("nonexistent_id") is None


class TestFillTemplate:
    """_fill_template 变量填充"""

    def test_fills_all_variables(self):
        template = "{stock_name}突破 {resistance_price} 元"
        result = _fill_template(template, {"stock_name": "贵州茅台", "resistance_price": 1800})
        assert "贵州茅台" in result
        assert "1800" in result

    def test_missing_variable_replaced_with_dash(self):
        """缺失变量用「—」占位"""
        template = "{stock_name} 涨跌幅 {change_pct}"
        result = _fill_template(template, {"stock_name": "贵州茅台"})
        assert "贵州茅台" in result
        assert "—" in result
        # {change_pct} 应被替换为 —,不应残留 {change_pct}
        assert "{change_pct}" not in result

    def test_float_formatted_to_2_decimal(self):
        """浮点数格式化为 2 位小数"""
        template = "PE {pe}"
        result = _fill_template(template, {"pe": 28.567})
        assert "28.57" in result

    def test_float_integer_no_decimal(self):
        """整数浮点不显示小数"""
        template = "价格 {price}"
        result = _fill_template(template, {"price": 100.0})
        assert "100" in result
        assert "100.00" not in result

    def test_empty_value_replaced_with_dash(self):
        """空字符串用「—」占位"""
        template = "{name} 测试"
        result = _fill_template(template, {"name": ""})
        assert "—" in result


class TestGenerateScript:
    """generate_script 完整生成"""

    def test_generate_valid_script(self):
        result = generate_script("stock_buy_breakthrough", {
            "stock_name": "贵州茅台",
            "resistance_price": 1800,
            "volume_ratio": 2.5,
            "stop_loss_price": 1700,
        })
        assert result is not None
        assert result["title"] == "突破买入"
        assert "贵州茅台" in result["content"]
        assert "1800" in result["content"]
        assert "2.50" in result["content"]

    def test_generate_nonexistent_returns_none(self):
        result = generate_script("nonexistent", {})
        assert result is None

    def test_generate_with_partial_variables(self):
        """部分变量缺失也能生成"""
        result = generate_script("stock_buy_breakthrough", {"stock_name": "测试股票"})
        assert result is not None
        assert "测试股票" in result["content"]
        assert "—" in result["content"]


class TestMatchFundScripts:
    """match_fund_scripts 基金建议匹配"""

    def test_match_take_profit_signal(self):
        """止盈信号匹配止盈话术"""
        advice = {
            "fund_name": "易方达消费",
            "fund_code": "110022",
            "current_nav": 2.724,
            "cost_nav": 2.0,
            "pnl_pct": 36.2,
            "advice": {"signal": "take_profit", "action": "止盈"},
            "market_signal": {"index_name": "上证指数", "change_pct": 0.5},
        }
        result = match_fund_scripts(advice)
        assert len(result) >= 1
        assert all(s["scene"] == "take_profit" for s in result)

    def test_match_hold_signal(self):
        """持有信号匹配持有话术"""
        advice = {
            "fund_name": "测试基金",
            "fund_code": "001",
            "current_nav": 1.5,
            "cost_nav": 1.4,
            "pnl_pct": 7.14,
            "advice": {"signal": "hold", "action": "持有"},
            "market_signal": {},
        }
        result = match_fund_scripts(advice)
        assert len(result) >= 1
        assert all(s["scene"] == "hold" for s in result)

    def test_match_dca_signal(self):
        """定投信号匹配定投话术"""
        advice = {
            "fund_name": "白酒基金",
            "fund_code": "161725",
            "current_nav": 1.2,
            "cost_nav": 1.3,
            "pnl_pct": -7.69,
            "advice": {"signal": "dca", "action": "定投"},
            "market_signal": {"index_name": "上证指数", "change_pct": -2.5},
        }
        result = match_fund_scripts(advice)
        assert len(result) >= 1
        assert all(s["scene"] == "dca" for s in result)

    def test_variables_filled_from_advice(self):
        """变量从 advice 中填充"""
        advice = {
            "fund_name": "招商白酒",
            "fund_code": "161725",
            "current_nav": 1.5,
            "cost_nav": 1.4,
            "pnl_pct": 7.14,
            "advice": {"signal": "hold", "action": "持有"},
            "market_signal": {},
        }
        result = match_fund_scripts(advice)
        # 至少有一条话术包含基金名称
        assert any("招商白酒" in s["content"] for s in result)


class TestMatchStockScripts:
    """match_stock_scripts 个股匹配"""

    def test_big_profit_matches_sell(self):
        """浮盈 > 20% 匹配卖出话术"""
        stock_data = {"name": "贵州茅台", "price": 1800, "change_pct": 2.0, "pnl_pct": 25.0}
        result = match_stock_scripts(stock_data)
        assert len(result) >= 1
        assert all(s["scene"] == "sell" for s in result)

    def test_big_rise_matches_sell(self):
        """涨幅 > 5% 匹配卖出话术"""
        stock_data = {"name": "测试股", "price": 100, "change_pct": 6.0, "pnl_pct": 0}
        result = match_stock_scripts(stock_data)
        assert all(s["scene"] == "sell" for s in result)

    def test_big_drop_matches_wait(self):
        """跌幅 > 5% 匹配观望话术"""
        stock_data = {"name": "测试股", "price": 100, "change_pct": -6.0, "pnl_pct": 0}
        result = match_stock_scripts(stock_data)
        assert all(s["scene"] == "wait" for s in result)

    def test_normal_matches_hold(self):
        """正常波动匹配持有话术"""
        stock_data = {"name": "测试股", "price": 100, "change_pct": 1.0, "pnl_pct": 5.0}
        result = match_stock_scripts(stock_data)
        assert all(s["scene"] == "hold" for s in result)


class TestGetScriptCategories:
    """get_script_categories 分类结构"""

    def test_returns_structure(self):
        result = get_script_categories()
        assert "stock" in result
        assert "fund" in result
        assert "total" in result
        assert result["total"] >= 24
        assert result["stock"]["count"] >= 12
        assert result["fund"]["count"] >= 12

    def test_stock_has_scenes(self):
        result = get_script_categories()
        assert "buy" in result["stock"]["scenes"]
        assert "sell" in result["stock"]["scenes"]
        assert "hold" in result["stock"]["scenes"]

    def test_fund_has_scenes(self):
        result = get_script_categories()
        assert "dca" in result["fund"]["scenes"]
        assert "take_profit" in result["fund"]["scenes"]

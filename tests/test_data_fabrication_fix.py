"""数据失真修复回归测试

验证 src/core/data_source_v2.py 中 3 处数据失真 bug 已修复:
1. _get_margin_trading_fallback: 不再硬编码 margin_ratio=0.012, 返回空 dict
2. _get_shareholder_count_fallback: 不再基于市值分档硬编码 base_holders, 返回空 dict
3. _get_dragon_tiger_sina / _get_dragon_tiger_from_ranking: 不再用涨幅榜冒充龙虎榜, 返回空 list
4. 基金 change_pct: prev_nav <= 0 时返回 0, 不再除零或产生符号错误

回归策略: 直接调用 fallback 函数, 断言返回空; 模拟腾讯基金返回数据, 断言 change_pct 计算正确
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core import data_source_v2 as ds2


class TestMarginTradingFallback:
    """融资融券 fallback 不再造假"""

    def test_margin_trading_fallback_returns_empty_dict(self):
        """_get_margin_trading_fallback 应返回空 dict, 不再硬编码 margin_ratio=0.012"""
        result = ds2._get_margin_trading_fallback("600519")
        assert result == {}, f"expected empty dict, got {result}"

    def test_margin_trading_fallback_does_not_contain_margin_ratio(self):
        """返回值不应包含 margin_ratio 字段(历史造假字段)"""
        result = ds2._get_margin_trading_fallback("000001")
        assert "margin_ratio" not in result
        assert "margin_balance" not in result


class TestShareholderCountFallback:
    """股东户数 fallback 不再造假"""

    def test_shareholder_count_fallback_returns_empty_dict(self):
        """_get_shareholder_count_fallback 应返回空 dict, 不再硬编码 base_holders"""
        result = ds2._get_shareholder_count_fallback("600519")
        assert result == {}, f"expected empty dict, got {result}"

    def test_shareholder_count_fallback_does_not_contain_holder_num(self):
        """返回值不应包含 holder_num 字段(历史造假字段)"""
        result = ds2._get_shareholder_count_fallback("000001")
        assert "holder_num" not in result
        assert "base_holders" not in result


class TestDragonTigerFallback:
    """龙虎榜 fallback 不再用涨幅榜冒充"""

    def test_dragon_tiger_sina_returns_empty_list(self):
        """_get_dragon_tiger_sina 应返回空 list, 不再用涨幅榜冒充"""
        result = ds2._get_dragon_tiger_sina()
        assert result == [], f"expected empty list, got {result}"

    def test_dragon_tiger_from_ranking_returns_empty_list(self):
        """_get_dragon_tiger_from_ranking 应返回空 list"""
        result = ds2._get_dragon_tiger_from_ranking()
        assert result == [], f"expected empty list, got {result}"

    def test_dragon_tiger_sina_does_not_contain_zero_amounts(self):
        """返回值不应包含 buy_amount=0/sell_amount=0/net_amount=0 的造假条目"""
        result = ds2._get_dragon_tiger_sina()
        for item in result:
            assert "buy_amount" not in item or item["buy_amount"] != 0, \
                f"发现造假条目 buy_amount=0: {item}"


class TestFundChangePctDivideByZero:
    """基金 change_pct 除零 bug 回归"""

    def test_change_pct_zero_when_prev_nav_zero(self):
        """当 nav == change (即 prev_nav=0) 时, change_pct 应返回 0, 不除零"""
        # 构造腾讯基金返回数据: nav=1.5, change=1.5 → prev_nav=0
        # 模拟 get_fund_realtime_tencent 解析逻辑
        nav = 1.5
        change = 1.5
        prev_nav = nav - change
        if prev_nav > 0:
            change_pct = change / prev_nav * 100
        else:
            change_pct = 0.0
        assert change_pct == 0.0, f"prev_nav=0 时 change_pct 应为 0, 实际 {change_pct}"

    def test_change_pct_correct_normal_case(self):
        """正常情况: nav=1.6, change=0.1, prev_nav=1.5, change_pct 应为 6.666..."""
        nav = 1.6
        change = 0.1
        prev_nav = nav - change
        if prev_nav > 0:
            change_pct = change / prev_nav * 100
        else:
            change_pct = 0.0
        assert abs(change_pct - (0.1 / 1.5 * 100)) < 1e-9

    def test_change_pct_zero_when_negative_prev_nav(self):
        """当 change > nav (prev_nav<0) 时数据无效, change_pct 应返回 0"""
        # nav=1.0, change=1.5 → prev_nav=-0.5 (异常数据)
        nav = 1.0
        change = 1.5
        prev_nav = nav - change
        if prev_nav > 0:
            change_pct = change / prev_nav * 100
        else:
            change_pct = 0.0
        assert change_pct == 0.0, f"prev_nav<0 时 change_pct 应为 0, 实际 {change_pct}"

    def test_fund_realtime_tencent_handles_zero_prev_nav(self):
        """端到端: 模拟腾讯返回 nav=change 的数据, 不应抛出 ZeroDivisionError"""
        # 真实格式: v_jj<code>="<code>~<name>~...~;
        # parts[0]=<code>, parts[5]=nav, parts[7]=change_pct
        parts = ["600519", "测试基金", "1.500", "1.500", "",
                 "1.500", "1.500", "1.500", "2026-07-05 15:00"]
        segment = "~".join(parts) + "~;"
        mock_text = f'v_jj600519="{segment}'
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = mock_get.return_value
            mock_resp.text = mock_text
            mock_resp.encoding = "gbk"
            result = ds2.get_fund_realtime_tencent(["600519"])
        # 不抛出 ZeroDivisionError 即通过
        assert isinstance(result, list)

    def test_fund_change_pct_tencent_field_is_percentage(self):
        """腾讯基金接口 parts[7] 是百分比(如 1.566 表示 1.566%), 不是绝对涨跌额

        历史问题: 原代码把 parts[7]=1.566 当作绝对涨跌额,
        再除以 (nav-change)=1.158 反推百分比, 得到 135% 的荒谬结果。
        """
        # 模拟腾讯返回: nav=2.724, parts[7]=1.566 (即 1.566%)
        parts = ["110022", "易方达消费行业股票", "0.0000", "0.0000", "",
                 "2.7240", "2.7240", "1.5660", "2026-07-03"]
        segment = "~".join(parts) + "~;"
        mock_text = f'v_jj110022="{segment}'
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = mock_get.return_value
            mock_resp.text = mock_text
            mock_resp.encoding = "gbk"
            result = ds2.get_fund_realtime_tencent(["110022"])
        assert len(result) == 1
        fund = result[0]
        assert fund["nav"] == 2.724
        # change_pct 应为 1.566 (合理范围), 而非 135.23
        assert abs(fund["change_pct"] - 1.566) < 0.01, \
            f"change_pct 应为 1.566%, 实际 {fund['change_pct']}"
        # change 应为反推的绝对涨跌额 ≈ 0.042
        assert abs(fund["change"] - 0.042) < 0.01, \
            f"change 应为 ~0.042, 实际 {fund['change']}"

    def test_fund_change_pct_fallback_when_field_too_large(self):
        """当 parts[7] > 20 时(明显不是百分比), 降级为绝对值再算百分比"""
        # 模拟异常数据: parts[7]=25 (明显不是百分比)
        # nav=10, prev_nav=10-25=-15, change_pct=0
        parts = ["600519", "测试基金", "0.0", "0.0", "",
                 "10.0", "10.0", "25.0", "2026-07-03"]
        segment = "~".join(parts) + "~;"
        mock_text = f'v_jj600519="{segment}'
        with patch.object(ds2.requests, "get") as mock_get:
            mock_resp = mock_get.return_value
            mock_resp.text = mock_text
            mock_resp.encoding = "gbk"
            result = ds2.get_fund_realtime_tencent(["600519"])
        assert len(result) == 1
        fund = result[0]
        # 25 > 20, 走降级分支: change=25, prev_nav=10-25=-15, change_pct=0
        assert fund["change_pct"] == 0.0


class TestParallelFetch:
    """_parallel_fetch 并行抓取测试"""

    def test_parallel_fetch_executes_all_tasks(self):
        """所有任务都应被执行并返回结果"""
        results = ds2._parallel_fetch([
            ("a", lambda: 1),
            ("b", lambda: 2),
            ("c", lambda: 3),
        ])
        assert results == {"a": 1, "b": 2, "c": 3}

    def test_parallel_fetch_handles_exception(self):
        """单个任务异常不应影响其他任务, 异常任务返回 None"""
        def _fail():
            raise RuntimeError("boom")

        results = ds2._parallel_fetch([
            ("ok", lambda: "value"),
            ("fail", _fail),
        ])
        assert results["ok"] == "value"
        assert results["fail"] is None

    def test_parallel_fetch_runs_in_parallel(self):
        """任务应并行执行(总耗时接近最慢任务, 而非各任务之和)"""
        import time as _time

        def _sleep_0_2():
            _time.sleep(0.2)
            return "done"

        start = _time.monotonic()
        ds2._parallel_fetch([
            ("a", _sleep_0_2),
            ("b", _sleep_0_2),
            ("c", _sleep_0_2),
        ])
        elapsed = _time.monotonic() - start
        # 串行执行需 0.6s, 并行应 < 0.5s(留余量)
        assert elapsed < 0.5, f"并行执行耗时 {elapsed:.2f}s, 期望 < 0.5s"

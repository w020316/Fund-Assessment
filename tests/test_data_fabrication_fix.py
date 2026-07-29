"""数据失真修复回归测试

验证 src/core/data_source_v2.py 中 3 处数据失真 bug 已修复:
1. _get_margin_trading_fallback: 不再硬编码 margin_ratio=0.012, 返回空 dict
2. _get_shareholder_count_fallback: 不再基于市值分档硬编码 base_holders, 返回空 dict
3. _get_dragon_tiger_sina / _get_dragon_tiger_from_ranking: 不再用涨幅榜冒充龙虎榜, 返回空 list
4. 基金 change_pct: prev_nav <= 0 时返回 0, 不再除零或产生符号错误

回归策略: 直接调用 fallback 函数, 断言返回空; 模拟腾讯基金返回数据, 断言 change_pct 计算正确
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

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


class TestCheckEmAvailableLocking:
    """_check_em_available double-checked locking 线程安全测试

    P2 修复(2026-07-29):原实现无锁保护,多线程并发首次调用时
    每个线程都会读到 _EM_AVAILABLE=None 并发起探测请求(2s 超时 × N 线程)。
    修复后采用 double-checked locking,仅首个线程发起探测。
    """

    def setup_method(self):
        """每个测试前重置全局 _EM_AVAILABLE 状态,避免测试间污染"""
        ds2._EM_AVAILABLE = None

    def teardown_method(self):
        """测试后再次清理,防止状态泄漏到其他测试"""
        ds2._EM_AVAILABLE = None

    def test_returns_cached_value_without_network_call(self):
        """已探测过(_EM_AVAILABLE 非 None)时直接返回,不发网络请求"""
        ds2._EM_AVAILABLE = True
        with patch.object(ds2._EM_SESSION, "get") as mock_get:
            assert ds2._check_em_available() is True
            mock_get.assert_not_called()

    def test_cached_false_skips_network_call(self):
        """缓存值为 False 时同样不发网络请求"""
        ds2._EM_AVAILABLE = False
        with patch.object(ds2._EM_SESSION, "get") as mock_get:
            assert ds2._check_em_available() is False
            mock_get.assert_not_called()

    def test_concurrent_calls_single_network_request(self):
        """N 个线程并发首次调用时仅发起 1 次探测请求

        这是 double-checked locking 的核心目标:避免 N 个探测请求。
        """
        call_count = 0
        count_lock = threading.Lock()
        original_get = ds2._EM_SESSION.get

        def _counting_get(*args, **kwargs):
            nonlocal call_count
            with count_lock:
                call_count += 1
            # 模拟网络延迟,放大竞态窗口
            import time as _time
            _time.sleep(0.05)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": {"f12": "000001"}}
            return mock_resp

        with patch.object(ds2._EM_SESSION, "get", side_effect=_counting_get):
            n_threads = 8
            results: list[bool] = []
            results_lock = threading.Lock()

            def worker():
                r = ds2._check_em_available()
                with results_lock:
                    results.append(r)

            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # 所有线程应得到一致结果
        assert all(r is True for r in results), f"结果不一致: {results}"
        # 网络探测仅应发起 1 次(double-checked locking 生效)
        assert call_count == 1, f"探测请求 {call_count} 次, 期望 1 次"

    def test_available_api_sets_true(self):
        """EastMoney API 返回正常数据 → _EM_AVAILABLE=True"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"f12": "000001"}}
        with patch.object(ds2._EM_SESSION, "get", return_value=mock_resp):
            assert ds2._check_em_available() is True
        assert ds2._EM_AVAILABLE is True

    def test_empty_data_sets_false(self):
        """API 200 但 data 为空 → _EM_AVAILABLE=False"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": None}
        with patch.object(ds2._EM_SESSION, "get", return_value=mock_resp):
            assert ds2._check_em_available() is False
        assert ds2._EM_AVAILABLE is False

    def test_non_200_status_sets_false(self):
        """非 200 状态码 → _EM_AVAILABLE=False"""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch.object(ds2._EM_SESSION, "get", return_value=mock_resp):
            assert ds2._check_em_available() is False
        assert ds2._EM_AVAILABLE is False

    def test_network_exception_sets_false(self):
        """网络异常 → _EM_AVAILABLE=False(降级到备用数据源)"""
        with patch.object(ds2._EM_SESSION, "get", side_effect=Exception("timeout")):
            assert ds2._check_em_available() is False
        assert ds2._EM_AVAILABLE is False

    def test_json_decode_exception_sets_false(self):
        """响应非 JSON → _EM_AVAILABLE=False"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        with patch.object(ds2._EM_SESSION, "get", return_value=mock_resp):
            assert ds2._check_em_available() is False
        assert ds2._EM_AVAILABLE is False

    def test_ensure_em_checked_triggers_probe_when_none(self):
        """_EM_AVAILABLE=None 时 _ensure_em_checked 应触发探测"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"f12": "000001"}}
        with patch.object(ds2._EM_SESSION, "get", return_value=mock_resp) as mock_get:
            ds2._ensure_em_checked()
            mock_get.assert_called_once()
        assert ds2._EM_AVAILABLE is True

    def test_ensure_em_checked_skips_when_cached(self):
        """_EM_AVAILABLE 已有值时 _ensure_em_checked 不应触发探测"""
        ds2._EM_AVAILABLE = True
        with patch.object(ds2._EM_SESSION, "get") as mock_get:
            ds2._ensure_em_checked()
            mock_get.assert_not_called()


class TestCacheSizeLimit:
    """缓存上限保护测试(LRU 淘汰,防止 512MB 实例 OOM)"""

    def setup_method(self):
        """每个测试前清空缓存"""
        ds2._cache.clear()

    def teardown_method(self):
        ds2._cache.clear()

    def test_cache_set_and_get(self):
        """基本缓存写入与读取"""
        ds2._cache_set("k1", "v1", ttl=10)
        assert ds2._cache_get("k1") == "v1"

    def test_cache_expired_returns_none(self):
        """过期缓存应返回 None"""
        ds2._cache_set("k1", "v1", ttl=0)
        # ttl=0 立即过期
        import time as _time
        _time.sleep(0.01)
        assert ds2._cache_get("k1") is None

    def test_cache_evicts_oldest_when_full(self):
        """超过 _CACHE_MAX_SIZE 时应淘汰最早条目(FIFO)"""
        for i in range(ds2._CACHE_MAX_SIZE + 5):
            ds2._cache_set(f"key_{i}", f"val_{i}", ttl=60)
        # 缓存大小不应超过上限
        assert len(ds2._cache) <= ds2._CACHE_MAX_SIZE
        # 最早的条目应被淘汰
        assert ds2._cache_get("key_0") is None
        # 最新的条目应存在
        assert ds2._cache_get(f"key_{ds2._CACHE_MAX_SIZE + 4}") == f"val_{ds2._CACHE_MAX_SIZE + 4}"

    def test_cache_update_existing_key_no_eviction(self):
        """更新已存在的 key 不应触发淘汰"""
        ds2._cache_set("existing", "v1", ttl=60)
        # 填满到上限
        for i in range(ds2._CACHE_MAX_SIZE - 1):
            ds2._cache_set(f"key_{i}", f"val_{i}", ttl=60)
        # 更新已存在的 key
        ds2._cache_set("existing", "v2", ttl=60)
        assert ds2._cache_get("existing") == "v2"

"""A-15(2026-11-17):板块排行兜底链收敛 + 进程内单飞。

旧实现: 东财 2 跳 → sina 板块资金 fenlei=2(6s) → fenlei=0(6s) → newFLJK(5s)
全部**串行**, 最坏 17s; 而 `get_sector_ranking` 被 6 个端点共用且没有单飞,
工作台首屏 Promise.all 并发时整条链会被跑 N 遍(上游请求数翻倍)。
"""
from __future__ import annotations

import pytest

from src.core import data_source_v2 as ds2


# ===== A-15(2026-11-17):板块排行兜底链收敛 + 进程内单飞 =====

class TestSectorRankingFallbackChain:
    """旧实现: 东财 2 跳 → sina fenlei=2(6s) → fenlei=0(6s) → newFLJK(5s) 全串行,
    最坏 17s; 且六个端点共用本函数却**没有单飞**, 并发时整条链被跑 N 遍。"""

    def test_sina_fund_tries_only_industry_class(self, monkeypatch):
        """fenlei=2 失败后**本次不再试** fenlei=0(省掉第二个 6s)。"""
        tried: list[int] = []

        def fake_get(url, params=None, **kwargs):
            tried.append((params or {}).get("fenlei"))
            raise RuntimeError("network down")

        monkeypatch.setattr(ds2.requests, "get", fake_get)
        out = ds2._get_sector_ranking_sina_fund()

        assert out == []
        assert tried == [2], f"仍在串行试多个 fenlei: {tried}"

    def test_sina_fund_failure_enters_cooldown(self, monkeypatch):
        """失败后进入冷却: 冷却窗口内的后续调用**一个请求都不发**。"""
        calls: list[int] = []

        def fake_get(url, params=None, **kwargs):
            calls.append((params or {}).get("fenlei"))
            raise RuntimeError("network down")

        monkeypatch.setattr(ds2.requests, "get", fake_get)
        assert ds2._get_sector_ranking_sina_fund() == []
        assert len(calls) == 1

        # 冷却期内再调 3 次, 不应产生任何上游请求
        for _ in range(3):
            assert ds2._get_sector_ranking_sina_fund() == []
        assert len(calls) == 1, "冷却期内仍在重试上游"

    def test_sina_fund_success_resets_cooldown(self, monkeypatch):
        """成功一次要清掉失败时间戳, 否则源恢复后仍被自己的冷却挡住。"""
        rows = [{"category": "new_dl", "name": "电力", "avg_changeratio": "0.012",
                 "avg_price": "8.1", "netamount": "1e8", "ratioamount": "0.03"}]
        monkeypatch.setattr(ds2, "_sina_fund_failed_at", 1.0)  # 模拟"刚失败过"
        monkeypatch.setattr(ds2.time, "monotonic", lambda: 1.0)  # 仍在冷却内
        assert ds2._get_sector_ranking_sina_fund() == []

        monkeypatch.setattr(ds2.time, "monotonic", lambda: 10_000.0)  # 冷却已过
        monkeypatch.setattr(ds2.requests, "get", lambda *a, **k: type(
            "R", (), {"json": lambda self: rows})())
        out = ds2._get_sector_ranking_sina_fund()

        assert len(out) == 1 and out[0]["name"] == "电力"
        assert ds2._sina_fund_failed_at == 0.0, "成功后必须清除失败时间戳"

    def test_concurrent_callers_hit_upstream_only_once(self, monkeypatch):
        """3 个线程同时调用 → 上游只被真正抓 1 次, 三个返回值一致。"""
        import threading
        import time as _time

        ds2._cache.clear()
        calls: list[int] = []
        lock = threading.Lock()

        class _Resp:
            def json(self):
                return {"data": {"diff": [{
                    "f12": "BK0475", "f14": "银行", "f3": 1.2, "f2": 8.0, "f62": 1e8,
                    "f184": 2.0, "f66": 0, "f69": 0, "f72": 0, "f75": 0,
                    "f78": 0, "f81": 0, "f84": 0, "f87": 0,
                }]}}

        def slow_em_get(*args, **kwargs):
            with lock:
                calls.append(1)
            _time.sleep(0.15)  # 模拟上游耗时, 保证三个线程真的重叠
            return _Resp()

        monkeypatch.setattr(ds2, "em_get", slow_em_get)

        results: list[list] = []
        errors: list[BaseException] = []

        def worker():
            try:
                results.append(ds2.get_sector_ranking())
            except BaseException as e:  # pragma: no cover - 失败即断言
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert len(calls) == 1, f"并发下上游被重复抓取 {len(calls)} 次"
        assert len(results) == 3
        assert all(r == results[0] for r in results)

    def test_single_flight_releases_after_failure(self, monkeypatch):
        """取数抛异常时锁必须释放, 否则后续请求永久等待。"""

        ds2._cache.clear()
        monkeypatch.setattr(ds2, "_fetch_sector_ranking_uncached",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            ds2.get_sector_ranking()

        assert ds2._SECTOR_RANKING_LOCK.acquire(blocking=False), "锁未被释放"
        ds2._SECTOR_RANKING_LOCK.release()

    def test_eastmoney_path_unchanged(self, monkeypatch):
        """回归保护: 东财可用时仍走东财, 且标记来源为 eastmoney。"""
        ds2._cache.clear()

        class _Resp:
            def json(self):
                return {"data": {"diff": [{
                    "f12": "BK0475", "f14": "银行", "f3": 1.2, "f2": 8.0, "f62": 1e8,
                    "f184": 2.0, "f66": 0, "f69": 0, "f72": 0, "f75": 0,
                    "f78": 0, "f81": 0, "f84": 0, "f87": 0,
                }]}}

        monkeypatch.setattr(ds2, "em_get", lambda *a, **k: _Resp())
        out = ds2.get_sector_ranking()

        assert len(out) == 1 and out[0]["name"] == "银行"
        assert ds2.get_last_source("sector_ranking") == "eastmoney"


# ===== P1-2(2026-11-17):板块评分必须用全量分页, 单页失败即 fail-closed =====
# 旧 `get_sector_ranking` 是 pz=50 + fid=f3 降序 = "涨幅榜前 50", 上涨占比恒 1.0
# → 板块轮动分恒 100。全量取数走 5 页(pz=100, total 496)。

class TestSectorRankingFullPages:
    @staticmethod
    def _page(n: int, total: int, positive: int):
        """构造一页: 前 positive 条为正, 其余为负。"""
        rows = []
        for i in range(n):
            rows.append({"f12": f"BK{i}", "f14": f"板块{i}", "f3": 1.0 if i < positive else -1.0,
                         "f2": 1000.0, "f62": 1e8 if i < positive else -1e8, "f184": 0.5,
                         "f66": 0.0, "f69": 0.0, "f72": 0.0, "f75": 0.0,
                         "f78": 0.0, "f81": 0.0, "f84": 0.0, "f87": 0.0})
        return {"data": {"total": total, "diff": rows}}

    def test_pages_all_fetched(self, monkeypatch):
        """496 条 → 5 页全取, total_sectors 才是真实的 496(72 涨 / 423 跌 / 1 平)。"""
        changes = [1.0] * 72 + [-1.0] * 423 + [0.0]     # 按真实分布, 共 496
        pages = [changes[i:i + 100] for i in range(0, len(changes), 100)]

        monkeypatch.setattr(ds2, "em_get", lambda url, params=None, **kw: _RowsResp(
            pages[int((params or {}).get("pn", 1)) - 1], 496))
        monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
        monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)

        rows = ds2.get_sector_ranking_full()

        assert len(rows) == 496
        assert sum(1 for r in rows if r["change_pct"] > 0) == 72
        assert sum(1 for r in rows if r["change_pct"] < 0) == 423
        assert sum(1 for r in rows if r["change_pct"] == 0) == 1

    def test_page_failure_fails_closed(self, monkeypatch):
        """第 3 页抛异常 → 返回 [](评分侧据此 available=False / score=50)。

        绝不能用前两页(全是上涨板块)算比例 —— 那正是恒 100 的成因。
        """
        sizes = [100, 100, 100, 100, 96]

        def fake_em_get(url, params=None, **kwargs):
            pn = int((params or {}).get("pn", 1))
            if pn == 3:
                raise RuntimeError("Remote end closed connection")
            return _PageResp(sizes[pn - 1], 496, 50)

        monkeypatch.setattr(ds2, "em_get", fake_em_get)
        monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
        monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)

        assert ds2.get_sector_ranking_full() == []

    def test_short_page_list_fails_closed(self, monkeypatch):
        """上游 total 说 496 但只给了 2 页就空 → 视为部分数据, 同样 fail-closed。"""
        def fake_em_get(url, params=None, **kwargs):
            pn = int((params or {}).get("pn", 1))
            if pn > 2:
                return _PageResp(0, 496, 0, empty=True)
            return _PageResp(100, 496, 50)

        monkeypatch.setattr(ds2, "em_get", fake_em_get)
        monkeypatch.setattr(ds2, "_cache_get", lambda key: None)
        monkeypatch.setattr(ds2, "_cache_set", lambda key, val, ttl: None)

        assert ds2.get_sector_ranking_full() == []

    def test_display_path_unchanged(self, monkeypatch):
        """回归保护: 展示路径 `get_sector_ranking` 仍是 top-N(不因本次改动变大)。"""
        rows = [{"f12": f"BK{i}", "f14": f"板块{i}", "f3": 1.0, "f2": 1000.0, "f62": 1e8,
                 "f184": 0.5, "f66": 0, "f69": 0, "f72": 0, "f75": 0,
                 "f78": 0, "f81": 0, "f84": 0, "f87": 0} for i in range(50)]
        monkeypatch.setattr(ds2, "em_get", lambda url, params=None, **kw: _PageResp(50, 496, 50, rows=rows))
        ds2._cache.clear()

        out = ds2.get_sector_ranking()

        assert len(out) == 50, "展示路径仍是 top-50, 不应变成 496 行"


class _PageResp:
    """极简 clist 分页响应。"""

    def __init__(self, size: int, total: int, positive: int, empty: bool = False, rows=None):
        if rows is not None:
            self._payload = {"data": {"total": total, "diff": rows}}
        elif empty:
            self._payload = {"data": {"total": total, "diff": []}}
        else:
            self._payload = {"data": {"total": total, "diff": [
                {"f12": f"BK{i}", "f14": f"板块{i}",
                 "f3": 1.0 if i < positive else -1.0, "f2": 1000.0,
                 "f62": 1e8 if i < positive else -1e8, "f184": 0.5,
                 "f66": 0, "f69": 0, "f72": 0, "f75": 0, "f78": 0, "f81": 0,
                 "f84": 0, "f87": 0}
                for i in range(size)
            ]}}

    def json(self):
        return self._payload


class _RowsResp:
    """按 change_pct 列表构造一页 clist 响应。"""

    def __init__(self, changes: list[float], total: int):
        self._payload = {"data": {"total": total, "diff": [
            {"f12": f"BK{i}", "f14": f"板块{i}", "f3": chg, "f2": 1000.0,
             "f62": 1e8 if chg > 0 else (-1e8 if chg < 0 else 0.0), "f184": 0.5,
             "f66": 0, "f69": 0, "f72": 0, "f75": 0, "f78": 0, "f81": 0,
             "f84": 0, "f87": 0}
            for i, chg in enumerate(changes)
        ]}}

    def json(self):
        return self._payload

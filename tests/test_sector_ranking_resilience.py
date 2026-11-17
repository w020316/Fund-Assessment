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
        import threading

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

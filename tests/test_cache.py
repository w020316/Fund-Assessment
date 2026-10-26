"""DataCache 缓存模块单元测试

验证 src/core/cache.py 的核心功能:
- get/set 基础读写
- TTL 过期失效
- delete/clear 清理
- 文件名非法字符转义(Windows 兼容)
- Pydantic 模型序列化
- 损坏文件兜底
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from src.core.cache import DataCache, _serialize


class _Item(BaseModel):
    """测试用 Pydantic 模型"""
    code: str
    price: float


@pytest.fixture
def cache(tmp_path):
    """每个测试用独立临时目录的缓存实例"""
    return DataCache(cache_dir=str(tmp_path / "test_cache"), default_ttl=60)


class TestSerialize:
    """_serialize 序列化辅助函数"""

    def test_serializes_pydantic_model(self):
        """Pydantic 模型序列化为 dict"""
        item = _Item(code="600519", price=1688.0)
        result = _serialize(item)
        assert result == {"code": "600519", "price": 1688.0}

    def test_serializes_set_to_list(self):
        """set 序列化为 list(JSON 不支持 set)"""
        result = _serialize({1, 2, 3})
        assert sorted(result) == [1, 2, 3]

    def test_serializes_bytes_to_str(self):
        """bytes 解码为字符串"""
        result = _serialize(b"hello")
        assert result == "hello"

    def test_serializes_other_types_as_str(self):
        """其他类型转字符串"""
        assert _serialize(123) == "123"


class TestCacheGetSet:
    """get/set 读写"""

    def test_set_then_get_returns_value(self, cache):
        """写入后读取应返回原值"""
        cache.set("key1", {"a": 1})
        assert cache.get("key1") == {"a": 1}

    def test_get_nonexistent_returns_none(self, cache):
        """读取不存在的键返回 None"""
        assert cache.get("not_exists") is None

    def test_set_with_pydantic_model(self, cache):
        """写入 Pydantic 模型应正常序列化"""
        item = _Item(code="600519", price=1688.0)
        cache.set("stock", item)
        result = cache.get("stock")
        assert result == {"code": "600519", "price": 1688.0}

    def test_set_with_list(self, cache):
        """写入列表"""
        data = [{"code": "001"}, {"code": "002"}]
        cache.set("list", data)
        assert cache.get("list") == data

    def test_set_overwrites_existing(self, cache):
        """重复 set 覆盖旧值"""
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"

    def test_set_with_custom_ttl(self, cache):
        """自定义 TTL"""
        cache.set("short", "v", ttl=1)
        assert cache.get("short") == "v"


class TestCacheTTL:
    """TTL 过期机制"""

    def test_expired_entry_returns_none(self, cache):
        """过期后返回 None 并删除文件"""
        cache.set("k", "v", ttl=1)
        # 模拟时间流逝
        with patch("src.core.cache.time.time", return_value=time.time() + 2):
            assert cache.get("k") is None

    def test_non_expired_entry_returns_value(self, cache):
        """未过期返回值"""
        cache.set("k", "v", ttl=100)
        assert cache.get("k") == "v"

    def test_uses_default_ttl_when_none(self, cache):
        """ttl=None 时用 default_ttl"""
        cache.set("k", "v", ttl=None)
        # 读取元数据验证
        path = cache.cache_dir / "k.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["_ttl"] == 60


class TestCacheDelete:
    """delete 删除"""

    def test_delete_existing_key(self, cache):
        """删除已存在的键"""
        cache.set("k", "v")
        cache.delete("k")
        assert cache.get("k") is None

    def test_delete_nonexistent_no_error(self, cache):
        """删除不存在的键不报错"""
        cache.delete("not_exists")  # 不应抛异常


class TestCacheClear:
    """clear 清空"""

    def test_clear_removes_all_entries(self, cache):
        """清空所有缓存"""
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None

    def test_clear_empty_dir_no_error(self, cache):
        """空目录 clear 不报错"""
        cache.clear()


class TestInvalidateByPrefix:
    """invalidate_by_prefix 按前缀批量失效(借鉴 diskcache evict(tag) 设计)"""

    def test_invalidates_matching_prefix(self, cache):
        """应删除所有匹配前缀的缓存条目"""
        cache.set("fund_110022_nav", {"nav": 1.5})
        cache.set("fund_110022_holdings", [{"code": "600519"}])
        cache.set("fund_161725_nav", {"nav": 2.0})
        cache.set("sector_rotation", [{"name": "白酒"}])

        deleted = cache.invalidate_by_prefix("fund_110022")
        assert deleted == 2
        assert cache.get("fund_110022_nav") is None
        assert cache.get("fund_110022_holdings") is None
        # 其他前缀不受影响
        assert cache.get("fund_161725_nav") == {"nav": 2.0}
        assert cache.get("sector_rotation") is not None

    def test_invalidates_all_fund_prefix(self, cache):
        """前缀 'fund_' 应匹配所有基金缓存"""
        cache.set("fund_110022", 1)
        cache.set("fund_161725", 2)
        cache.set("stock_600519", 3)

        deleted = cache.invalidate_by_prefix("fund_")
        assert deleted == 2
        assert cache.get("stock_600519") == 3

    def test_empty_prefix_returns_zero(self, cache):
        """空前缀不删除任何内容(防止误删全部)"""
        cache.set("k1", "v1")
        assert cache.invalidate_by_prefix("") == 0
        assert cache.get("k1") == "v1"

    def test_no_match_returns_zero(self, cache):
        """无匹配返回 0"""
        cache.set("k1", "v1")
        assert cache.invalidate_by_prefix("nonexistent_prefix") == 0
        assert cache.get("k1") == "v1"

    def test_prefix_with_special_chars_safe(self, cache):
        """含特殊字符的前缀也应安全工作(经 _safe_key 转义)"""
        cache.set("market:stock/600519", {"price": 1688})
        cache.set("market:stock/000001", {"price": 15})
        cache.set("market:index/000300", {"price": 4000})

        deleted = cache.invalidate_by_prefix("market:stock/")
        assert deleted == 2
        assert cache.get("market:stock/600519") is None
        assert cache.get("market:index/000300") is not None

    def test_partial_prefix_not_matched(self, cache):
        """前缀匹配应精确到字符,不匹配部分前缀"""
        cache.set("fund_110022", 1)
        cache.set("fund_a110022", 2)  # 注意 'fund_a' 不匹配 'fund_1'
        deleted = cache.invalidate_by_prefix("fund_1")
        assert deleted == 1
        assert cache.get("fund_a110022") == 2


class TestInvalidateBySuffix:
    """invalidate_by_suffix 按后缀批量失效"""

    def test_invalidates_matching_suffix(self, cache):
        """应删除所有匹配后缀的缓存条目"""
        cache.set("stock_600519_realtime", {"price": 1688})
        cache.set("stock_000001_realtime", {"price": 15})
        cache.set("stock_600519_history", [{"nav": 1.5}])

        deleted = cache.invalidate_by_suffix("_realtime")
        assert deleted == 2
        assert cache.get("stock_600519_realtime") is None
        assert cache.get("stock_000001_realtime") is None
        # history 不受影响
        assert cache.get("stock_600519_history") is not None

    def test_empty_suffix_returns_zero(self, cache):
        """空后缀不删除任何内容"""
        cache.set("k1", "v1")
        assert cache.invalidate_by_suffix("") == 0
        assert cache.get("k1") == "v1"

    def test_no_match_returns_zero(self, cache):
        """无匹配返回 0"""
        cache.set("k1", "v1")
        assert cache.invalidate_by_suffix("_nonexistent") == 0


class TestSafeKey:
    """_safe_key 文件名非法字符转义"""

    def test_replaces_colon(self, cache):
        """冒号转下划线"""
        assert cache._safe_key("a:b") == "a_b"

    def test_replaces_slash(self, cache):
        """斜杠转下划线"""
        assert cache._safe_key("a/b") == "a_b"
        assert cache._safe_key("a\\b") == "a_b"

    def test_replaces_question_and_star(self, cache):
        """问号和星号转下划线"""
        assert cache._safe_key("a?b") == "a_b"
        assert cache._safe_key("a*b") == "a_b"

    def test_set_with_special_chars_persists(self, cache):
        """含特殊字符的键也能正常存取"""
        cache.set("market:stock/600519?x", {"price": 1688})
        assert cache.get("market:stock/600519?x") == {"price": 1688}


class TestCorruptedCache:
    """损坏文件兜底"""

    def test_corrupted_json_returns_none(self, cache):
        """JSON 损坏返回 None 而非抛异常"""
        path = cache.cache_dir / "bad.json"
        path.write_text("not a valid json {", encoding="utf-8")
        assert cache.get("bad") is None

    def test_missing_timestamp_returns_none(self, cache):
        """缺少 _timestamp 字段返回 None"""
        path = cache.cache_dir / "no_ts.json"
        path.write_text('{"value": "v"}', encoding="utf-8")
        # time.time() - 0 > 60(TTL),会判过期
        assert cache.get("no_ts") is None


class TestCacheDirCreation:
    """缓存目录自动创建"""

    def test_creates_cache_dir_if_not_exists(self, tmp_path):
        """目录不存在时自动创建"""
        cache_dir = tmp_path / "new" / "cache"
        assert not cache_dir.exists()
        DataCache(cache_dir=str(cache_dir))
        assert cache_dir.exists()


class TestFileLayerTypeFidelity:
    """文件层类型保真（2026-10-26 P1 修复回归）

    缺陷：DataCache.set 对整个 value 调 _serialize()，而 _serialize 的兜底分支是
    str(obj)，导致 list/dict 被整体转成 Python repr 字符串写入文件。
    内存层命中时无感知，但进程重启或 LRU 淘汰后回落到文件层即读回字符串，
    表现为接口返回 "[{...}]" 这样的字符串而非数组。
    本类锁定：跨实例（模拟进程重启）读回必须保持原始 JSON 类型。
    """

    def test_list_survives_new_instance(self, tmp_path):
        """list 经文件层跨实例读回应仍为 list"""
        from src.core.cache import DataCache

        cache_dir = str(tmp_path / "c")
        payload = [{"date": "2026-07-28", "nav": 2.71}, {"date": "2026-07-29", "nav": 2.724}]
        DataCache(cache_dir=cache_dir, default_ttl=300).set("fund_history:110022:1y", payload, ttl=300)

        got = DataCache(cache_dir=cache_dir, default_ttl=300).get("fund_history:110022:1y")
        assert isinstance(got, list), f"期望 list，实际 {type(got).__name__}: {got!r}"
        assert got == payload

    def test_nested_dict_survives_new_instance(self, tmp_path):
        """嵌套 dict（行情快照典型结构）跨实例读回结构不丢"""
        from src.core.cache import DataCache

        cache_dir = str(tmp_path / "c")
        payload = {"top_gainers": [{"code": "600519", "change_pct": 3.2}], "count": 1}
        DataCache(cache_dir=cache_dir, default_ttl=300).set("market:hot_stocks", payload, ttl=300)

        got = DataCache(cache_dir=cache_dir, default_ttl=300).get("market:hot_stocks")
        assert isinstance(got, dict), f"期望 dict，实际 {type(got).__name__}: {got!r}"
        assert got["top_gainers"][0]["code"] == "600519"
        assert got["count"] == 1

    def test_pydantic_model_still_serialized(self, tmp_path):
        """Pydantic 模型仍可写入（回归保护）"""
        from src.core.cache import DataCache

        cache_dir = str(tmp_path / "c")
        DataCache(cache_dir=cache_dir, default_ttl=300).set("stock", _Item(code="600519", price=1688.0))

        got = DataCache(cache_dir=cache_dir, default_ttl=300).get("stock")
        assert got == {"code": "600519", "price": 1688.0}


class TestStaleWhileRevalidate:
    """get_with_stale：过期仍可用的旧值（2026-10-26 A-11 新增）

    背景：首屏 /api/market/hot_stocks 冷态 0.999s、TTL 仅 60s，
    每逢过期的那位用户要等完整一轮外部请求。SWR 让过期数据先顶上、后台再刷新。
    """

    def test_fresh_value_reports_not_stale(self, cache):
        cache.set("k", {"v": 1}, ttl=100)
        value, stale = cache.get_with_stale("k", max_stale=600)
        assert value == {"v": 1}
        assert stale is False

    def test_expired_within_window_reports_stale(self, cache):
        cache.set("k", [1, 2, 3], ttl=60)
        cache._memory.clear()  # 强制走文件层，验证跨进程场景
        with patch("src.core.cache.time.time", return_value=time.time() + 120):
            value, stale = cache.get_with_stale("k", max_stale=600)
        assert value == [1, 2, 3]
        assert stale is True, "过期但在容忍窗口内应返回旧值并标记 stale"

    def test_expired_beyond_window_returns_none(self, cache):
        cache.set("k", "v", ttl=60)
        cache._memory.clear()
        with patch("src.core.cache.time.time", return_value=time.time() + 10_000):
            value, stale = cache.get_with_stale("k", max_stale=600)
        assert value is None
        assert stale is False

    def test_missing_key_returns_none(self, cache):
        assert cache.get_with_stale("nope", max_stale=600) == (None, False)


class TestFileSweep:
    """文件层按上限清理（2026-10-26 A-08 修复回归）

    缺陷：set 每次写一个 json，过期只删内存不删文件，"后台清理"从未实现 → 磁盘只增不减。
    """

    def test_sweep_bounds_file_count(self, tmp_path):
        from src.core.cache import DataCache

        c = DataCache(cache_dir=str(tmp_path / "c"), default_ttl=60, max_files=5, sweep_every=1)
        for i in range(20):
            c.set(f"key_{i}", {"i": i}, ttl=60)
        files = list(c.cache_dir.glob("*.json"))
        assert len(files) <= 5, f"文件数 {len(files)} 应被限制在 5 以内"

    def test_sweep_keeps_newest(self, tmp_path):
        from src.core.cache import DataCache

        c = DataCache(cache_dir=str(tmp_path / "c"), default_ttl=60, max_files=3, sweep_every=1)
        for i in range(10):
            c.set(f"key_{i}", {"i": i}, ttl=60)
            time.sleep(0.01)  # 保证 mtime 可区分
        assert (c.cache_dir / "key_9.json").exists(), "最新写入的文件不应被清理"

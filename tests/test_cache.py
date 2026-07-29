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

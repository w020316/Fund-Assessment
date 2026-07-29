from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _serialize(obj: Any) -> Any:
    """JSON serializer that handles Pydantic models and common types."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


class DataCache:
    def __init__(self, cache_dir: str = "data/cache", default_ttl: int = 300):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl

    def _safe_key(self, key: str) -> str:
        """将缓存键中的非法文件名字符替换为下划线（Windows兼容）"""
        return key.replace(":", "_").replace("/", "_").replace("\\", "_").replace("?", "_").replace("*", "_")

    def get(self, key: str) -> dict | None:
        path = self.cache_dir / f"{self._safe_key(key)}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("_timestamp", 0) > data.get("_ttl", self.default_ttl):
                path.unlink(missing_ok=True)
                return None
            return data.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int | None = None):
        path = self.cache_dir / f"{self._safe_key(key)}.json"
        data = {
            "value": value,
            "_timestamp": time.time(),
            "_ttl": ttl or self.default_ttl,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, default=_serialize), encoding="utf-8")

    def delete(self, key: str):
        path = self.cache_dir / f"{self._safe_key(key)}.json"
        path.unlink(missing_ok=True)

    def clear(self):
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)

    # ===== Tags 批量失效(借鉴 grantjenks/python-diskcache 的 tag 失效设计) =====
    # diskcache 支持 cache.evict(tag=...) 按标签批量失效,
    # 本项目用文件名前缀实现等价语义:同一业务模块的缓存键使用统一前缀,
    # 例如 "fund_110022_*" / "sector_*" / "news_*",
    # 调用 invalidate_by_prefix("fund_110022") 即可批量失效该基金的所有缓存。
    def invalidate_by_prefix(self, prefix: str) -> int:
        """按缓存键前缀批量失效缓存。

        借鉴 grantjenks/python-diskcache(2.4K Star)的 evict(tag) 设计:
        diskcache 用 Tags 批量失效,本项目用文件名前缀实现等价语义,
        便于在"数据源更新"时一键清理该模块所有缓存,避免脏数据。

        Args:
            prefix: 缓存键前缀(如 "fund_110022" / "sector" / "news")

        Returns:
            被删除的缓存条目数

        示例:
            >>> cache.set("fund_110022_nav", {...})
            >>> cache.set("fund_110022_holdings", {...})
            >>> cache.invalidate_by_prefix("fund_110022")  # 清理该基金所有缓存
            2
        """
        if not prefix:
            return 0
        safe_prefix = self._safe_key(prefix)
        deleted = 0
        for f in self.cache_dir.glob(f"{safe_prefix}*.json"):
            try:
                f.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                pass
        return deleted

    def invalidate_by_suffix(self, suffix: str) -> int:
        """按缓存键后缀批量失效缓存(辅助方法,用于按数据类型清理)。

        借鉴 grantjenks/python-diskcache 的批量失效设计,
        场景:按数据类型清理,如 "_realtime"(所有实时行情缓存) / "_history"(所有历史数据)。

        Args:
            suffix: 缓存键后缀(如 "_realtime" / "_history")

        Returns:
            被删除的缓存条目数
        """
        if not suffix:
            return 0
        safe_suffix = self._safe_key(suffix)
        deleted = 0
        for f in self.cache_dir.glob(f"*{safe_suffix}.json"):
            try:
                f.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                pass
        return deleted

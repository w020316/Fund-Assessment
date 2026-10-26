from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel

# 文件层原子写入修复(2026-07-31):原 cache.set 用 write_text 直接写文件,
# 并发写时可能产生半截 JSON 损坏文件。复用 file_io.atomic_write_json 原子写入(写临时文件再 rename)。
from src.utils.file_io import atomic_write_json, safe_read_json


def _serialize(obj: Any) -> Any:
    """JSON serializer that handles Pydantic models and common types."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


class _MemoryLayer:
    """内存层 LRU+TTL 缓存(借鉴 Redisamples/python-lru-cache 设计)

    设计目的:
    - 文件 I/O 阻塞 FastAPI 事件循环(尤其 512MB Render Free 实例),
      增加 LRU 内存层命中时直接返回,避免磁盘读取
    - TTL 自动过期,LRU 淘汰最旧条目,防止内存无限增长(OOM 防护)
    - 线程安全(asyncio.to_thread 会并发访问)
    """

    def __init__(self, maxsize: int = 512):
        self._maxsize = max(maxsize, 8)
        self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        # 命中率统计(供 /api/health 返回)
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            # 用 time.time()(与文件层一致),让测试 patch time.time 生效
            if time.time() - entry["ts"] > entry["ttl"]:
                # 过期:仅内存删除,不删除文件(避免文件竞态)
                self._store.pop(key, None)
                self.misses += 1
                return None
            # 命中:LRU 移到末尾(最近使用)
            self._store.move_to_end(key)
            self.hits += 1
            return entry["val"]

    def set(self, key: str, val: Any, ttl: float) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            # 序列化 pydantic 模型为 dict(与文件层一致,避免内存层返回原对象被外部修改)
            # 文件层通过 json.dumps(default=_serialize) 自动转换,内存层需手动处理
            if isinstance(val, BaseModel):
                stored_val = val.model_dump()
            elif isinstance(val, (set, frozenset)):
                stored_val = list(val)
            elif isinstance(val, bytes):
                stored_val = val.decode("utf-8", errors="replace")
            else:
                stored_val = val
            self._store[key] = {"val": stored_val, "ts": time.time(), "ttl": ttl}
            # LRU 淘汰:超过 maxsize 时删除最旧条目
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._store),
                "maxsize": self._maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total > 0 else 0.0,
            }


class DataCache:
    def __init__(
        self,
        cache_dir: str = "data/cache",
        default_ttl: int = 300,
        memory_maxsize: int = 512,
        max_files: int = 512,
        sweep_every: int = 50,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        # 内存层 LRU+TTL(命中时跳过文件 I/O,减少事件循环阻塞)
        self._memory = _MemoryLayer(maxsize=memory_maxsize)
        # P2 修复(2026-10-26)：文件层此前**只增不减** —— set 每次写一个 json，
        # 过期后 get 只删内存条目（注释明确"文件由后续 set 覆盖或后台清理"），
        # 但"后台清理"从未实现。以基金代码/搜索词作 key 时可被无限撑大磁盘。
        # 现在按 max_files 上限做周期性清理（按 mtime 淘汰最旧），代价是每 N 次写入扫一次目录。
        self.max_files = max_files
        self.sweep_every = max(sweep_every, 1)
        self._set_count = 0

    def _safe_key(self, key: str) -> str:
        """将缓存键中的非法文件名字符替换为下划线（Windows兼容）"""
        return key.replace(":", "_").replace("/", "_").replace("\\", "_").replace("?", "_").replace("*", "_")

    def get(self, key: str) -> dict | None:
        # 1. 先查内存层(命中直接返回,无文件 I/O)
        mem_val = self._memory.get(key)
        if mem_val is not None:
            return mem_val
        # 2. 内存未命中:查文件层(用 safe_read_json 防止损坏文件导致异常)
        path = self.cache_dir / f"{self._safe_key(key)}.json"
        data = safe_read_json(path)
        if data is None:
            return None
        try:
            ttl = data.get("_ttl", self.default_ttl)
            if time.time() - data.get("_timestamp", 0) > ttl:
                # 修复(2026-07-29):原代码 unlink 在并发读时引发 FileNotFoundError 竞态
                # 改为:仅删除内存条目,文件由后续 set 覆盖或后台清理
                self._memory.delete(key)
                return None
            value = data.get("value")
            # 回填内存层(下次命中直接走内存)
            self._memory.set(key, value, ttl=ttl)
            return value
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int | None = None):
        effective_ttl = ttl or self.default_ttl
        # 1. 写内存层(后续命中走内存)
        self._memory.set(key, value, ttl=effective_ttl)
        # 2. 写文件层(原子写入,防止并发写损坏)
        # P1 修复(2026-10-26):原实现对**整个 value** 调用 _serialize(),
        # 而 _serialize 的兜底分支是 str(obj) —— 于是 list/dict 被整体转成
        # Python repr 字符串写入文件(如 "[{'date': '2026-07-28', ...}]")。
        # 内存层命中时看不出问题,但一旦进程重启或 LRU 淘汰后回落到文件层,
        # 读回的就是 str 而非 list/dict,前端会拿到字符串 → 渲染异常/"暂无数据"。
        # 现改为:value 原样写入,仅把 _serialize 作为 json.dump 的 default 回调,
        # 只在遇到 Pydantic 模型/set/bytes 等无法原生序列化的叶子时触发。
        path = self.cache_dir / f"{self._safe_key(key)}.json"
        data = {
            "value": value,
            "_timestamp": time.time(),
            "_ttl": effective_ttl,
        }
        atomic_write_json(path, data, default=_serialize)
        self._maybe_sweep()

    def _maybe_sweep(self) -> None:
        """周期性清理文件层，把文件数控制在 max_files 以内（淘汰最旧）。"""
        self._set_count += 1
        if self._set_count % self.sweep_every:
            return
        try:
            files = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError as e:
            logger.debug(f"cache sweep 列目录失败: {e}")
            return
        overflow = len(files) - self.max_files
        if overflow <= 0:
            return
        removed = 0
        for f in files[:overflow]:
            try:
                f.unlink(missing_ok=True)
                removed += 1
            except OSError as e:
                logger.debug(f"cache sweep 删除失败 {f}: {e}")
        if removed:
            logger.info(f"cache sweep: 文件数 {len(files)} 超过上限 {self.max_files}，已清理最旧 {removed} 个")

    def get_with_stale(self, key: str, max_stale: int = 300) -> tuple[Any | None, bool]:
        """读取缓存，允许返回"已过期但仍可用"的旧值。

        P2 新增(2026-10-26)，用于 stale-while-revalidate：
        首屏接口（如 /api/market/hot_stocks）冷态要 1s，而 TTL 只有 60s，
        过期瞬间的那位用户就得等完整一轮外部请求。改成"先返回旧值、后台刷新"后，
        用户侧看到的是十几毫秒。

        Returns:
            (value, is_stale)
            - (None, False)：无可用数据（内存/文件都没有，或超出 max_stale 容忍窗口）
            - (value, False)：新鲜命中（已回填内存层）
            - (value, True)：过期但在容忍窗口内，调用方应立即响应并触发后台刷新
        """
        mem_val = self._memory.get(key)
        if mem_val is not None:
            return mem_val, False

        path = self.cache_dir / f"{self._safe_key(key)}.json"
        data = safe_read_json(path)
        if data is None:
            return None, False
        try:
            value = data.get("value")
            ttl = data.get("_ttl", self.default_ttl)
            age = time.time() - data.get("_timestamp", 0)
            if age <= ttl:
                self._memory.set(key, value, ttl=ttl)
                return value, False
            if max_stale > 0 and age <= ttl + max_stale:
                # 刻意不回填内存层：避免把过期数据"续命"，下次仍走文件层判断
                return value, True
            return None, False
        except Exception as e:
            logger.debug(f"cache get_with_stale 解析失败 {key}: {e}")
            return None, False

    def delete(self, key: str):
        self._memory.delete(key)
        path = self.cache_dir / f"{self._safe_key(key)}.json"
        path.unlink(missing_ok=True)

    def clear(self):
        self._memory.clear()
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)

    def memory_stats(self) -> dict[str, Any]:
        """返回内存层命中率统计(供 /api/health 返回)"""
        return self._memory.stats()

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
        # 1. 同步清理内存层(避免命中脏数据)
        with self._memory._lock:
            stale_keys = [k for k in self._memory._store if k.startswith(prefix)]
            for k in stale_keys:
                self._memory._store.pop(k, None)
        # 2. 清理文件层
        deleted = 0
        for f in self.cache_dir.glob(f"{safe_prefix}*.json"):
            try:
                f.unlink(missing_ok=True)
                deleted += 1
            except Exception as e:
                # P2 修复(2026-07-29):原 except:pass 完全静默,缓存清理失败时运维无法发现
                # 改为 debug 级别日志(降级返回值不变,仅记录失败原因)
                logger.debug(f"cache invalidate_by_prefix unlink failed: file={f}, err={e}")
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
        # 1. 同步清理内存层
        with self._memory._lock:
            stale_keys = [k for k in self._memory._store if k.endswith(suffix)]
            for k in stale_keys:
                self._memory._store.pop(k, None)
        # 2. 清理文件层
        deleted = 0
        for f in self.cache_dir.glob(f"*{safe_suffix}.json"):
            try:
                f.unlink(missing_ok=True)
                deleted += 1
            except Exception as e:
                # P2 修复(2026-07-29):原 except:pass 完全静默,改为 debug 级别日志
                logger.debug(f"cache invalidate_by_suffix unlink failed: file={f}, err={e}")
        return deleted

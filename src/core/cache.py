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

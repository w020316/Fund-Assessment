"""JSON 文件原子读写 + 并发安全

P0 修复(2026-07-30):原 web/routes/{config,fund,dashboard}.py 直接 json.dump 到主文件,
存在两个风险:
1. 并发写无锁保护,两个请求同时写会产生部分覆盖的损坏 JSON
2. 写入过程崩溃(如 Render 重启/内存超限)会留下截断的 JSON,下次启动无法读取

本模块提供:
- atomic_write_json: 写 .tmp → os.replace 原子替换,并同步 .bak 备份
- safe_read_json: 主文件损坏时自动回退到 .bak 备份

每个文件路径有独立 threading.Lock,保证同文件串行写,不同文件不互斥。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from loguru import logger

# 全局文件锁池(按路径规范化后粒度)
_file_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    """获取指定路径的锁(每个文件独立锁,不同文件不互斥)。"""
    with _locks_guard:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


def atomic_write_json(file_path: str | Path, data: Any, indent: int = 2) -> None:
    """原子写入 JSON 文件(防止崩溃导致数据损坏)。

    流程:
    1. 写入 <path>.tmp 临时文件,fsync 保证落盘
    2. 同步备份现有主文件到 <path>.bak
    3. os.replace 原子替换(POSIX/Windows 均保证原子性)

    Raises:
        OSError/IOError: 文件系统错误(空间不足/权限拒绝)
        TypeError: data 含不可 JSON 序列化的对象
    """
    path = str(file_path)
    tmp_path = f"{path}.tmp"
    bak_path = f"{path}.bak"
    lock = _get_lock(path)
    with lock:
        # 1. 写入 .tmp 临时文件(若存在残留则覆盖)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # 某些文件系统不支持 fsync
        # 2. 备份现有主文件(若存在)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as src, \
                     open(bak_path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            except Exception as e:
                logger.warning(f"backup {path} -> .bak failed: {e}")
        # 3. 原子替换
        os.replace(tmp_path, path)


def safe_read_json(file_path: str | Path, default: Any = None) -> Any:
    """安全读取 JSON 文件(主文件损坏时尝试 .bak 备份恢复)。

    流程:
    1. 读取主文件并 JSON 解析
    2. 解析失败 → 尝试读取 .bak 备份
    3. 备份成功 → 用备份覆盖主文件(自动恢复)
    4. 备份也失败 → 返回 default

    Returns:
        解析后的 JSON 数据,或 default(若文件不存在/全部损坏)
    """
    path = str(file_path)
    bak_path = f"{path}.bak"
    if not os.path.exists(path):
        return default
    lock = _get_lock(path)
    with lock:
        # 1. 尝试主文件
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error in {path}: {e}, trying .bak backup")
        except Exception as e:
            logger.warning(f"read {path} failed: {e}, trying .bak backup")
        # 2. 尝试 .bak 备份
        if os.path.exists(bak_path):
            try:
                with open(bak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"recovered {path} from .bak backup")
                # 自动用备份恢复主文件
                try:
                    os.replace(bak_path, path)
                    logger.info(f"restored main file {path} from backup")
                except Exception as e:
                    logger.warning(f"restore {path} from backup failed: {e}")
                return data
            except Exception as e:
                logger.error(f"backup {bak_path} also corrupted: {e}")
        # 3. 全部失败,返回默认值
        return default

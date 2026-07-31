"""JSON 文件原子读写单元测试

验证 src/utils/file_io.py 的核心函数:
- atomic_write_json: 原子写入(.tmp -> os.replace),并同步 .bak 备份
- safe_read_json: 主文件损坏时自动回退 .bak 备份,全部失败返回 default

测试场景:
- 正常写入读取往返(round-trip)
- 写入后文件存在且内容正确
- 主文件损坏时从 .bak 恢复
- 并发写入不冲突(线程池并发,锁保证串行)
- 缺失文件返回默认值
- 中文/Unicode 内容保持正确(ensure_ascii=False)

使用 tmp_path fixture 隔离测试目录,避免污染仓库。
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.utils.file_io import atomic_write_json, safe_read_json


class TestAtomicWriteJson:
    """atomic_write_json 原子写入"""

    def test_write_creates_file_with_correct_content(self, tmp_path):
        """写入后文件存在,内容与传入数据一致"""
        target = tmp_path / "data.json"
        payload = {"name": "易方达消费", "code": "110022", "shares": 1000.0}
        atomic_write_json(target, payload)
        # 文件应存在
        assert target.exists()
        # 内容应可被标准 json.load 正确解析
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == payload

    def test_write_round_trip_with_safe_read(self, tmp_path):
        """写入后用 safe_read_json 读取,数据一致(往返)"""
        target = tmp_path / "rt.json"
        payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        atomic_write_json(target, payload)
        assert safe_read_json(target) == payload

    def test_write_preserves_chinese_unicode(self, tmp_path):
        """中文/Unicode 字符保持原样(ensure_ascii=False)"""
        target = tmp_path / "cn.json"
        payload = {"基金名称": "招商中证白酒", "代码": "161725", "涨跌幅": 1.23}
        atomic_write_json(target, payload)
        # 直接读取原始字节,确保中文字符未被转义为 \uXXXX
        raw = target.read_text(encoding="utf-8")
        assert "招商中证白酒" in raw
        assert "161725" in raw
        # safe_read 应返回相同结构
        assert safe_read_json(target) == payload

    def test_write_creates_bak_on_overwrite(self, tmp_path):
        """二次写入时,旧主文件被备份到 .bak"""
        target = tmp_path / "bak.json"
        # 第一次写入
        atomic_write_json(target, {"version": 1})
        # 第二次写入(覆盖)
        atomic_write_json(target, {"version": 2})
        # 主文件应为新版本
        assert safe_read_json(target) == {"version": 2}
        # .bak 应保留旧版本
        bak_path = target.with_suffix(target.suffix + ".bak")
        assert bak_path.exists()
        with open(bak_path, "r", encoding="utf-8") as f:
            assert json.load(f) == {"version": 1}

    def test_write_does_not_leave_tmp_file(self, tmp_path):
        """写入完成后 .tmp 临时文件应被清理(os.replace 移走)"""
        target = tmp_path / "notmp.json"
        atomic_write_json(target, {"x": 1})
        tmp_path_file = target.with_suffix(target.suffix + ".tmp")
        assert not tmp_path_file.exists()

    def test_write_supports_path_and_str(self, tmp_path):
        """支持 Path 与 str 两种路径参数"""
        target_path = tmp_path / "p.json"
        atomic_write_json(target_path, {"k": "path"})
        assert safe_read_json(target_path) == {"k": "path"}

        target_str = str(tmp_path / "s.json")
        atomic_write_json(target_str, {"k": "str"})
        assert safe_read_json(target_str) == {"k": "str"}

    def test_concurrent_writes_no_corruption(self, tmp_path):
        """并发写入同一路径不会产生损坏的 JSON

        场景:多线程同时调用 atomic_write_json 写同一文件,
        借助 _file_locks 串行化,最终文件应为合法 JSON。
        """
        target = tmp_path / "concurrent.json"
        # 每个线程写入不同的 payload(均合法 JSON)
        payloads = [{"thread_id": i, "data": list(range(i, i + 10))} for i in range(20)]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(atomic_write_json, str(target), p) for p in payloads]
            for fut in futures:
                fut.result()  # 等待全部完成,异常会抛出

        # 最终文件必须可被 json.load 解析(未损坏)
        with open(target, "r", encoding="utf-8") as f:
            final = json.load(f)
        # 内容应为 payloads 中的某一个(具体哪个取决于线程调度)
        assert final in payloads

    def test_concurrent_writes_different_files_no_blocking(self, tmp_path):
        """并发写入不同文件互不阻塞(每个文件独立锁)"""
        targets = [tmp_path / f"f{i}.json" for i in range(5)]

        def write_one(path: Path, idx: int) -> None:
            atomic_write_json(path, {"idx": idx})

        threads = [threading.Thread(target=write_one, args=(t, i))
                   for i, t in enumerate(targets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 每个文件应独立写入成功
        for i, t in enumerate(targets):
            assert safe_read_json(t) == {"idx": i}


class TestSafeReadJson:
    """safe_read_json 安全读取"""

    def test_read_normal_file(self, tmp_path):
        """正常文件返回解析后的数据"""
        target = tmp_path / "ok.json"
        atomic_write_json(target, {"foo": "bar", "n": 42})
        assert safe_read_json(target) == {"foo": "bar", "n": 42}

    def test_missing_file_returns_default(self, tmp_path):
        """文件不存在时返回 default(默认 None)"""
        missing = tmp_path / "no_exist.json"
        # 默认 default=None
        assert safe_read_json(missing) is None
        # 自定义 default
        assert safe_read_json(missing, default=[]) == []
        assert safe_read_json(missing, default={}) == {}

    def test_corrupted_main_falls_back_to_bak(self, tmp_path):
        """主文件损坏时从 .bak 备份恢复"""
        target = tmp_path / "corrupt.json"
        # 先写入合法内容(产生 .bak 为空,因为首次无主文件)
        atomic_write_json(target, {"version": "v1"})
        # 再次写入合法内容(此时 .bak 备份了 v1)
        atomic_write_json(target, {"version": "v2"})
        # 主文件当前为 v2,.bak 为 v1
        assert safe_read_json(target) == {"version": "v2"}
        # 损坏主文件(写入非法 JSON)
        target.write_text("{ this is not valid json ]", encoding="utf-8")
        # safe_read 应从 .bak 恢复,返回 v1
        recovered = safe_read_json(target)
        assert recovered == {"version": "v1"}

    def test_corrupted_main_restores_bak_to_main(self, tmp_path):
        """主文件损坏且 .bak 可用时,自动用备份覆盖主文件(自愈)"""
        target = tmp_path / "selfheal.json"
        atomic_write_json(target, {"good": True})
        atomic_write_json(target, {"good": False})  # .bak = {"good": True}
        # 损坏主文件
        target.write_text("<<<corrupted>>>", encoding="utf-8")
        # 第一次读取触发自愈(从 .bak 恢复)
        result = safe_read_json(target)
        assert result == {"good": True}
        # 第二次读取:主文件已被恢复为 .bak 内容,可直接读取
        # (.bak 已被 os.replace 移走,主文件已是合法 JSON)
        result2 = safe_read_json(target)
        assert result2 == {"good": True}

    def test_both_corrupted_returns_default(self, tmp_path):
        """主文件与 .bak 均损坏时返回 default"""
        target = tmp_path / "both_bad.json"
        bak_path = target.with_suffix(target.suffix + ".bak")
        # 手动构造两个损坏文件
        target.write_text("not json {{{", encoding="utf-8")
        bak_path.write_text("also not json }}}", encoding="utf-8")
        # 默认 default=None
        assert safe_read_json(target) is None
        # 自定义 default
        assert safe_read_json(target, default={"fallback": True}) == {"fallback": True}

    def test_bak_missing_returns_default(self, tmp_path):
        """主文件损坏且无 .bak 备份时返回 default"""
        target = tmp_path / "nobak.json"
        # 仅构造损坏主文件,无 .bak
        target.write_text("garbage content", encoding="utf-8")
        assert safe_read_json(target) is None
        assert safe_read_json(target, default=0) == 0

    def test_read_uses_lock_per_path(self, tmp_path):
        """读取也走 _get_lock,与写入共用同一把锁(保证读写一致)"""
        target = tmp_path / "lock.json"
        atomic_write_json(target, {"counter": 0})

        # 并发:1 个写线程 + 多个读线程,均应成功无异常
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    safe_read_json(target)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def writer() -> None:
            try:
                for i in range(50):
                    atomic_write_json(target, {"counter": i})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发读写出现异常: {errors}"
        # 最终文件仍应为合法 JSON
        assert safe_read_json(target) is not None


class TestEdgeCases:
    """边界场景"""

    def test_write_empty_dict(self, tmp_path):
        """空 dict 可正常写入读取"""
        target = tmp_path / "empty.json"
        atomic_write_json(target, {})
        assert safe_read_json(target) == {}

    def test_write_empty_list(self, tmp_path):
        """空 list 可正常写入读取"""
        target = tmp_path / "empty_list.json"
        atomic_write_json(target, [])
        assert safe_read_json(target) == []

    def test_write_nested_structure(self, tmp_path):
        """嵌套结构(list + dict 混合)保持正确"""
        target = tmp_path / "nested.json"
        payload = {
            "positions": [
                {"code": "110022", "name": "易方达", "weights": [0.3, 0.5, 0.2]},
                {"code": "161725", "name": "招商", "weights": [0.4, 0.6]},
            ],
            "meta": {"updated_at": "2026-07-31", "count": 2},
        }
        atomic_write_json(target, payload)
        assert safe_read_json(target) == payload

    def test_write_indent_param(self, tmp_path):
        """indent 参数控制缩进(默认 2)"""
        target = tmp_path / "indent.json"
        atomic_write_json(target, {"a": 1}, indent=4)
        raw = target.read_text(encoding="utf-8")
        # indent=4 应产生 4 空格缩进
        assert "    " in raw  # 4 个空格

    def test_overwrite_many_times_stable(self, tmp_path):
        """连续覆盖写入 100 次,每次均应可读且无残留 .tmp"""
        target = tmp_path / "loop.json"
        for i in range(100):
            atomic_write_json(target, {"iter": i})
            assert safe_read_json(target) == {"iter": i}
        # 最终无 .tmp 残留
        assert not target.with_suffix(target.suffix + ".tmp").exists()

"""统一响应封装单元测试

验证 src/core/response.py 的 APIResponse / success / error:
- APIResponse 默认值与自定义构造
- success() 返回字典结构(code=0)
- error() 返回字典结构(默认 code=-1, 自定义 code)
- data 字段支持任意类型(列表/字典/None)
- Pydantic 模型序列化与 Config.arbitrary_types_allowed
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel

from src.core.response import APIResponse, error, success


class TestAPIResponseDefaults:
    """APIResponse 默认值"""

    def test_default_code_is_zero(self):
        resp = APIResponse()
        assert resp.code == 0

    def test_default_message_is_success(self):
        resp = APIResponse()
        assert resp.message == "success"

    def test_default_data_is_none(self):
        resp = APIResponse()
        assert resp.data is None

    def test_is_pydantic_basemodel(self):
        assert isinstance(APIResponse(), BaseModel)

    def test_arbitrary_types_allowed_config(self):
        """Config.arbitrary_types_allowed 应为 True(允许任意类型 data)"""
        assert APIResponse.Config.arbitrary_types_allowed is True


class TestAPIResponseConstruction:
    """APIResponse 自定义构造"""

    def test_custom_code_message_data(self):
        resp = APIResponse(code=200, message="ok", data={"k": 1})
        assert resp.code == 200
        assert resp.message == "ok"
        assert resp.data == {"k": 1}

    def test_data_accepts_list(self):
        resp = APIResponse(data=[1, 2, 3])
        assert resp.data == [1, 2, 3]

    def test_data_accepts_nested_dict(self):
        nested = {"a": {"b": [1, 2]}}
        resp = APIResponse(data=nested)
        assert resp.data == nested

    def test_data_accepts_arbitrary_object(self):
        """data 类型为 Any,应接受任意对象(配 arbitrary_types_allowed)"""
        obj = datetime(2026, 7, 29)
        resp = APIResponse(data=obj)
        assert resp.data == obj

    def test_negative_code_allowed(self):
        resp = APIResponse(code=-99, message="err")
        assert resp.code == -99

    def test_model_dump_returns_dict(self):
        resp = APIResponse(code=1, message="m", data="v")
        dumped = resp.model_dump()
        assert dumped == {"code": 1, "message": "m", "data": "v"}


class TestSuccessFunction:
    """success() 工厂函数"""

    def test_returns_dict_with_code_zero(self):
        result = success()
        assert isinstance(result, dict)
        assert result["code"] == 0

    def test_default_message_success(self):
        assert success()["message"] == "success"

    def test_default_data_none(self):
        assert success()["data"] is None

    def test_with_data_and_message(self):
        result = success(data=[1, 2], message="created")
        assert result == {"code": 0, "message": "created", "data": [1, 2]}

    def test_keys_complete(self):
        """返回字典应包含 code/message/data 三个键"""
        result = success(data="x")
        assert set(result.keys()) == {"code", "message", "data"}

    def test_independent_instances(self):
        """两次调用应返回独立字典(不共享引用)"""
        r1 = success(data=[])
        r2 = success(data=[])
        r1["data"].append(1)
        assert r2["data"] == []


class TestErrorFunction:
    """error() 工厂函数"""

    def test_returns_dict(self):
        result = error("失败")
        assert isinstance(result, dict)

    def test_default_code_is_negative_one(self):
        assert error("e")["code"] == -1

    def test_message_passed_through(self):
        assert error("boom")["message"] == "boom"

    def test_default_data_none(self):
        assert error("e")["data"] is None

    def test_custom_code(self):
        result = error("not found", code=404)
        assert result["code"] == 404
        assert result["message"] == "not found"

    def test_with_data_payload(self):
        result = error("bad", code=-2, data={"field": "name"})
        assert result == {"code": -2, "message": "bad", "data": {"field": "name"}}

    def test_message_required_argument(self):
        """message 是必填位置参数,缺省应抛 TypeError"""
        with pytest.raises(TypeError):
            error()  # type: ignore[call-arg]

    def test_keys_complete(self):
        result = error("e", code=-3, data=None)
        assert set(result.keys()) == {"code", "message", "data"}


class TestSuccessVsErrorContract:
    """success 与 error 的契约对比"""

    def test_success_code_zero_error_code_nonzero(self):
        s = success()
        e = error("e")
        assert s["code"] == 0
        assert e["code"] != 0

    def test_same_key_set(self):
        assert set(success().keys()) == set(error("x").keys())

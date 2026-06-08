from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class APIResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None

    class Config:
        arbitrary_types_allowed = True


def success(data: Any = None, message: str = "success") -> dict:
    return {"code": 0, "message": message, "data": data}


def error(message: str, code: int = -1, data: Any = None) -> dict:
    return {"code": code, "message": message, "data": data}

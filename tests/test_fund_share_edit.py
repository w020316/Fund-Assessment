"""tests/test_fund_share_edit.py — A10 份额行内编辑(R7.1)验收。

覆盖: 合法更新(信封结构 + 落盘持久)、非法值拒绝(0/负数/NaN/Inf/非数字)、
未找到持仓 code=-1、GET /positions 的 shares_estimated 标识注入。
测试一律 monkeypatch 到 tmp 受控文件(绝不触碰真实 web/user_fund_positions.json)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from web.api import app  # noqa: E402
from web.routes import fund as fund_routes  # noqa: E402

client = TestClient(app)


@pytest.fixture()
def positions_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """受控持仓文件(替代真实文件)。"""
    p = tmp_path / "user_fund_positions.json"
    p.write_text(json.dumps({
        "positions": [
            {"fund_code": "002891", "fund_name": "近似A", "shares": 1000.0,
             "cost_nav": 1.5, "buy_date": "", "note": ""},
            {"fund_code": "161725", "fund_name": "确定B", "shares": 500.0,
             "cost_nav": 2.0, "buy_date": "", "note": ""},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(fund_routes, "_FUND_POSITIONS_FILE", p)
    return p


def test_update_shares_valid(positions_file: Path) -> None:
    resp = client.put("/api/fund/positions/002891/shares", json={"shares": 12345.67})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "success"
    data = body["data"]
    assert data == {"fund_code": "002891", "shares": 12345.67, "estimated": False}
    # 落盘持久(重读文件断言)
    on_disk = json.loads(positions_file.read_text(encoding="utf-8"))
    row = next(p for p in on_disk["positions"] if p["fund_code"] == "002891")
    assert row["shares"] == 12345.67
    # 其它字段原样保留
    assert row["cost_nav"] == 1.5 and row["fund_name"] == "近似A"
    # 未编辑的行不受影响
    other = next(p for p in on_disk["positions"] if p["fund_code"] == "161725")
    assert other["shares"] == 500.0


def test_update_shares_updates_second_row(positions_file: Path) -> None:
    resp = client.put("/api/fund/positions/161725/shares", json={"shares": 88})
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["shares"] == 88.0


@pytest.mark.parametrize("bad_shares", [0, -1, -0.001])
def test_update_shares_rejects_non_positive(positions_file: Path, bad_shares: float) -> None:
    resp = client.put("/api/fund/positions/002891/shares", json={"shares": bad_shares})
    assert resp.status_code == 422
    # 拒绝后文件未被改动
    on_disk = json.loads(positions_file.read_text(encoding="utf-8"))
    row = next(p for p in on_disk["positions"] if p["fund_code"] == "002891")
    assert row["shares"] == 1000.0


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_update_shares_rejects_non_finite(positions_file: Path, raw: str) -> None:
    """NaN/Inf 一旦落盘, GET /positions 的 JSONResponse 会持续 500, 必须在入口拒绝。"""
    resp = client.put(
        "/api/fund/positions/002891/shares",
        content=json.dumps({"shares": raw}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_update_shares_nonexistent_code(positions_file: Path) -> None:
    resp = client.put("/api/fund/positions/999999/shares", json={"shares": 10.0})
    body = resp.json()
    assert body["code"] == -1
    assert "999999" in body["message"]


def test_update_shares_invalid_code_format(positions_file: Path) -> None:
    resp = client.put("/api/fund/positions/abc123/shares", json={"shares": 10.0})
    assert resp.json()["code"] == -1


def test_positions_response_has_shares_estimated_flag(
    positions_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7.1: GET /positions 为近似持仓注入 shares_estimated 标识(向后兼容只加字段)。"""
    # 行情取数 mock(避免网络)
    monkeypatch.setattr(
        fund_routes, "get_fund_realtime_tencent",
        lambda codes, **kw: [],
    )
    resp = client.get("/api/fund/positions")
    assert resp.status_code == 200
    data = resp.json()
    rows = {p["fund_code"]: p for p in data["positions"]}
    assert rows["002891"]["shares_estimated"] is True  # 默认近似清单含 002891
    assert rows["161725"]["shares_estimated"] is False

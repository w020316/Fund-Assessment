"""持仓分组单元测试(阶段六批次 B,功能 #8)。

覆盖:
- 存储层:默认组回退、白名单校验、roundtrip、损坏文件回退
- 路由:GET/PUT、effective 映射(旧数据自动归默认组)、非法配置整包拒绝、401 鉴权

铁律:groups.json 与持仓文件一律 monkeypatch 到 tmp_path,绝不触碰真实配置与持仓。
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core import portfolio_store as pstore


# ============ 存储层 ============


class TestGroupsStore:
    def test_load_defaults_when_missing(self, tmp_path):
        cfg = pstore.load_groups(tmp_path / "groups.json")
        assert cfg["groups"] == [{"id": "default", "name": "我的持仓"}]
        assert cfg["fund_groups"] == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "groups.json"
        cfg = {
            "schema_version": 1,
            "groups": [{"id": "default", "name": "我的持仓"}, {"id": "tech", "name": "科技"}],
            "fund_groups": {"002891": "tech", "008984": "tech"},
        }
        assert pstore.save_groups(cfg, path) is True
        assert pstore.load_groups(path) == cfg

    def test_corrupt_file_falls_back_to_default(self, tmp_path):
        path = tmp_path / "groups.json"
        path.write_text("{ not-json", encoding="utf-8")
        cfg = pstore.load_groups(path)
        assert cfg["groups"][0]["id"] == "default"
        assert cfg["fund_groups"] == {}

    def test_normalize_rejects_missing_default(self):
        errors, cleaned = pstore.normalize_groups({
            "groups": [{"id": "tech", "name": "科技"}], "fund_groups": {},
        })
        assert errors
        assert cleaned["groups"][0]["id"] == "default"

    def test_normalize_rejects_unknown_group_and_bad_code(self):
        errors, _ = pstore.normalize_groups({
            "groups": [{"id": "default", "name": "我的持仓"}],
            "fund_groups": {"002891": "nope", "abc": "default"},
        })
        assert len(errors) == 2

    def test_normalize_rejects_duplicate_and_bad_id(self):
        errors, _ = pstore.normalize_groups({
            "groups": [
                {"id": "default", "name": "我的持仓"},
                {"id": "default", "name": "重复"},
                {"id": "BAD ID", "name": "空格"},
            ],
            "fund_groups": {},
        })
        assert any("重复" in e for e in errors)
        assert any("非法" in e for e in errors)

    def test_group_for_fund_falls_back(self):
        cfg = pstore.load_groups(None)
        assert pstore.group_for_fund(cfg, "110022") == "default"
        cfg["fund_groups"] = {"110022": "ghost"}
        assert pstore.group_for_fund(cfg, "110022") == "default"

    def test_save_rejects_invalid_config(self, tmp_path):
        path = tmp_path / "groups.json"
        assert pstore.save_groups({"groups": [], "fund_groups": {}}, path) is False
        assert not path.exists()

    def test_save_creates_missing_parent_dir(self, tmp_path):
        """Render 临时磁盘首次部署时 data/portfolio 不存在,保存需自动建目录。"""
        path = tmp_path / "portfolio" / "nested" / "groups.json"
        cfg = {"groups": [{"id": "default", "name": "我的持仓"}], "fund_groups": {}}
        assert pstore.save_groups(cfg, path) is True
        assert path.exists()


# ============ 路由 ============


@pytest.fixture
def client():
    from web.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def no_admin_token():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADMIN_TOKEN", None)
        yield


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr("web.routes.fund._FUND_POSITIONS_FILE", tmp_path / "positions.json")
    monkeypatch.setattr(pstore, "GROUPS_FILE", tmp_path / "groups.json")
    yield


def _write_positions(tmp_path, positions: list[dict]):
    (tmp_path / "positions.json").write_text(
        json.dumps({"positions": positions}, ensure_ascii=False), encoding="utf-8"
    )


_POSITIONS = [
    {"fund_code": "110022", "fund_name": "易方达消费", "shares": 100.0, "cost_nav": 1.0},
    {"fund_code": "002891", "fund_name": "华夏移动互联", "shares": 50.0, "cost_nav": 2.0},
]


class TestGroupsRoute:
    def test_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/portfolio/groups").status_code == 401
        assert client.put("/api/portfolio/groups", json={"groups": []}).status_code == 401

    def test_get_defaults_and_effective(self, client, tmp_path):
        _write_positions(tmp_path, _POSITIONS)
        resp = client.get("/api/portfolio/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["groups"] == [{"id": "default", "name": "我的持仓"}]
        assert data["effective"] == {"110022": "default", "002891": "default"}

    def test_put_persists_and_get_reflects(self, client, tmp_path):
        _write_positions(tmp_path, _POSITIONS)
        payload = {
            "groups": [
                {"id": "default", "name": "我的持仓"},
                {"id": "tech", "name": "科技"},
            ],
            "fund_groups": {"002891": "tech"},
        }
        resp = client.put("/api/portfolio/groups", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["effective"]["002891"] == "tech"
        assert data["effective"]["110022"] == "default"
        # 已落盘
        saved = json.loads((tmp_path / "groups.json").read_text(encoding="utf-8"))
        assert saved["fund_groups"] == {"002891": "tech"}
        # 再读一致
        again = client.get("/api/portfolio/groups").json()
        assert again["groups"] == payload["groups"]

    def test_put_invalid_rejected_without_write(self, client, tmp_path):
        payload = {
            "groups": [{"id": "tech", "name": "科技"}],  # 缺 default
            "fund_groups": {"002891": "tech"},
        }
        resp = client.put("/api/portfolio/groups", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["errors"]
        assert not (tmp_path / "groups.json").exists()

    def test_effective_skips_malformed_codes(self, client, tmp_path):
        _write_positions(tmp_path, _POSITIONS + [{"fund_code": "", "shares": 1.0}])
        data = client.get("/api/portfolio/groups").json()
        assert set(data["effective"]) == {"110022", "002891"}

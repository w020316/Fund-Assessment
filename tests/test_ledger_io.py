"""账本 JSON 全量导入导出单元测试(阶段六批次 A,T-A6)。

覆盖:
- 导出:全量分区聚合、凭据字段置空(双保险 scrub)、schema 版本
- 导入:schema 校验整包拒绝、dry-run diff 预览不落盘、confirm 确认语义、
  执行前自动备份、缺 confirm 拒绝执行
- 401 鉴权用例(export/import 均 require_admin)

铁律:持仓/自选/配置文件路径一律 monkeypatch 到 tmp_path,
绝不触碰 web/user_fund_positions.json。
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core import portfolio_store as pstore


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
    """隔离:全部账本文件路径 → tmp。"""
    monkeypatch.setattr("web.routes.fund._FUND_POSITIONS_FILE", tmp_path / "fund_positions.json")
    monkeypatch.setattr("web.routes.config._USER_POSITIONS_FILE", tmp_path / "stock_positions.json")
    monkeypatch.setattr("web.routes.monitor._WATCHLIST_FILE", str(tmp_path / "watchlist.json"))
    monkeypatch.setattr(pstore, "WEBHOOK_CONFIG_FILE", tmp_path / "webhook_config.json")
    monkeypatch.setattr(pstore, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(pstore, "BACKUP_DIR", tmp_path / "backup")
    yield
    (tmp_path / "backup")


def _write_fund_positions(tmp_path, positions: list[dict]):
    (tmp_path / "fund_positions.json").write_text(
        json.dumps({"positions": positions}, ensure_ascii=False), encoding="utf-8")


def _payload(positions: list[dict], **extra_sections) -> dict:
    sections = {"fund_positions": {"positions": positions}}
    sections.update(extra_sections)
    return {"schema_version": 1, "app": "QuantFlow Pro", "exported_at": "2026-10-26T00:00:00",
            "sections": sections}


_VALID_POSITIONS = [
    {"fund_code": "110022", "fund_name": "易方达消费行业", "shares": 100.0, "cost_nav": 2.0,
     "buy_date": "", "note": ""},
    {"fund_code": "008984", "fund_name": "财通科技创新混合C", "shares": 50.0, "cost_nav": 1.5,
     "buy_date": "", "note": ""},
]


# ============ 导出 ============


class TestLedgerExport:
    def test_export_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/portfolio/export").status_code == 401

    def test_export_full_sections(self, client, tmp_path):
        """导出聚合全部分区 + schema 版本 + 时间戳。"""
        _write_fund_positions(tmp_path, _VALID_POSITIONS)
        (tmp_path / "watchlist.json").write_text(json.dumps({"600519": ["price_surge"]}), encoding="utf-8")
        resp = client.get("/api/portfolio/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["schema_version"] == 1
        assert data["exported_at"]
        s = data["sections"]
        assert len(s["fund_positions"]["positions"]) == 2
        assert s["watchlist"] == {"600519": ["price_surge"]}
        assert "stock_positions" in s
        assert "portfolio_config" in s
        # 批次 B:分组默认值随账本导出(旧数据自动归 default)
        assert s["portfolio_config"]["groups"]["groups"] == [{"id": "default", "name": "我的持仓"}]
        assert s["portfolio_config"]["groups"]["fund_groups"] == {}

    def test_export_includes_custom_groups(self, client, tmp_path):
        """自定义分组与映射随账本导出(批次 B 功能 #8)。"""
        (tmp_path / "groups.json").write_text(json.dumps({
            "schema_version": 1,
            "groups": [{"id": "default", "name": "我的持仓"},
                       {"id": "core", "name": "核心仓"}],
            "fund_groups": {"110022": "core"},
        }, ensure_ascii=False), encoding="utf-8")
        resp = client.get("/api/portfolio/export")
        assert resp.status_code == 200
        groups = resp.json()["sections"]["portfolio_config"]["groups"]
        assert [g["id"] for g in groups["groups"]] == ["default", "core"]
        assert groups["fund_groups"] == {"110022": "core"}

    def test_export_scrubs_credentials(self, client, tmp_path):
        """凭据字段置空:即使配置文件被塞入 sendkey,导出也一律置空(双保险)。"""
        (tmp_path / "webhook_config.json").write_text(json.dumps({
            "schema_version": 1, "channel": "serverchan", "enabled": True,
            "thresholds": {"est_pct_abs": 2.0},
            "sendkey": "SCT_EVIL", "webhook_url": "https://evil.example/hook",
        }), encoding="utf-8")
        resp = client.get("/api/portfolio/export")
        text = json.dumps(resp.json())
        assert "SCT_EVIL" not in text
        assert "evil.example" not in text
        assert resp.json()["sections"]["portfolio_config"]["webhook"]["enabled"] is True


# ============ 导入:校验与确认语义 ============


class TestLedgerImport:
    def test_import_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.post("/api/portfolio/import", json={"payload": {}}).status_code == 401

    def test_import_invalid_schema_rejected(self, client, tmp_path):
        """schema_version 错误 / 结构缺失 → 整包拒绝,不落盘。"""
        for bad in [
            {"schema_version": 99, "sections": {}},
            {"schema_version": 1, "sections": {}},  # 缺 fund_positions
            {"schema_version": 1, "sections": {"fund_positions": {"positions": [
                {"fund_code": "abc", "shares": 1, "cost_nav": 1}]}}},
            {"schema_version": 1, "sections": {"fund_positions": {"positions": [
                {"fund_code": "110022", "shares": -5, "cost_nav": 1}]}}},
            {"schema_version": 1, "sections": {"fund_positions": {"positions": [
                {"fund_code": "110022", "shares": 1, "cost_nav": 1},
                {"fund_code": "110022", "shares": 2, "cost_nav": 1}]}}},  # 重复
        ]:
            resp = client.post("/api/portfolio/import", json={"payload": bad, "dry_run": True})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False, f"应拒绝: {bad}"
            assert data["errors"]
        # 拒绝后未产生任何写入
        assert not (tmp_path / "fund_positions.json").exists()

    def test_import_deep_validates_stock_positions(self, client, tmp_path):
        """stock_positions 元素必须是对象、available_cash 合法 → 否则整包拒绝。"""
        for bad in (
            {"positions": ["not-a-dict"]},
            {"positions": [], "available_cash": -1},
        ):
            resp = client.post("/api/portfolio/import", json={
                "payload": _payload(_VALID_POSITIONS, stock_positions=bad), "dry_run": True})
            data = resp.json()
            assert data["success"] is False
            assert any("stock_positions" in e for e in data["errors"]), bad
        assert not (tmp_path / "stock_positions.json").exists()

    def test_import_deep_validates_watchlist(self, client, tmp_path):
        """watchlist 需为 {6位代码: [规则字符串]} → 否则整包拒绝。"""
        for bad in (
            {"60051": ["price_surge"]},           # 非 6 位代码
            {"600519": "price_surge"},            # 规则不是数组
            {"600519": [1, 2]},                   # 规则非字符串
            {"600519": ["r"] * 21},               # 规则数超限
        ):
            resp = client.post("/api/portfolio/import", json={
                "payload": _payload(_VALID_POSITIONS, watchlist=bad), "dry_run": True})
            data = resp.json()
            assert data["success"] is False
            assert any("watchlist" in e for e in data["errors"]), bad

    def test_import_item_count_cap(self, client, tmp_path):
        """持仓条数 > 500 → 整包拒绝(不写入)。"""
        payload = _payload([_VALID_POSITIONS[0]] * 501)
        resp = client.post("/api/portfolio/import", json={"payload": payload, "dry_run": True})
        data = resp.json()
        assert data["success"] is False
        assert "超限" in data["errors"][0]

    def test_import_payload_size_cap(self, client, tmp_path):
        """请求体序列化后 > 2MB → 整包拒绝。"""
        payload = _payload(_VALID_POSITIONS)
        payload["sections"]["junk"] = "x" * (2 * 1024 * 1024)
        resp = client.post("/api/portfolio/import", json={"payload": payload, "dry_run": True})
        data = resp.json()
        assert data["success"] is False
        assert "过大" in data["errors"][0]

    def test_import_dry_run_no_write(self, client, tmp_path):
        """dry-run:校验 + diff 预览,不落盘。"""
        _write_fund_positions(tmp_path, [_VALID_POSITIONS[0]])  # 现网只有 110022
        payload = _payload(_VALID_POSITIONS)  # 导入含 008984 → added 1
        resp = client.post("/api/portfolio/import", json={"payload": payload, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["dry_run"] is True
        diff = data["diff"]["fund_positions"]
        assert len(diff["added"]) == 1
        assert diff["added"][0]["fund_code"] == "008984"
        assert len(diff["deleted"]) == 0
        # 未落盘:现网文件内容未变
        cur = json.loads((tmp_path / "fund_positions.json").read_text(encoding="utf-8"))
        assert len(cur["positions"]) == 1

    def test_import_without_confirm_rejected(self, client, tmp_path):
        """dry_run=false 但缺 confirm → 拒绝执行(破坏性操作确认范式)。"""
        payload = _payload(_VALID_POSITIONS)
        resp = client.post("/api/portfolio/import", json={"payload": payload, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data.get("requires_confirm") is True
        assert not (tmp_path / "fund_positions.json").exists()

    def test_import_confirm_restores(self, client, tmp_path):
        """confirm 执行:覆盖写入 + 自动备份旧文件到 backup 目录。"""
        _write_fund_positions(tmp_path, [{"fund_code": "000001", "fund_name": "旧持仓", "shares": 1.0, "cost_nav": 1.0}])
        (tmp_path / "watchlist.json").write_text(json.dumps({"600519": ["a"]}), encoding="utf-8")
        payload = _payload(
            _VALID_POSITIONS,
            watchlist={"300308": ["price_surge"]},
            portfolio_config={"webhook": {"channel": "serverchan", "enabled": True,
                                          "thresholds": {"est_pct_abs": 1.5}}},
        )
        resp = client.post("/api/portfolio/import",
                           json={"payload": payload, "dry_run": False, "confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["applied"]["fund_positions"] is True
        assert data["applied"]["watchlist"] is True
        # 持仓已恢复
        cur = json.loads((tmp_path / "fund_positions.json").read_text(encoding="utf-8"))
        assert [p["fund_code"] for p in cur["positions"]] == ["110022", "008984"]
        # watchlist 已恢复
        assert json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8")) == {"300308": ["price_surge"]}
        # webhook 配置已恢复
        cfg = pstore.load_webhook_config(tmp_path / "webhook_config.json")
        assert cfg["enabled"] is True
        assert cfg["thresholds"]["est_pct_abs"] == 1.5
        # 自动备份目录存在,含旧持仓文件
        assert data["backup_dir"]
        import pathlib
        backup_dir = pathlib.Path(data["backup_dir"])
        assert backup_dir.exists()
        assert (backup_dir / "fund_positions.json").exists()

    def test_import_credential_fields_stripped(self, client, tmp_path):
        """导入 payload 携带凭据字段 → 白名单清洗,sendkey 不落盘。"""
        payload = _payload(_VALID_POSITIONS)
        payload["sections"]["portfolio_config"] = {
            "webhook": {"channel": "serverchan", "enabled": True,
                        "thresholds": {"est_pct_abs": 2.0},
                        "sendkey": "SCT_TRY_INJECT"}}
        resp = client.post("/api/portfolio/import",
                           json={"payload": payload, "dry_run": False, "confirm": True})
        assert resp.status_code == 200
        raw = json.loads((tmp_path / "webhook_config.json").read_text(encoding="utf-8"))
        assert "sendkey" not in raw  # 白名单落盘,凭据字段不可能出现

    def test_import_backup_failure_cancels_without_writes(self, client, tmp_path, monkeypatch):
        """备份失败 → 整单取消,绝不无备份执行(原实现直接 500)。"""
        _write_fund_positions(tmp_path, [{"fund_code": "000001", "fund_name": "旧持仓",
                                          "shares": 1.0, "cost_nav": 1.0}])
        monkeypatch.setattr(pstore, "backup_files",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
        resp = client.post("/api/portfolio/import",
                           json={"payload": _payload(_VALID_POSITIONS), "dry_run": False, "confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "备份失败" in data["errors"][0]
        # 未做任何写入:旧内容原样保留
        cur = json.loads((tmp_path / "fund_positions.json").read_text(encoding="utf-8"))
        assert [p["fund_code"] for p in cur["positions"]] == ["000001"]

    def test_import_midway_failure_rolls_back(self, client, tmp_path, monkeypatch):
        """中途写入失败 → 已写文件全部回滚到导入前内容(不再半套数据)。"""
        _write_fund_positions(tmp_path, [{"fund_code": "000001", "fund_name": "旧持仓",
                                          "shares": 1.0, "cost_nav": 1.0}])
        (tmp_path / "watchlist.json").write_text(json.dumps({"600519": ["price_surge"]}),
                                                 encoding="utf-8")
        payload = _payload(_VALID_POSITIONS, watchlist={"300308": ["price_surge"]})

        from src.utils import file_io
        real_write = file_io.atomic_write_json

        def _boom(path, data, **kwargs):
            if str(path).endswith("watchlist.json"):
                raise OSError("disk full")
            return real_write(path, data, **kwargs)

        monkeypatch.setattr("web.routes.portfolio.atomic_write_json", _boom)
        resp = client.post("/api/portfolio/import",
                           json={"payload": payload, "dry_run": False, "confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["rolled_back"] is True
        # 持仓已回滚为旧内容,watchlist 未被破坏
        cur = json.loads((tmp_path / "fund_positions.json").read_text(encoding="utf-8"))
        assert [p["fund_code"] for p in cur["positions"]] == ["000001"]
        wl = json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8"))
        assert wl == {"600519": ["price_surge"]}


# ============ 分组配置随账本往返(批次 B) ============


class TestLedgerGroups:
    def test_import_confirm_restores_groups(self, client, tmp_path):
        """confirm 导入恢复分组配置,并备份旧 groups.json。"""
        (tmp_path / "groups.json").write_text(json.dumps({
            "schema_version": 1,
            "groups": [{"id": "default", "name": "旧默认"}],
            "fund_groups": {"000001": "default"},
        }, ensure_ascii=False), encoding="utf-8")
        payload = _payload(
            _VALID_POSITIONS,
            portfolio_config={"groups": {
                "schema_version": 1,
                "groups": [{"id": "default", "name": "我的持仓"},
                           {"id": "core", "name": "核心仓"}],
                "fund_groups": {"110022": "core", "008984": "default"},
            }},
        )
        resp = client.post("/api/portfolio/import",
                           json={"payload": payload, "dry_run": False, "confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["applied"]["groups"] is True
        saved = json.loads((tmp_path / "groups.json").read_text(encoding="utf-8"))
        assert [g["id"] for g in saved["groups"]] == ["default", "core"]
        assert saved["fund_groups"] == {"110022": "core", "008984": "default"}
        # 旧分组文件已进入自动备份
        from pathlib import Path
        backup_dir = Path(data["backup_dir"])
        assert (backup_dir / "groups.json").exists()

    def test_import_invalid_groups_rejected(self, client, tmp_path):
        """分组配置非法(缺 default / 未知分组映射)→ 整包拒绝,不落盘。"""
        for bad_groups in [
            {"groups": [{"id": "core", "name": "核心仓"}], "fund_groups": {}},
            {"groups": [{"id": "default", "name": "我的持仓"}],
             "fund_groups": {"110022": "ghost"}},
            {"groups": [{"id": "BadId", "name": "非法 id"}], "fund_groups": {}},
            {"groups": [], "fund_groups": {}},
        ]:
            payload = _payload(_VALID_POSITIONS,
                               portfolio_config={"groups": bad_groups})
            resp = client.post("/api/portfolio/import",
                               json={"payload": payload, "dry_run": True})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False, f"应拒绝: {bad_groups}"
            assert any("groups" in e for e in data["errors"])
        assert not (tmp_path / "groups.json").exists()
        assert not (tmp_path / "fund_positions.json").exists()

    def test_import_dry_run_previews_group_counts(self, client, tmp_path):
        """dry-run diff 展示分组数量对比,不落盘。"""
        payload = _payload(
            _VALID_POSITIONS,
            portfolio_config={"groups": {
                "groups": [{"id": "default", "name": "我的持仓"},
                           {"id": "core", "name": "核心仓"}],
                "fund_groups": {"110022": "core"},
            }},
        )
        resp = client.post("/api/portfolio/import", json={"payload": payload, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        diff = data["diff"]["portfolio_config"]["groups"]
        assert diff["current_groups"] == 1
        assert diff["incoming_groups"] == 2
        assert diff["incoming_mappings"] == 1
        assert not (tmp_path / "groups.json").exists()

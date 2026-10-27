"""Webhook 推送单元测试(阶段六批次 A,T-A5)。

覆盖:
- Server酱适配器:env 凭据、HTTP 打桩、日志脱敏、未配置降级、绝不抛异常
- 通用 webhook 渠道(预留位)
- scan 端点:X-Api-Key 鉴权(401/500 用例)、阈值触发、2h 文件级节流幂等、
  推送失败不落节流时间戳、config enabled=false 短路
- webhook config GET/PUT:白名单字段、密钥不回显

铁律:
- 节流文件/配置文件一律 monkeypatch 到 tmp_path
- 网络请求 100% mock(httpx.post 打桩),持仓文件指向 tmp
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.core import portfolio_store as pstore
from src.core import webhook_push as wpush


# ============ 适配器层 ============


class TestServerChanChannel:
    def test_not_configured_without_env(self, monkeypatch):
        monkeypatch.delenv("PUSH_SERVERCHAN_SENDKEY", raising=False)
        monkeypatch.delenv("WEBHOOK_SERVERCHAN_SENDKEY", raising=False)
        ch = wpush.ServerChanChannel()
        assert ch.is_configured() is False
        r = ch.send("t", "c")
        assert r.success is False
        assert r.error == "not_configured"

    def test_send_success(self, monkeypatch):
        monkeypatch.setenv("PUSH_SERVERCHAN_SENDKEY", "SCT123456789")
        captured = {}

        class _Resp:
            status_code = 200
            text = '{"code":0}'

        def _fake_post(url, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

        with patch("src.core.webhook_push.httpx.post", side_effect=_fake_post):
            r = wpush.ServerChanChannel().send("标题", "正文")
        assert r.success is True
        assert r.status_code == 200
        # URL 与 form 字段符合 Server酱协议
        assert captured["url"] == "https://sctapi.ftqq.com/SCT123456789.send"
        assert captured["data"] == {"title": "标题", "desp": "正文"}

    def test_send_upstream_error_no_raise(self, monkeypatch):
        """上游 4xx → success=False,绝不抛异常。"""
        monkeypatch.setenv("PUSH_SERVERCHAN_SENDKEY", "SCT123456789")

        class _Resp:
            status_code = 400
            text = "bad request"

        with patch("src.core.webhook_push.httpx.post", return_value=_Resp()):
            r = wpush.ServerChanChannel().send("t", "c")
        assert r.success is False
        assert "400" in r.error

    def test_send_network_exception_no_raise(self, monkeypatch):
        """网络异常 → success=False,绝不抛异常。"""
        monkeypatch.setenv("PUSH_SERVERCHAN_SENDKEY", "SCT123456789")
        with patch("src.core.webhook_push.httpx.post", side_effect=RuntimeError("dns fail")):
            r = wpush.ServerChanChannel().send("t", "c")
        assert r.success is False
        assert "dns fail" in r.error

    def test_mask_secret(self):
        assert wpush.mask_secret("SCT123456789") == "SCT1****"
        assert wpush.mask_secret("") == "****"
        assert wpush.mask_secret(None) == "****"


class TestChannelSelection:
    def test_default_serverchan(self, monkeypatch):
        monkeypatch.delenv("PUSH_CHANNEL", raising=False)
        assert wpush.get_channel().name == "serverchan"

    def test_env_channel_generic(self, monkeypatch):
        monkeypatch.setenv("PUSH_CHANNEL", "generic")
        assert wpush.get_channel().name == "generic"

    def test_unknown_channel_raises(self):
        with pytest.raises(ValueError):
            wpush.get_channel("dingtalk")


# ============ 存储层:配置与节流 ============


class TestPortfolioStore:
    def test_config_defaults(self, tmp_path, monkeypatch):
        f = tmp_path / "webhook_config.json"
        cfg = pstore.load_webhook_config(f)
        assert cfg["channel"] == "serverchan"
        assert cfg["enabled"] is False
        assert cfg["thresholds"]["est_pct_abs"] == 2.0

    def test_config_roundtrip_and_whitelist(self, tmp_path):
        f = tmp_path / "webhook_config.json"
        ok = pstore.save_webhook_config(
            {"channel": "generic", "enabled": True,
             "thresholds": {"est_pct_abs": 1.5, "evil_key": "hack"}},
            path=f,
        )
        assert ok is True
        raw = json.loads(f.read_text(encoding="utf-8"))
        assert "evil_key" not in raw["thresholds"]  # 白名单外字段拒绝落盘
        cfg = pstore.load_webhook_config(f)
        assert cfg["channel"] == "generic"
        assert cfg["enabled"] is True
        assert cfg["thresholds"]["est_pct_abs"] == 1.5

    def test_throttle_window(self, tmp_path, monkeypatch):
        """2h 节流:窗口内 is_throttled=True,窗口外 False;mark 后生效。"""
        f = tmp_path / "throttle.json"
        t0 = 1_000_000.0
        assert pstore.is_throttled("k", 7200, now=t0, path=f) is False
        pstore.mark_pushed("k", now=t0, path=f)
        assert pstore.is_throttled("k", 7200, now=t0 + 7199, path=f) is True
        assert pstore.is_throttled("k", 7200, now=t0 + 7201, path=f) is False

    def test_throttle_persisted_to_file(self, tmp_path):
        """节流时间戳落盘(文件级,重启不丢)。"""
        f = tmp_path / "throttle.json"
        pstore.mark_pushed("est_110022", now=123.0, path=f)
        raw = json.loads(f.read_text(encoding="utf-8"))
        assert raw["est_110022"] == 123.0

    def test_saves_create_missing_parent_dir(self, tmp_path):
        """Render 临时磁盘首次部署 data/portfolio 不存在:配置与节流写入需自动建目录。"""
        cfg_file = tmp_path / "portfolio" / "webhook_config.json"
        thr_file = tmp_path / "portfolio" / "webhook_throttle.json"
        assert pstore.save_webhook_config({"enabled": True}, path=cfg_file) is True
        assert pstore.mark_pushed("k", now=123.0, path=thr_file) is True
        assert cfg_file.exists()
        assert thr_file.exists()


# ============ 路由层 ============


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
    """隔离:持仓/配置/节流文件 → tmp;缓存隔离。"""
    monkeypatch.setattr("web.routes.fund._FUND_POSITIONS_FILE", tmp_path / "positions.json")
    monkeypatch.setattr(pstore, "WEBHOOK_CONFIG_FILE", tmp_path / "webhook_config.json")
    monkeypatch.setattr(pstore, "THROTTLE_FILE", tmp_path / "throttle.json")
    monkeypatch.setattr("web.routes.portfolio._last_known", {})
    from src.core.cache import DataCache
    monkeypatch.setattr(
        "web.routes.portfolio._portfolio_cache",
        DataCache(cache_dir=str(tmp_path / "cache"), default_ttl=60),
    )
    monkeypatch.delenv("PUSH_SERVERCHAN_SENDKEY", raising=False)
    yield


_POSITIONS = (
    '{"positions":[{"fund_code":"110022","fund_name":"消费","shares":100.0,"cost_nav":2.0}]}'
)
_QUOTES = [{
    "code": "110022", "name": "易方达消费行业",
    "nav": 2.500, "estimated_nav": 2.575, "change": 0.075,
    "change_pct": 3.0, "update_time": "2026-10-26 14:30",
}]


def _enable_push(monkeypatch, tmp_path, sendkey="SCT123456789"):
    monkeypatch.setenv("PUSH_SERVERCHAN_SENDKEY", sendkey)
    pstore.save_webhook_config(
        {"channel": "serverchan", "enabled": True, "thresholds": {"est_pct_abs": 2.0}},
        path=tmp_path / "webhook_config.json",
    )


class TestWebhookConfigRoute:
    def test_config_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/portfolio/webhook/config").status_code == 401
        assert client.put("/api/portfolio/webhook/config", json={}).status_code == 401

    def test_get_config_no_credentials_leaked(self, client, tmp_path):
        """GET 不回显任何凭据字段,仅返回 sendkey_configured 布尔值。"""
        resp = client.get("/api/portfolio/webhook/config")
        assert resp.status_code == 200
        data = resp.json()["config"]
        text = json.dumps(data)
        assert "sendkey" not in text.lower() or "sendkey_configured" in text
        assert data["sendkey_configured"] is False
        assert data["enabled"] is False

    def test_put_config_roundtrip(self, client, tmp_path):
        resp = client.put("/api/portfolio/webhook/config", json={
            "channel": "serverchan", "enabled": True, "thresholds": {"est_pct_abs": 1.0},
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        cfg = pstore.load_webhook_config(tmp_path / "webhook_config.json")
        assert cfg["enabled"] is True
        assert cfg["thresholds"]["est_pct_abs"] == 1.0

    def test_put_config_invalid_channel(self, client):
        resp = client.put("/api/portfolio/webhook/config", json={"channel": "wecom"})
        assert resp.status_code == 200
        assert resp.json()["success"] is False


class TestWebhookTestRoute:
    def test_test_requires_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "k")
        monkeypatch.setenv("APP_ENV", "production")
        assert client.post("/api/portfolio/webhook/test").status_code == 401

    def test_test_push_success(self, client, tmp_path, monkeypatch):
        _enable_push(monkeypatch, tmp_path)
        class _Resp:
            status_code = 200
        with patch("src.core.webhook_push.httpx.post", return_value=_Resp()) as fake:
            resp = client.post("/api/portfolio/webhook/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["channel"] == "serverchan"

    def test_test_push_not_configured(self, client, tmp_path, monkeypatch):
        """渠道未配置 → success=False(不抛异常、不 500)。"""
        pstore.save_webhook_config(
            {"channel": "serverchan", "enabled": True, "thresholds": {}},
            path=tmp_path / "webhook_config.json",
        )
        resp = client.post("/api/portfolio/webhook/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert resp.json()["error"] == "not_configured"


class TestWebhookScan:
    def test_scan_missing_key_401(self, client, monkeypatch):
        """铁律:401 用例——配置了 key 但请求头缺失。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        resp = client.post("/api/portfolio/webhook/scan")
        assert resp.status_code == 401

    def test_scan_wrong_key_401(self, client, monkeypatch):
        """铁律:401 用例——key 不匹配。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        resp = client.post("/api/portfolio/webhook/scan", headers={"X-Api-Key": "wrong"})
        assert resp.status_code == 401

    def test_scan_key_unconfigured_500(self, client, monkeypatch):
        """env 未配置 → 500 fail-closed(不裸奔)。"""
        monkeypatch.delenv("PORTFOLIO_SCAN_KEY", raising=False)
        resp = client.post("/api/portfolio/webhook/scan", headers={"X-Api-Key": "x"})
        assert resp.status_code == 500

    def test_scan_correct_key_passes_auth(self, client, tmp_path, monkeypatch):
        """正确 key 通过鉴权(推送未启用短路,证明鉴权层已放行)。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        resp = client.post("/api/portfolio/webhook/scan",
                           headers={"X-Api-Key": "scan-secret"}, json={})
        assert resp.status_code == 200
        assert resp.json()["reason"] == "disabled"

    def test_scan_disabled_short_circuit(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        resp = client.post("/api/portfolio/webhook/scan",
                           headers={"X-Api-Key": "scan-secret"}, json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["pushed"] is False
        assert data["reason"] == "disabled"

    def test_scan_triggers_push_with_content(self, client, tmp_path, monkeypatch):
        """触发推送:内容含触发时间/基金/阈值/估值/置信度;成功后落节流时间戳。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        _enable_push(monkeypatch, tmp_path)
        captured = {}

        class _Resp:
            status_code = 200

        def _fake_post(url, data=None, timeout=None):
            captured["data"] = data
            return _Resp()

        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=_QUOTES), \
             patch("web.routes.fund._today", return_value=_dt.date(2026, 10, 26)), \
             patch("src.core.webhook_push.httpx.post", side_effect=_fake_post):
            resp = client.post("/api/portfolio/webhook/scan",
                               headers={"X-Api-Key": "scan-secret"}, json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["pushed"] is True
        assert data["triggered"][0]["fund_code"] == "110022"
        assert data["triggered"][0]["est_pct"] == 3.0
        assert data["triggered"][0]["confidence"] == "high"
        desp = captured["data"]["desp"]
        assert "触发时间" in desp and "阈值" in desp
        assert "110022" in desp and "置信度" in desp
        # 节流时间戳已落盘
        assert pstore.is_throttled("est_110022", path=tmp_path / "throttle.json") is True

    def test_scan_idempotent_throttled(self, client, tmp_path, monkeypatch):
        """幂等:2h 内重复 scan → 节流跳过,不重复推送。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        _enable_push(monkeypatch, tmp_path)

        class _Resp:
            status_code = 200

        calls = {"n": 0}

        def _fake_post(url, data=None, timeout=None):
            calls["n"] += 1
            return _Resp()

        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=_QUOTES), \
             patch("src.core.webhook_push.httpx.post", side_effect=_fake_post):
            r1 = client.post("/api/portfolio/webhook/scan", headers={"X-Api-Key": "scan-secret"}, json={})
            r2 = client.post("/api/portfolio/webhook/scan", headers={"X-Api-Key": "scan-secret"}, json={})
        assert r1.json()["pushed"] is True
        d2 = r2.json()
        assert d2["pushed"] is False
        assert d2["triggered"] == []
        assert all(s["reason"] == "throttled" for s in d2["skipped"])
        assert calls["n"] == 1  # 只推送一次

    def test_scan_push_failure_no_throttle(self, client, tmp_path, monkeypatch):
        """推送失败 → 不落节流时间戳,下次 scan 可重试。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        _enable_push(monkeypatch, tmp_path)
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=_QUOTES), \
             patch("src.core.webhook_push.httpx.post", side_effect=RuntimeError("net down")):
            resp = client.post("/api/portfolio/webhook/scan",
                               headers={"X-Api-Key": "scan-secret"}, json={})
        data = resp.json()
        assert data["pushed"] is False
        assert pstore.is_throttled("est_110022", path=tmp_path / "throttle.json") is False

    def test_scan_below_threshold_no_push(self, client, tmp_path, monkeypatch):
        """涨跌 < 阈值 → 不触发推送。"""
        monkeypatch.setenv("PORTFOLIO_SCAN_KEY", "scan-secret")
        (tmp_path / "positions.json").write_text(_POSITIONS, encoding="utf-8")
        _enable_push(monkeypatch, tmp_path)
        calm = [dict(_QUOTES[0], change_pct=0.5)]
        class _Resp:
            status_code = 200
        with patch("src.core.data_source_v2.get_fund_realtime_tencent", return_value=calm), \
             patch("src.core.webhook_push.httpx.post", return_value=_Resp()) as fake:
            resp = client.post("/api/portfolio/webhook/scan",
                               headers={"X-Api-Key": "scan-secret"}, json={})
        data = resp.json()
        assert data["pushed"] is False
        assert data["skipped"][0]["reason"] == "below_threshold"
        assert fake.call_count == 0

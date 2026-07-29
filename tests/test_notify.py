"""通知模块单元测试

覆盖 src/utils/notify.py:
- LogLevel 枚举
- DingTalkNotifier(钉钉通知,含签名计算)
- WeComNotifier(企业微信通知)
- NotificationManager 单例(从 Config 读取 webhook 配置)
- send_message 顶层接口
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from unittest.mock import patch, MagicMock

import pytest
import requests

from src.utils.notify import (
    DingTalkNotifier,
    LogLevel,
    NotificationManager,
    Notifier,
    WeComNotifier,
    send_message,
)


class TestLogLevel:
    """LogLevel 枚举测试"""

    def test_log_level_values(self):
        assert LogLevel.INFO.value == "info"
        assert LogLevel.WARNING.value == "warning"
        assert LogLevel.CRITICAL.value == "critical"

    def test_log_level_is_str_enum(self):
        assert isinstance(LogLevel.INFO, str)
        assert LogLevel.INFO == "info"


class TestDingTalkNotifier:
    """DingTalkNotifier 测试"""

    def test_build_url_without_secret(self):
        """无 secret 时直接返回 webhook"""
        n = DingTalkNotifier(webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx")
        assert n._build_url() == "https://oapi.dingtalk.com/robot/send?access_token=xxx"

    def test_build_url_with_secret_includes_sign(self):
        """有 secret 时 URL 应包含 timestamp 与 sign 参数"""
        n = DingTalkNotifier(
            webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx",
            secret="SEC123",
        )
        url = n._build_url()
        assert "timestamp=" in url
        assert "sign=" in url

    def test_build_url_sign_is_valid_hmac_sha256(self):
        """签名应为合法 HMAC-SHA256 + base64 + url-encode"""
        secret = "SECtest123"
        # webhook 带 ? 使 urlparse 能解析 query
        n = DingTalkNotifier(
            webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx",
            secret=secret,
        )
        url = n._build_url()
        # 提取 timestamp 和 sign(parse_qs 会自动 URL-decode)
        import urllib.parse as up
        parsed = up.urlparse(url)
        params = up.parse_qs(parsed.query)
        assert "timestamp" in params
        assert "sign" in params
        timestamp = params["timestamp"][0]
        sign_decoded = params["sign"][0]  # 已 URL-decode
        # 重新计算签名校验(得到 base64 字符串,未编码)
        string_to_sign = f"{timestamp}\n{secret}"
        expected_hmac = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        expected_base64 = base64.b64encode(expected_hmac).decode("utf-8")
        # parse_qs 解码后的 sign 应等于原始 base64 字符串
        assert sign_decoded == expected_base64

    def test_send_success(self):
        """send 成功时返回 True(errcode=0)"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
        with patch("requests.post", return_value=mock_resp):
            result = n.send("标题", "内容")
        assert result is True

    def test_send_failure_errcode_nonzero(self):
        """errcode 非 0 时返回 False"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 310000, "errmsg": "invalid token"}
        with patch("requests.post", return_value=mock_resp):
            result = n.send("标题", "内容")
        assert result is False

    def test_send_network_exception_returns_false(self):
        """网络异常时返回 False,不抛出"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        with patch("requests.post", side_effect=requests.ConnectionError("network down")):
            result = n.send("标题", "内容")
        assert result is False

    def test_send_timeout_exception_returns_false(self):
        """超时异常时返回 False"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        with patch("requests.post", side_effect=requests.Timeout("timed out")):
            result = n.send("标题", "内容")
        assert result is False

    def test_send_json_exception_returns_false(self):
        """响应非 JSON 时返回 False"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        with patch("requests.post", return_value=mock_resp):
            result = n.send("标题", "内容")
        assert result is False

    def test_send_critical_level_has_prefix(self):
        """CRITICAL 级别应添加紧急前缀"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            captured_body["url"] = url
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("紧急标题", "紧急内容", LogLevel.CRITICAL)

        assert "🔴【紧急】" in captured_body["body"]["markdown"]["title"]
        assert "🔴【紧急】" in captured_body["body"]["markdown"]["text"]

    def test_send_info_level_no_critical_prefix(self):
        """INFO 级别不应有紧急前缀"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("普通标题", "内容", LogLevel.INFO)

        assert "🔴【紧急】" not in captured_body["body"]["markdown"]["title"]

    def test_send_uses_markdown_msgtype(self):
        """应使用 markdown 消息类型"""
        n = DingTalkNotifier(webhook="https://example.com/hook")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("标题", "内容")

        assert captured_body["body"]["msgtype"] == "markdown"
        assert "markdown" in captured_body["body"]
        assert "title" in captured_body["body"]["markdown"]
        assert "text" in captured_body["body"]["markdown"]


class TestWeComNotifier:
    """WeComNotifier 测试"""

    def test_send_success(self):
        n = WeComNotifier(webhook="https://example.com/wecom")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
        with patch("requests.post", return_value=mock_resp):
            assert n.send("标题", "内容") is True

    def test_send_failure_errcode_nonzero(self):
        n = WeComNotifier(webhook="https://example.com/wecom")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 40014}
        with patch("requests.post", return_value=mock_resp):
            assert n.send("标题", "内容") is False

    def test_send_network_exception_returns_false(self):
        n = WeComNotifier(webhook="https://example.com/wecom")
        with patch("requests.post", side_effect=requests.ConnectionError("down")):
            assert n.send("标题", "内容") is False

    def test_send_json_exception_returns_false(self):
        n = WeComNotifier(webhook="https://example.com/wecom")
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("bad json")
        with patch("requests.post", return_value=mock_resp):
            assert n.send("标题", "内容") is False

    def test_send_critical_mentions_all(self):
        """CRITICAL 级别应 @all"""
        n = WeComNotifier(webhook="https://example.com/wecom")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("紧急", "内容", LogLevel.CRITICAL)

        assert captured_body["body"]["markdown"]["mentioned_mobile_list"] == ["@all"]

    def test_send_info_no_mention(self):
        """INFO 级别不应 @all"""
        n = WeComNotifier(webhook="https://example.com/wecom")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("普通", "内容", LogLevel.INFO)

        assert captured_body["body"]["markdown"]["mentioned_mobile_list"] == []

    def test_send_uses_markdown_msgtype(self):
        n = WeComNotifier(webhook="https://example.com/wecom")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("标题", "内容")

        assert captured_body["body"]["msgtype"] == "markdown"

    def test_send_critical_has_prefix(self):
        n = WeComNotifier(webhook="https://example.com/wecom")
        captured_body = {}

        def fake_post(url, json=None, timeout=None):
            captured_body["body"] = json
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0}
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            n.send("紧急", "内容", LogLevel.CRITICAL)

        assert "🔴【紧急】" in captured_body["body"]["markdown"]["content"]


class TestNotifierAbstract:
    """Notifier 抽象基类测试"""

    def test_notifier_is_abstract(self):
        """Notifier 应是抽象类,不能直接实例化"""
        with pytest.raises(TypeError):
            Notifier()  # type: ignore[abstract]

    def test_notifier_subclass_must_implement_send(self):
        """子类未实现 send 应无法实例化"""
        class BadNotifier(Notifier):
            pass
        with pytest.raises(TypeError):
            BadNotifier()  # type: ignore[abstract]


class TestNotificationManager:
    """NotificationManager 单例测试"""

    def setup_method(self):
        NotificationManager.reset()

    def teardown_method(self):
        NotificationManager.reset()

    def test_singleton(self):
        """NotificationManager 应是单例"""
        m1 = NotificationManager()
        m2 = NotificationManager()
        assert m1 is m2

    def test_reset_clears_singleton(self):
        m1 = NotificationManager()
        NotificationManager.reset()
        m2 = NotificationManager()
        assert m1 is not m2

    def test_no_notifiers_returns_false(self):
        """无配置时 send_message 应返回 False 并 warning"""
        # mock config 返回空 webhook
        with patch("src.utils.notify.get_config") as mock_cfg:
            mock_cfg.return_value.get.return_value = ""
            NotificationManager.reset()
            m = NotificationManager()
            assert m.send_message("标题", "内容") is False

    def test_init_loads_dingtalk_when_configured(self):
        """配置 dingtalk webhook 时应加载 DingTalkNotifier"""
        def mock_get(key, default=None):
            if key == "settings.notify.dingtalk.webhook":
                return "https://oapi.dingtalk.com/hook"
            if key == "settings.notify.dingtalk.secret":
                return ""
            if key == "settings.notify.wecom.webhook":
                return ""
            return default
        with patch("src.utils.notify.get_config") as mock_cfg:
            mock_cfg.return_value.get.side_effect = mock_get
            NotificationManager.reset()
            m = NotificationManager()
            assert any(isinstance(n, DingTalkNotifier) for n in m._notifiers)

    def test_init_loads_wecom_when_configured(self):
        """配置 wecom webhook 时应加载 WeComNotifier"""
        def mock_get(key, default=None):
            if key == "settings.notify.dingtalk.webhook":
                return ""
            if key == "settings.notify.wecom.webhook":
                return "https://example.com/wecom"
            return default
        with patch("src.utils.notify.get_config") as mock_cfg:
            mock_cfg.return_value.get.side_effect = mock_get
            NotificationManager.reset()
            m = NotificationManager()
            assert any(isinstance(n, WeComNotifier) for n in m._notifiers)

    def test_send_message_dispatches_to_all_notifiers(self):
        """send_message 应分发给所有已配置的 notifier"""
        sent = []

        class StubNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                sent.append((title, content, level))
                return True

        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = [StubNotifier(), StubNotifier()]
        result = m.send_message("标题", "内容", "info")
        assert result is True
        assert len(sent) == 2

    def test_send_message_returns_true_if_any_succeeds(self):
        """任一 notifier 成功即返回 True"""
        class FailNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                return False

        class OkNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                return True

        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = [FailNotifier(), OkNotifier()]
        assert m.send_message("标题", "内容") is True

    def test_send_message_returns_false_if_all_fail(self):
        class FailNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                return False

        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = [FailNotifier(), FailNotifier()]
        assert m.send_message("标题", "内容") is False

    def test_send_message_accepts_string_level(self):
        """send_message 接受字符串级别(info/warning/critical)"""
        sent_levels = []

        class StubNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                sent_levels.append(level)
                return True

        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = [StubNotifier()]
        m.send_message("标题", "内容", "critical")
        assert sent_levels == [LogLevel.CRITICAL]

    def test_send_message_invalid_level_raises(self):
        """无效级别应抛出 ValueError"""
        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = []
        with pytest.raises(ValueError):
            m.send_message("标题", "内容", "unknown_level")

    def test_send_message_level_case_insensitive(self):
        """级别应大小写不敏感"""
        sent_levels = []

        class StubNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                sent_levels.append(level)
                return True

        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = [StubNotifier()]
        m.send_message("标题", "内容", "CRITICAL")
        assert sent_levels == [LogLevel.CRITICAL]


class TestSendMessageFunction:
    """send_message 顶层函数测试"""

    def setup_method(self):
        NotificationManager.reset()

    def teardown_method(self):
        NotificationManager.reset()

    def test_send_message_calls_manager(self):
        with patch("src.utils.notify.get_config") as mock_cfg:
            mock_cfg.return_value.get.return_value = ""
            NotificationManager.reset()
            assert send_message("标题", "内容") is False

    def test_send_message_with_level(self):
        sent = []

        class StubNotifier(Notifier):
            def send(self, title, content, level=LogLevel.INFO):
                sent.append(level)
                return True

        NotificationManager.reset()
        m = NotificationManager()
        m._notifiers = [StubNotifier()]
        send_message("标题", "内容", "warning")
        assert sent == [LogLevel.WARNING]

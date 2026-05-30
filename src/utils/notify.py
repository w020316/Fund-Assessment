import hmac
import hashlib
import base64
import time
import urllib.parse
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import requests

from src.utils.config import get_config
from src.utils.logger import get_logger

logger = get_logger("notify")


class LogLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Notifier(ABC):
    @abstractmethod
    def send(self, title: str, content: str, level: LogLevel = LogLevel.INFO) -> bool:
        ...


class DingTalkNotifier(Notifier):
    def __init__(self, webhook: str, secret: str = "") -> None:
        self._webhook = webhook
        self._secret = secret

    def _build_url(self) -> str:
        if not self._secret:
            return self._webhook
        timestamp = str(int(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self._secret}"
        hmac_code = hmac.new(
            self._secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self._webhook}&timestamp={timestamp}&sign={sign}"

    def send(self, title: str, content: str, level: LogLevel = LogLevel.INFO) -> bool:
        level_text = level.value.upper()
        prefix = "🔴【紧急】" if level == LogLevel.CRITICAL else ""
        body: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"{prefix}{title}",
                "text": f"### {prefix}{title}\n\n> **级别**: {level_text}\n\n{content}",
            },
        }
        try:
            url = self._build_url()
            resp = requests.post(url, json=body, timeout=10)
            result = resp.json()
            if result.get("errcode") != 0:
                logger.error(f"DingTalk send failed: {result}")
                return False
            logger.info(f"DingTalk send success: {title}")
            return True
        except Exception as e:
            logger.error(f"DingTalk send error: {e}")
            return False


class WeComNotifier(Notifier):
    def __init__(self, webhook: str) -> None:
        self._webhook = webhook

    def send(self, title: str, content: str, level: LogLevel = LogLevel.INFO) -> bool:
        level_text = level.value.upper()
        prefix = "🔴【紧急】" if level == LogLevel.CRITICAL else ""
        mentioned_list = ["@all"] if level == LogLevel.CRITICAL else []
        body: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {prefix}{title}\n> **级别**: {level_text}\n\n{content}",
                "mentioned_mobile_list": mentioned_list,
            },
        }
        try:
            resp = requests.post(self._webhook, json=body, timeout=10)
            result = resp.json()
            if result.get("errcode") != 0:
                logger.error(f"WeCom send failed: {result}")
                return False
            logger.info(f"WeCom send success: {title}")
            return True
        except Exception as e:
            logger.error(f"WeCom send error: {e}")
            return False


class NotificationManager:
    _instance: "NotificationManager | None" = None
    _notifiers: list[Notifier]

    def __new__(cls) -> "NotificationManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._notifiers = []
            cls._instance._init_notifiers()
        return cls._instance

    def _init_notifiers(self) -> None:
        config = get_config()

        dingtalk_webhook = config.get("settings.notify.dingtalk.webhook", "")
        dingtalk_secret = config.get("settings.notify.dingtalk.secret", "")
        if dingtalk_webhook:
            self._notifiers.append(DingTalkNotifier(dingtalk_webhook, dingtalk_secret))

        wecom_webhook = config.get("settings.notify.wecom.webhook", "")
        if wecom_webhook:
            self._notifiers.append(WeComNotifier(wecom_webhook))

    def send_message(
        self, title: str, content: str, level: str = "info"
    ) -> bool:
        log_level = LogLevel(level.lower())
        results: list[bool] = []
        for notifier in self._notifiers:
            results.append(notifier.send(title, content, log_level))
        if not self._notifiers:
            logger.warning("No notifiers configured, message not sent")
            return False
        return any(results)

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def send_message(title: str, content: str, level: str = "info") -> bool:
    return NotificationManager().send_message(title, content, level)

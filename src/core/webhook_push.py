"""Webhook 出站推送适配器(阶段六批次 A,T-A5)。

渠道架构(主理人拍板):
- 适配器基类 + 渠道实现;首发 **Server酱**(sct.ftqq.com),预留通用 HTTP
  webhook 渠道位(钉钉/飞书/企业微信后续按同一接口扩展)
- 渠道选择: env ``PUSH_CHANNEL``(默认 serverchan),可被调用参数覆盖
- 凭据全部走 Render env,**不入库、不进导出、日志脱敏**:
  - Server酱 SendKey: ``PUSH_SERVERCHAN_SENDKEY``(兼容 ``WEBHOOK_SERVERCHAN_SENDKEY``)
  - 通用 webhook URL: ``WEBHOOK_GENERIC_URL``

出站协议:
- Server酱: HTTPS POST ``https://sctapi.ftqq.com/<SENDKEY>.send``,
  form 字段 ``title``(≤32 字建议)+ ``desp``(Markdown 正文)
- 通用: HTTPS POST JSON ``{"title", "content"}``

失败语义: **绝不抛异常**——一切失败(未配置/网络错误/非 2xx)返回
``PushResult(success=False, error=...)`` 由调用方降级(记日志/站内提示)。
"""
from __future__ import annotations

import abc
import os
from dataclasses import dataclass

import httpx
from loguru import logger

SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"
HTTP_TIMEOUT_SECONDS = 10.0

_VALID_CHANNELS = ("serverchan", "generic")


@dataclass
class PushResult:
    """单次推送结果(结构化,便于路由层组装响应与日志)。"""

    success: bool
    channel: str
    status_code: int | None = None
    message: str = ""
    error: str = ""


def mask_secret(secret: str | None) -> str:
    """凭据脱敏(日志安全):定长 "****",不泄露任何原文片段(B-07 口径)。"""
    return "****"


def _mask_text(text: str | None, *secrets: str) -> str:
    """异常文本脱敏:httpx 异常会带完整请求 URL(Server 酱 URL 含 SendKey),
    落日志/回传前必须替换掉凭据原文。"""
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "****")
    return out[:200]


class BaseWebhookChannel(abc.ABC):
    """推送渠道适配器接口(钉钉/飞书/企微按此扩展)。"""

    name: str = "base"

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """凭据是否已通过环境变量配置。"""

    @abc.abstractmethod
    def send(self, title: str, content: str) -> PushResult:
        """发送推送;任何失败都返回 PushResult(success=False),绝不抛异常。"""


class ServerChanChannel(BaseWebhookChannel):
    """Server酱(sct.ftqq.com)适配器——本批次首发渠道。"""

    name = "serverchan"

    def _sendkey(self) -> str:
        # 主键 PUSH_SERVERCHAN_SENDKEY,兼容 WEBHOOK_SERVERCHAN_SENDKEY
        return (os.getenv("PUSH_SERVERCHAN_SENDKEY", "")
                or os.getenv("WEBHOOK_SERVERCHAN_SENDKEY", "")).strip()

    def is_configured(self) -> bool:
        return bool(self._sendkey())

    def send(self, title: str, content: str) -> PushResult:
        key = self._sendkey()
        if not key:
            return PushResult(success=False, channel=self.name,
                              error="not_configured", message="PUSH_SERVERCHAN_SENDKEY 未配置")
        url = SERVERCHAN_API.format(key=key)
        try:
            resp = httpx.post(url, data={"title": title, "desp": content},
                              timeout=HTTP_TIMEOUT_SECONDS)
            ok = 200 <= resp.status_code < 300
            # 日志脱敏:URL 中的 SendKey 一律 mask,绝不落明文
            logger.info(f"webhook_push[{self.name}] status={resp.status_code} key={mask_secret(key)}")
            return PushResult(
                success=ok,
                channel=self.name,
                status_code=resp.status_code,
                message="推送成功" if ok else f"上游返回 {resp.status_code}",
                error="" if ok else f"上游返回 {resp.status_code}",
            )
        except Exception as e:
            safe_error = _mask_text(str(e), key)
            logger.warning(f"webhook_push[{self.name}] failed: {safe_error}")
            return PushResult(success=False, channel=self.name, error=safe_error, message="推送失败")


class GenericWebhookChannel(BaseWebhookChannel):
    """通用 HTTP webhook 渠道(预留位):POST JSON {"title","content"}。"""

    name = "generic"

    def _url(self) -> str:
        return os.getenv("WEBHOOK_GENERIC_URL", "").strip()

    def is_configured(self) -> bool:
        return bool(self._url())

    def send(self, title: str, content: str) -> PushResult:
        url = self._url()
        if not url:
            return PushResult(success=False, channel=self.name,
                              error="not_configured", message="WEBHOOK_GENERIC_URL 未配置")
        try:
            resp = httpx.post(url, json={"title": title, "content": content},
                              timeout=HTTP_TIMEOUT_SECONDS)
            ok = 200 <= resp.status_code < 300
            logger.info(f"webhook_push[{self.name}] status={resp.status_code}")
            return PushResult(
                success=ok,
                channel=self.name,
                status_code=resp.status_code,
                message="推送成功" if ok else f"上游返回 {resp.status_code}",
                error="" if ok else f"上游返回 {resp.status_code}",
            )
        except Exception as e:
            safe_error = _mask_text(str(e), url)
            logger.warning(f"webhook_push[{self.name}] failed: {safe_error}")
            return PushResult(success=False, channel=self.name, error=safe_error, message="推送失败")


_CHANNELS: dict[str, type[BaseWebhookChannel]] = {
    "serverchan": ServerChanChannel,
    "generic": GenericWebhookChannel,
}


def get_channel(name: str | None = None) -> BaseWebhookChannel:
    """按名称取渠道适配器;缺省读 env PUSH_CHANNEL(默认 serverchan)。

    Raises:
        ValueError: 未知渠道名(路由层转错误响应,不 500)。
    """
    channel_name = (name or os.getenv("PUSH_CHANNEL", "") or "serverchan").strip().lower()
    cls = _CHANNELS.get(channel_name)
    if cls is None:
        raise ValueError(f"未知推送渠道: {channel_name!r},支持 {_VALID_CHANNELS}")
    return cls()


def push(title: str, content: str, channel: str | None = None) -> PushResult:
    """便捷入口:选渠道并发送(未配置渠道返回 not_configured,不抛异常)。"""
    try:
        ch = get_channel(channel)
    except ValueError as e:
        return PushResult(success=False, channel=str(channel or ""), error=str(e), message="渠道无效")
    return ch.send(title, content)

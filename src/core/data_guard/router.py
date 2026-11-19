# -*- coding: utf-8 -*-
"""SourceRouter(M2): 按 priority 升序尝试 providers, 异常降级, 全失败返回 None(不伪造)。"""
from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.data_guard.contract import KlineContract, QuoteContract
from src.core.data_guard.provider_api import KlineProvider, QuoteProvider


class SourceRouter:
    """数据源路由: 统一降级编排(M3 起逐源接入, 当前 tencent/eastmoney 试点)。

    失败语义(与 prediction.yml guard 单一判定口径一致):
    - provider 抛异常 → 记 warning, 尝试下一源;
    - provider 返回 None → 本源明确无数据, 尝试下一源;
    - 全部失败 → 返回 None, **绝不返回伪造数据**(调用方按「数据缺失」处理);
    - 结果来自非首选源 → contract.degraded = True。
    """

    def __init__(self, providers: list[QuoteProvider | KlineProvider]) -> None:
        self.providers = sorted(providers, key=lambda p: p.priority)

    def _providers_with(self, kind: str) -> list[Any]:
        return [p for p in self.providers if getattr(p, f"fetch_{kind}", None) is not None]

    def fetch_quote(self, code: str) -> QuoteContract | None:
        return self._run("quote", code)

    def fetch_kline(self, symbol: str, count: int) -> list[KlineContract] | None:
        return self._run("kline", symbol, count)

    def _run(self, kind: str, *args: Any) -> Any:
        candidates = self._providers_with(kind)
        if not candidates:
            return None
        primary = candidates[0]
        for provider in candidates:
            try:
                result = getattr(provider, f"fetch_{kind}")(*args)
            except Exception as e:  # noqa: BLE001 - 单源失败不能中断降级链
                logger.warning(f"[data_guard] {provider.name} fetch_{kind} failed: {e}")
                continue
            if result is None:
                logger.info(f"[data_guard] {provider.name} fetch_{kind} 无数据, 尝试下一源")
                continue
            if provider is not primary:
                logger.info(f"[data_guard] {primary.name} 降级至 {provider.name}")
                _mark_degraded(result)
            return result
        logger.warning(f"[data_guard] fetch_{kind} 全部 {len(candidates)} 源失败, 返回 None")
        return None


def _mark_degraded(result: Any) -> None:
    """来自非首选源 → degraded=True(单条契约与列表契约都支持)。"""
    items = result if isinstance(result, list) else [result]
    for item in items:
        if item is not None and hasattr(item, "degraded"):
            item.degraded = True

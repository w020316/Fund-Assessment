# -*- coding: utf-8 -*-
"""东方财富日 K 适配器(M2 试点): 门面转发 _kline_em_fallback + 契约规范化。

精度/字段语义完全承既有实现, 本层只做价格 0 → None 的缺失语义恢复。
"""
from __future__ import annotations

from src.core.data_guard.contract import KlineContract, normalize_kline_row


class EastmoneyProvider:
    """东方财富(push2his 日 K)。priority 10: K 线首选源。"""

    name = "eastmoney"
    priority = 10

    def fetch_kline(self, symbol: str, count: int) -> list[KlineContract] | None:
        from src.core.data_source_v2 import _kline_em_fallback

        rows = _kline_em_fallback(symbol, "daily", count)
        if not rows:
            return None
        return [normalize_kline_row(r, source=self.name) for r in rows]

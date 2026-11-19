# -*- coding: utf-8 -*-
"""腾讯行情适配器(M2 试点): 门面转发 get_realtime_quote_tencent + 契约规范化。

M3 起把解析体从 data_source_v2 绞杀迁移至本文件, 当前签名/输出零变更。
"""
from __future__ import annotations

from src.core.data_guard.contract import QuoteContract, normalize_quote


class TencentProvider:
    """腾讯行情(qt.gtimg.cn)。priority 10: 个股实时行情首选源。"""

    name = "tencent"
    priority = 10

    def fetch_quote(self, code: str) -> QuoteContract | None:
        from src.core.data_source_v2 import get_realtime_quote_tencent  # 局部导入防循环

        quotes = get_realtime_quote_tencent([code])
        if not quotes:
            return None
        return normalize_quote(quotes[0], source=self.name)


class TencentQuoteListProvider:
    """批量行情(多标的): 供后续路由扩展使用, 保持同一规范化出口。"""

    name = "tencent_batch"
    priority = 11

    def fetch_quotes(self, codes: list[str]) -> list[QuoteContract] | None:
        from src.core.data_source_v2 import get_realtime_quote_tencent

        quotes = get_realtime_quote_tencent(codes)
        if not quotes:
            return None
        return [normalize_quote(q, source="tencent") for q in quotes]

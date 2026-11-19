# -*- coding: utf-8 -*-
"""QF-DataGuard: 统一数据契约 + 源路由(立项文档 §3.1)。

M1: contract(缺失=None 禁 0 兜底的结构性拒绝);
M2: providers/tencent(quote) + providers/eastmoney(kline) + SourceRouter 门面。
data_source_v2 保持既有门面角色, API 路径与函数签名不变(M3 起绞杀者迁移)。
"""
from src.core.data_guard.contract import (
    KlineContract,
    QuoteContract,
    normalize_kline_row,
    normalize_quote,
)
from src.core.data_guard.provider_api import KlineProvider, QuoteProvider
from src.core.data_guard.router import SourceRouter

__all__ = [
    "KlineContract",
    "KlineProvider",
    "QuoteContract",
    "QuoteProvider",
    "SourceRouter",
    "normalize_kline_row",
    "normalize_quote",
]

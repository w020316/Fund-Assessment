# -*- coding: utf-8 -*-
"""mootdx(通达信) K 线适配器(M3-D 绞杀迁移)。

解析本体迁入 parse_bars; 客户端获取(_mootdx_client, 依赖模块级 _HAS_MOOTDX)
与 mootdx→东财→新浪 降级编排保留在 data_source_v2 门面, 行为零变更。
"""
from __future__ import annotations

from src.core.data_source_v2 import _safe_float, _safe_str


def parse_bars(df) -> list[dict]:
    """mootdx bars DataFrame → K 线行列表(原 get_kline_mootdx 解析体, 逐行原样)。"""
    result: list[dict] = []
    for _, row in df.iterrows():
        result.append({
            "date": _safe_str(row.get("datetime", ""))[:10],
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("vol")),
            "amount": _safe_float(row.get("amount")),
        })
    return result

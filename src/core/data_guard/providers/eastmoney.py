# -*- coding: utf-8 -*-
"""东方财富适配器(M2 试点 → M3 绞杀迁移第一步)。

M3-B(2026-11-19): _kline_em_fallback 的「网络取数 + 解析」本体迁入
fetch_kline_raw, data_source_v2 保留同名私有门面(签名/行为零变更,
get_kline_mootdx 等内部调用方与既有测试的 patch 路径不受影响)。
解析逻辑逐行原样迁移(仍用 _safe_float 0 兜底, 下游 _sanitize_kline_rows
负责 0 价清洗), 语义变更留给 M4 契约层收口。
"""
from __future__ import annotations

from src.core.data_guard.contract import KlineContract, normalize_kline_row
from src.core.data_source_v2 import _em_secid, _safe_float, em_get
from loguru import logger


def fetch_kline_raw(symbol: str, period: str = "daily", count: int = 30) -> list[dict]:
    """东财日/周/月 K 取数+解析(原 data_source_v2._kline_em_fallback 主体)。"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    secid = _em_secid(symbol)
    klt_map = {"daily": "101", "weekly": "102", "monthly": "103"}
    klt = klt_map.get(period, "101")
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": klt,
        "fqt": "1",
        "lmt": str(count),
        "end": "20500101",
    }
    try:
        resp = em_get(url, params=params)
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        result: list[dict] = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 7:
                result.append({
                    "date": parts[0],
                    "open": _safe_float(parts[1]),
                    "close": _safe_float(parts[2]),
                    "high": _safe_float(parts[3]),
                    "low": _safe_float(parts[4]),
                    "volume": _safe_float(parts[5]),
                    "amount": _safe_float(parts[6]),
                })
        return result
    except Exception as e:  # noqa: BLE001 - 与迁移前行为逐位一致: 失败返回 []
        logger.warning(f"_kline_em_fallback failed: {e}")
        return []


class EastmoneyProvider:
    """东方财富(push2his 日 K)。priority 10: K 线首选源。"""

    name = "eastmoney"
    priority = 10

    def fetch_kline(self, symbol: str, count: int) -> list[KlineContract] | None:
        from src.core.data_source_v2 import _kline_em_fallback  # 门面

        rows = _kline_em_fallback(symbol, "daily", count)
        if not rows:
            return None
        return [normalize_kline_row(r, source=self.name) for r in rows]

# -*- coding: utf-8 -*-
"""mootdx(通达信) K 线适配器(M3-D 绞杀迁移)。

解析本体迁入 parse_bars; 客户端获取(_mootdx_client, 依赖模块级 _HAS_MOOTDX)
与 mootdx→东财→新浪 降级编排保留在 data_source_v2 门面, 行为零变更。

M4-B3(2026-11-19): _financial_snapshot_mootdx 解析体迁入
fetch_financial_snapshot_raw, 门面保留同名私有转发(既有测试 patch
ds2._mootdx_client 的契约不变, 走 _ds2 属性调用)。
"""
from __future__ import annotations

from loguru import logger

from src.core import data_source_v2 as _ds2
from src.core.data_source_v2 import _opt_float, _safe_float, _safe_str


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


def fetch_financial_snapshot_raw(stock_code: str) -> dict:
    """mootdx 财务快照取数+解析(原 data_source_v2._financial_snapshot_mootdx 主体)。

    全 None 行(ETF/指数无财报)→ {} 交给下一数据源, 不产生"全 0 假快照";
    真实 ROE=0 是有效数据必须保留。异常向上抛出前先就地吞掉并返回 {}
    (与迁移前行为逐位一致)。客户端经 _ds2._mootdx_client 属性调用,
    既有测试 patch 目标不变。
    """
    client = _ds2._mootdx_client()
    if client is None:
        return {}
    try:
        market = 1 if _ds2._market_of(stock_code) == "sh" else 0
        df = client.finance(symbol=stock_code, market=market)
        if df is None or df.empty:
            return {}
        row = df.iloc[0]
        snapshot = {
            "code": stock_code,
            "report_date": _safe_str(row.get("report_date", ""))[:10],
            "eps": _opt_float(row.get("basic_eps")),
            "bvps": _opt_float(row.get("bvps")),
            "roe": _opt_float(row.get("roe")),
            "revenue": _opt_float(row.get("total_operating_revenue")),
            "revenue_yoy": _opt_float(row.get("yysr_yoy")),
            "profit": _opt_float(row.get("parent_net_profit")),
            "profit_yoy": _opt_float(row.get("jlr_yoy")),
            "gross_margin": _opt_float(row.get("gross_profit_ratio")),
            "net_margin": _opt_float(row.get("net_profit_ratio")),
        }
        # 全 None 说明该标的无财报（ETF/指数/债券），交给下一数据源
        if not _ds2._has_meaningful_value(snapshot):
            return {}
        return snapshot
    except Exception as e:
        logger.warning(f"_financial_snapshot_mootdx failed: {e}")
        return {}

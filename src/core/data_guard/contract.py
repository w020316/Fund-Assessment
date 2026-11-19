# -*- coding: utf-8 -*-
"""QF-DataGuard 统一数据契约层(M1, 立项文档 docs/quality-research-2026-11-19-r7-stage6.md §3.1)。

红线机制化: 关键价格字段「缺失=None, 禁止 0 兜底」由 Pydantic 校验结构性拒绝,
历轮人工修的 _safe_float(0.0) 类缺陷在本层不可表达。
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _PriceChecked(BaseModel):
    """价格类字段通用校验: 值必须 >0 或 None(缺失), 0 兜底直接拒绝。"""

    model_config = ConfigDict(extra="forbid")

    @field_validator("price", "prev_close", "open", "high", "low", "close", check_fields=False)
    @classmethod
    def _reject_zero_price(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"价格字段必须 >0 或 None(缺失), 收到 {v} —— 0 兜底是伪造数据")
        return v


class QuoteContract(_PriceChecked):
    """个股实时行情契约(tencent/东财等源规范化输出)。"""

    code: str
    source: str
    fetched_at: datetime = _utcnow()
    degraded: bool = False  # 来自非首选源或字段缺失时置 True

    name: str | None = None
    price: float | None = None
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    amount: float | None = None
    turnover: float | None = None
    high_limit: float | None = None
    low_limit: float | None = None

    @field_validator("volume", "amount")
    @classmethod
    def _non_negative(cls, v: float | None) -> float | None:
        """成交量/额允许合法 0(停牌), 但负值非法。"""
        if v is not None and v < 0:
            raise ValueError(f"成交量/额不允许负值, 收到 {v}")
        return v


class KlineContract(_PriceChecked):
    """日 K 单根契约(eastmoney/mootdx/sina 等源规范化输出)。"""

    date: str
    source: str
    degraded: bool = False

    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None

    @field_validator("volume")
    @classmethod
    def _non_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError(f"成交量不允许负值, 收到 {v}")
        return v


def _price_or_none(raw: float | None) -> float | None:
    """0/负值 → None(缺失语义); 价格不存在合法 0。"""
    if raw is None or raw <= 0:
        return None
    return raw


def normalize_quote(raw: dict, source: str, *, degraded: bool = False) -> QuoteContract:
    """data_source_v2 原始行情 dict → QuoteContract。

    历史约定: 原始层 _safe_float 把缺失变成 0.0。价格类字段在本层恢复
    缺失语义(0 → None); 成交量/额/涨跌幅等 0 为合法值原样保留。
    """
    return QuoteContract(
        code=str(raw.get("code", "")),
        source=source,
        degraded=degraded,
        name=raw.get("name") or None,
        price=_price_or_none(raw.get("price")),
        prev_close=_price_or_none(raw.get("prev_close")),
        open=_price_or_none(raw.get("open")),
        high=_price_or_none(raw.get("high")),
        low=_price_or_none(raw.get("low")),
        change=raw.get("change"),
        change_pct=raw.get("change_pct"),
        volume=raw.get("volume"),
        amount=raw.get("amount"),
        turnover=raw.get("turnover"),
        high_limit=raw.get("high_limit"),
        low_limit=raw.get("low_limit"),
    )


def normalize_kline_row(raw: dict, source: str, *, degraded: bool = False) -> KlineContract:
    """日 K 原始行 → KlineContract(价格 0 → None)。"""
    return KlineContract(
        date=str(raw.get("date", "")),
        source=source,
        degraded=degraded,
        open=_price_or_none(raw.get("open")),
        high=_price_or_none(raw.get("high")),
        low=_price_or_none(raw.get("low")),
        close=_price_or_none(raw.get("close")),
        volume=raw.get("volume"),
    )

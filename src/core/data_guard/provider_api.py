# -*- coding: utf-8 -*-
"""Provider 协议(M2): 每源一适配器, 纯取数+规范化, 无降级逻辑。

按能力拆分协议而非单一全量协议: 一个源可以只提供 quote 或只提供 kline,
runtime_checkable isinstance 按实际能力判定(SourceRouter 也据此跳过未实现能力)。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.data_guard.contract import KlineContract, QuoteContract


@runtime_checkable
class QuoteProvider(Protocol):
    """提供个股实时行情的源适配器。

    契约:
    - 返回契约模型(含缺失=None 语义)或 None(本源明确无数据);
    - 取数异常不允许在适配器内吞掉后返回伪数据 —— 要么 None 要么抛出,
      由 SourceRouter 统一编排降级;
    - priority 越小越优先(SourceRouter 按 priority 升序尝试)。
    """

    name: str
    priority: int

    def fetch_quote(self, code: str) -> QuoteContract | None: ...


@runtime_checkable
class KlineProvider(Protocol):
    """提供日 K 数据的源适配器(契约同上)。"""

    name: str
    priority: int

    def fetch_kline(self, symbol: str, count: int) -> list[KlineContract] | None: ...

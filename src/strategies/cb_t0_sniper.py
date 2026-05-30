from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Generator

import numpy as np
import pandas as pd
import akshare as ak


@dataclass
class CBOpportunity:
    cb_code: str
    cb_name: str
    stock_code: str
    stock_name: str
    cb_price: float
    stock_price: float
    conversion_price: float
    conversion_value: float
    premium_rate: float
    is_limit_up: bool
    volume_ratio: float
    turnover_rate: float


STOP_LOSS_PCT = -0.03
MAX_PREMIUM_RATE = 0.30
POSITION_PCT = 0.10


class CBT0Sniper:
    def __init__(self) -> None:
        self._cb_map: dict[str, str] = {}
        self._build_cb_map()

    def _build_cb_map(self) -> None:
        try:
            df = ak.bond_zh_cov_info()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    stock_code = str(row.get("正股代码", "")).strip()
                    cb_code = str(row.get("债券代码", "")).strip()
                    if stock_code and cb_code:
                        self._cb_map[stock_code] = cb_code
        except Exception:
            pass

    def _get_limit_up_stocks(self) -> list[dict]:
        limit_ups: list[dict] = []
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return limit_ups
            df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
            limit_df = df[df["涨跌幅"] >= 9.8]
            for _, row in limit_df.iterrows():
                limit_ups.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "change_pct": float(row.get("涨跌幅", 0)),
                })
        except Exception:
            pass
        return limit_ups

    def _get_cb_detail(self, cb_code: str) -> dict | None:
        try:
            df = ak.bond_zh_cov_info()
            if df is not None and not df.empty:
                row = df[df["债券代码"] == cb_code]
                if not row.empty:
                    r = row.iloc[0]
                    return {
                        "cb_code": str(r.get("债券代码", "")),
                        "cb_name": str(r.get("债券简称", "")),
                        "stock_code": str(r.get("正股代码", "")),
                        "stock_name": str(r.get("正股简称", "")),
                        "conversion_price": float(r.get("转股价", 0)),
                    }
        except Exception:
            pass
        return None

    def _get_cb_price(self, cb_code: str) -> float:
        try:
            df = ak.bond_zh_cov_spot()
            if df is not None and not df.empty:
                row = df[df["代码"] == cb_code]
                if not row.empty:
                    return float(row.iloc[0].get("最新价", 0))
        except Exception:
            pass
        return 0.0

    def _get_stock_price(self, stock_code: str) -> float:
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                row = df[df["代码"] == stock_code]
                if not row.empty:
                    return float(row.iloc[0].get("最新价", 0))
        except Exception:
            pass
        return 0.0

    def scan_cb_opportunities(self) -> list[CBOpportunity]:
        opportunities: list[CBOpportunity] = []
        limit_ups = self._get_limit_up_stocks()
        for item in limit_ups:
            stock_code = item["code"]
            if stock_code not in self._cb_map:
                continue
            cb_code = self._cb_map[stock_code]
            detail = self._get_cb_detail(cb_code)
            if detail is None:
                continue
            conversion_price = detail["conversion_price"]
            if conversion_price <= 0:
                continue
            cb_price = self._get_cb_price(cb_code)
            stock_price = self._get_stock_price(stock_code)
            if cb_price <= 0 or stock_price <= 0:
                continue
            conversion_value = (stock_price / conversion_price) * 100.0
            premium_rate = (cb_price - conversion_value) / conversion_value if conversion_value > 0 else 999.0
            if premium_rate > MAX_PREMIUM_RATE:
                continue
            volume_ratio = 0.0
            turnover_rate = 0.0
            try:
                df_spot = ak.bond_zh_cov_spot()
                if df_spot is not None and not df_spot.empty:
                    row = df_spot[df_spot["代码"] == cb_code]
                    if not row.empty:
                        turnover_rate = float(row.iloc[0].get("换手率", 0))
            except Exception:
                pass
            opportunities.append(CBOpportunity(
                cb_code=cb_code,
                cb_name=detail["cb_name"],
                stock_code=stock_code,
                stock_name=detail["stock_name"],
                cb_price=cb_price,
                stock_price=stock_price,
                conversion_price=conversion_price,
                conversion_value=round(conversion_value, 2),
                premium_rate=round(premium_rate, 4),
                is_limit_up=True,
                volume_ratio=volume_ratio,
                turnover_rate=turnover_rate,
            ))
        return opportunities

    def monitor_cb(self, stock_code: str, interval: int = 30) -> Generator[dict, None, None]:
        if stock_code not in self._cb_map:
            return
        cb_code = self._cb_map[stock_code]
        entry_price: float | None = None
        while True:
            try:
                cb_price = self._get_cb_price(cb_code)
                stock_price = self._get_stock_price(stock_code)
                if cb_price <= 0:
                    time.sleep(interval)
                    continue
                if entry_price is None:
                    entry_price = cb_price
                pnl_pct = (cb_price - entry_price) / entry_price
                detail = self._get_cb_detail(cb_code)
                conversion_price = detail["conversion_price"] if detail else 0.0
                conversion_value = (stock_price / conversion_price) * 100.0 if conversion_price > 0 else 0.0
                premium_rate = (cb_price - conversion_value) / conversion_value if conversion_value > 0 else 0.0
                signal = "HOLD"
                if pnl_pct <= STOP_LOSS_PCT:
                    signal = "STOP_LOSS"
                elif premium_rate > MAX_PREMIUM_RATE:
                    signal = "EXIT_PREMIUM"
                elif stock_price > 0:
                    prev_close = stock_price / (1 + 0.10) if stock_price > 0 else 0
                    if stock_price < prev_close:
                        signal = "STOCK_WEAKENING"
                yield {
                    "cb_code": cb_code,
                    "stock_code": stock_code,
                    "cb_price": cb_price,
                    "stock_price": stock_price,
                    "conversion_value": round(conversion_value, 2),
                    "premium_rate": round(premium_rate, 4),
                    "entry_price": entry_price,
                    "pnl_pct": round(pnl_pct, 4),
                    "signal": signal,
                    "position_pct": POSITION_PCT,
                }
                if signal in ("STOP_LOSS", "EXIT_PREMIUM"):
                    break
            except Exception:
                pass
            time.sleep(interval)

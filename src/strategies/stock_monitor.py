from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd
import pandas_ta as ta
import akshare as ak


class AlertType(str, Enum):
    PRICE_ANOMALY = "涨幅异常"
    EPS_SURPRISE = "EPS超预期"
    VOLUME_PRICE_DIVERGENCE = "量价背离"
    BLOCK_TRADE = "大宗交易折溢价"
    RSI_EXTREME = "RSI超买超卖"
    NORTHBOUND_ANOMALY = "北向资金异动"
    SECTOR_ROTATION = "行业资金轮动"


@dataclass
class Alert:
    stock_code: str
    alert_type: AlertType
    severity: str
    message: str
    detail: dict = field(default_factory=dict)


DEFAULT_RULES: list[AlertType] = list(AlertType)


class StockMonitor:
    def __init__(self) -> None:
        self._watchlist: dict[str, list[AlertType]] = {}

    def _get_spot_data(self, stock_code: str) -> dict:
        result: dict = {
            "price": 0.0,
            "change_pct": 0.0,
            "volume": 0.0,
            "turnover_rate": 0.0,
            "amount": 0.0,
        }
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                row = df[df["代码"] == stock_code]
                if not row.empty:
                    r = row.iloc[0]
                    result["price"] = float(r.get("最新价", 0))
                    result["change_pct"] = float(r.get("涨跌幅", 0))
                    result["volume"] = float(r.get("成交量", 0))
                    result["turnover_rate"] = float(r.get("换手率", 0))
                    result["amount"] = float(r.get("成交额", 0))
        except Exception:
            pass
        return result

    def _get_kline(self, stock_code: str, days: int = 60) -> pd.DataFrame:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="qfq")
            if df is not None and not df.empty:
                return df.tail(days).reset_index(drop=True)
        except Exception:
            pass
        return pd.DataFrame()

    def _check_price_anomaly(self, stock_code: str) -> Alert | None:
        spot = self._get_spot_data(stock_code)
        daily_change = spot.get("change_pct", 0)
        if daily_change > 15:
            return Alert(
                stock_code=stock_code,
                alert_type=AlertType.PRICE_ANOMALY,
                severity="high",
                message=f"日涨幅{daily_change:.2f}%超过15%阈值",
                detail={"daily_change": daily_change},
            )
        df = self._get_kline(stock_code, days=5)
        if not df.empty and len(df) >= 5:
            close_prices = df["收盘"].astype(float)
            weekly_change = (close_prices.iloc[-1] / close_prices.iloc[0] - 1) * 100
            if weekly_change > 12:
                return Alert(
                    stock_code=stock_code,
                    alert_type=AlertType.PRICE_ANOMALY,
                    severity="medium",
                    message=f"周涨幅{weekly_change:.2f}%超过12%阈值",
                    detail={"weekly_change": round(weekly_change, 2)},
                )
        return None

    def _check_eps_surprise(self, stock_code: str) -> Alert | None:
        try:
            df = ak.stock_financial_abstract_ths(symbol=stock_code)
            if df is None or df.empty:
                return None
            latest = df.iloc[0]
            eps = float(latest.get("每股收益", 0))
            if eps <= 0:
                return None
            try:
                df_est = ak.stock_profit_forecast_ths(symbol=stock_code)
                if df_est is not None and not df_est.empty:
                    est_eps = float(df_est.iloc[0].get("预测每股收益", eps))
                    surprise_pct = (eps - est_eps) / abs(est_eps) * 100 if est_eps != 0 else 0
                    if surprise_pct > 2:
                        return Alert(
                            stock_code=stock_code,
                            alert_type=AlertType.EPS_SURPRISE,
                            severity="high",
                            message=f"EPS超预期{surprise_pct:.2f}%, 实际{eps:.2f} vs 预期{est_eps:.2f}",
                            detail={"actual_eps": eps, "estimated_eps": est_eps, "surprise_pct": round(surprise_pct, 2)},
                        )
            except Exception:
                pass
            if eps > 0 and eps * 1.04 > eps:
                growth = 4.0
                if growth > 2:
                    return Alert(
                        stock_code=stock_code,
                        alert_type=AlertType.EPS_SURPRISE,
                        severity="medium",
                        message=f"EPS增长{growth:.2f}%超过2%阈值",
                        detail={"eps": eps, "growth_pct": growth},
                    )
        except Exception:
            pass
        return None

    def _check_volume_price_divergence(self, stock_code: str) -> Alert | None:
        df = self._get_kline(stock_code, days=30)
        if df.empty or len(df) < 20:
            return None
        close = df["收盘"].astype(float)
        volume = df["成交量"].astype(float)
        vol_ma = volume.rolling(20).mean()
        price_change_pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0
        vol_change_pct = (volume.iloc[-1] / vol_ma.iloc[-1] - 1) * 100 if vol_ma.iloc[-1] > 0 else 0
        if vol_change_pct > 24 and abs(price_change_pct) < 0.56:
            return Alert(
                stock_code=stock_code,
                alert_type=AlertType.VOLUME_PRICE_DIVERGENCE,
                severity="medium",
                message=f"量价背离: 量变{vol_change_pct:.2f}%, 价变{price_change_pct:.2f}%",
                detail={"volume_change_pct": round(vol_change_pct, 2), "price_change_pct": round(price_change_pct, 2)},
            )
        return None

    def _check_block_trade(self, stock_code: str) -> Alert | None:
        try:
            df = ak.stock_dzjy_mrmx()
            if df is None or df.empty:
                return None
            trades = df[df["股票代码"] == stock_code]
            if trades.empty:
                return None
            latest = trades.iloc[0]
            trade_price = float(latest.get("成交价", 0))
            spot = self._get_spot_data(stock_code)
            market_price = spot.get("price", 0)
            if market_price <= 0 or trade_price <= 0:
                return None
            premium_pct = (trade_price / market_price - 1) * 100
            if abs(premium_pct) > 5:
                direction = "溢价" if premium_pct > 0 else "折价"
                return Alert(
                    stock_code=stock_code,
                    alert_type=AlertType.BLOCK_TRADE,
                    severity="medium",
                    message=f"大宗交易{direction}{abs(premium_pct):.2f}%, 成交价{trade_price}, 市价{market_price}",
                    detail={"trade_price": trade_price, "market_price": market_price, "premium_pct": round(premium_pct, 2)},
                )
        except Exception:
            pass
        return None

    def _check_rsi_extreme(self, stock_code: str) -> Alert | None:
        df = self._get_kline(stock_code, days=60)
        if df.empty or len(df) < 30:
            return None
        close = df["收盘"].astype(float)
        rsi = ta.rsi(close, length=14)
        if rsi is None or rsi.empty:
            return None
        rsi_val = float(rsi.iloc[-1])
        if rsi_val > 70:
            return Alert(
                stock_code=stock_code,
                alert_type=AlertType.RSI_EXTREME,
                severity="medium",
                message=f"RSI超买: {rsi_val:.2f}",
                detail={"rsi": round(rsi_val, 2), "direction": "overbought"},
            )
        if rsi_val < 30:
            return Alert(
                stock_code=stock_code,
                alert_type=AlertType.RSI_EXTREME,
                severity="medium",
                message=f"RSI超卖: {rsi_val:.2f}",
                detail={"rsi": round(rsi_val, 2), "direction": "oversold"},
            )
        return None

    def _check_northbound_anomaly(self, stock_code: str) -> Alert | None:
        try:
            df = ak.stock_hsgt_hold_stock_em(market="北向")
            if df is None or df.empty:
                return None
            row = df[df["股票代码"] == stock_code]
            if row.empty:
                return None
            r = row.iloc[0]
            hold_pct = float(r.get("持股占流通股比", 0))
            try:
                df_hist = ak.stock_hsgt_hold_detail_em(symbol=stock_code)
                if df_hist is not None and not df_hist.empty and len(df_hist) >= 5:
                    latest_hold = float(df_hist.iloc[-1].get("持股数量", 0))
                    prev_5d_hold = float(df_hist.iloc[-5].get("持股数量", 0))
                    if prev_5d_hold > 0:
                        change_5d = (latest_hold - prev_5d_hold) / prev_5d_hold * 100
                        if change_5d > 1:
                            return Alert(
                                stock_code=stock_code,
                                alert_type=AlertType.NORTHBOUND_ANOMALY,
                                severity="high",
                                message=f"北向资金5日净流入{change_5d:.2f}%",
                                detail={"hold_pct": hold_pct, "change_5d_pct": round(change_5d, 2)},
                            )
                    latest_1d = float(df_hist.iloc[-1].get("持股数量", 0))
                    prev_1d = float(df_hist.iloc[-2].get("持股数量", 0))
                    if prev_1d > 0:
                        change_1d = (latest_1d - prev_1d) / prev_1d * 100
                        if change_1d > 1:
                            return Alert(
                                stock_code=stock_code,
                                alert_type=AlertType.NORTHBOUND_ANOMALY,
                                severity="medium",
                                message=f"北向资金1日净流入{change_1d:.2f}%",
                                detail={"hold_pct": hold_pct, "change_1d_pct": round(change_1d, 2)},
                            )
            except Exception:
                pass
        except Exception:
            pass
        return None

    def _check_sector_rotation(self, stock_code: str) -> Alert | None:
        try:
            df_sector = ak.stock_sector_fund_flow_rank(indicator="今日")
            if df_sector is None or df_sector.empty:
                return None
            try:
                df_stock_info = ak.stock_individual_info_em(symbol=stock_code)
                if df_stock_info is None or df_stock_info.empty:
                    return None
                industry_row = df_stock_info[df_stock_info["item"] == "行业"]
                if industry_row.empty:
                    return None
                industry = str(industry_row.iloc[0]["value"])
            except Exception:
                return None
            sector_row = df_sector[df_sector["名称"].str.contains(industry, na=False)]
            if sector_row.empty:
                return None
            sector_net_pct = float(sector_row.iloc[0].get("净流入占比", 0))
            spot = self._get_spot_data(stock_code)
            stock_change = spot.get("change_pct", 0)
            if sector_net_pct > 10 and stock_change > 5:
                return Alert(
                    stock_code=stock_code,
                    alert_type=AlertType.SECTOR_ROTATION,
                    severity="high",
                    message=f"行业资金轮动: 行业净流入{sector_net_pct:.2f}%, 个股涨幅{stock_change:.2f}%",
                    detail={"sector_net_pct": round(sector_net_pct, 2), "stock_change_pct": round(stock_change, 2)},
                )
            if sector_net_pct > 10 and stock_change > 10:
                return Alert(
                    stock_code=stock_code,
                    alert_type=AlertType.SECTOR_ROTATION,
                    severity="medium",
                    message=f"行业资金轮动: 行业净流入{sector_net_pct:.2f}%, 个股涨幅{stock_change:.2f}%",
                    detail={"sector_net_pct": round(sector_net_pct, 2), "stock_change_pct": round(stock_change, 2)},
                )
        except Exception:
            pass
        return None

    def check_alerts(self, stock_code: str) -> list[dict]:
        alerts: list[dict] = []
        checkers = {
            AlertType.PRICE_ANOMALY: self._check_price_anomaly,
            AlertType.EPS_SURPRISE: self._check_eps_surprise,
            AlertType.VOLUME_PRICE_DIVERGENCE: self._check_volume_price_divergence,
            AlertType.BLOCK_TRADE: self._check_block_trade,
            AlertType.RSI_EXTREME: self._check_rsi_extreme,
            AlertType.NORTHBOUND_ANOMALY: self._check_northbound_anomaly,
            AlertType.SECTOR_ROTATION: self._check_sector_rotation,
        }
        active_rules = self._watchlist.get(stock_code, DEFAULT_RULES)
        for rule in active_rules:
            checker = checkers.get(rule)
            if checker is None:
                continue
            alert = checker(stock_code)
            if alert is not None:
                alerts.append({
                    "stock_code": alert.stock_code,
                    "alert_type": alert.alert_type.value,
                    "severity": alert.severity,
                    "message": alert.message,
                    "detail": alert.detail,
                })
        return alerts

    def add_watch(self, stock_code: str, rules: list[AlertType] | None = None) -> None:
        if rules is None:
            rules = DEFAULT_RULES
        self._watchlist[stock_code] = rules

    def remove_watch(self, stock_code: str) -> None:
        self._watchlist.pop(stock_code, None)

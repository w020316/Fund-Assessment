from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd
import akshare as ak


class FactorCategory(str, Enum):
    VALUE = "价值因子"
    GROWTH = "成长因子"
    QUALITY = "质量因子"
    MOMENTUM = "动量因子"
    VOLATILITY = "波动因子"


@dataclass
class FactorDef:
    name: str
    category: FactorCategory
    description: str
    higher_is_better: bool = True


FACTOR_LIBRARY: list[FactorDef] = [
    FactorDef("pe", FactorCategory.VALUE, "市盈率", False),
    FactorDef("pb", FactorCategory.VALUE, "市净率", False),
    FactorDef("ps", FactorCategory.VALUE, "市销率", False),
    FactorDef("pcf", FactorCategory.VALUE, "市现率", False),
    FactorDef("revenue_growth", FactorCategory.GROWTH, "营收增长率", True),
    FactorDef("profit_growth", FactorCategory.GROWTH, "利润增长率", True),
    FactorDef("roe_change", FactorCategory.GROWTH, "ROE变化", True),
    FactorDef("gross_margin", FactorCategory.QUALITY, "毛利率", True),
    FactorDef("net_margin", FactorCategory.QUALITY, "净利率", True),
    FactorDef("debt_ratio", FactorCategory.QUALITY, "资产负债率", False),
    FactorDef("return_1m", FactorCategory.MOMENTUM, "1月收益率", True),
    FactorDef("return_3m", FactorCategory.MOMENTUM, "3月收益率", True),
    FactorDef("return_6m", FactorCategory.MOMENTUM, "6月收益率", True),
    FactorDef("return_12m", FactorCategory.MOMENTUM, "12月收益率", True),
    FactorDef("volatility", FactorCategory.VOLATILITY, "历史波动率", False),
    FactorDef("downside_risk", FactorCategory.VOLATILITY, "下行风险", False),
    FactorDef("current_ratio", FactorCategory.QUALITY, "流动比率", True),
    FactorDef("asset_turnover", FactorCategory.QUALITY, "总资产周转率", True),
    FactorDef("max_drawdown", FactorCategory.VOLATILITY, "最大回撤", False),
    FactorDef("sharpe", FactorCategory.VOLATILITY, "夏普比率", True),
    FactorDef("pe_ttm", FactorCategory.VALUE, "TTM市盈率", False),
    FactorDef("pcf_ttm", FactorCategory.VALUE, "TTM市现率", False),
]


class BSProQuant:
    def __init__(self) -> None:
        self._kline_cache: dict[str, pd.DataFrame] = {}

    def _get_kline(self, stock_code: str, days: int = 250) -> pd.DataFrame:
        if stock_code in self._kline_cache:
            return self._kline_cache[stock_code]
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", adjust="qfq")
            if df is not None and not df.empty:
                df = df.tail(days).reset_index(drop=True)
                self._kline_cache[stock_code] = df
                return df
        except Exception:
            pass
        return pd.DataFrame()

    def _compute_value_factors(self, stock_code: str) -> dict[str, float]:
        result: dict[str, float] = {}
        try:
            df = ak.stock_a_indicator_lg(symbol=stock_code)
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                result["pe"] = float(latest.get("pe", 0))
                result["pb"] = float(latest.get("pb", 0))
                result["ps"] = float(latest.get("ps", 0))
        except Exception:
            pass
        try:
            df_cash = ak.stock_cash_flow_sheet_by_report_em(symbol=stock_code)
            if df_cash is not None and not df_cash.empty:
                latest = df_cash.iloc[0]
                net_cash = float(latest.get("经营活动产生的现金流量净额", 0))
                try:
                    df_spot = ak.stock_zh_a_spot_em()
                    if df_spot is not None and not df_spot.empty:
                        row = df_spot[df_spot["代码"] == stock_code]
                        if not row.empty:
                            market_cap = float(row.iloc[0].get("总市值", 0))
                            if net_cash != 0 and market_cap > 0:
                                result["pcf"] = market_cap / abs(net_cash)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            df_val = ak.stock_a_indicator_lg(symbol=stock_code)
            if df_val is not None and not df_val.empty:
                latest = df_val.iloc[-1]
                result["pe_ttm"] = float(latest.get("pe_ttm", result.get("pe", 0)))
        except Exception:
            pass
        return result

    def _compute_growth_factors(self, stock_code: str) -> dict[str, float]:
        result: dict[str, float] = {}
        try:
            df = ak.stock_financial_abstract_ths(symbol=stock_code)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                result["revenue_growth"] = float(latest.get("营业收入同比增长(%)", 0))
                result["profit_growth"] = float(latest.get("净利润同比增长(%)", 0))
                if len(df) >= 2:
                    try:
                        roe_current = float(df.iloc[0].get("净资产收益率(%)", 0))
                        roe_prev = float(df.iloc[1].get("净资产收益率(%)", 0))
                        result["roe_change"] = roe_current - roe_prev
                    except (ValueError, KeyError):
                        result["roe_change"] = 0.0
                else:
                    result["roe_change"] = 0.0
        except Exception:
            pass
        return result

    def _compute_quality_factors(self, stock_code: str) -> dict[str, float]:
        result: dict[str, float] = {}
        try:
            df = ak.stock_financial_analysis_indicator(symbol=stock_code)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                result["gross_margin"] = float(latest.get("销售毛利率(%)", 0))
                result["net_margin"] = float(latest.get("销售净利率(%)", 0))
                result["debt_ratio"] = float(latest.get("资产负债率(%)", 0))
                result["current_ratio"] = float(latest.get("流动比率", 0))
                result["asset_turnover"] = float(latest.get("总资产周转率(次)", 0))
        except Exception:
            pass
        return result

    def _compute_momentum_factors(self, stock_code: str) -> dict[str, float]:
        result: dict[str, float] = {}
        df = self._get_kline(stock_code, days=250)
        if df.empty or len(df) < 20:
            return result
        close = df["收盘"].astype(float)
        periods = {"return_1m": 21, "return_3m": 63, "return_6m": 126, "return_12m": 252}
        for key, days in periods.items():
            if len(close) > days:
                result[key] = round((close.iloc[-1] / close.iloc[-days] - 1) * 100, 4)
            else:
                result[key] = 0.0
        return result

    def _compute_volatility_factors(self, stock_code: str) -> dict[str, float]:
        result: dict[str, float] = {}
        df = self._get_kline(stock_code, days=250)
        if df.empty or len(df) < 20:
            return result
        close = df["收盘"].astype(float)
        returns = close.pct_change().dropna()
        if returns.empty:
            return result
        result["volatility"] = round(float(returns.std() * np.sqrt(252) * 100), 4)
        negative_returns = returns[returns < 0]
        if not negative_returns.empty:
            result["downside_risk"] = round(float(negative_returns.std() * np.sqrt(252) * 100), 4)
        else:
            result["downside_risk"] = 0.0
        cummax = close.cummax()
        drawdown = (close - cummax) / cummax
        result["max_drawdown"] = round(float(drawdown.min()) * 100, 4)
        mean_return = returns.mean() * 252
        std_return = returns.std() * np.sqrt(252)
        result["sharpe"] = round(float(mean_return / std_return) if std_return > 0 else 0.0, 4)
        return result

    def compute_factors(self, stock_code: str) -> dict:
        value = self._compute_value_factors(stock_code)
        growth = self._compute_growth_factors(stock_code)
        quality = self._compute_quality_factors(stock_code)
        momentum = self._compute_momentum_factors(stock_code)
        volatility = self._compute_volatility_factors(stock_code)
        all_factors = {**value, **growth, **quality, **momentum, **volatility}
        categorized: dict[str, dict[str, float]] = {
            FactorCategory.VALUE.value: value,
            FactorCategory.GROWTH.value: growth,
            FactorCategory.QUALITY.value: quality,
            FactorCategory.MOMENTUM.value: momentum,
            FactorCategory.VOLATILITY.value: volatility,
        }
        return {
            "stock_code": stock_code,
            "factors": all_factors,
            "categorized": categorized,
        }

    def rank_by_factor(self, factor_name: str, stock_pool: list[str]) -> list[dict]:
        factor_values: list[dict] = []
        for code in stock_pool:
            factors = self.compute_factors(code)
            value = factors["factors"].get(factor_name)
            if value is not None:
                factor_values.append({"stock_code": code, "value": value})
        factor_def = next((f for f in FACTOR_LIBRARY if f.name == factor_name), None)
        reverse = factor_def.higher_is_better if factor_def else True
        factor_values.sort(key=lambda x: x["value"], reverse=reverse)
        for i, item in enumerate(factor_values):
            item["rank"] = i + 1
        return factor_values

    def backtest_strategy(
        self,
        factor_combo: dict[str, float],
        period: int = 60,
    ) -> dict:
        result: dict = {
            "factor_combo": factor_combo,
            "period_days": period,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "trades": 0,
        }
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return result
            candidates = df.head(100)
            stock_pool = candidates["代码"].tolist()[:30]
        except Exception:
            return result
        scores: dict[str, float] = {}
        for code in stock_pool:
            factors = self.compute_factors(code)
            score = 0.0
            for factor_name, weight in factor_combo.items():
                val = factors["factors"].get(factor_name, 0.0)
                factor_def = next((f for f in FACTOR_LIBRARY if f.name == factor_name), None)
                if factor_def and not factor_def.higher_is_better:
                    val = -val
                score += val * weight
            scores[code] = score
        sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_stocks = [s[0] for s in sorted_stocks[:5]]
        returns: list[float] = []
        for code in top_stocks:
            df_kline = self._get_kline(code, days=period + 10)
            if df_kline.empty or len(df_kline) < period:
                continue
            close = df_kline["收盘"].astype(float)
            ret = (close.iloc[-1] / close.iloc[-period] - 1) * 100
            returns.append(ret)
        if not returns:
            return result
        avg_return = float(np.mean(returns))
        result["total_return"] = round(avg_return, 2)
        result["annualized_return"] = round(avg_return * (252 / period), 2)
        result["win_rate"] = round(sum(1 for r in returns if r > 0) / len(returns) * 100, 2)
        result["trades"] = len(top_stocks)
        all_returns: list[float] = []
        for code in top_stocks:
            df_kline = self._get_kline(code, days=period + 10)
            if df_kline.empty or len(df_kline) < period:
                continue
            close = df_kline["收盘"].astype(float)
            daily_ret = close.pct_change().dropna()
            all_returns.extend(daily_ret.tolist())
        if all_returns:
            arr = np.array(all_returns)
            std = float(arr.std() * np.sqrt(252))
            mean = float(arr.mean() * 252)
            result["sharpe_ratio"] = round(mean / std if std > 0 else 0.0, 4)
            cummax = pd.Series(all_returns).cumsum().cummax()
            drawdown = pd.Series(all_returns).cumsum() - cummax
            result["max_drawdown"] = round(float(drawdown.min()) * 100, 2)
        return result

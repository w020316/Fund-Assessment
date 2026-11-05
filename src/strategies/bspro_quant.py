from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd
import akshare as ak
from loguru import logger

# 阶段四 R-06:akshare 内部 requests 调用默认无超时,上游抖动时会单次挂起数十秒。
# 统一给支持 timeout 参数的接口传显式上限,失败快速返回而不是无限等待。
AK_TIMEOUT_SECONDS = 8.0
# 因子并发计算的整体预算:到点即用已完成部分,避免个别慢请求拖垮整次回测。
FACTOR_BUDGET_SECONDS = 20.0


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
        # 阶段四 R-06:回测路径可跳过"全市场快照"这类分钟级调用(pcf 成本高、收益小),
        # 其它调用方(单票因子查询)行为保持不变。
        self.skip_heavy_spot = False

    def _get_kline(self, stock_code: str, days: int = 250) -> pd.DataFrame:
        if stock_code in self._kline_cache:
            return self._kline_cache[stock_code]
        try:
            df = ak.stock_zh_a_hist(
                symbol=stock_code, period="daily", adjust="qfq",
                timeout=AK_TIMEOUT_SECONDS,
            )
            if df is not None and not df.empty:
                df = df.tail(days).reset_index(drop=True)
                self._kline_cache[stock_code] = df
                return df
        except Exception as e:
            logger.warning(f"获取K线数据 failed: {e}")
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
        except Exception as e:
            logger.warning(f"获取估值指标 failed: {e}")
        # 阶段四 R-06:原实现在这里对每只股票调用一次 ak.stock_zh_a_spot_em(),
        # 该接口内部按页拉取全市场快照(实测 59 页、约 70s),30 只股票会放大到
        # 不可用级别(回测恒超时)。回测路径(skip_heavy_spot=True)跳过该调用,
        # 单票因子查询保持原行为。
        if not self.skip_heavy_spot:
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
                    except Exception as e:
                        logger.warning(f"获取市值数据 failed: {e}")
            except Exception as e:
                logger.warning(f"获取现金流数据 failed: {e}")
        try:
            df_val = ak.stock_a_indicator_lg(symbol=stock_code)
            if df_val is not None and not df_val.empty:
                latest = df_val.iloc[-1]
                result["pe_ttm"] = float(latest.get("pe_ttm", result.get("pe", 0)))
        except Exception as e:
            logger.warning(f"获取TTM估值指标 failed: {e}")
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
        except Exception as e:
            logger.warning(f"获取财务摘要 failed: {e}")
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
        except Exception as e:
            logger.warning(f"获取财务分析指标 failed: {e}")
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

    def compute_factors(self, stock_code: str, categories: set[str] | None = None) -> dict:
        """计算因子。

        categories=None 时计算全部五类(原有行为);回测只传组合用到的类别,
        避免为"不参与打分"的因子支付网络与计算成本(阶段四 R-06)。
        """
        need = categories or {c.value for c in FactorCategory}
        value = self._compute_value_factors(stock_code) if FactorCategory.VALUE.value in need else {}
        growth = self._compute_growth_factors(stock_code) if FactorCategory.GROWTH.value in need else {}
        quality = self._compute_quality_factors(stock_code) if FactorCategory.QUALITY.value in need else {}
        momentum = self._compute_momentum_factors(stock_code) if FactorCategory.MOMENTUM.value in need else {}
        volatility = self._compute_volatility_factors(stock_code) if FactorCategory.VOLATILITY.value in need else {}
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
        """基于当前因子的假设性回测(非真正历史回测)。

        警告: 因子值为当前快照,无法回溯历史时点的因子值,
        因此本回测存在"未来函数"偏差,结果仅供因子筛选参考,
        不构成真实历史业绩。正确的历史回测需引入因子历史数据库。

        修复点(2026-07-29):
        1. 修复收益序列拼接错误:原代码把多只股票日线收益拼接成一条曲线算回撤,数值无意义
           → 改为按等权组合计算每日组合收益,再算累计收益与回撤
        2. 添加未来函数偏差警告标注
        """
        result: dict = {
            "factor_combo": factor_combo,
            "period_days": period,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "trades": 0,
            "warning": "假设性回测:因子为当前快照,存在未来函数偏差,结果仅供参考",
        }
        # 阶段四 R-06:候选池不再用 ak.stock_zh_a_spot_em()。
        # 实测该接口分 59 页拉全市场快照、约 70s(超过端点 45s 上限,回测恒失败);
        # 改用 data_source_v2 的东财榜单(1 次请求,实测 1.2s,失败时自动回退新浪)。
        try:
            from src.core.data_source_v2 import get_stock_ranking_em
            ranking = get_stock_ranking_em(sort_field="f3", sort_order=0, count=30)
            stock_pool = [str(s.get("code", "")).zfill(6) for s in (ranking or []) if s.get("code")][:15]
        except Exception as e:
            logger.warning(f"回测候选池获取失败: {e}")
            stock_pool = []
        if not stock_pool:
            # 明确失败,由路由层转成可读的 503,不再静默返回全零
            result["error"] = "DATA_SOURCE_UNAVAILABLE"
            result["error_message"] = "候选股票池获取失败:行情数据源暂不可用"
            return result

        # 候选池 15 只、4 线程并发:实测 12~18s,30s 上限留有余量
        # (上游抖动时会更快触发超时并返回可读 503,而不是无限挂起)。
        self.skip_heavy_spot = True
        # 只计算组合里真正用到的因子类别(省掉不参与打分的一类财务接口)
        need_categories: set[str] = set()
        for factor_name in factor_combo:
            factor_def = next((f for f in FACTOR_LIBRARY if f.name == factor_name), None)
            if factor_def:
                need_categories.add(factor_def.category.value)

        def _factor_of(code: str) -> tuple[str, dict]:
            try:
                return code, self.compute_factors(code, need_categories).get("factors", {})
            except Exception as e:
                logger.warning(f"计算 {code} 因子失败: {e}")
                return code, {}

        pool = ThreadPoolExecutor(max_workers=4)
        futures = {pool.submit(_factor_of, c): c for c in stock_pool}
        factor_map: dict[str, dict] = {}
        deadline = time.monotonic() + FACTOR_BUDGET_SECONDS
        try:
            for fut in as_completed(futures, timeout=max(0.0, deadline - time.monotonic())):
                code, factors = fut.result()
                factor_map[code] = factors
        except FuturesTimeout:
            logger.warning(
                f"回测因子计算超过 {FACTOR_BUDGET_SECONDS}s 预算,"
                f"已得 {len(factor_map)}/{len(stock_pool)} 只,使用已完成部分"
            )
        finally:
            # 不等待未完成的线程(cancel 只能取消未开始任务),保证接口按预算返回
            pool.shutdown(wait=False, cancel_futures=True)

        if len(factor_map) < 3:
            result["error"] = "DATA_SOURCE_UNAVAILABLE"
            result["error_message"] = "因子计算超时:行情数据源响应过慢"
            return result
        if len(factor_map) < len(stock_pool):
            result["partial"] = True
            result["warning"] += "(部分股票因子未在预算内完成)"

        scores: dict[str, float] = {}
        for code in stock_pool:
            factors = factor_map.get(code, {})
            score = 0.0
            for factor_name, weight in factor_combo.items():
                val = factors.get(factor_name, 0.0)
                factor_def = next((f for f in FACTOR_LIBRARY if f.name == factor_name), None)
                if factor_def and not factor_def.higher_is_better:
                    val = -val
                score += val * weight
            scores[code] = score
        sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        # 只用已完成因子的股票(预算内未完成的股票分数是默认 0,不能当"低分"入选,
        # 也会额外触发一轮 K 线请求)
        top_stocks = [s[0] for s in sorted_stocks if s[0] in factor_map][:5]

        # 收集每只股票最近 period 天的日线数据(对齐日期)
        stock_closes: dict[str, pd.Series] = {}
        for code in top_stocks:
            df_kline = self._get_kline(code, days=period + 10)
            if df_kline.empty or len(df_kline) < period:
                continue
            close = df_kline["收盘"].astype(float)
            # 只取最近 period 天
            stock_closes[code] = close.iloc[-period:].reset_index(drop=True)

        if not stock_closes:
            return result

        # 计算每只股票的区间收益(用于 win_rate)
        returns: list[float] = []
        for code, close in stock_closes.items():
            ret = (close.iloc[-1] / close.iloc[0] - 1) * 100
            returns.append(ret)

        avg_return = float(np.mean(returns))
        result["total_return"] = round(avg_return, 2)
        result["annualized_return"] = round(avg_return * (252 / period), 2)
        result["win_rate"] = round(sum(1 for r in returns if r > 0) / len(returns) * 100, 2)
        result["trades"] = len(stock_closes)

        # 修复:按等权组合计算每日组合收益(而非拼接所有股票的日线收益)
        # 组合每日收益 = 各股票当日收益的等权平均
        daily_returns_by_stock = []
        for code, close in stock_closes.items():
            daily_ret = close.pct_change().dropna()
            daily_returns_by_stock.append(daily_ret)

        if not daily_returns_by_stock:
            return result

        # 对齐各股票的日线收益(按日期索引对齐),取等权平均
        combined = pd.concat(daily_returns_by_stock, axis=1)
        portfolio_daily_ret = combined.mean(axis=1).dropna()  # 等权组合

        if len(portfolio_daily_ret) > 0:
            # 年化波动率
            std = float(portfolio_daily_ret.std() * np.sqrt(252))
            # 年化收益
            mean = float(portfolio_daily_ret.mean() * 252)
            result["sharpe_ratio"] = round(mean / std if std > 0 else 0.0, 4)
            # 组合累计收益与回撤(基于组合收益,而非拼接序列)
            cum_returns = (1 + portfolio_daily_ret).cumprod()
            cummax = cum_returns.cummax()
            drawdown = (cum_returns - cummax) / cummax
            result["max_drawdown"] = round(float(drawdown.min()) * 100, 2)

        return result

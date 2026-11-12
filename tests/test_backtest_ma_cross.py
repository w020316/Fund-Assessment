"""ma_cross 金叉死叉策略 / IR 信息比率 / optimize_ma_cross 参数网格 测试。

对应视频"量化对轰"链路补全的三项能力:
- STEP 09/12: MA5>MA20 满仓持有 / MA5<MA20 空仓观望
- IR = E[Rp-Rb] / σ(Rp-Rb) 年化,基准为买入持有
- STEP 15: MA_short × MA_long 参数网格优化(按 IR 排序)
"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.core.backtest import (
    BacktestEngine,
    BacktestResult,
    _analyze_plateau,
    _calc_information_ratio,
    make_ma_cross_strategy,
    ma_cross_strategy,
)


def _make_kline(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "日期": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
        "开盘": [c - 0.1 for c in closes],
        "最高": [c + 0.5 for c in closes],
        "最低": [c - 0.5 for c in closes],
        "收盘": closes,
        "成交量": [10000] * n,
    })


def _mock_dm(df: pd.DataFrame) -> MagicMock:
    mock_dm = MagicMock()
    mock_kline = MagicMock()
    mock_kline.data = df
    mock_dm.get_history_kline.return_value = mock_kline
    return mock_dm


def _run_strategy(closes: list[float], strategy=ma_cross_strategy):
    """逐日喂数据跑策略并按引擎规则执行信号,返回 (信号列表, 最终 portfolio)。"""
    engine = BacktestEngine.__new__(BacktestEngine)
    df = _make_kline(closes)
    portfolio = {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0, "symbol": "600519"}
    signals = []
    for idx in range(len(df)):
        row = df.iloc[idx]
        current_date = pd.Timestamp(row["日期"]).date()
        current_price = float(row["收盘"])
        for sym in portfolio["positions"]:
            portfolio["positions"][sym]["current_price"] = current_price
        day_signals = strategy(current_date, portfolio, df.iloc[:idx + 1].copy())
        signals.extend(day_signals)
        for sig in day_signals:
            engine._execute_signal(sig, portfolio, [], current_date)
    return signals, portfolio


# ===== IR 信息比率 =====


class TestCalcInformationRatio:
    def test_aligned_positive_excess(self):
        """策略超额收益均值为正且超额有波动 → IR 为正"""
        strat = [0.006, 0.009] * 50
        bench = [0.004, 0.006] * 50
        ir = _calc_information_ratio(strat, bench)
        assert ir > 0

    def test_equal_returns_zero(self):
        """策略与基准完全一致 → 超额恒为 0,IR = 0"""
        ret = [0.005, -0.002, 0.003, 0.001] * 10
        assert _calc_information_ratio(ret, list(ret)) == 0.0

    def test_zero_tracking_error_returns_zero(self):
        """策略恒定收益、基准波动 → 跟踪误差>0 仍可算;两者均恒定 → TE=0 → 0.0"""
        assert _calc_information_ratio([0.01] * 50, [0.01] * 50) == 0.0

    def test_mismatched_length_returns_zero(self):
        assert _calc_information_ratio([0.01] * 10, [0.01] * 5) == 0.0

    def test_empty_or_short_returns_zero(self):
        assert _calc_information_ratio([], []) == 0.0
        assert _calc_information_ratio([0.01], [0.01]) == 0.0

    def test_none_benchmark_returns_zero(self):
        assert _calc_information_ratio([0.01] * 10, None) == 0.0

    def test_negative_excess_negative_ir(self):
        """策略每日稳定跑输基准 → IR 为负"""
        strat = [0.0] * 100
        bench = [0.002] * 100
        assert _calc_information_ratio(strat, bench) < 0


class TestIRInMetrics:
    def test_calc_metrics_without_benchmark_defaults_zero(self):
        """不传基准 → information_ratio=0(向后兼容旧调用)"""
        engine = BacktestEngine.__new__(BacktestEngine)
        result = engine._calc_metrics(
            equity_curve=[1_000_000.0, 1_010_000.0, 1_005_000.0],
            trades=[],
            initial_capital=1_000_000.0,
            start_date="2025-01-01",
            end_date="2025-01-03",
            max_drawdown=0.0,
        )
        assert result.information_ratio == 0.0

    def test_calc_metrics_with_benchmark_populates_ir(self):
        engine = BacktestEngine.__new__(BacktestEngine)
        strat_curve = [1_000_000.0, 1_010_000.0, 1_020_000.0]
        bench = [0.005, 0.008]  # 与策略日收益序列(len=2)按日对齐
        result = engine._calc_metrics(
            equity_curve=strat_curve,
            trades=[],
            initial_capital=1_000_000.0,
            start_date="2025-01-01",
            end_date="2025-01-03",
            max_drawdown=0.0,
            benchmark_returns=bench,
        )
        assert result.information_ratio != 0.0

    def test_backtest_result_backward_compatible(self):
        """不传新字段仍可构造(向后兼容)"""
        r = BacktestResult(
            total_return=0.1, annual_return=0.1, max_drawdown=0.05,
            sharpe_ratio=1.0, win_rate=0.5, profit_loss_ratio=1.5,
            trade_count=1, equity_curve=[1.0], trades=[],
        )
        assert r.information_ratio == 0.0


# ===== ma_cross 策略 =====


class TestMaCrossStrategy:
    def test_golden_cross_buys_full_position(self):
        """持续上涨 → MA5>MA20 → 触发满仓买入"""
        # 先跌后涨:让 MA5 上穿 MA20
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 11)]
        signals, portfolio = _run_strategy(closes)
        buys = [s for s in signals if s.side.value == "buy"]
        assert len(buys) >= 1
        assert buys[0].quantity == int(1_000_000.0 / buys[0].price / 100) * 100
        assert "金叉" in buys[0].reason

    def test_death_cross_sells_all(self):
        """买入后持续下跌 → MA5<MA20 → 全仓卖出"""
        # 涨(建仓) → 跌(死叉)
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 11)] \
            + [110.0 - i for i in range(1, 26)]
        signals, portfolio = _run_strategy(closes)
        buys = [s for s in signals if s.side.value == "buy"]
        sells = [s for s in signals if s.side.value == "sell"]
        assert len(buys) >= 1
        assert len(sells) >= 1
        # 卖出数量 = 最后一次实际成交的买入数量(空仓观望)
        # 注:满仓买入可能因佣金超现金被引擎拒单,次日按剩余现金重发,故对比 buys[-1]
        assert sells[0].quantity == buys[-1].quantity
        assert "死叉" in sells[0].reason
        assert not portfolio["positions"]

    def test_insufficient_data_no_signal(self):
        """数据长度 < long_window → 不产生任何信号"""
        closes = [100.0] * 15
        signals, _ = _run_strategy(closes)
        assert signals == []

    def test_downtrend_never_buys(self):
        """单边下跌 → MA5<MA20 恒成立 → 全程空仓"""
        closes = [100.0 - i * 0.5 for i in range(60)]
        signals, portfolio = _run_strategy(closes)
        assert not any(s.side.value == "buy" for s in signals)
        assert not portfolio["positions"]

    def test_factory_custom_windows(self):
        """工厂参数 (3, 10) 生效:10 根K线即可触发,默认(5,20)不能"""
        closes = [100.0] * 10 + [100.0 + i for i in range(1, 6)]
        custom = make_ma_cross_strategy(3, 10)
        signals, _ = _run_strategy(closes, custom)
        assert any(s.side.value == "buy" for s in signals)

        signals_default, _ = _run_strategy(closes, make_ma_cross_strategy())
        assert not any(s.side.value == "buy" for s in signals_default)

    def test_strategy_name_in_signals(self):
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 11)]
        signals, _ = _run_strategy(closes, make_ma_cross_strategy(5, 20, name="ma_cross_custom"))
        assert signals[0].strategy == "ma_cross_custom"


# ===== 引擎集成 / 参数网格 =====


class TestEngineMaCrossIntegration:
    def test_run_returns_information_ratio(self):
        """run() 端到端:结果含 information_ratio 字段"""
        rng = np.random.default_rng(3)
        closes = (100 + np.cumsum(rng.normal(0, 1, 120))).tolist()
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline(closes)))
        result = engine.run(ma_cross_strategy, "600519", "2025-01-01", "2025-06-30")
        assert isinstance(result, BacktestResult)
        assert isinstance(result.information_ratio, float)

    def test_run_all_strategies_includes_ma_cross(self):
        closes = [100.0] * 20 + [100.0 + i for i in range(1, 11)]
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline(closes)))
        results = engine.run_all_strategies("600519", "2025-01-01", "2025-03-31")
        assert "ma_cross" in results

    def test_optimize_ma_cross_grid_sorted_by_ir(self):
        """网格优化:仅 short<long、按 IR 降序、best 为首项"""
        rng = np.random.default_rng(5)
        closes = (100 + np.cumsum(rng.normal(0.05, 1.5, 150))).tolist()
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline(closes)))
        opt = engine.optimize_ma_cross(
            "600519", "2025-01-01", "2025-08-31",
            short_windows=range(3, 7), long_windows=range(10, 26, 5),
        )
        assert opt["stock_code"] == "600519"
        grid = opt["grid"]
        assert grid, "网格不应为空"
        assert all(g["short_window"] < g["long_window"] for g in grid)
        irs = [g["information_ratio"] for g in grid]
        assert irs == sorted(irs, reverse=True)
        assert opt["best"] == grid[0]
        for g in grid:
            for key in ("total_return", "annual_return", "max_drawdown",
                        "sharpe_ratio", "information_ratio", "trade_count"):
                assert isinstance(g[key], (int, float))

    def test_optimize_ma_cross_skips_invalid_pairs(self):
        """short >= long 的组合必须被跳过"""
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline([100.0] * 30)))
        opt = engine.optimize_ma_cross(
            "600519", "2025-01-01", "2025-02-28",
            short_windows=range(5, 8), long_windows=range(3, 6),
        )
        # 5..7 × 3..5 中 short<long 的组合不存在 → 网格为空
        assert opt["grid"] == []
        assert opt["best"] is None

    def test_optimize_ma_cross_no_data(self):
        """取数失败 → 网格为空且不抛异常"""
        mock_dm = MagicMock()
        mock_dm.get_history_kline.side_effect = RuntimeError("upstream down")
        engine = BacktestEngine(data_manager=mock_dm)
        opt = engine.optimize_ma_cross("600519", "2025-01-01", "2025-03-31")
        assert opt["grid"] == []
        assert opt["best"] is None


class TestVectorizedEventEquivalence:
    """立项 2(借鉴 vectorbt): 向量化快路径 vs 事件引擎交叉校验。

    快路径复刻了事件引擎的成交语义(手数/佣金/印花税/买入失败重试),
    残差仅可能来自 MA 求和口径的 1 ulp 级差异导致交叉日偏移 — 因此
    断言用容差而非精确相等, 容差已在多 seed 随机游走下验证充分小。
    """

    @staticmethod
    def _run(seed: int, rows: int = 180) -> tuple[dict, dict]:
        rng = np.random.default_rng(seed)
        closes = (100 + np.cumsum(rng.normal(0.03, 1.2, rows))).tolist()
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline(closes)))
        kw = dict(stock_code="600519", start_date="2025-01-01", end_date="2025-12-31",
                  short_windows=range(3, 9), long_windows=range(10, 31, 5))
        return engine.optimize_ma_cross(**kw), engine.optimize_ma_cross(**kw, use_vectorized=False)

    @staticmethod
    def _by_combo(opt: dict) -> dict:
        return {(g["short_window"], g["long_window"]): g for g in opt["grid"]}

    def test_grid_matches_event_engine(self):
        for seed in (1, 5, 42):
            fast, slow = self._run(seed)
            assert set(self._by_combo(fast)) == set(self._by_combo(slow)), f"seed={seed}"
            for key, f in self._by_combo(fast).items():
                s = self._by_combo(slow)[key]
                assert f["trade_count"] == s["trade_count"], f"seed={seed} combo={key}"
                assert f["total_return"] == pytest.approx(s["total_return"], abs=1e-6)
                assert f["sharpe_ratio"] == pytest.approx(s["sharpe_ratio"], abs=1e-6)
                assert f["information_ratio"] == pytest.approx(s["information_ratio"], abs=1e-6)
                assert f["max_drawdown"] == pytest.approx(s["max_drawdown"], abs=1e-6)

    def test_best_tear_sheet_matches_event_engine(self):
        fast, slow = self._run(7)
        fb, sb = fast["best"], slow["best"]
        assert fb["monthly_returns"] or sb["monthly_returns"]
        assert len(fb["monthly_returns"]) == len(sb["monthly_returns"])
        for fm, sm in zip(fb["monthly_returns"], sb["monthly_returns"]):
            assert fm["month"] == sm["month"]
            assert fm["return_pct"] == pytest.approx(sm["return_pct"], abs=1e-6)
        assert fb["skewness"] == pytest.approx(sb["skewness"], abs=1e-6)
        assert fb["kurtosis"] == pytest.approx(sb["kurtosis"], abs=1e-6)

    def test_fallback_on_unparseable_dates(self):
        """日期含无法解析的行 → 自动回退事件引擎, 网格仍非空且与强制事件路径一致"""
        closes = (100 + np.cumsum(np.random.default_rng(9).normal(0.03, 1.2, 120))).tolist()
        df = _make_kline(closes)
        df.loc[7, "日期"] = "not-a-date"
        engine = BacktestEngine(data_manager=_mock_dm(df))
        kw = dict(stock_code="600519", start_date="2025-01-01", end_date="2025-12-31",
                  short_windows=range(3, 6), long_windows=range(10, 21, 5))
        auto = engine.optimize_ma_cross(**kw)
        forced = engine.optimize_ma_cross(**kw, use_vectorized=False)
        assert auto["grid"], "回退后网格不应为空"
        assert len(auto["grid"]) == len(forced["grid"])
        for a, f in zip(auto["grid"], forced["grid"]):
            assert a["trade_count"] == f["trade_count"]
            assert a["total_return"] == pytest.approx(f["total_return"], abs=1e-9)

    def test_dense_grid_800_plus_combos(self):
        """高密度网格(短窗 2-60 × 长窗 5-200)可完成且结果结构完整"""
        rng = np.random.default_rng(11)
        closes = (100 + np.cumsum(rng.normal(0.03, 1.2, 250))).tolist()
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline(closes)))
        opt = engine.optimize_ma_cross(
            "600519", "2025-01-01", "2025-12-31",
            short_windows=range(2, 62, 2), long_windows=range(4, 204, 4),
        )
        assert len(opt["grid"]) >= 800
        assert opt["best"]["monthly_returns"] is not None


class TestPlateauDetection:
    """网格高原识别(启发式): 正 IR 连通域区分连片高原与孤立尖峰"""

    @staticmethod
    def _grid(ir_by_combo: dict[tuple[int, int], float]) -> list[dict]:
        rows = [
            {"short_window": s, "long_window": lw, "information_ratio": ir}
            for (s, lw), ir in ir_by_combo.items()
        ]
        rows.sort(key=lambda r: r["information_ratio"], reverse=True)
        return rows

    def test_connected_blob_is_plateau(self):
        # 最优(3,10) 与 (4,10)、(3,20) 正 IR 相邻 → 连通域 3 个
        grid = self._grid({
            (3, 10): 1.2, (4, 10): 0.8, (3, 20): 0.5,
            (5, 20): -0.4, (4, 20): -0.2, (5, 10): -0.6,
        })
        plateau = _analyze_plateau(grid)
        assert plateau["best_short"] == 3 and plateau["best_long"] == 10
        assert plateau["component_size"] == 3
        assert plateau["component_ir_mean"] == pytest.approx((1.2 + 0.8 + 0.5) / 3, abs=1e-4)
        assert plateau["grid_positive_count"] == 3

    def test_isolated_spike(self):
        # 最优正 IR, 但所有邻居负 IR → 连通域只有自己
        grid = self._grid({
            (3, 10): 1.5, (4, 10): -0.8, (3, 20): -0.5, (4, 20): -0.3,
        })
        plateau = _analyze_plateau(grid)
        assert plateau["component_size"] == 1
        assert plateau["component_ir_mean"] == pytest.approx(1.5, abs=1e-9)
        assert plateau["grid_positive_count"] == 1

    def test_nonpositive_best_returns_none(self):
        assert _analyze_plateau(self._grid({(3, 10): -0.5, (4, 10): -0.2})) is None
        assert _analyze_plateau([]) is None

    def test_diagonal_is_not_adjacent(self):
        # 仅对角线邻居正 IR 不连通(4-邻接), 连通域仍为 1
        grid = self._grid({(3, 10): 1.0, (4, 20): 0.9, (4, 10): -0.5, (3, 20): -0.5})
        plateau = _analyze_plateau(grid)
        assert plateau["component_size"] == 1
        assert plateau["grid_positive_count"] == 2

    def test_optimize_result_carries_plateau(self):
        closes = (100 + np.cumsum(np.random.default_rng(3).normal(0.05, 1.0, 180))).tolist()
        engine = BacktestEngine(data_manager=_mock_dm(_make_kline(closes)))
        opt = engine.optimize_ma_cross(
            "600519", "2025-01-01", "2025-12-31",
            short_windows=range(3, 9), long_windows=range(10, 31, 5),
        )
        assert "plateau" in opt
        if opt["best"] and opt["best"]["information_ratio"] > 0:
            assert opt["plateau"]["component_size"] >= 1
            assert opt["plateau"]["best_short"] == opt["best"]["short_window"]
        else:
            assert opt["plateau"] is None

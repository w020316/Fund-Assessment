"""回测结果验证:滚动样本外 + 蒙特卡洛

参考 Vibe-Trading "研究 → 回测 → 验证" 的工程思路,补上当前回测只输出
单一收益指标、无法判断"是否过拟合/结果是否稳健"的缺口。

说明:
- 这里的 Walk-Forward 是基于已有收益序列的滚动样本外统计(不重新训练模型),
  用于观察策略表现是否在时间上稳定
- 蒙特卡洛使用有放回重采样,给出收益与回撤分布,避免只看一条回测曲线
"""
from __future__ import annotations

import math
import random


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    position = (len(sorted_values) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _max_drawdown(returns: list[float]) -> float:
    """由逐期收益计算最大回撤(返回 0-1 的正数)"""
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _cumulative_return(returns: list[float]) -> float:
    equity = 1.0
    for r in returns:
        equity *= 1.0 + r
    return equity - 1.0


def _win_rate(returns: list[float]) -> float:
    if not returns:
        return 0.0
    return sum(1 for r in returns if r > 0) / len(returns)


def walk_forward_splits(
    n_samples: int,
    *,
    train_size: int = 60,
    test_size: int = 20,
    step: int | None = None,
) -> list[dict]:
    """生成滚动切分区间(索引半开区间)"""
    if n_samples <= 0 or train_size <= 0 or test_size <= 0:
        return []
    step = test_size if step is None else max(1, int(step))
    splits: list[dict] = []
    start = 0
    while start + train_size + test_size <= n_samples:
        splits.append({
            "train_start": start,
            "train_end": start + train_size,
            "test_start": start + train_size,
            "test_end": start + train_size + test_size,
        })
        start += step
    return splits


def monte_carlo(
    returns: list[float],
    *,
    runs: int = 1000,
    seed: int | None = 42,
) -> dict:
    """对收益序列做有放回重采样,输出收益/回撤分布"""
    clean = [float(r) for r in returns if isinstance(r, (int, float)) and math.isfinite(float(r))]
    if not clean:
        return {"available": False, "message": "收益序列为空"}
    runs = max(100, min(5000, int(runs or 1000)))
    # 防止长序列 × 高 runs 造成 CPU/内存放大(最多约 500 万次采样)
    if len(clean) * runs > 5_000_000:
        runs = max(100, 5_000_000 // len(clean))
    rng = random.Random(seed)

    final_returns: list[float] = []
    drawdowns: list[float] = []
    win_rates: list[float] = []
    for _ in range(runs):
        sample = [clean[rng.randrange(len(clean))] for _ in range(len(clean))]
        final_returns.append(_cumulative_return(sample))
        drawdowns.append(_max_drawdown(sample))
        win_rates.append(_win_rate(sample))

    final_sorted = sorted(final_returns)
    dd_sorted = sorted(drawdowns)
    wr_sorted = sorted(win_rates)

    return {
        "available": True,
        "runs": runs,
        "samples": len(clean),
        "final_return": {
            "p5": round(_percentile(final_sorted, 0.05) * 100, 2),
            "p25": round(_percentile(final_sorted, 0.25) * 100, 2),
            "p50": round(_percentile(final_sorted, 0.50) * 100, 2),
            "p75": round(_percentile(final_sorted, 0.75) * 100, 2),
            "p95": round(_percentile(final_sorted, 0.95) * 100, 2),
        },
        "max_drawdown": {
            "p50": round(_percentile(dd_sorted, 0.50) * 100, 2),
            "p95": round(_percentile(dd_sorted, 0.95) * 100, 2),
        },
        "win_rate": {
            "p50": round(_percentile(wr_sorted, 0.50) * 100, 2),
        },
        "prob_profit": round(sum(1 for r in final_returns if r > 0) / runs, 4),
    }


def validate_returns(
    returns: list[float],
    *,
    train_size: int = 60,
    test_size: int = 20,
    step: int | None = None,
    runs: int = 1000,
    seed: int | None = 42,
) -> dict:
    """滚动样本外统计 + 蒙特卡洛,返回可直接展示的验证报告"""
    clean = [float(r) for r in returns if isinstance(r, (int, float)) and math.isfinite(float(r))]
    if not clean:
        return {"available": False, "message": "收益序列为空,无法验证"}

    splits = walk_forward_splits(
        len(clean), train_size=train_size, test_size=test_size, step=step
    )
    windows = []
    for split in splits:
        test_slice = clean[split["test_start"]:split["test_end"]]
        if not test_slice:
            continue
        windows.append({
            "test_start": split["test_start"],
            "test_end": split["test_end"],
            "test_return_pct": round(_cumulative_return(test_slice) * 100, 2),
            "win_rate_pct": round(_win_rate(test_slice) * 100, 1),
        })

    positive_ratio = (
        round(sum(1 for w in windows if w["test_return_pct"] > 0) / len(windows), 4)
        if windows else 0.0
    )
    if windows:
        avg_return = round(sum(w["test_return_pct"] for w in windows) / len(windows), 2)
        stability = "稳定" if positive_ratio >= 0.7 else ("一般" if positive_ratio >= 0.5 else "不稳定")
    else:
        avg_return = 0.0
        stability = "样本不足"

    mc = monte_carlo(clean, runs=runs, seed=seed)

    notes = [
        "Walk-Forward 为已有收益序列的滚动样本外统计,不含模型重新训练",
        "蒙特卡洛为有放回重采样,p5 可视为保守情形、p95 为乐观情形",
        "回测表现不代表未来收益,实盘需叠加交易成本与滑点",
    ]
    if len(clean) < train_size + test_size:
        notes.append(f"样本数 {len(clean)} 小于训练+测试窗口,Walk-Forward 未生成窗口")

    return {
        "available": True,
        "samples": len(clean),
        "walk_forward": {
            "train_size": train_size,
            "test_size": test_size,
            "window_count": len(windows),
            "windows": windows,
            "positive_ratio": positive_ratio,
            "avg_test_return_pct": avg_return,
            "stability": stability,
        },
        "monte_carlo": mc,
        "notes": notes,
    }

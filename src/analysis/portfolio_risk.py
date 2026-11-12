"""组合风险指标计算(阶段六批次 A,T-A2)—— 纯 numpy,零副作用。

统一口径(架构文档 §5,主理人拍板):
- 无风险利率 rf = 2.0%(模块级常量,未来可配置)
- 年化交易日 244(A 股)
- 年化收益 = (nav_end/nav_start)^(244/n) − 1,n 为间隔交易日数
- 最大回撤 MDD = max(1 − nav_t / max(nav_0..t))
- 年化波动 = std(daily_ret, ddof=1) × √244
- 夏普 = (年化收益 − rf) / 年化波动;Sortino 用下行波动(仅负收益日 std × √244)
- 卡玛 = 年化收益 / |MDD|
- 序列样本上限 250 点(内存硬预算,Render Free 512MB)
- 组合合成: 当前份额静态加权(method="static-shares",忽略申赎现金流)
"""
from __future__ import annotations

from typing import Any

import numpy as np

# ===== 统一口径常量(集中定义,未来可配置) =====
RISK_FREE_RATE: float = 0.02      # 无风险利率(年化)
TRADING_DAYS_PER_YEAR: int = 244  # A 股年交易日
MAX_SERIES_POINTS: int = 250      # 单序列样本上限(内存硬预算)

_WINDOW_PERIOD_MAP: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "6m": "6m",
    "1y": "1y",
    "3y": "3y",
    "max": "all",
}


def resolve_period(window: str) -> str:
    """窗口参数 → 历史接口 period;非法窗口抛 ValueError(路由转 422/降级)。"""
    period = _WINDOW_PERIOD_MAP.get((window or "").strip().lower())
    if period is None:
        raise ValueError(f"非法窗口: {window!r},支持 {sorted(_WINDOW_PERIOD_MAP)}")
    return period


def _clean_series(nav_series: list[float] | np.ndarray) -> np.ndarray:
    """清洗净值序列:转 float64、剔除非正值与 NaN,截取最近 MAX_SERIES_POINTS 点。"""
    arr = np.asarray(nav_series, dtype=np.float64)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size > MAX_SERIES_POINTS:
        arr = arr[-MAX_SERIES_POINTS:]
    return arr


def compute_risk_metrics(nav_series: list[float] | np.ndarray) -> dict[str, Any]:
    """计算单序列风险指标。

    Args:
        nav_series: 日频累计净值(复权),按时间**升序**。

    Returns:
        dict(total_return, annual_return, max_drawdown, annual_vol, sharpe,
             sortino, calmar, nav_points)。样本不足(<2 点)时各比率返回 None,
        nav_points 如实返回——不编数据,由调用方标注可信度。

    手算对拍样例(见 tests/test_portfolio_risk.py):
        nav = [1.0, 1.1, 1.21, 0.968] → MDD = 1 − 0.968/1.21 = 0.20
    """
    arr = _clean_series(nav_series)
    n = arr.size
    result: dict[str, Any] = {
        "total_return": None,
        "annual_return": None,
        "max_drawdown": None,
        "annual_vol": None,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "nav_points": int(n),
    }
    if n < 2:
        return result  # 样本不足,显式 None(fail-closed,不撒谎)

    total_return = float(arr[-1] / arr[0] - 1.0)
    # 年化收益:间隔交易日数 n-1(日频序列)
    periods = float(n - 1)
    annual_return = float(arr[-1] / arr[0]) ** (TRADING_DAYS_PER_YEAR / periods) - 1.0

    daily_ret = np.diff(arr) / arr[:-1]
    # 仅 1 个日收益时 ddof=1 的样本标准差为 NaN,JSON 序列化会直接 500 → 按 0 处理
    annual_vol = (
        float(np.std(daily_ret, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if daily_ret.size >= 2 else 0.0
    )

    # 最大回撤:相对历史新高的最大跌幅
    running_max = np.maximum.accumulate(arr)
    max_drawdown = float(np.max(1.0 - arr / running_max))

    sharpe = float((annual_return - RISK_FREE_RATE) / annual_vol) if annual_vol > 0 else None

    # Sortino:下行波动 = 仅负收益日的样本标准差 × √244(负收益日 <2 时视为 0)
    downside = daily_ret[daily_ret < 0]
    downside_vol = float(np.std(downside, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if downside.size >= 2 else 0.0
    sortino = float((annual_return - RISK_FREE_RATE) / downside_vol) if downside_vol > 0 else None

    calmar = float(annual_return / abs(max_drawdown)) if max_drawdown > 0 else None

    result.update({
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "annual_vol": round(annual_vol, 6),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "sortino": round(sortino, 4) if sortino is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
        "nav_points": int(n),
    })
    return result


def combine_portfolio_nav(
    series_map: dict[str, list[tuple[str, float]]],
    shares_map: dict[str, float],
) -> list[tuple[str, float]]:
    """按当前份额静态加权,合成组合净值序列(method="static-shares")。

    假设(架构文档 §3.4): 以当前份额固定合成,忽略申赎现金流影响;
    对齐口径: 取各基金日期的**交集**(缺日期的基金不参与该日加权,
    避免静默用 0 填充造成净值跳变),组合净值 = Σ shares_i × nav_i,t。

    Args:
        series_map: {fund_code: [(date_str, acc_nav), ...] 升序}
        shares_map: {fund_code: 持有份额}

    Returns:
        [(date, portfolio_value), ...] 升序;无法合成时返回 []。
    """
    if not series_map:
        return []
    valid = {
        code: [(d, float(v)) for d, v in seq if float(v) > 0]
        for code, seq in series_map.items()
        if _safe_shares(shares_map.get(code)) > 0 and seq
    }
    valid = {c: s for c, s in valid.items() if s}
    if not valid:
        return []
    # 日期交集
    common: set[str] | None = None
    for seq in valid.values():
        dates = {d for d, _ in seq}
        common = dates if common is None else (common & dates)
    if not common:  # type: ignore[operator]
        return []
    nav_lookup = {code: dict(seq) for code, seq in valid.items()}
    out: list[tuple[str, float]] = []
    for date in sorted(common):  # type: ignore[arg-type]
        total = sum(
            _safe_shares(shares_map.get(code)) * nav_lookup[code][date]
            for code in valid
        )
        out.append((date, round(total, 6)))
    return out


def _safe_shares(val: Any) -> float:
    """份额安全转换(负数/非数按 0 处理)。"""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 and np.isfinite(v) else 0.0


# ===== 分散度诊断(2026-11-12,范围裁决后量化向) =====

MIN_CORR_OVERLAP_RETURNS: int = 21  # 相关性最少重叠日收益数(约一个月),不足按缺失


def compute_correlation(series_map: dict[str, list[tuple[str, float]]]) -> dict[str, Any]:
    """持仓间相关性: 日期交集上的日收益 Pearson 相关矩阵。

    与 combine_portfolio_nav 同一交集口径(缺日期不静默填 0);交集日收益
    < MIN_CORR_OVERLAP_RETURNS 点,或某基金零方差(如货基) → 该基金对为
    None(不编相关系数)。对称矩阵,对角线恒 1.0(自身)。
    """
    valid = {
        code: [(d, float(v)) for d, v in seq if float(v) > 0]
        for code, seq in series_map.items() if seq
    }
    codes = sorted(valid)
    n = len(codes)
    out: dict[str, Any] = {
        "codes": codes,
        "matrix": [[1.0 if i == j else None for j in range(n)] for i in range(n)],
        "avg_correlation": None,
        "overlap_points": 0,
        "valid_pairs": 0,
    }
    if n < 2:
        return out
    common: set[str] | None = None
    for seq in valid.values():
        dates = {d for d, _ in seq}
        common = dates if common is None else (common & dates)
    if not common:  # type: ignore[operator]
        return out
    lookup = {code: dict(seq) for code, seq in valid.items()}
    dates_sorted = sorted(common)  # type: ignore[arg-type]
    rets: dict[str, np.ndarray] = {}
    for code in codes:
        arr = np.array([lookup[code][d] for d in dates_sorted], dtype=np.float64)
        r = np.diff(arr) / arr[:-1]
        if r.size >= MIN_CORR_OVERLAP_RETURNS and np.std(r, ddof=1) > 0:
            rets[code] = r
    out["overlap_points"] = int(next(iter(rets.values())).size) if rets else 0
    pair_vals: list[float] = []
    for i, a in enumerate(codes):
        for j in range(i + 1, n):
            b = codes[j]
            if a in rets and b in rets:
                corr = float(np.corrcoef(rets[a], rets[b])[0, 1])
                if np.isfinite(corr):
                    out["matrix"][i][j] = out["matrix"][j][i] = round(corr, 4)
                    pair_vals.append(corr)
    out["valid_pairs"] = len(pair_vals)
    out["avg_correlation"] = round(sum(pair_vals) / len(pair_vals), 4) if pair_vals else None
    return out


def compute_concentration(
    shares_map: dict[str, float], last_nav_map: dict[str, float],
) -> dict[str, Any]:
    """权重集中度: HHI 与有效持仓数(=1/HHI,赫芬达尔口径)。

    权重按市值(份额 × 最新净值);市值合计 ≤0 或净值缺失 → None(缺失显示缺失)。
    effective_holdings 是"若各持仓等权,多少只等权持仓的 HHI 与当前相同"。
    """
    mv: dict[str, float] = {}
    for code, nav in last_nav_map.items():
        shares = _safe_shares(shares_map.get(code))
        try:
            nav_f = float(nav)
        except (TypeError, ValueError):
            continue
        if shares > 0 and nav_f > 0 and np.isfinite(nav_f):
            mv[code] = shares * nav_f
    total = sum(mv.values())
    if total <= 0:
        return {"weights": {}, "hhi": None, "effective_holdings": None}
    weights = {code: mv[code] / total for code in mv}
    hhi = float(sum(w * w for w in weights.values()))
    return {
        "weights": {code: round(w, 6) for code, w in weights.items()},
        "hhi": round(hhi, 4),
        "effective_holdings": round(1.0 / hhi, 2) if hhi > 0 else None,
    }

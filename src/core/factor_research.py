"""仅使用上传 CSV 的 Alpha144 因子研究诊断，不执行交易或模型训练。"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from numbers import Integral, Real

import numpy as np
import pandas as pd


_REQUIRED = (
    "date", "stock_code", "open", "close", "amount", "adj_factor",
    "is_st", "listing_days",
)
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_CODE = re.compile(r"[0-9]{6}\Z")


def _integer(name: str, value, low: int, high: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError(f"参数 {name} 必须是 {low} 到 {high} 的整数")
    if not low <= value <= high:
        raise ValueError(f"参数 {name} 必须在 {low} 到 {high} 之间")
    return int(value)


def _real(name: str, value, low: float, high: float) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"参数 {name} 必须是有限数值")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"参数 {name} 必须是有限数值") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"参数 {name} 必须在 {low} 到 {high} 之间且为有限数值")
    return result


def _numeric(text: str, field: str, row: int) -> float:
    text = text.strip()
    if not text:
        return float("nan")
    prefix = f"CSV 第 {row} 行字段 {field}"
    if not _NUMBER.fullmatch(text):
        raise ValueError(f"{prefix} 必须是有限数值或留空，不能使用非法字符串")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{prefix} 不允许 NaN 或无穷等非有限数值")
    try:
        exact = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{prefix} 不是合法的有限数值") from None
    if field == "amount" and exact < 0:
        raise ValueError(f"{prefix} 成交金额不能为负数")
    if field == "is_st" and exact not in (0, 1):
        raise ValueError(f"{prefix} 只能是 0、1 或留空")
    if field == "listing_days" and (exact < 0 or exact != exact.to_integral_value()):
        raise ValueError(f"{prefix} 上市交易日数必须是非负整数或留空")
    if field in ("open", "close", "adj_factor", "market_cap") and value <= 0:
        return float("nan")
    return value


def _read_csv(csv_data: str) -> pd.DataFrame:
    if not isinstance(csv_data, str):
        raise ValueError("CSV 必须是 UTF-8 解码后的文本")
    if len(csv_data) > 2_000_000:
        raise ValueError("CSV 字符数不能超过 2000000")
    csv_data = csv_data.removeprefix("\ufeff")
    if not csv_data.strip():
        raise ValueError("CSV 数据不能为空")
    reader = csv.reader(io.StringIO(csv_data, newline=""), strict=True)
    records = []
    dates, stocks, keys = set(), set(), set()
    try:
        header = next(reader)
        if len(set(header)) != len(header):
            raise ValueError("CSV 存在重复表头")
        missing = set(_REQUIRED) - set(header)
        if missing:
            raise ValueError("CSV 缺少必需列：" + "、".join(sorted(missing)))
        unknown = set(header) - set(_REQUIRED) - {"market_cap"}
        if unknown:
            raise ValueError("CSV 包含未知字段：" + "、".join(sorted(unknown)))
        for values in reader:
            line = reader.line_num
            if len(records) >= 20_000:
                raise ValueError("CSV 数据行数不能超过 20000")
            if len(values) != len(header):
                raise ValueError(f"CSV 第 {line} 行列数不齐，须与表头一致")
            record = dict(zip(header, values))
            day, code = record["date"], record["stock_code"]
            if not _DATE.fullmatch(day):
                raise ValueError(f"CSV 第 {line} 行 date 必须严格使用 YYYY-MM-DD 日期")
            if day not in dates:
                try:
                    date.fromisoformat(day)
                except ValueError:
                    raise ValueError(f"CSV 第 {line} 行 date 不是合法日期") from None
            if not _CODE.fullmatch(code):
                raise ValueError(f"CSV 第 {line} 行 stock_code 必须是保留前导零的六位数字字符")
            if (day, code) in keys:
                raise ValueError(f"CSV 存在重复 date/stock_code：{day}/{code}")
            dates.add(day)
            stocks.add(code)
            keys.add((day, code))
            if len(stocks) > 200:
                raise ValueError("CSV 股票数不能超过 200")
            if len(dates) > 1000:
                raise ValueError("CSV 日期数不能超过 1000")
            if len(dates) * len(stocks) > 100_000:
                raise ValueError("CSV 日期数 × 股票数的面板大小不能超过 100000")
            for field in header:
                if field not in ("date", "stock_code"):
                    record[field] = _numeric(record[field], field, line)
            records.append(record)
    except csv.Error as exc:
        raise ValueError(f"CSV 格式错误：{exc}") from None
    if not records:
        raise ValueError("CSV 没有数据行，数据不能为空")
    return pd.DataFrame.from_records(records, columns=header)


def _finite(frame):
    return frame.where(np.isfinite(frame))


def _positive(frame):
    return frame.where(np.isfinite(frame) & (frame > 0))


def alpha144(close: pd.DataFrame, amount: pd.DataFrame, window: int) -> pd.DataFrame:
    """close 为已复权价格；每个窗口要求全部收益及正成交金额有效。"""
    window = _integer("window", window, 2, 120)
    close = _positive(close)
    amount = _positive(amount.reindex(index=close.index, columns=close.columns))
    with np.errstate(divide="ignore", invalid="ignore", over="ignore", under="ignore"):
        returns = _finite(close / close.shift(1) - 1)
        valid = returns.notna() & amount.notna()
        loss = _finite((-returns).clip(lower=0) / amount).where(valid)
        down = (returns < 0).astype(float).where(valid)
        numerator = loss.rolling(window, min_periods=window).sum()
        denominator = down.rolling(window, min_periods=window).sum()
        return _finite(numerator / denominator.where(denominator > 0))


def _number(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _mean(values) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(value)]
    if not clean:
        return None
    scale = max(abs(value) for value in clean)
    return float(np.mean(np.asarray(clean) / scale) * scale) if scale else 0.0


def _pearson(x: pd.Series, y: pd.Series, minimum: int = 2) -> float | None:
    pair = pd.concat([x, y], axis=1)
    pair = pair.loc[np.isfinite(pair).all(axis=1)]
    if len(pair) < minimum:
        return None
    a, b = pair.iloc[:, 0].to_numpy(dtype=float), pair.iloc[:, 1].to_numpy(dtype=float)
    if a.min() == a.max() or b.min() == b.max():
        return None
    a, b = a / np.max(np.abs(a)), b / np.max(np.abs(b))
    a, b = a - a.mean(), b - b.mean()
    result = np.dot(a, b) / math.sqrt(float(np.dot(a, a) * np.dot(b, b)))
    return _number(np.clip(result, -1.0, 1.0))


def _group_ids(score: pd.Series, groups: int) -> pd.Series:
    return np.ceil(score.rank(method="average", pct=True) * groups).astype(int)


def _daily_diagnostics(score: pd.Series, forward: pd.Series, groups: int) -> dict:
    """先用全部 eligible 排组，再对共同有效样本计算相关及组收益。"""
    score = _finite(score).dropna()
    membership = _group_ids(score, groups)
    forward = _finite(forward.reindex(score.index))
    paired = forward.notna()
    x, y = score.loc[paired], forward.loc[paired]
    minimum = max(5, groups)
    ic = _pearson(x, y, minimum)
    rank_ic = _pearson(x.rank(method="average"), y.rank(method="average"), minimum)
    returns, counts = [], []
    for group in range(1, groups + 1):
        selected = forward.loc[membership == group].dropna()
        mean = _mean(selected)
        returns.append(_number(mean * 100) if mean is not None else None)
        counts.append(int(len(selected)))
    return {
        "samples": int(paired.sum()), "ic": ic, "rank_ic": rank_ic,
        "group_returns_pct": returns, "group_counts": counts,
    }


def _summary(daily: list[dict], groups: int) -> dict:
    ranks = [row["rank_ic"] for row in daily if row["rank_ic"] is not None]
    mean = _mean(ranks)
    std = _number(np.std(ranks, ddof=1)) if len(ranks) >= 2 else None
    group_returns = [_mean(row["group_returns_pct"][i] for row in daily) for i in range(groups)]
    spreads = [
        _number(row["group_returns_pct"][-1] - row["group_returns_pct"][0])
        for row in daily
        if row["group_returns_pct"][0] is not None and row["group_returns_pct"][-1] is not None
    ]
    return {
        "days": len(ranks),
        "ic_mean": _mean(row["ic"] for row in daily),
        "rank_ic_mean": mean,
        "rank_ic_std": std,
        "rank_ic_ir": _number(mean / std) if std is not None and std > 0 else None,
        "rank_ic_positive_ratio": sum(value > 0 for value in ranks) / len(ranks) if ranks else None,
        "group_returns_pct": group_returns,
        "group_counts": [sum(row["group_counts"][i] for row in daily) for i in range(groups)],
        "top_bottom_spread_pct": _mean(spreads),
    }


def research_alpha144(
    csv_data: str, *, window=20, horizon=20, groups=5, min_listing_days=120,
    min_average_amount=5000000.0, direction=1, train_ratio=0.7,
) -> dict:
    """返回可直接 JSON 序列化的因子、时间留出及市值暴露诊断。"""
    window = _integer("window", window, 2, 120)
    horizon = _integer("horizon", horizon, 1, 120)
    groups = _integer("groups", groups, 2, 10)
    min_listing_days = _integer("min_listing_days", min_listing_days, 0, 1000)
    direction = _integer("direction", direction, -1, 1)
    if direction not in (-1, 1):
        raise ValueError("参数 direction 只能是 -1 或 1")
    min_average_amount = _real("min_average_amount", min_average_amount, 0, 1e12)
    train_ratio = _real("train_ratio", train_ratio, 0.5, 0.9)
    parameters = {
        "window": window, "horizon": horizon, "groups": groups,
        "min_listing_days": min_listing_days, "min_average_amount": min_average_amount,
        "direction": direction, "train_ratio": train_ratio,
    }
    frame = _read_csv(csv_data)
    dates, stocks = sorted(frame["date"].unique()), sorted(frame["stock_code"].unique())
    panel = {
        field: frame.pivot(index="date", columns="stock_code", values=field).reindex(index=dates, columns=stocks)
        for field in frame.columns if field not in ("date", "stock_code")
    }
    with np.errstate(divide="ignore", invalid="ignore", over="ignore", under="ignore"):
        close = _positive(panel["close"] * panel["adj_factor"])
        opening = _positive(panel["open"] * panel["adj_factor"])
        factor = alpha144(close, panel["amount"], window)
        forward = _finite(opening.shift(-(horizon + 1)) / opening.shift(-1) - 1)
        average_amount = _finite(panel["amount"].rolling(window, min_periods=window).mean())
    eligible = (
        factor.notna() & opening.notna() & close.notna()
        & panel["is_st"].eq(0) & panel["listing_days"].ge(min_listing_days)
        & average_amount.ge(min_average_amount)
    )
    cutoff = math.floor(len(dates) * train_ratio)
    daily = []
    size_correlations = []
    cap_observations = top_cap_count = top_small_count = 0
    size_ties = False
    for position, day in enumerate(dates):
        end = position + horizon + 1
        if end >= len(dates):
            segment = "unlabelled"
        elif position < cutoff:
            segment = "train" if end < cutoff else "purged"
        else:
            segment = "test"
        score = factor.loc[day, eligible.loc[day]] * direction
        diagnostic = _daily_diagnostics(score, forward.loc[day], groups)
        daily.append({"date": day, "segment": segment, **diagnostic})
        if "market_cap" not in panel:
            continue
        caps = panel["market_cap"].loc[day].reindex(score.index).dropna()
        cap_observations += len(caps)
        if caps.empty:
            continue
        correlation = _pearson(score.loc[caps.index].rank(), caps.rank())
        if correlation is not None:
            size_correlations.append(correlation)
        threshold = caps.quantile(0.2)
        small = caps <= threshold
        if int(small.sum()) > math.ceil(len(caps) * 0.2):
            size_ties = True
        top = _group_ids(score, groups).reindex(caps.index).eq(groups)
        top_cap_count += int(top.sum())
        top_small_count += int((top & small).sum())
    summary = {
        "all": _summary(daily, groups),
        "train": _summary([row for row in daily if row["segment"] == "train"], groups),
        "test": _summary([row for row in daily if row["segment"] == "test"], groups),
    }
    eligible_count = int(eligible.to_numpy().sum())
    paired_count = sum(row["samples"] for row in daily)
    missing_forward = eligible_count - paired_count
    available = summary["all"]["days"] > 0
    notes = [
        "本结果仅供 CSV 因子研究，不是交易回测、净值或收益承诺；不联网、不写持仓、不自动交易。",
        "Alpha144 使用复权收盘收益与原始成交金额；不填充价格、金额或状态，空数值保留缺失。",
        "日期轴严格采用上传日期的升序并集，不把工作日当交易日；完整交易日历由用户负责。",
        "须审查股票池幸存者偏差，提供截至当日的历史 ST、上市交易日数、复权因子和市值，不能用当前状态回填历史。",
        "未扣费用、滑点，未验证涨跌停、停牌及实际可成交性；标签仅为信号次日开盘至再过 horizon 个上传日期的复权开盘收益。",
        "仅按时间留出并剔除跨界标签，未训练模型，不属于模型样本外训练；summary.all 含训练、隔离及测试段，仅供全段描述，不作为检验表现。",
        "horizon 标签可能重叠并存在序列相关；RankIC 标准差使用 ddof=1，IR 未年化且不能当作独立样本显著性。",
        "direction 仅按用户指定改变排序；负 IC 不应无脑丢弃，只能在训练段研究方向，不得用全样本或测试段自动选方向。",
        "分组先用全部 eligible 的平均并列百分位秩再 ceil(rank×groups)，不拆并列值；其后才排除缺失远期收益，空组为 null。",
        "分组收益为各组有效日期等权平均的远期收益百分比，不复利；group_counts 为配对股票日数，价差仅在同日高低组均有效时计算再平均，单位为百分点。",
        "coverage.missing_cells 指日期×股票面板中未上传的股票日格数；daily.samples 为配对数，summary.days 为有效 RankIC 日数，split 天数按日期分段含预热。",
        "市值相关为每日 eligible 且有市值的共同样本重新排名后的相关均值；高分组小市值占比按有市值的最高组股票日统计，底20%采用当日有效市值分位阈值。",
    ]
    if missing_forward:
        notes.append(
            f"警告：eligible 中缺失远期收益 {missing_forward}/{eligible_count} "
            f"（{missing_forward / eligible_count:.2%}），含尾部未观测或开盘价缺失；不补零，需检查缺失选择偏差。"
        )
    if cap_observations == 0:
        notes.append("没有 eligible 样本的有效历史市值，市值暴露不可用，相关及小市值占比为 null，不代表中性。")
    elif cap_observations < eligible_count:
        notes.append("历史市值覆盖不完整，市值诊断只基于已覆盖样本，不能将缺失伪造为中性。")
    if size_ties:
        notes.append("市值底20%阈值存在并列，包含全部阈值并列值，实际小市值样本占比可能扩张超过20%。")
    return {
        "available": available,
        "message": "" if available else f"无有效 RankIC 日：每日须至少 {max(5, groups)} 个配对样本且因子、收益均非常数；请检查预热、缺失及样本长度。",
        "factor": "alpha144", "parameters": parameters,
        "coverage": {
            "rows": len(frame), "dates": len(dates), "stocks": len(stocks),
            "missing_cells": len(dates) * len(stocks) - len(frame),
            "eligible_observations": eligible_count, "paired_observations": paired_count,
            "missing_forward_observations": missing_forward, "ic_days": summary["all"]["days"],
        },
        "split": {
            "cutoff_date": dates[cutoff],
            "train_days": sum(row["segment"] == "train" for row in daily),
            "purged_days": sum(row["segment"] == "purged" for row in daily),
            "test_days": sum(row["segment"] == "test" for row in daily),
            "label_rule": "label_end=t+horizon+1；train 要求 label_end<cutoff；跨 cutoff 标签 purged，尾部不可观测为 unlabelled",
            "method": "purged_time_holdout",
        },
        "summary": summary, "daily": daily,
        "size_exposure": {
            "available": cap_observations > 0, "observations": cap_observations,
            "coverage_ratio": cap_observations / eligible_count if eligible_count else None,
            "rank_correlation": _mean(size_correlations),
            "top_group_small_cap_share": top_small_count / top_cap_count if top_cap_count else None,
        },
        "notes": notes,
    }

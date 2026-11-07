"""策略自进化 · 纯逻辑核心（策略自进化 B1-B5）

职责边界（设计文档 docs/strategy-evolution-design-2026-11-07.md §8）:
- 本模块为纯逻辑层: 代际存储 / 变异器 / 净值择时评估器 / run_evolution 编排;
- **不 import 任何 fastapi/web 模块**（import 闭包由测试断言, 见 tests/test_strategy_evolution.py B7）;
- 不触碰 TradeExecutor / RiskManager, 后台零任务（无定时器/无线程常驻/不产生真实交易信号）;
- 代文件 ≤50KB: 净值序列不落盘（可由缓存重取）, 只落参数与指标。

真源约定: config/strategies.yaml 的 trading_quant 段是参数**真源**;
data/strategy_evolution/ 下 generations/、lineage.json、active.json 是可再生分析产物
（Render 重启丢失可接受, 设计文档 §8 已裁定）。

择时模拟口径说明: 代理分数矩阵基于**历史净值的假设性模拟, 非真实五维数据**
（technical/capital/fundamental/news/sentiment 均为净值可观察量的代理映射,
见设计文档 §1.2, 代码注释与前端详情页均需标注）。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
from loguru import logger

from src.utils.file_io import atomic_write_json, safe_read_json

# ---------- 路径与常量 ----------
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "strategy_evolution"
GENERATIONS_DIR = DATA_DIR / "generations"
LINEAGE_FILE = DATA_DIR / "lineage.json"
ACTIVE_FILE = DATA_DIR / "active.json"
LINEAGE_VERSION = 1
MAX_GENERATIONS = 50        # 代际滚动保留上限(第 51 代写入时清理最旧)
BUDGET_SECONDS = 25.0       # 单次进化预算(time.monotonic 计时, 到点收割)
MIN_FUNDS = 2               # 可用基金数量下限
MIN_SAMPLE_DAYS = 30        # 单只基金净值样本下限
DEFAULT_CANDIDATES = 5      # 每代候选数(1-10 由路由层校验)
NAV_PERIOD = "6m"           # 评估窗: 180 自然日 ≈ 120 交易日
POSITIONS_FILE = Path(__file__).resolve().parents[2] / "web" / "user_fund_positions.json"


class EvolutionDataUnavailable(RuntimeError):
    """净值数据不可用（可用基金 <MIN_FUNDS 或样本 <MIN_SAMPLE_DAYS）。

    路由层转 503 EVOLUTION_DATA_UNAVAILABLE; 铁律: 失败路径零写盘。
    """


# ---------- 时间与摘要工具 ----------

def now_iso() -> str:
    """当前时刻 ISO8601（带本地时区偏移）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_stamp() -> str:
    """文件名/种子用时间戳。"""
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def params_digest(params: dict[str, Any]) -> str:
    """参数摘要（8 位十六进制），用于 lineage 索引比对。"""
    raw = json.dumps(params, ensure_ascii=False, sort_keys=True, default=float)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def ensure_dirs() -> None:
    """建立代际存储目录（幂等）。"""
    GENERATIONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------- lineage / active / generation 读写（原子写 + .bak 回退） ----------

def _empty_lineage() -> dict[str, Any]:
    return {
        "version": LINEAGE_VERSION,
        "updated_at": now_iso(),
        "next_gen_id": 1,
        "generations": [],
    }


def load_lineage() -> dict[str, Any]:
    """读 lineage 索引（损坏走 safe_read_json 的 .bak 回退, 再失败给空谱系）。"""
    data = safe_read_json(LINEAGE_FILE, default=None)
    if not isinstance(data, dict) or not isinstance(data.get("generations"), list):
        return _empty_lineage()
    data.setdefault("next_gen_id", 1)
    return data


def save_lineage(lineage: dict[str, Any]) -> None:
    ensure_dirs()
    lineage["version"] = LINEAGE_VERSION
    lineage["updated_at"] = now_iso()
    atomic_write_json(LINEAGE_FILE, lineage)


def load_active() -> dict[str, Any]:
    """读激活指针; 从未激活时 generation_id=None（当前为出厂参数）。"""
    data = safe_read_json(ACTIVE_FILE, default=None)
    if not isinstance(data, dict):
        return {
            "version": LINEAGE_VERSION,
            "generation_id": None,
            "activated_at": None,
            "activated_by": None,
            "params_snapshot": None,
            "note": "真源为 config/strategies.yaml 的 trading_quant 段;此文件是谱系指针与展示快照",
        }
    return data


def save_active(record: dict[str, Any]) -> None:
    """写激活指针（注意写序铁律: 必须先写 yaml 再写本指针, 见路由层）。"""
    ensure_dirs()
    record["version"] = LINEAGE_VERSION
    atomic_write_json(ACTIVE_FILE, record)


def save_generation(record: dict[str, Any]) -> str:
    """写单代文件（gen_<id>_<ts>.json），返回文件名（相对 generations/）。"""
    ensure_dirs()
    filename = f"gen_{record['generation_id']}_{now_stamp()}.json"
    atomic_write_json(GENERATIONS_DIR / filename, record)
    return filename


def load_generation(gen_id: int) -> dict[str, Any] | None:
    """按代号读单代完整 JSON（经 lineage 索引定位文件）。

    Returns:
        单代记录; 代号不存在返回 None(GEN_NOT_FOUND);
        代号存在但文件损坏/缺失返回 None → 路由层区分转 503 EVOLUTION_CORRUPTED。
    """
    lineage = load_lineage()
    for entry in lineage.get("generations", []):
        if entry.get("generation_id") == int(gen_id):
            data = safe_read_json(GENERATIONS_DIR / str(entry.get("file", "")), default=None)
            if isinstance(data, dict) and data.get("generation_id") == int(gen_id):
                return data
            logger.warning(f"evolution gen {gen_id} file missing/corrupted: {entry.get('file')}")
            return None
    return None


def lineage_entry(lineage: dict[str, Any], gen_id: int) -> dict[str, Any] | None:
    """按代号取 lineage 摘要条目。"""
    for entry in lineage.get("generations", []):
        if entry.get("generation_id") == int(gen_id):
            return entry
    return None


def latest_generation_id(lineage: dict[str, Any]) -> int | None:
    """最新一代代号（lineage 按写入序存储, 取末位）。"""
    gens = lineage.get("generations") or []
    return int(gens[-1]["generation_id"]) if gens else None


def append_lineage(lineage: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """追加代际摘要并执行 50 代滚动清理（被裁剪的最旧代文件同步删除）。"""
    lineage.setdefault("generations", []).append(entry)
    lineage["next_gen_id"] = int(entry["generation_id"]) + 1
    overflow: list[dict[str, Any]] = []
    if len(lineage["generations"]) > MAX_GENERATIONS:
        overflow = lineage["generations"][:-MAX_GENERATIONS]
        lineage["generations"] = lineage["generations"][-MAX_GENERATIONS:]
    save_lineage(lineage)
    for old in overflow:
        try:
            stale = GENERATIONS_DIR / str(old.get("file", ""))
            if stale.exists():
                stale.unlink()
                logger.info(f"evolution pruned oldest generation file: {stale.name}")
        except OSError as e:
            logger.warning(f"evolution prune {old.get('file')} failed: {e}")
    return lineage


# =====================================================================
# B3 · 变异器（P0-3）
# =====================================================================

DIM_KEYS = ("technical", "capital", "fundamental", "news", "sentiment")
SIGNAL_ORDER = ("strong_buy", "buy", "watch", "hold", "sell")
SIGNAL_LIMITS: dict[str, tuple[float, float]] = {
    "strong_buy": (70.0, 95.0),
    "buy": (55.0, 80.0),
    "watch": (40.0, 65.0),
    "hold": (25.0, 50.0),
    "sell": (5.0, 35.0),
}
WEIGHT_MUTATION_SIGMA = 0.05
WEIGHT_CLIP = (0.05, 0.5)
SIGNAL_MUTATION_SIGMA = 3.0
EVAL_WINDOW_DAYS = 120      # 评估窗（交易日上限, 6m 自然日 ≈ 120 交易日）


def _repair_signal_order(signals: dict[str, float]) -> dict[str, float]:
    """强制信号阈值严格递降 strong_buy>buy>watch>hold>sell。

    设计文档 §1.2: 不满足时取相邻中点修复。实现: 先降序排序,
    再对每对违序相邻值取中点上下各让 1.0 拆开, 迭代至严格递降
    （各值限幅后取值域为 [5,95], 上述修复必收敛, 迭代上限为保险丝）。
    """
    vals = sorted((float(signals[k]) for k in SIGNAL_ORDER), reverse=True)
    for _ in range(32):
        violated = False
        for i in range(len(vals) - 1):
            if vals[i] <= vals[i + 1]:
                mid = (vals[i] + vals[i + 1]) / 2.0
                vals[i], vals[i + 1] = mid + 1.0, mid - 1.0
                violated = True
        if not violated:
            break
    return {k: vals[i] for i, k in enumerate(SIGNAL_ORDER)}


def rng_from_seed(seed: str) -> np.random.Generator:
    """字符串种子 → 确定性 Generator（default_rng 仅接受 int, 取 sha256 前 8 字节）。"""
    entropy = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")
    return np.random.default_rng(entropy)


def mutate_params(parent_params: dict[str, Any], rng: np.random.Generator) -> dict[str, Any]:
    """由父代参数变异出一个候选（P0-3）。

    - weights: 每维 +N(0,0.05) → clip [0.05,0.5] → 除以总和归一（和=1±1e-12）;
    - signals: 各阈值 +N(0,3) → 按键限幅 → 强制严格递降修复;
      strong_buy/watch/hold 参与变异改变 sig 分布, 但 P0 模拟仅用 buy/sell
      两执行阈值择时（设计文档 §1.2 已注明, 分级仓位留 P2 接口）。
    """
    w = np.array([float(parent_params["weights"][k]) for k in DIM_KEYS], dtype=float)
    w = np.clip(w + rng.normal(0.0, WEIGHT_MUTATION_SIGMA, size=len(DIM_KEYS)), *WEIGHT_CLIP)
    w = w / w.sum()
    weights = {k: float(x) for k, x in zip(DIM_KEYS, w)}

    raw = {}
    for k in SIGNAL_ORDER:
        lo, hi = SIGNAL_LIMITS[k]
        raw[k] = float(np.clip(float(parent_params["signals"][k]) + rng.normal(0.0, SIGNAL_MUTATION_SIGMA), lo, hi))
    signals = _repair_signal_order(raw)
    return {"weights": weights, "signals": signals}


# =====================================================================
# B4 · 净值择时模拟评估器（P0-4 具体口径, §1.2）
# =====================================================================

TARGET_VOL_DAILY_ANNUALIZED = 0.012   # 目标波动率参考（年化 1.2%/√252 语义见公式, 直接用年化口径）


def _pct_change(nav: np.ndarray) -> np.ndarray:
    ret = np.zeros_like(nav)
    if len(nav) > 1:
        ret[1:] = nav[1:] / nav[:-1] - 1.0
    return ret


def _mom(nav: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros_like(nav)
    if len(nav) > n:
        out[n:] = nav[n:] / nav[:-n] - 1.0
    return out


def _rolling_r2_ln(nav: np.ndarray, window: int = 60, min_periods: int = 30) -> np.ndarray:
    """逐日 60 日窗 ln(nav) 线性回归 R²（代理基本面稳定性）。"""
    ln_nav = np.log(np.clip(nav, 1e-9, None))
    r2 = np.zeros_like(nav)
    for d in range(len(nav)):
        lo = max(0, d - window + 1)
        seg = ln_nav[lo:d + 1]
        if len(seg) < min_periods:
            r2[d] = 0.0
            continue
        t = np.arange(len(seg), dtype=float)
        t_c = t - t.mean()
        var_t = float((t_c ** 2).sum())
        if var_t <= 0:
            r2[d] = 0.0
            continue
        slope = float((t_c * (seg - seg.mean())).sum()) / var_t
        pred = seg.mean() + slope * t_c
        ss_res = float(((seg - pred) ** 2).sum())
        ss_tot = float(((seg - seg.mean()) ** 2).sum())
        r2[d] = 0.0 if ss_tot <= 0 else max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
    return r2


def _breakout_signal(nav: np.ndarray, ref: int = 60, lookback: int = 5) -> np.ndarray:
    """近 lookback 日创 ref 日新高(+)/新低(−) 的净计数（代理事件面）。"""
    sig = np.zeros_like(nav)
    for d in range(len(nav)):
        lo = max(0, d - ref)
        prev = nav[lo:d]
        if len(prev) < ref // 2:
            continue
        hi, low = float(prev.max()), float(prev.min())
        cnt = 0
        for j in range(max(0, d - lookback + 1), d + 1):
            if nav[j] > hi:
                cnt += 1
            elif nav[j] < low:
                cnt -= 1
        sig[d] = cnt
    return sig


def _rolling_vol_annualized(nav: np.ndarray, window: int = 20) -> np.ndarray:
    """逐日 20 日已实现波动（日收益 std × √252 年化）。"""
    ret = _pct_change(nav)
    vol = np.zeros_like(nav)
    for d in range(len(nav)):
        seg = ret[max(0, d - window + 1):d + 1]
        if len(seg) >= 5:
            vol[d] = float(seg.std()) * float(np.sqrt(252.0))
    return vol


def build_proxy_matrix(navs: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """构造代理分数矩阵与日收益矩阵（一次性预计算, 全部候选共享）。

    代理口径, 非真实五维数据（设计文档 §1.2）:
    - technical = clip(50 + 25·tanh(mom5/0.03) + 25·tanh(mom20/0.08), 0, 100)
    - capital   = 50 + 50·tanh((mom5−mom20)/0.05)   （短期相对长期加速度）
    - fundamental = 50 + 50·R²(ln(nav) 60 日线性回归)
    - news      = 50 + 25·tanh(突破净计数/5)         （近5日创60日新高/低净计数）
    - sentiment = 50 + 50·tanh((0.012 − 已实现20日年化波动)/0.08)（低波动加分）

    Returns:
        (proxy, rets, codes): proxy shape (F, D, 5); rets shape (F, D);
        D = 各基金样本长度的最小值（共同窗）。
    """
    codes = list(navs.keys())
    d_common = min(len(v) for v in navs.values())
    trimmed = [np.asarray(navs[c], dtype=float)[-d_common:] for c in codes]
    f_count = len(codes)
    proxy = np.zeros((f_count, d_common, 5), dtype=float)
    for i, nav in enumerate(trimmed):
        mom5 = _mom(nav, 5)
        mom20 = _mom(nav, 20)
        proxy[i, :, 0] = np.clip(50.0 + 25.0 * np.tanh(mom5 / 0.03) + 25.0 * np.tanh(mom20 / 0.08), 0.0, 100.0)
        proxy[i, :, 1] = np.clip(50.0 + 50.0 * np.tanh((mom5 - mom20) / 0.05), 0.0, 100.0)
        proxy[i, :, 2] = np.clip(50.0 + 50.0 * _rolling_r2_ln(nav), 0.0, 100.0)
        proxy[i, :, 3] = np.clip(50.0 + 25.0 * np.tanh(_breakout_signal(nav) / 5.0), 0.0, 100.0)
        proxy[i, :, 4] = np.clip(50.0 + 50.0 * np.tanh((TARGET_VOL_DAILY_ANNUALIZED - _rolling_vol_annualized(nav)) / 0.08), 0.0, 100.0)
    rets = np.vstack([_pct_change(nav) for nav in trimmed])
    return proxy, rets, codes


def evaluate_candidate(
    proxy: np.ndarray,
    rets: np.ndarray,
    weights: dict[str, float],
    signals: dict[str, float],
) -> dict[str, float]:
    """单候选评估: 合成 → buy/sell 两阈值状态机择时 → T+1 错位收益 → 指标/适应度。

    - 持仓状态机: position[d]=1 若 sig[d]≥buy; =0 若 sig[d]≤sell; 否则保持前值
      （首日按 sig 判定, 无前值时默认空仓）。strong_buy/watch/hold 不直接参与
      P0 执行（设计注释: 通过变异改变 sig 分布间接影响结果）;
    - T+1 执行语义: r[d] = position[d-1] × 组合日收益 —— 与真实交易 T+1 同构,
      纯数组运算, **不触碰 TradeExecutor**;
    - win_rate 口径: r[d]>0 占有效收益日（D-1 日, 首日无 T-1 持仓不计）比例;
    - fitness = 0.4·tanh(annual/0.30) + 0.25·tanh(sharpe/2.0) + 0.1·win_rate
                − 0.25·min(1, |max_dd|/0.30)   （PRD 权重, tanh 归一）。
    """
    w = np.array([float(weights[k]) for k in DIM_KEYS], dtype=float)
    composite = np.tensordot(proxy, w, axes=([2], [0]))       # (F, D)
    sig = composite.mean(axis=0)                              # 等权组合信号 (D,)
    d_days = sig.shape[0]
    buy = float(signals["buy"])
    sell = float(signals["sell"])

    position = np.zeros(d_days, dtype=float)
    position[0] = 1.0 if sig[0] >= buy else 0.0
    for d in range(1, d_days):
        if sig[d] >= buy:
            position[d] = 1.0
        elif sig[d] <= sell:
            position[d] = 0.0
        else:
            position[d] = position[d - 1]

    combo_ret = rets.mean(axis=0)                             # 等权 9 只 (D,)
    r = np.zeros(d_days, dtype=float)
    if d_days > 1:
        r[1:] = position[:-1] * combo_ret[1:]                 # T+1 错位
    n_days = max(d_days - 1, 1)

    equity = np.cumprod(1.0 + r)
    total = float(equity[-1] - 1.0)
    annual = (1.0 + total) ** (252.0 / n_days) - 1.0 if total > -1.0 else -1.0
    peak = np.maximum.accumulate(equity)
    max_dd = -float((1.0 - equity / peak).max())
    std = float(r.std())
    sharpe = 0.0 if std <= 1e-12 else float(r.mean()) / std * float(np.sqrt(252.0))
    win_rate = float((r[1:] > 0).sum()) / n_days

    fitness = (
        0.4 * float(np.tanh(annual / 0.30))
        + 0.25 * float(np.tanh(sharpe / 2.0))
        + 0.1 * win_rate
        - 0.25 * min(1.0, abs(max_dd) / 0.30)
    )
    return {
        "annual_return": round(annual, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "fitness": round(fitness, 4),
    }


# =====================================================================
# B5 · run_evolution 编排（预算收割 / 失败零写盘）
# =====================================================================

def _default_nav_loader(code: str, period: str) -> list[dict[str, Any]]:
    """真实净值装载器: 走 fund_nav_source(内部 DataCache 文件缓存 TTL 1h)。"""
    from src.core.fund_nav_source import get_fund_nav_history  # 延迟导入, 避免模块级重依赖

    return get_fund_nav_history(code, period=period)


def load_fund_codes(positions_file: Path | None = None) -> list[str]:
    """读用户持仓基金代码（只读 web/user_fund_positions.json, 不动 fund.py）。"""
    path = positions_file or POSITIONS_FILE
    data = safe_read_json(path, default=None)
    positions = (data or {}).get("positions") or []
    codes: list[str] = []
    for p in positions:
        code = str((p or {}).get("fund_code") or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def load_navs(
    codes: list[str],
    nav_loader: Callable[[str, str], list[dict[str, Any]]] | None = None,
) -> dict[str, np.ndarray]:
    """逐基金取净值序列（整代只取一次, N 候选共享）; 不足下限抛 EvolutionDataUnavailable。"""
    loader = nav_loader or _default_nav_loader
    navs: dict[str, np.ndarray] = {}
    for code in codes:
        try:
            rows = loader(code, NAV_PERIOD) or []
        except Exception as e:
            logger.warning(f"evolution nav fetch {code} failed: {e}")
            rows = []
        vals: list[float] = []
        for row in rows:
            try:
                v = float(row.get("nav") or 0.0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                vals.append(v)
        if len(vals) >= MIN_SAMPLE_DAYS:
            navs[code] = np.asarray(vals[-EVAL_WINDOW_DAYS:], dtype=float)
    if len(navs) < MIN_FUNDS:
        raise EvolutionDataUnavailable(
            f"可用基金净值不足（{len(navs)}<{MIN_FUNDS} 只, 或样本 <{MIN_SAMPLE_DAYS} 日）"
        )
    return navs


def run_evolution(
    candidates: int = DEFAULT_CANDIDATES,
    budget_seconds: float = BUDGET_SECONDS,
    nav_loader: Callable[[str, str], list[dict[str, Any]]] | None = None,
    fund_codes: list[str] | None = None,
) -> dict[str, Any]:
    """一次进化编排（线程池内同步执行, 路由层用 wait_for(…, 25) 包裹）。

    流程（设计文档 §5）: 父代参数（active.json → 无则 yaml 现值/出厂常量）
    → 读持仓基金码 → 取净值（共享）→ 代理矩阵 → 父代基线重评 → 逐候选
    预算检查/变异/评估 → Top-1 vs 父代: 更优才写盘, 否则零写盘。

    fund_codes 显式注入用于测试; 缺省读 web/user_fund_positions.json。

    铁律: 任一步骤异常向上抛（路由层转 503）; **失败路径零写盘**。
    """
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    lineage = load_lineage()
    active = load_active()
    parent_params = active.get("params_snapshot")
    if not isinstance(parent_params, dict) or "weights" not in (parent_params or {}):
        # 从未激活/快照缺失 → 用 yaml 现值（缺段回退出厂常量, 见 trading_quant._read_active_params）
        from src.strategies.trading_quant import _read_active_params

        w, s = _read_active_params()
        parent_params = {"weights": dict(w), "signals": dict(s)}

    codes = fund_codes if fund_codes is not None else load_fund_codes()
    if len(codes) < MIN_FUNDS:
        raise EvolutionDataUnavailable(f"持仓基金不足（{len(codes)}<{MIN_FUNDS} 只）")
    navs = load_navs(codes, nav_loader)
    proxy, rets, used_codes = build_proxy_matrix(navs)

    parent_metrics = evaluate_candidate(proxy, rets, parent_params["weights"], parent_params["signals"])

    rng = rng_from_seed(f"gen{lineage.get('next_gen_id', 1)}-{now_stamp()}")
    best: dict[str, Any] | None = None
    evaluated = 0
    for _ in range(int(candidates)):
        if budget_seconds - (time.monotonic() - started) <= 0.0:
            break  # 预算到点收割已完成者
        cand_params = mutate_params(parent_params, rng)
        cand_metrics = evaluate_candidate(proxy, rets, cand_params["weights"], cand_params["signals"])
        evaluated += 1
        if best is None or cand_metrics["fitness"] > best["metrics"]["fitness"]:
            best = {"params": cand_params, "metrics": cand_metrics}

    improved = bool(best and best["metrics"]["fitness"] > parent_metrics["fitness"])
    result: dict[str, Any] = {
        "generation_id": None,
        "improved": improved,
        "parent_fitness": parent_metrics["fitness"],
        "new_fitness": best["metrics"]["fitness"] if best else parent_metrics["fitness"],
        "metrics": best["metrics"] if best else parent_metrics,
        "candidates_evaluated": evaluated,
        "elapsed_ms": elapsed_ms(),
        "message": "",
    }
    if not improved:
        result["message"] = (
            f"评估 {evaluated}/{candidates} 个候选, 无优于父代(基线 {parent_metrics['fitness']})者, 未写盘"
            if evaluated else "预算到点, 未评估任何候选, 未写盘"
        )
        return result

    # ---- 写盘路径: gen 文件 + lineage(含 50 代滚动清理) ----
    gen_id = int(lineage.get("next_gen_id", 1))
    metrics_full = {
        **best["metrics"],
        "eval_window_days": int(rets.shape[1] - 1),
        "fund_count": len(used_codes),
        "fund_codes": used_codes,
        "evaluated_candidates": evaluated,
        "budget_used_ms": result["elapsed_ms"],
    }
    record = {
        "version": LINEAGE_VERSION,
        "generation_id": gen_id,
        "created_at": now_iso(),
        "parent_id": active.get("generation_id"),
        "origin": "evolution",
        "params": best["params"],
        "metrics": metrics_full,
        "parent_fitness": parent_metrics["fitness"],
        "improved_over_parent": True,
    }
    filename = save_generation(record)
    entry = {
        "generation_id": gen_id,
        "created_at": record["created_at"],
        "parent_id": record["parent_id"],
        "fitness": best["metrics"]["fitness"],
        "improved": True,
        "origin": "evolution",
        "file": filename,
        "params_digest": params_digest(best["params"]),
    }
    append_lineage(lineage, entry)
    result["generation_id"] = gen_id
    result["message"] = (
        f"第 {gen_id} 代: 评估 {evaluated}/{candidates} 个候选, "
        f"fitness {parent_metrics['fitness']} → {best['metrics']['fitness']}, 已写入谱系"
    )
    return result

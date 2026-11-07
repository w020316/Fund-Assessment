"""规则信号引擎(设计文档 §1.1: 可追溯性的唯一真源)。

铁律(设计文档 §六 #7):
- 本模块为**纯函数**: 零 IO(除 load_signal_config 读配置文件)、零 LLM、零网络
- direction / confidence / 推荐标的只能出自这里; LLM 输出只进 llm_* 字段
- 无法映射 source_signal 的推荐条目由 service 层丢弃并 logger.warning

信号合成口径(P0 拍板):
- 广度(rise/(rise+fall)) + 市场情绪分 + 板块动量净条数 → 合成得分 composite
- composite > +threshold → up, < -threshold → down, 否则 flat
- confidence = clamp(base + |composite|, min, max)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# 配置文件路径(项目根/config/prediction_signals.yaml)
_SIGNALS_YAML = Path(__file__).resolve().parents[2] / "config" / "prediction_signals.yaml"

# ===== 默认阈值(与 config/prediction_signals.yaml 同步; 文件缺失/损坏时兜底) =====

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "breadth": {"ratio_bull": 0.60, "ratio_bear": 0.40},
    "sentiment": {"score_bull": 60, "score_bear": 40},
    "sector": {"momentum_threshold_pct": 1.0, "top_n": 8},
    "confidence": {
        "base": 50,
        "weights": {"breadth": 25, "sentiment": 15, "sector": 10},
        "min": 5,
        "max": 95,
    },
    "direction": {"composite_threshold": 8.0},
    "verify": {"flat_band_pct": 0.3, "final_after_cst": "16:10"},
    "recommend": {"max_items": 5, "min_confidence": 55},
    "sector_candidates": {},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并(override 优先), 返回新 dict, 不改入参。"""
    out = json.loads(json.dumps(base))  # 深拷贝兜底
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_signal_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载信号阈值配置(R6.5: 改配置不改代码)。

    fail-safe: 文件缺失/损坏/非法 → 逐段回退默认值, 绝不抛异常。
    """
    file = Path(path) if path else _SIGNALS_YAML
    if not file.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(file, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            logger.warning(f"prediction_signals 配置非对象, 使用默认值: {file}")
            return json.loads(json.dumps(DEFAULT_CONFIG))
        merged = _deep_merge(DEFAULT_CONFIG, raw)
        # 关键数值兜底: 非法值回退默认(防止配置手滑把权重写成字符串)
        weights = merged.get("confidence", {}).get("weights", {})
        for key, default_v in DEFAULT_CONFIG["confidence"]["weights"].items():
            if not isinstance(weights.get(key), (int, float)):
                weights[key] = default_v
        return merged
    except Exception as e:  # noqa: BLE001 — 配置加载绝不抛异常
        logger.warning(f"prediction_signals 配置加载失败, 使用默认值: {e}")
        return json.loads(json.dumps(DEFAULT_CONFIG))


# ===== 快照规则摘要(R1.2: rule_summary 必有, 纯函数) =====


def build_rule_summary(payload: dict[str, Any]) -> str:
    """由快照数值拼一句规则摘要(涨跌家数 + 板块领涨领跌), 不含买卖建议。"""
    parts: list[str] = []
    indices = payload.get("indices") or []
    if isinstance(indices, list) and indices:
        changes = []
        for i in indices:
            try:
                changes.append(float(i.get("change_pct", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        if changes:
            avg = sum(changes) / len(changes)
            if avg > 0.05:
                word = "集体上涨"
            elif avg < -0.05:
                word = "集体下跌"
            else:
                word = "窄幅震荡"
            parts.append(f"主要指数{word}(均值 {avg:+.2f}%)")
    sentiment = payload.get("sentiment") or {}
    if isinstance(sentiment, dict):
        rise = int(sentiment.get("rise_count", 0) or 0)
        fall = int(sentiment.get("fall_count", 0) or 0)
        flat = int(sentiment.get("flat_count", 0) or 0)
        total = rise + fall + flat
        if total > 0:
            parts.append(
                f"上涨{rise}家/下跌{fall}家/平盘{flat}家,"
                f"上涨占比{rise / total * 100:.1f}%"
            )
    top_sectors = payload.get("top_sectors") or []
    if isinstance(top_sectors, list) and top_sectors:
        try:
            best = max(top_sectors, key=lambda s: float(s.get("change_pct", 0.0) or 0.0))
            worst = min(top_sectors, key=lambda s: float(s.get("change_pct", 0.0) or 0.0))
            parts.append(
                f"领涨板块 {best.get('name', '')}({float(best.get('change_pct', 0.0) or 0.0):+.2f}%),"
                f"领跌 {worst.get('name', '')}({float(worst.get('change_pct', 0.0) or 0.0):+.2f}%)"
            )
        except (TypeError, ValueError):
            pass
    if not parts:
        return "关键数据源暂未覆盖, 暂无规则摘要。"
    return "；".join(parts) + "。"


# ===== 三信号合成(R3.1: 规则结论 = 最终结论) =====


def _breadth_ratio(sentiment: dict[str, Any] | None) -> float | None:
    if not isinstance(sentiment, dict):
        return None
    rise = float(sentiment.get("rise_count", 0) or 0)
    fall = float(sentiment.get("fall_count", 0) or 0)
    if rise + fall <= 0:
        return None
    return rise / (rise + fall)


def synthesize(
    sentiment: dict[str, Any] | None,
    indices: list[dict[str, Any]] | None,
    sectors: list[dict[str, Any]] | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """三信号合成: 返回 {direction, confidence, rule_direction, rule_confidence,
    composite, reasons[]}。reasons 每项 {signal, value, verdict} 可追溯。"""
    cfg = cfg or DEFAULT_CONFIG
    weights = cfg["confidence"]["weights"]
    threshold = float(cfg["direction"]["composite_threshold"])

    reasons: list[dict[str, Any]] = []
    composite = 0.0

    # ① 广度
    ratio = _breadth_ratio(sentiment)
    if ratio is not None:
        ratio_bull = float(cfg["breadth"]["ratio_bull"])
        ratio_bear = float(cfg["breadth"]["ratio_bear"])
        if ratio >= ratio_bull:
            verdict, delta = "bullish", float(weights["breadth"])
        elif ratio <= ratio_bear:
            verdict, delta = "bearish", -float(weights["breadth"])
        else:
            verdict, delta = "neutral", 0.0
        composite += delta
        reasons.append({"signal": "breadth_ratio", "value": round(ratio, 4), "verdict": verdict})
    else:
        reasons.append({"signal": "breadth_ratio", "value": None, "verdict": "missing"})

    # ② 市场情绪分(0~100, 用上涨占比作为分数口径)
    score: float | None = None
    if isinstance(sentiment, dict):
        raw_ratio = sentiment.get("ratio")
        try:
            score = float(raw_ratio) * 100 if raw_ratio is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            score = None if ratio is None else round(ratio * 100, 1)
    if score is not None:
        s_bull = float(cfg["sentiment"]["score_bull"])
        s_bear = float(cfg["sentiment"]["score_bear"])
        if score >= s_bull:
            verdict, delta = "bullish", float(weights["sentiment"])
        elif score <= s_bear:
            verdict, delta = "bearish", -float(weights["sentiment"])
        else:
            verdict, delta = "neutral", 0.0
        composite += delta
        reasons.append({"signal": "sentiment_score", "value": round(score, 1), "verdict": verdict})
    else:
        reasons.append({"signal": "sentiment_score", "value": None, "verdict": "missing"})

    # ③ 板块动量(前 top_n 板块中涨跌板块净条数)
    sector_up = 0
    sector_down = 0
    mom_threshold = float(cfg["sector"]["momentum_threshold_pct"])
    top_n = int(cfg["sector"]["top_n"])
    ranked: list[dict[str, Any]] = []
    if isinstance(sectors, list):
        for s in sectors:
            try:
                chg = float(s.get("change_pct", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            ranked.append({"name": str(s.get("name", "")), "change_pct": chg})
        ranked.sort(key=lambda x: x["change_pct"], reverse=True)
        for s in ranked[:top_n]:
            if s["change_pct"] >= mom_threshold:
                sector_up += 1
            elif s["change_pct"] <= -mom_threshold:
                sector_down += 1
    if ranked:
        net = sector_up - sector_down
        if net > 0:
            verdict, delta = "bullish", float(weights["sector"])
        elif net < 0:
            verdict, delta = "bearish", -float(weights["sector"])
        else:
            verdict, delta = "neutral", 0.0
        composite += delta
        reasons.append({
            "signal": "sector_momentum",
            "value": {"up": sector_up, "down": sector_down},
            "verdict": verdict,
        })
    else:
        reasons.append({"signal": "sector_momentum", "value": None, "verdict": "missing"})

    if composite > threshold:
        direction = "up"
    elif composite < -threshold:
        direction = "down"
    else:
        direction = "flat"

    cmin = float(cfg["confidence"]["min"])
    cmax = float(cfg["confidence"]["max"])
    base = float(cfg["confidence"]["base"])
    confidence = round(min(cmax, max(cmin, base + abs(composite))), 1)

    return {
        "direction": direction,
        "confidence": confidence,
        "rule_direction": direction,
        "rule_confidence": confidence,
        "composite": round(composite, 2),
        "reasons": reasons,
    }


# ===== 推荐标的选取(R4.1: 标的必须可追溯到板块动量信号) =====


def _sector_matches(sector_name: str, key: str) -> bool:
    """板块名与配置 key 精确匹配或互含子串('酿酒行业' 匹配 '酿酒')。"""
    if not sector_name or not key:
        return False
    return sector_name == key or key in sector_name or sector_name in key


def select_recommendations(
    sectors: list[dict[str, Any]] | None,
    candidates_map: dict[str, list[dict[str, Any]]] | None,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """由板块动量选出推荐标的(纯函数)。

    返回条目: {symbol, kind, name(""), direction, confidence, source_signal}。
    name 留空, 由 service 层用实时行情回填(配置层禁止编造名称)。
    无动量信号 / 无候选映射 → 空列表(PRD 允许的正确降级)。
    """
    cfg = cfg or DEFAULT_CONFIG
    if not isinstance(sectors, list) or not isinstance(candidates_map, dict):
        return []
    mom_threshold = float(cfg["sector"]["momentum_threshold_pct"])
    max_items = int(cfg["recommend"]["max_items"])
    min_confidence = float(cfg["recommend"]["min_confidence"])
    cmax = float(cfg["confidence"]["max"])
    base = float(cfg["confidence"]["base"])

    ranked: list[dict[str, Any]] = []
    for s in sectors:
        try:
            chg = float(s.get("change_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        ranked.append({"name": str(s.get("name", "")), "change_pct": chg})
    ranked.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    picked: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for s in ranked:
        if abs(s["change_pct"]) < mom_threshold:
            continue
        matched_key = next(
            (k for k in candidates_map if _sector_matches(s["name"], k)), None
        )
        if matched_key is None:
            continue
        direction = "up" if s["change_pct"] > 0 else "down"
        confidence = round(min(cmax, max(0.0, base + abs(s["change_pct"]) * 5.0)), 1)
        if confidence < min_confidence:
            continue
        for cand in candidates_map[matched_key] or []:
            symbol = str(cand.get("symbol", "")).strip()
            kind = str(cand.get("kind", "stock")).strip()
            if not symbol or not re.fullmatch(r"\d{6}", symbol) or symbol in seen_symbols:
                continue
            if kind not in ("stock", "fund"):
                continue
            seen_symbols.add(symbol)
            picked.append({
                "symbol": symbol,
                "name": "",
                "kind": kind,
                "direction": direction,
                "confidence": confidence,
                # 可追溯: 指向具体板块动量信号与数值(R4.1 红线)
                "source_signal": f"sector_momentum:{s['name']}|change_pct={s['change_pct']:+.2f}",
            })
            if len(picked) >= max_items:
                return picked
    return picked


# ===== 验证三分类(R6.1: up→涨>0.3%, down→跌>0.3%, flat→±0.3% 内) =====


def classify_hit(
    expected: str, actual_return: float | None, flat_band_pct: float = 0.3
) -> int | None:
    """三分类判命中: 缺实际涨跌返回 None(验证缺失)。"""
    if actual_return is None:
        return None
    ret = float(actual_return)
    band = float(flat_band_pct)
    if expected == "up":
        return 1 if ret > band else 0
    if expected == "down":
        return 1 if ret < -band else 0
    if expected == "flat":
        return 1 if abs(ret) <= band else 0
    return 0


def direction_from_return(actual_return: float | None, flat_band_pct: float = 0.3) -> str:
    """实际涨跌 → 三分类方向(验证写入 actual 字段)。"""
    if actual_return is None:
        return ""
    ret = float(actual_return)
    if ret > flat_band_pct:
        return "up"
    if ret < -flat_band_pct:
        return "down"
    return "flat"

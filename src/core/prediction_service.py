"""时段预测编排服务(设计文档 §4 时序 / T02)。

职责: 采集 / 预测 / 推荐 / 验证 / 统计五个编排函数。
- IO(数据源)与 LLM 调用只在本层; 规则结论全部出自 prediction_signals 纯函数
- 任一上游/LLM 失败不抛 500: 字段置 None/空串, 记 missing_fields(设计 §六 #8)
- 非交易日守卫: 018957 T+0 净值探针(独立实现, 不从 scripts/ 反向 import)
- LLM 分层: 快照摘要 light(15s) / 次日预测 deep(≤60s) / 推荐理由润色 light
  —— 全部走 llm_router.chat(task_tier=...), 只总结/润色, 不产生结论与标的(§六 #7)
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
from typing import Any

from loguru import logger

from src.core import data_source_v2 as ds2
from src.core import prediction_signals as sig
from src.core.prediction_store import get_store

# 北京时间 UTC+8(Render 为 UTC, 一律显式换算, 与 gen_daily_report 同口径)
_CST = _dt.timezone(_dt.timedelta(hours=8))

# 交易日守卫探针: A股基金 T+0 净值, update_time 日期 == 北京今天 → 交易日
_TRADE_GUARD_FUND = "018957"

_VALID_SESSIONS = ("morning", "noon", "close")

# 手动补采幂等窗口(秒, R1.4: 5 分钟内重复点击直接返回已有快照)
_COLLECT_IDEMPOTENT_SECONDS = 300

# LLM 预算(设计 §1.2: light 15s / deep ≤60s, 不破坏前端 90s 约束)
_LLM_LIGHT_TIMEOUT = 15.0
_LLM_DEEP_TIMEOUT = 60.0

_DATE_DASHED_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_COMPACT_RE = re.compile(r"\d{8}")


# ===== 时间与交易日守卫 =====


def beijing_now() -> _dt.datetime:
    return _dt.datetime.now(_CST)


def beijing_today_str() -> str:
    return beijing_now().strftime("%Y-%m-%d")


def _extract_date_part(update_time: str) -> str:
    if not update_time:
        return ""
    m = _DATE_DASHED_RE.search(update_time)
    if m:
        return m.group(0)
    m = _DATE_COMPACT_RE.search(update_time)
    if m:
        raw = m.group(0)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def check_trading_day(now: _dt.datetime | None = None) -> bool:
    """018957 净值日期探针(fail-closed: 取不到一律按非交易日)。"""
    target = (now or beijing_now()).strftime("%Y-%m-%d")
    try:
        rows = ds2.get_fund_realtime_tencent([_TRADE_GUARD_FUND])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"prediction 交易日守卫探针取数失败: {exc}")
        return False
    if not rows:
        logger.warning("prediction 交易日守卫: 探针基金无返回数据, 按非交易日处理")
        return False
    return _extract_date_part(str(rows[0].get("update_time", ""))) == target


def next_trading_date(today: str) -> str:
    """次日预测目标日: 工作日顺推(仓库无交易日历, 周末回退口径与 fund.py 一致)。"""
    d = _dt.datetime.strptime(today, "%Y-%m-%d").date() + _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def prev_trading_date(today: str) -> str:
    """上一交易日(工作日回退), 供回看/报表等场景取前一交易日。"""
    d = _dt.datetime.strptime(today, "%Y-%m-%d").date() - _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ===== 数据采集(逐源容错, R1.1) =====


def _fetch_indices() -> list[dict[str, Any]]:
    rows = ds2.get_index_realtime() or []
    out: list[dict[str, Any]] = []
    for r in rows[:8]:
        try:
            out.append({
                "name": str(r.get("name", "")),
                "code": str(r.get("code", "")),
                "price": float(r.get("price", 0.0) or 0.0),
                "change_pct": float(r.get("change_pct", 0.0) or 0.0),
            })
        except (TypeError, ValueError):
            continue
    return out


def _fetch_sentiment() -> dict[str, Any]:
    d = ds2.get_market_sentiment() or {}
    return {
        "rise_count": int(d.get("rise_count", 0) or 0),
        "fall_count": int(d.get("fall_count", 0) or 0),
        "flat_count": int(d.get("flat_count", 0) or 0),
        "sentiment": str(d.get("sentiment", "") or ""),
        "ratio": float(d.get("ratio", 0.0) or 0.0),
    }


def _fetch_sectors(top: int = 10) -> list[dict[str, Any]]:
    rows = ds2.get_sector_ranking() or []
    ranked: list[dict[str, Any]] = []
    for r in rows:
        try:
            ranked.append({
                "name": str(r.get("name", "")),
                "change_pct": float(r.get("change_pct", 0.0) or 0.0),
            })
        except (TypeError, ValueError):
            continue
    ranked.sort(key=lambda x: x["change_pct"], reverse=True)
    return ranked[:top]


def _collect_market_payload() -> tuple[dict[str, Any], list[str]]:
    """逐源 try/except 采集, 失败字段置空并记入 missing_fields, 绝不中断(R1.1)。"""
    payload: dict[str, Any] = {}
    missing: list[str] = []
    try:
        payload["indices"] = _fetch_indices()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"collect indices failed: {e}")
        payload["indices"] = []
        missing.append("indices")
    try:
        payload["sentiment"] = _fetch_sentiment()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"collect sentiment failed: {e}")
        payload["sentiment"] = {}
        missing.append("sentiment")
    try:
        payload["top_sectors"] = _fetch_sectors()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"collect sectors failed: {e}")
        payload["top_sectors"] = []
        missing.append("top_sectors")
    return payload, missing


# ===== LLM 辅助(全部 try/except 降级, §六 #8) =====


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """宽容解析 LLM 输出中的 JSON 对象(剥 ```json 围栏 / 截取首尾大括号)。"""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def _parse_json_array(text: str) -> list[Any] | None:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        arr = json.loads(cleaned)
        return arr if isinstance(arr, list) else None
    except (ValueError, TypeError):
        pass
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
            return arr if isinstance(arr, list) else None
        except (ValueError, TypeError):
            return None
    return None


def _llm_snapshot_summary(payload: dict[str, Any], rule_summary: str) -> str:
    """快照一句话总结(light 档, 失败 → 空串, 不阻塞采集)。"""
    try:
        from src.core.llm_router import get_llm_router
        router = get_llm_router()
        prompt = (
            "你是 A 股市场分析师。根据以下盘面数据用不超过 40 个汉字写一句中文总结, "
            "只陈述事实, 不给买卖建议, 直接输出正文:\n"
            f"{rule_summary}\n"
            f"数据: {json.dumps(payload, ensure_ascii=False)[:600]}"
        )
        resp = router.chat(
            [{"role": "user", "content": prompt}],
            task_tier="light",
            total_timeout=_LLM_LIGHT_TIMEOUT,
        )
        text = (resp.content or "").strip()
        return text[:120]
    except Exception as e:  # noqa: BLE001 — LLM 失败降级为空串
        logger.warning(f"prediction llm snapshot summary failed: {e}")
        return ""


def _llm_prediction(
    syn: dict[str, Any], payload: dict[str, Any]
) -> tuple[str | None, float | None, str]:
    """次日预测辅助判断(deep 档 ≤60s)。失败 → (None, None, '')。

    红线: LLM 结论只落 llm_* 字段, 不参与 direction/confidence 生成(§六 #7)。
    """
    try:
        from src.core.llm_router import get_llm_router
        router = get_llm_router()
        signals_payload = json.dumps(
            {
                "direction": syn["direction"],
                "composite": syn["composite"],
                "reasons": syn["reasons"],
            },
            ensure_ascii=False,
        )
        prompt = (
            "你是 A 股策略分析师。以下是当前盘面的规则信号数值, 请对沪深300次日方向"
            "给出辅助判断。只输出 JSON 对象, 格式: "
            '{"direction":"up|down|flat","confidence":0到100的数字,"reason":"不超过60字"}\n'
            f"规则信号: {signals_payload}\n"
            f"盘面数据: {json.dumps(payload, ensure_ascii=False)[:600]}"
        )
        resp = router.chat(
            [{"role": "user", "content": prompt}],
            task_tier="deep",
            total_timeout=_LLM_DEEP_TIMEOUT,
        )
        obj = _parse_json_object(resp.content or "")
        if not obj:
            return None, None, ""
        direction = str(obj.get("direction", "")).strip().lower()
        if direction not in ("up", "down", "flat"):
            direction = ""
        conf_raw = obj.get("confidence")
        try:
            conf = round(float(conf_raw), 1) if conf_raw is not None else None
        except (TypeError, ValueError):
            conf = None
        if direction == "":
            return None, None, ""
        return direction, conf, str(obj.get("reason", ""))[:200]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"prediction llm deep failed: {e}")
        return None, None, ""


def _llm_polish_reasons(items: list[dict[str, Any]]) -> list[str]:
    """推荐理由润色(light 档): 只润色已选定标的的理由, 不产生标的(R4.1 红线)。"""
    rule_reasons = [str(it.get("rule_reason", "")) for it in items]
    if not items:
        return []
    try:
        from src.core.llm_router import get_llm_router
        router = get_llm_router()
        prompt = (
            "你是投资内容编辑。下面是若干条由规则信号生成的市场观察(含标的代码与方向), "
            "请逐条把理由改写为不超过 40 个汉字的通顺中文, 保持原方向与事实, 不新增标的, "
            "不给买卖建议。只输出 JSON 数组, 与输入一一对应, 元素为字符串。\n"
            f"输入: {json.dumps(rule_reasons, ensure_ascii=False)}"
        )
        resp = router.chat(
            [{"role": "user", "content": prompt}],
            task_tier="light",
            total_timeout=_LLM_LIGHT_TIMEOUT,
        )
        arr = _parse_json_array(resp.content or "")
        if not arr or len(arr) != len(items):
            return rule_reasons
        out: list[str] = []
        for i, v in enumerate(arr):
            text = str(v).strip() if v is not None else ""
            out.append(text[:80] if text else rule_reasons[i])
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"prediction llm polish failed: {e}")
        return rule_reasons


# ===== 编排 1: 时段快照采集(R1.1/R1.4) =====


def collect_snapshot(session: str, now: _dt.datetime | None = None) -> dict[str, Any]:
    """采集一个时段快照(非交易日/5 分钟内重复 → 幂等返回)。"""
    if session not in _VALID_SESSIONS:
        raise ValueError(f"session 必须是 {'/'.join(_VALID_SESSIONS)} 之一, 收到: {session!r}")
    now = now or beijing_now()
    today = now.strftime("%Y-%m-%d")
    store = get_store()

    existing = store.get_snapshot(today, session)
    if existing and not _snapshot_is_stale(existing):
        return {**existing, "idempotent": True}

    if not check_trading_day(now):
        logger.info(f"collect_snapshot: 非交易日({today}), 跳过 session={session}")
        return {
            "trade_date": today,
            "session": session,
            "skipped": True,
            "reason": "non_trading_day",
        }

    payload, missing = _collect_market_payload()
    rule_summary = sig.build_rule_summary(payload)
    llm_summary = _llm_snapshot_summary(payload, rule_summary)
    row = store.upsert_snapshot(
        trade_date=today,
        session=session,
        payload=payload,
        missing_fields=missing,
        rule_summary=rule_summary,
        llm_summary=llm_summary,
    )
    if row is None:
        # 写库失败也不抛(§六 #8): 返回内存快照, 标记持久化失败
        return {
            "trade_date": today,
            "session": session,
            "collected_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "payload": payload,
            "missing_fields": missing + ["persist"],
            "rule_summary": rule_summary,
            "llm_summary": llm_summary,
            "skipped": False,
            "persisted": False,
        }
    return {**row, "skipped": False, "persisted": True}


def _snapshot_is_stale(snapshot: dict[str, Any]) -> bool:
    """已有快照是否已过 5 分钟幂等窗口(过窗口允许重新采集覆盖)。"""
    raw = str(snapshot.get("collected_at", "") or "")
    try:
        ts = _dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return True  # 时间不可解析 → 视为过期, 允许重采
    age = (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds()
    return age >= _COLLECT_IDEMPOTENT_SECONDS


# ===== 编排 2: 次日预测生成(R3.1/R3.3) =====


def generate_prediction(now: _dt.datetime | None = None) -> dict[str, Any]:
    """生成当日对次交易日的沪深300方向预测(同日幂等覆盖)。

    三信号全缺失(insufficient)时不生成预测: 返回 skipped=True 且不落库,
    避免把"无证据"伪装成"确定结论"污染准确率统计。
    """
    now = now or beijing_now()
    today = now.strftime("%Y-%m-%d")
    store = get_store()
    if not check_trading_day(now):
        logger.info(f"generate_prediction: 非交易日({today}), 跳过")
        return {"trade_date": today, "skipped": True, "reason": "non_trading_day"}

    payload, missing = _collect_market_payload()
    syn = sig.synthesize(
        payload.get("sentiment") or None,
        payload.get("indices") or None,
        payload.get("top_sectors") or None,
        sig.load_signal_config(),
    )
    # P0 QF3-01 守卫: 三信号全缺失 → 无证据可依, 不调 LLM(不消耗额度)、不落库。
    # 落库一条"全盲却确定"的预测会污染 /verify 的准确率分子分母, 必须阻断。
    if syn["insufficient"]:
        missing_signals = [r["signal"] for r in syn["reasons"] if r.get("verdict") == "missing"]
        logger.warning(
            f"generate_prediction: 数据不足, 跳过预测(不调 LLM/不落库) "
            f"trade_date={today} missing={missing} missing_signals={missing_signals}"
        )
        return {
            "trade_date": today,
            "skipped": True,
            "reason": "insufficient_data",
            "missing_fields": missing,
        }

    reasons: list[dict[str, Any]] = list(syn["reasons"])
    if missing:
        reasons.append({"signal": "missing_fields", "value": ",".join(missing), "verdict": "missing"})

    llm_direction, llm_confidence, llm_reason = _llm_prediction(syn, payload)
    conflict = 0
    if llm_direction and llm_direction != syn["rule_direction"]:
        conflict = 1
        reasons.append({
            "signal": "llm_conflict",
            "value": llm_direction,
            "verdict": "rule_wins",  # 冲突以规则为主(设计 §1.1)
        })

    target_date = next_trading_date(today)
    row = store.upsert_prediction(
        trade_date=today,
        target_date=target_date,
        benchmark="HS300",
        direction=syn["direction"],
        confidence=syn["confidence"],
        rule_direction=syn["rule_direction"],
        rule_confidence=syn["rule_confidence"],
        rule_reasons=reasons,
        llm_direction=llm_direction,
        llm_confidence=llm_confidence,
        llm_reason=llm_reason,
        conflict_flag=conflict,
    )
    if row is None:
        raise RuntimeError("prediction 落库失败(generate_prediction)")
    return {**row, "missing_fields": missing, "skipped": False}


# ===== 编排 3: 推荐生成(R4.1) =====


def _fill_names_and_quotes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用实时行情回填名称并过滤不可得标的(名称来自上游, 不编造)。"""
    stock_codes = [it["symbol"] for it in items if it["kind"] == "stock"]
    fund_codes = [it["symbol"] for it in items if it["kind"] == "fund"]
    quote_map: dict[str, dict[str, Any]] = {}
    if stock_codes:
        try:
            for q in ds2.get_realtime_quote_tencent(stock_codes) or []:
                quote_map[str(q.get("code", ""))] = q
        except Exception as e:  # noqa: BLE001
            logger.warning(f"recommend stock quotes failed: {e}")
    if fund_codes:
        try:
            for q in ds2.get_fund_realtime_tencent(fund_codes) or []:
                quote_map[str(q.get("code", ""))] = q
        except Exception as e:  # noqa: BLE001
            logger.warning(f"recommend fund quotes failed: {e}")
    out: list[dict[str, Any]] = []
    for it in items:
        q = quote_map.get(it["symbol"])
        if not q:
            logger.warning(
                f"recommendation {it['symbol']} 无实时行情, 丢弃(无法回填名称/确认可交易)"
            )
            continue
        out.append({**it, "name": str(q.get("name", "") or it["symbol"])})
    return out


def generate_recommendations(now: _dt.datetime | None = None) -> dict[str, Any]:
    """信号驱动推荐(R4.1): 标的全部来自 sector_candidates × 板块动量, 可追溯。"""
    now = now or beijing_now()
    today = now.strftime("%Y-%m-%d")
    store = get_store()
    if not check_trading_day(now):
        logger.info(f"generate_recommendations: 非交易日({today}), 跳过")
        return {"trade_date": today, "skipped": True, "reason": "non_trading_day"}

    cfg = sig.load_signal_config()
    try:
        sectors = _fetch_sectors(top=80)  # 全量板块榜, 供动量匹配
    except Exception as e:  # noqa: BLE001
        logger.warning(f"generate_recommendations sectors failed: {e}")
        sectors = []

    picked = sig.select_recommendations(sectors, cfg.get("sector_candidates") or {}, cfg)
    for p in picked:
        p["rule_reason"] = (
            f"所属板块{p['source_signal'].split('|')[0].split(':', 1)[1]}动量信号触发, "
            f"信号方向{'看涨' if p['direction'] == 'up' else '看跌'}"
        )
    # 可追溯红线兜底: 无 source_signal 的条目一律丢弃并告警(§六 #7)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for p in picked:
        (kept if p.get("source_signal") else dropped).append(p)
    if dropped:
        logger.warning(
            f"generate_recommendations: 丢弃 {len(dropped)} 条无 source_signal 的推荐: "
            f"{[p.get('symbol') for p in dropped]}"
        )
    picked = kept

    filled = _fill_names_and_quotes(picked)
    polished = _llm_polish_reasons(filled)
    items: list[dict[str, Any]] = []
    for it, reason in zip(filled, polished):
        items.append({
            "symbol": it["symbol"],
            "name": it["name"],
            "kind": it["kind"],
            "direction": it["direction"],
            "confidence": it["confidence"],
            "reason": reason,
            "source_signal": it["source_signal"],
        })

    target_date = next_trading_date(today)
    written = store.replace_recommendations(today, target_date, items)
    return {
        "trade_date": today,
        "target_date": target_date,
        "items": items,
        "count": written,
        "skipped": False,
    }


# ===== 编排 4: 每日验证(R6.1) =====


def _hs300_change_pct() -> float | None:
    """沪深300当日实际涨跌幅; 取不到返回 None。"""
    try:
        rows = ds2.get_index_realtime() or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"verify index realtime failed: {e}")
        return None
    for r in rows:
        name = str(r.get("name", ""))
        code = str(r.get("code", ""))
        if "沪深300" in name or code in ("000300", "sh000300", "1.000300"):
            try:
                return float(r.get("change_pct", 0.0) or 0.0)
            except (TypeError, ValueError):
                return None
    return None


def _symbol_change_pct(symbol: str, kind: str) -> float | None:
    try:
        if kind == "fund":
            rows = ds2.get_fund_realtime_tencent([symbol]) or []
        else:
            rows = ds2.get_realtime_quote_tencent([symbol]) or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"verify quote {symbol} failed: {e}")
        return None
    for r in rows:
        if str(r.get("code", "")) == symbol:
            try:
                return float(r.get("change_pct", 0.0) or 0.0)
            except (TypeError, ValueError):
                return None
    return None


def _is_verify_final(now: _dt.datetime, cfg: dict[str, Any]) -> bool:
    """北京时间过 final_after_cst(默认 16:10)后的 verify 为最终跑: 数据仍取不到
    → 写 missing=1 占位; 之前的 verify(15:40 首跑)只跳过不占位。"""
    raw = str(cfg.get("verify", {}).get("final_after_cst", "16:10"))
    try:
        hh, mm = raw.split(":")
        final = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, TypeError):
        final = now.replace(hour=16, minute=10, second=0, microsecond=0)
    return now >= final


def _verify_earliest(now: _dt.datetime, cfg: dict[str, Any]) -> _dt.datetime:
    """当日最早可验证时点(默认 15:00), 由 cfg["verify"]["earliest_hhmm"] 配置。

    兼容 "1500" 与 "15:00" 两种写法; 配置非法时回退 15:00, 绝不抛异常。
    """
    raw = str(cfg.get("verify", {}).get("earliest_hhmm", "1500"))
    try:
        digits = raw.replace(":", "").strip()
        if len(digits) != 4 or not digits.isdigit():
            raise ValueError(f"earliest_hhmm 非法: {raw!r}")
        hh, mm = int(digits[:2]), int(digits[2:])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"earliest_hhmm 越界: {raw!r}")
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except (ValueError, TypeError):
        logger.warning(f"verify earliest_hhmm 配置非法({raw!r}), 回退 15:00")
        return now.replace(hour=15, minute=0, second=0, microsecond=0)


def verify_daily(now: _dt.datetime | None = None) -> dict[str, Any]:
    """验证今日(target_date=今日, 由上一交易日 15:40 生成的次日)预测与推荐(R6.1)。

    时序口径: D 日 15:40 generate → target_date = next_trading_date(D);
    D+1(=target_date) 日 15:40 verify 时今日恰为该目标日, 且今日 15:40 的
    实时涨跌即该目标日的最终涨跌。周末自然衔接: 周五生成的预测 target=周一,
    周一验证。幂等 upsert, 16:30 重试覆盖。

    时点守卫(QF3-04): 早于 earliest_hhmm(默认 15:00)直接跳过, 不读库不写库。
    """
    now = now or beijing_now()
    today = now.strftime("%Y-%m-%d")
    cfg = sig.load_signal_config()

    # QF3-04: 盘中(_hs300_change_pct 取的是实时涨跌)若被调用, 会把分钟级波动当作
    # 目标日最终涨跌写入并 missing=0; 15:40 正式跑时 _pending() 已为 False, 不再覆盖,
    # 错误结果被永久固化进准确率。故早于可验证时点一律不读不写直接返回。
    earliest = _verify_earliest(now, cfg)
    if now < earliest:
        logger.info(
            f"verify_daily: 时点过早({now.strftime('%H:%M')} < {earliest.strftime('%H:%M')}), "
            f"盘中涨跌非最终收盘涨跌, 跳过且不写库"
        )
        return {"verified": 0, "missing": 0, "skipped": True, "reason": "too_early", "detail": []}

    store = get_store()
    if not check_trading_day(now):
        logger.info(f"verify_daily: 非交易日({today}), 跳过")
        return {"verified": 0, "missing": 0, "skipped": True, "reason": "non_trading_day", "detail": []}

    band = float(cfg.get("verify", {}).get("flat_band_pct", 0.3))
    # 验证目标日 = 今日(check_trading_day 已保证是交易日)。今日即上一交易日
    # 生成预测的 target_date, 今日 15:40 的实时涨跌即该目标日的最终涨跌;
    # 周一验证的目标=周一(由周五 next_trading_date 生成)。
    target_date = today
    preds = store.list_predictions_for_target(target_date)
    recs = store.list_recommendations_for_target(target_date)
    detail: list[dict[str, Any]] = []
    verified = 0
    missing = 0

    # 待验证 = 尚无验证行, 或已有 missing=1 占位(允许重试覆盖)
    def _pending(kind: str, target_id: int) -> bool:
        v = store.get_verification(target_date, kind, target_id)
        return v is None or int(v.get("missing", 0)) == 1

    hs300_ret = _hs300_change_pct()
    if hs300_ret is None:
        if _is_verify_final(now, cfg):
            for p in preds:
                if _pending("prediction", p["id"]):
                    store.upsert_verification(
                        target_date, "prediction", p["id"], p["direction"], "", None, None, missing=1
                    )
                    missing += 1
                    detail.append({"target_kind": "prediction", "target_id": p["id"], "missing": 1})
            for r in recs:
                if _pending("recommendation", r["id"]):
                    store.upsert_verification(
                        target_date, "recommendation", r["id"], r["direction"], "", None, None, missing=1
                    )
                    missing += 1
                    detail.append({"target_kind": "recommendation", "target_id": r["id"], "missing": 1})
            logger.warning(f"verify_daily: 实际行情取不到且已过最终时点, 写 missing 占位 {missing} 条")
        else:
            logger.info("verify_daily: 实际行情暂取不到, 等待 16:30 重试(不写占位)")
        compute_accuracy(store=store, persist=True)
        return {
            "verified": verified,
            "missing": missing,
            "skipped": False,
            "detail": detail,
            "target_date": target_date,
        }

    actual_dir = sig.direction_from_return(hs300_ret, band)
    for p in preds:
        if not _pending("prediction", p["id"]):
            continue
        hit = sig.classify_hit(p["direction"], hs300_ret, band)
        store.upsert_verification(
            target_date, "prediction", p["id"], p["direction"], actual_dir,
            hit, hs300_ret, missing=0,
        )
        verified += 1
        detail.append({"target_kind": "prediction", "target_id": p["id"], "hit": hit})

    for r in recs:
        if not _pending("recommendation", r["id"]):
            continue
        ret = _symbol_change_pct(r["symbol"], r["kind"])
        if ret is None:
            if _is_verify_final(now, cfg):
                store.upsert_verification(
                    target_date, "recommendation", r["id"], r["direction"], "", None, None, missing=1
                )
                missing += 1
                detail.append({"target_kind": "recommendation", "target_id": r["id"], "missing": 1})
            continue
        hit = sig.classify_hit(r["direction"], ret, band)
        store.upsert_verification(
            target_date, "recommendation", r["id"], r["direction"],
            sig.direction_from_return(ret, band), hit, ret, missing=0,
        )
        verified += 1
        detail.append({"target_kind": "recommendation", "target_id": r["id"], "hit": hit})

    compute_accuracy(store=store, persist=True)
    # 验证成功后顺带跑保留策略清理(R5.2)
    try:
        store.cleanup()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"verify_daily cleanup failed: {e}")
    return {
        "verified": verified,
        "missing": missing,
        "skipped": False,
        "detail": detail,
        "target_date": target_date,
    }


# ===== 编排 5: 准确率统计 + 看板(R6.2/R6.3, 双通道同源) =====


def _window_start(today: str, window: str) -> str:
    d = _dt.datetime.strptime(today, "%Y-%m-%d").date()
    days = {"day": 1, "7d": 7, "30d": 30}.get(window, 1)
    return (d - _dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")


def compute_accuracy(
    store: Any | None = None,
    stat_date: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """三窗口 × 两类型 × 来源命中率(样本 <5 → hit_rate=None)。

    persist=True(默认): 计算结果落 accuracy_stats —— 供 verify_daily 在有鉴权的
    写路径上落库(QF3-07: 落库只发生在 POST /verify 之后)。
    persist=False: 纯内存计算, 不写库 —— 供公开只读的 GET /accuracy 使用。
    GET 是匿名可达且前端每次切页都会调用的, 绝不能在读接口里产生写副作用。

    返回体含 persisted 标识, 让调用方(与前端)能区分"本次是否落库"。
    """
    store = store or get_store()
    today = stat_date or beijing_today_str()
    band = float(sig.load_signal_config().get("verify", {}).get("flat_band_pct", 0.3))

    # 一次取 30 天验证行, 内存过滤各窗口(与库内重算同源, 对账测试口径一致)
    start30 = _window_start(today, "30d")
    rows = store.list_verifications_between(start30, today, include_missing=False)
    preds = {p["id"]: p for p in store.list_predictions(days=40)}

    windows: dict[str, Any] = {}
    insufficient = False
    stat_rows: list[dict[str, Any]] = []
    for window in ("day", "7d", "30d"):
        start = _window_start(today, window)
        w_rows = [r for r in rows if start <= r["trade_date"] <= today]
        windows[window] = {}
        for kind in ("prediction", "recommendation"):
            k_rows = [r for r in w_rows if r["target_kind"] == kind]
            # rule 来源: 验证行本身就是按最终方向(规则)判的
            hits = sum(1 for r in k_rows if r["hit"] == 1)
            total = len(k_rows)
            rate: float | None = round(hits / total, 4) if total >= 5 else None
            if total < 5:
                insufficient = True
            windows[window][kind] = {
                "rule": {"hits": hits, "total": total, "hit_rate": rate},
            }
            stat_rows.append({
                "window": window, "target_kind": kind, "source": "rule",
                "hits": hits, "total": total, "hit_rate": rate,
            })
            # llm 来源: 仅预测类且 LLM 有结论的样本, 用同一实际涨跌按 LLM 方向重判
            if kind == "prediction":
                llm_rows = []
                for r in k_rows:
                    p = preds.get(r["target_id"])
                    if p and p.get("llm_direction"):
                        llm_rows.append((r, p))
                llm_hits = sum(
                    1 for r, p in llm_rows
                    if sig.classify_hit(p["llm_direction"], r["actual_return"], band) == 1
                )
                llm_total = len(llm_rows)
                llm_rate: float | None = round(llm_hits / llm_total, 4) if llm_total >= 5 else None
                if llm_total < 5:
                    insufficient = True
                windows[window][kind]["llm"] = {
                    "hits": llm_hits, "total": llm_total, "hit_rate": llm_rate,
                }
                stat_rows.append({
                    "window": window, "target_kind": "prediction", "source": "llm",
                    "hits": llm_hits, "total": llm_total, "hit_rate": llm_rate,
                })
            else:
                windows[window][kind]["llm"] = {"hits": 0, "total": 0, "hit_rate": None}

    # 30 日走势(按验证日聚合, 两类型合并)
    by_date: dict[str, list[int]] = {}
    for r in rows:
        by_date.setdefault(r["trade_date"], []).append(1 if r["hit"] == 1 else 0)
    trend_30d = [
        {
            "stat_date": d,
            "hit_rate": round(sum(v) / len(v), 4),
            "total": len(v),
        }
        for d, v in sorted(by_date.items())
    ]

    persisted = False
    if persist:
        try:
            store.upsert_accuracy_stats(stat_rows, stat_date=today)
            persisted = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"compute_accuracy upsert stats failed: {e}")

    return {
        "windows": windows,
        "insufficient_sample": insufficient,
        "trend_30d": trend_30d,
        "stat_date": today,
        "persisted": persisted,
    }


def get_accuracy_dashboard() -> dict[str, Any]:
    """看板口径(A7): 与 verify 后统计同一函数(双通道数字一致, 对账测试锚点)。

    QF3-07: GET /accuracy 是公开匿名接口且会被前端反复调用, 必须纯只读 ——
    统计值在内存算完直接返回, 不落库(persist=False), 响应带 persisted=False。
    落库动作只发生在有鉴权的 POST /verify 之后的 verify_daily 里。
    """
    return compute_accuracy(persist=False)


def get_predictions(days: int = 30) -> list[dict[str, Any]]:
    return get_store().list_predictions(days=days)


def get_recommendations(trade_date: str | None = None, days: int = 30) -> list[dict[str, Any]]:
    return get_store().list_recommendations(trade_date=trade_date, days=days)


def get_snapshots(trade_date: str | None = None, days: int = 5) -> list[dict[str, Any]]:
    return get_store().list_snapshots(trade_date=trade_date, days=days)


# ===== 异步包装(路由层用 asyncio.to_thread 调用同步编排) =====


async def async_collect_snapshot(session: str) -> dict[str, Any]:
    return await asyncio.to_thread(collect_snapshot, session)


async def async_generate_prediction() -> dict[str, Any]:
    return await asyncio.to_thread(generate_prediction)


async def async_generate_recommendations() -> dict[str, Any]:
    return await asyncio.to_thread(generate_recommendations)


async def async_verify_daily() -> dict[str, Any]:
    return await asyncio.to_thread(verify_daily)


async def async_accuracy_dashboard() -> dict[str, Any]:
    return await asyncio.to_thread(get_accuracy_dashboard)

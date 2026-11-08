"""短线复盘看板与情绪周期

参考 Vibe-Astock 的复盘思路,补齐当前项目缺失的"盘后复盘"环节:
- 今日盘面:涨跌家数、涨停/跌停/炸板、连板梯队、成交额
- 近 5 日情绪周期:涨停家数、最高连板、炸板率的变化
- 首板分析:封板时间、封板资金、炸板次数、封板质量评分
- 今晚判断 / 复盘后判断:纯规则引擎,输出可解释的结论

设计原则(与项目既有约束一致):
- 所有数值来自行情接口或由接口数据换算,不使用随机/占位数据
- 数据缺失时返回 0/空,并在 data_quality 标记 partial,绝不编造
- 判断逻辑不调用 LLM,保证同一份数据得到同一份结论
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Callable

from loguru import logger

try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:  # pragma: no cover - 依赖缺失时走降级路径
    ak = None
    _HAS_AKSHARE = False

from src.core import data_source_v2 as ds2


# ──────────────────────────────────────────────
# 轻量缓存:同一天的历史涨停池在盘后不会变化,可缓存较久
# ──────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_TODAY = 300
_CACHE_TTL_HISTORY = 3600


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expires_at, value = item
    if time.time() > expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: float) -> None:
    _CACHE[key] = (time.time() + ttl, value)


def clear_cache() -> None:
    """清空模块缓存(测试或手动刷新时使用)"""
    _CACHE.clear()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(num) or math.isinf(num):
        return default
    return num


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_time(value: Any) -> str:
    """把行情接口的封板时间统一成 HH:MM 格式"""
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        return f"{digits[:2]}:{digits[2:4]}"
    return ""


def _recent_trade_dates(days: int, end: date | None = None) -> list[str]:
    """返回最近 N 个工作日(新→旧),仅跳过周末;节假日由接口返回空数据自然过滤"""
    cursor = end or date.today()
    dates: list[str] = []
    while len(dates) < days:
        if cursor.weekday() < 5:
            dates.append(cursor.strftime("%Y%m%d"))
        cursor -= timedelta(days=1)
    return dates


def _is_today(date_str: str) -> bool:
    return date_str == date.today().strftime("%Y%m%d")


def _pool_ttl(date_str: str) -> float:
    return _CACHE_TTL_TODAY if _is_today(date_str) else _CACHE_TTL_HISTORY


# ──────────────────────────────────────────────
# 数据获取
# ──────────────────────────────────────────────

def _pick(row: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        try:
            if key in row:
                value = row[key]
                if value is None:
                    continue
                if isinstance(value, float) and math.isnan(value):
                    continue
                text = str(value).strip()
                if text and text.lower() != "nan":
                    return value
        except Exception:
            continue
    return default


def _normalize_pool_row(row: Any) -> dict:
    streak_raw = _pick(row, "连板数", "连续板数", default=None)
    if streak_raw is None:
        stats = str(_pick(row, "涨停统计", default="") or "")
        # 涨停统计格式常见为 "x/y"(x 天 y 板),取板数
        if "/" in stats:
            streak_raw = stats.split("/")[-1]
    streak = max(1, _to_int(streak_raw, 1))

    return {
        "code": str(_pick(row, "代码", "股票代码", default="")).strip().zfill(6),
        "name": str(_pick(row, "名称", "股票简称", default="")).strip(),
        "change_pct": round(_to_float(_pick(row, "涨跌幅", default=0)), 2),
        "price": _to_float(_pick(row, "最新价", default=0)),
        "amount": _to_float(_pick(row, "成交额", default=0)),
        "turnover": _to_float(_pick(row, "换手率", default=0)),
        "seal_amount": _to_float(_pick(row, "封板资金", default=0)),
        "first_seal_time": _normalize_time(_pick(row, "首次封板时间", default="")),
        "last_seal_time": _normalize_time(_pick(row, "最后封板时间", default="")),
        "break_count": max(0, _to_int(_pick(row, "炸板次数", default=0))),
        "streak": streak,
        "industry": str(_pick(row, "所属行业", "行业", default="")).strip(),
    }


def _fetch_pool(source: str, date_str: str) -> list[dict]:
    """从 akshare 拉取某类股票池并归一化

    source 取值: limit_up / broken / limit_down
    """
    cache_key = f"pool:{source}:{date_str}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not _HAS_AKSHARE:
        _cache_set(cache_key, [], _pool_ttl(date_str))
        return []

    func_name = {
        "limit_up": "stock_zt_pool_em",
        "broken": "stock_zt_pool_zbgc_em",
        "limit_down": "stock_zt_pool_dtgc_em",
    }[source]
    fn = getattr(ak, func_name, None)
    if fn is None:
        _cache_set(cache_key, [], _pool_ttl(date_str))
        return []

    result: list[dict] = []
    try:
        df = fn(date=date_str)
        if df is not None and not df.empty:
            result = [_normalize_pool_row(row) for _, row in df.iterrows()]
    except Exception as e:
        logger.warning(f"fetch_pool({source}, {date_str}) failed: {e}")

    _cache_set(cache_key, result, _pool_ttl(date_str))
    return result


def fetch_limit_pool(date_str: str) -> list[dict]:
    return _fetch_pool("limit_up", date_str)


def fetch_broken_pool(date_str: str) -> list[dict]:
    return _fetch_pool("broken", date_str)


def fetch_limit_down_pool(date_str: str) -> list[dict]:
    return _fetch_pool("limit_down", date_str)


def _fetch_market_context() -> dict:
    """行情上下文:涨跌家数 / 指数 / 板块排名,任一失败都不影响其他字段"""
    def _sentiment() -> dict:
        try:
            return ds2.get_market_sentiment() or {}
        except Exception as e:
            logger.warning(f"review get_market_sentiment failed: {e}")
            return {}

    def _indices() -> list[dict]:
        try:
            return ds2.get_index_realtime() or []
        except Exception as e:
            logger.warning(f"review get_index_realtime failed: {e}")
            return []

    def _sectors() -> list[dict]:
        try:
            return ds2.get_sector_ranking() or []
        except Exception as e:
            logger.warning(f"review get_sector_ranking failed: {e}")
            return []

    with ThreadPoolExecutor(max_workers=3) as executor:
        sentiment_future = executor.submit(_sentiment)
        indices_future = executor.submit(_indices)
        sectors_future = executor.submit(_sectors)
        return {
            "sentiment": sentiment_future.result(),
            "indices": indices_future.result(),
            "sectors": sectors_future.result(),
        }


# ──────────────────────────────────────────────
# 指标计算
# ──────────────────────────────────────────────

def _streak_bucket(streak: int) -> str:
    if streak <= 1:
        return "首板"
    if streak == 2:
        return "2板"
    if streak == 3:
        return "3板"
    return "4板+"


def _ladder(limit_pool: list[dict]) -> dict[str, int]:
    ladder = {"首板": 0, "2板": 0, "3板": 0, "4板+": 0}
    for item in limit_pool:
        ladder[_streak_bucket(item.get("streak", 1))] += 1
    return ladder


def _max_streak(limit_pool: list[dict]) -> int:
    return max((item.get("streak", 1) for item in limit_pool), default=0)


def _broken_rate(limit_count: int, broken_count: int) -> float:
    denominator = limit_count + broken_count
    if denominator <= 0:
        return 0.0
    return round(broken_count / denominator, 4)


def _index_summary(indices: list[dict]) -> dict:
    valid = [i for i in indices if _to_float(i.get("change_pct", 0)) != 0 or i.get("price")]
    changes = [_to_float(i.get("change_pct", 0)) for i in valid]
    avg_change = round(sum(changes) / len(changes), 2) if changes else 0.0
    total_amount = round(sum(_to_float(i.get("amount", 0)) for i in valid), 2)
    return {
        "avg_change_pct": avg_change,
        "total_amount": total_amount,
        "items": [
            {
                "name": str(i.get("name", "")),
                "price": round(_to_float(i.get("price", 0)), 2),
                "change_pct": round(_to_float(i.get("change_pct", 0)), 2),
                "amount": round(_to_float(i.get("amount", 0)), 2),
            }
            for i in valid
        ],
    }


def _sector_limit_leaders(limit_pool: list[dict], top_n: int = 6) -> list[dict]:
    counts: dict[str, int] = {}
    for item in limit_pool:
        industry = item.get("industry") or "未分类"
        counts[industry] = counts.get(industry, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [{"name": name, "limit_up_count": count} for name, count in ordered]


def compute_temperature(
    limit_up_count: int,
    max_streak: int,
    broken_rate: float,
    rise_ratio: float,
    index_avg_change: float,
) -> dict:
    """情绪温度(0-100)与分项拆解

    权重设计(合计 100):
    - 涨停强度 30:80 家涨停视为满分
    - 连板高度 20:6 板视为满分
    - 封板质量 20:(1-炸板率) 映射
    - 市场广度 20:上涨家数占比
    - 指数表现 10:涨跌幅 +5% 视为满分
    """
    limit_score = min(100.0, limit_up_count / 80.0 * 100.0)
    height_score = min(100.0, max_streak / 6.0 * 100.0)
    seal_score = max(0.0, min(100.0, (1.0 - broken_rate) * 100.0))
    breadth_score = max(0.0, min(100.0, rise_ratio * 100.0))
    index_score = max(0.0, min(100.0, 50.0 + index_avg_change * 10.0))

    total = (
        limit_score * 0.30
        + height_score * 0.20
        + seal_score * 0.20
        + breadth_score * 0.20
        + index_score * 0.10
    )
    return {
        "score": round(total, 1),
        "breakdown": {
            "涨停强度": round(limit_score, 1),
            "连板高度": round(height_score, 1),
            "封板质量": round(seal_score, 1),
            "市场广度": round(breadth_score, 1),
            "指数表现": round(index_score, 1),
        },
    }


def classify_phase(temperature: float) -> str:
    if temperature >= 80:
        return "高潮"
    if temperature >= 60:
        return "发酵"
    if temperature >= 40:
        return "分歧"
    if temperature >= 20:
        return "修复"
    return "冰点"


def _tonight_view(temperature: float, broken_rate: float, max_streak: int, limit_down: int, index_avg: float) -> dict:
    """盘后对次日的判断(规则化,非投资建议)"""
    level = classify_phase(temperature)
    if level == "高潮":
        stance = "防守"
        summary = "情绪处于高位,次日优先防分歧与兑现,不追高位加速。"
        focus = ["低位首板/补涨", "板块内前排的换手机会"]
    elif level == "发酵":
        stance = "精选"
        summary = "情绪偏暖,可围绕主线板块做前排,但只做符合模式的机会。"
        focus = ["主线板块前排", "首板晋级二板"]
    elif level == "分歧":
        stance = "观察"
        summary = "情绪分歧,次日以竞价强弱为准,弱转强才考虑出手。"
        focus = ["竞价高开且断板不破前收的标的", "低位首板"]
    elif level == "修复":
        stance = "试错"
        summary = "情绪修复初期,可用小仓位试错低位首板,不做高位接力。"
        focus = ["低位首板", "超跌反弹"]
    else:
        stance = "等待"
        summary = "情绪冰点,优先控制仓位,等待涨停家数与连板高度回升。"
        focus = ["空仓观察", "记录首板池,等待修复信号"]

    risks: list[str] = []
    if broken_rate >= 0.30:
        risks.append(f"炸板率 {broken_rate * 100:.0f}%,封板质量偏弱,追高容易吃面")
    if max_streak >= 5:
        risks.append(f"最高连板 {max_streak} 板,高位股存在补跌风险")
    if limit_down >= 5:
        risks.append(f"跌停 {limit_down} 家,亏钱效应仍在扩散")
    if index_avg <= -1.0:
        risks.append("指数明显走弱,仓位应低于常态")
    if not risks:
        risks.append("暂无突出风险项,仍需遵守单笔止损与总仓位上限")

    return {"stance": stance, "summary": summary, "focus": focus, "risk": risks}


def _review_view(temperature: float, prev_temperature: float | None, max_streak: int, broken_rate: float) -> dict:
    """复盘后判断:结合昨日温度与结构给出周期位置"""
    if prev_temperature is None:
        phase = "数据不足"
        summary = "缺少昨日情绪数据,暂不判断周期方向。"
    else:
        delta = temperature - prev_temperature
        if delta >= 8:
            phase = "升温"
            summary = f"情绪较昨日升温 {delta:.0f} 分,赚钱效应扩散中,关注能否持续。"
        elif delta <= -8:
            phase = "降温"
            summary = f"情绪较昨日降温 {abs(delta):.0f} 分,注意高位股兑现与仓位收缩。"
        else:
            phase = "震荡"
            summary = f"情绪较昨日变化 {delta:+.0f} 分,处于震荡期,按模式交易而非按情绪。"
    return {
        "phase": phase,
        "summary": summary,
        "verify": [
            "次日竞价:主线前排是否高开、断板股是否弱转强",
            "首板晋级率:昨日首板今日能晋级二板的比例",
            f"炸板率:当前 {broken_rate * 100:.0f}%,观察是否继续抬升",
            f"连板高度:当前最高 {max_streak} 板,观察是否出现断板",
        ],
    }


def _seal_quality(seal_time: str, break_count: int, seal_amount: float) -> float:
    score = 50.0
    if seal_time:
        try:
            hour, minute = (int(x) for x in seal_time.split(":")[:2])
            total_min = hour * 60 + minute
            if total_min <= 570:
                score += 25.0
            elif total_min <= 600:
                score += 15.0
            elif total_min <= 630:
                score += 5.0
            else:
                score -= 5.0
        except (ValueError, TypeError):
            pass
    if break_count == 0:
        score += 15.0
    elif break_count == 1:
        score += 5.0
    elif break_count >= 3:
        score -= 10.0
    if seal_amount >= 1e8:
        score += 10.0
    elif seal_amount >= 5e7:
        score += 5.0
    elif seal_amount < 1e7:
        score -= 5.0
    return max(0.0, min(100.0, score))


# ──────────────────────────────────────────────
# 对外接口
# ──────────────────────────────────────────────

def build_review_board(
    date_str: str | None = None,
    *,
    limit_pool: list[dict] | None = None,
    broken_pool: list[dict] | None = None,
    limit_down_pool: list[dict] | None = None,
    yesterday_limit: list[dict] | None = None,
    yesterday_broken: list[dict] | None = None,
    market: dict | None = None,
) -> dict:
    """构建复盘看板

    除 date_str 外的参数用于测试注入;生产路径会自动取数。
    """
    target = date_str or date.today().strftime("%Y%m%d")
    today = _is_today(target)
    try:
        target_date = datetime.strptime(target, "%Y%m%d").date()
    except ValueError:
        target_date = date.today()

    prior_dates = _recent_trade_dates(1, end=target_date - timedelta(days=1))
    prior = prior_dates[0] if prior_dates else ""

    # 今日三池 + 昨日两池并行拉取,避免 5 次串行网络请求拖慢首屏
    tasks: list[tuple[str, Callable[[], list[dict]]]] = []
    if limit_pool is None:
        tasks.append(("limit_pool", lambda: fetch_limit_pool(target)))
    if broken_pool is None:
        tasks.append(("broken_pool", lambda: fetch_broken_pool(target)))
    if limit_down_pool is None:
        tasks.append(("limit_down_pool", lambda: fetch_limit_down_pool(target)))
    if yesterday_limit is None and prior:
        tasks.append(("yesterday_limit", lambda: fetch_limit_pool(prior)))
    if yesterday_broken is None and prior:
        tasks.append(("yesterday_broken", lambda: fetch_broken_pool(prior)))

    fetched: dict[str, list[dict]] = {}
    if tasks:
        with ThreadPoolExecutor(max_workers=min(len(tasks), 5)) as executor:
            futures = {name: executor.submit(fn) for name, fn in tasks}
            fetched = {name: future.result() for name, future in futures.items()}

    limit_pool = fetched.get("limit_pool", limit_pool) or []
    broken_pool = fetched.get("broken_pool", broken_pool) or []
    limit_down_pool = fetched.get("limit_down_pool", limit_down_pool) or []
    yesterday_limit = fetched.get("yesterday_limit", yesterday_limit) or []
    yesterday_broken = fetched.get("yesterday_broken", yesterday_broken) or []

    if market is None and today:
        market = _fetch_market_context()
    market = market or {}

    sentiment = market.get("sentiment") or {}
    # 2026-11-08：涨跌家数缺失时保留 None，不再 _to_int 兜成 0 —— 0 会被前端渲染成
    # "涨 0 / 跌 0 / 平 0"，与"未获取到涨跌家数"是两件事。上游 get_market_sentiment
    # 在走指数代理路径时会显式给出 counts_available=False。
    _rise_raw = sentiment.get("rise_count")
    _fall_raw = sentiment.get("fall_count")
    _flat_raw = sentiment.get("flat_count")
    counts_missing = (
        sentiment.get("counts_available") is False
        or (_rise_raw is None and _fall_raw is None and _flat_raw is None)
    )
    rise_count = None if counts_missing else _to_int(_rise_raw)
    fall_count = None if counts_missing else _to_int(_fall_raw)
    flat_count = None if counts_missing else _to_int(_flat_raw)
    denom = (rise_count or 0) + (fall_count or 0) + (flat_count or 0)
    rise_ratio = round((rise_count or 0) / denom, 3) if denom > 0 else 0.5

    index_info = _index_summary(market.get("indices") or [])
    sectors = market.get("sectors") or []

    limit_count = len(limit_pool)
    broken_count = len(broken_pool)
    down_count = len(limit_down_pool)
    broken_rate = _broken_rate(limit_count, broken_count)
    max_streak = _max_streak(limit_pool)

    temperature = compute_temperature(
        limit_up_count=limit_count,
        max_streak=max_streak,
        broken_rate=broken_rate,
        rise_ratio=rise_ratio,
        index_avg_change=index_info["avg_change_pct"],
    )
    temp_score = temperature["score"]

    # 昨日结构(用于对比与复盘后判断)
    y_limit_count = len(yesterday_limit)
    y_broken_count = len(yesterday_broken)
    y_broken_rate = _broken_rate(y_limit_count, y_broken_count)
    y_max_streak = _max_streak(yesterday_limit)
    y_temp = compute_temperature(
        limit_up_count=y_limit_count,
        max_streak=y_max_streak,
        broken_rate=y_broken_rate,
        rise_ratio=0.5,
        index_avg_change=0.0,
    )["score"] if yesterday_limit else None

    # 数据完整性:核心池全部为空说明当天无数据(非交易日照常返回空)
    available = bool(limit_pool or broken_pool or limit_down_pool)
    # 有效涨跌家数 > 0 才算完整;仅拿到涨停池、缺市场广度时标记 partial
    data_quality = "normal" if available and (rise_count or 0) > 0 else ("partial" if available else "empty")

    return {
        "date": f"{target[:4]}-{target[4:6]}-{target[6:]}",
        "available": available,
        "data_quality": data_quality,
        "source": "akshare+tencent",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": {
            "rise_count": rise_count,
            "fall_count": fall_count,
            "flat_count": flat_count,
            "counts_available": not counts_missing,
            "sentiment_basis": str(sentiment.get("basis", "counts" if not counts_missing else "unavailable")),
            "rise_ratio": rise_ratio,
            "sentiment_label": str(sentiment.get("sentiment", "--")),
            "index_avg_change_pct": index_info["avg_change_pct"],
            "total_amount": index_info["total_amount"],
            "indices": index_info["items"],
        },
        "limit": {
            "limit_up_count": limit_count,
            "limit_down_count": down_count,
            "broken_count": broken_count,
            "broken_rate": broken_rate,
            "broken_rate_pct": round(broken_rate * 100, 1),
            "max_streak": max_streak,
            "ladder": _ladder(limit_pool),
            "top_sectors": _sector_limit_leaders(limit_pool),
            "sector_ranking_top": [
                {"name": str(s.get("name", "")), "change_pct": round(_to_float(s.get("change_pct", 0)), 2)}
                for s in sorted(sectors, key=lambda x: _to_float(x.get("change_pct", 0)), reverse=True)[:5]
            ],
        },
        "temperature": {
            "score": temp_score,
            "level": classify_phase(temp_score),
            "breakdown": temperature["breakdown"],
        },
        "yesterday": {
            "limit_up_count": y_limit_count,
            "broken_count": y_broken_count,
            "broken_rate_pct": round(y_broken_rate * 100, 1) if yesterday_limit else 0.0,
            "max_streak": y_max_streak,
            "temperature": y_temp,
            "limit_up_delta": limit_count - y_limit_count,
        },
        "tonight": _tonight_view(
            temperature=temp_score,
            broken_rate=broken_rate,
            max_streak=max_streak,
            limit_down=down_count,
            index_avg=index_info["avg_change_pct"],
        ),
        "review": _review_view(
            temperature=temp_score,
            prev_temperature=y_temp,
            max_streak=max_streak,
            broken_rate=broken_rate,
        ),
    }


def get_sentiment_cycle(days: int = 5, *, pools: dict[str, list[dict]] | None = None) -> dict:
    """近 N 个交易日的情绪周期

    pools 参数用于测试注入,key 为 YYYYMMDD。
    为避免网络请求过重,周期只统计涨停池(涨停家数/最高连板/梯队),
    炸板率仅在今日看板中计算。
    """
    days = max(2, min(10, int(days or 5)))
    dates = _recent_trade_dates(days)

    def _one(date_str: str) -> dict:
        if pools is not None and date_str in pools:
            pool = pools[date_str]
        else:
            pool = fetch_limit_pool(date_str)
        count = len(pool)
        max_streak = _max_streak(pool)
        ladder = _ladder(pool)
        temp = compute_temperature(
            limit_up_count=count,
            max_streak=max_streak,
            broken_rate=0.0,
            rise_ratio=0.5,
            index_avg_change=0.0,
        )["score"]
        return {
            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
            "limit_up_count": count,
            "max_streak": max_streak,
            "ladder": ladder,
            "temperature": temp,
        }

    with ThreadPoolExecutor(max_workers=min(days, 5)) as executor:
        series = list(executor.map(_one, dates))

    series.reverse()  # 旧→新,便于前端画趋势
    valid = [row for row in series if row["limit_up_count"] > 0]
    if len(valid) >= 2:
        delta = valid[-1]["temperature"] - valid[0]["temperature"]
        trend = "升温" if delta >= 5 else ("降温" if delta <= -5 else "震荡")
    else:
        delta = 0.0
        trend = "数据不足"

    return {
        "days": days,
        "trend": trend,
        "temperature_delta": round(delta, 1),
        "series": series,
        "source": "akshare",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def analyze_first_boards(
    date_str: str | None = None,
    *,
    limit_pool: list[dict] | None = None,
    top: int = 20,
) -> dict:
    """首板分析:筛选当日首板,按封板质量排序并给出跟踪级别"""
    target = date_str or date.today().strftime("%Y%m%d")
    if limit_pool is None:
        limit_pool = fetch_limit_pool(target)

    first_boards = [item for item in limit_pool if item.get("streak", 1) == 1]
    items: list[dict] = []
    for item in first_boards:
        quality = _seal_quality(
            seal_time=item.get("first_seal_time", ""),
            break_count=item.get("break_count", 0),
            seal_amount=item.get("seal_amount", 0),
        )
        if quality >= 80:
            watch_level = "强"
        elif quality >= 60:
            watch_level = "中"
        else:
            watch_level = "弱"
        items.append({
            **item,
            "seal_quality": round(quality, 1),
            "watch_level": watch_level,
        })

    items.sort(key=lambda x: (x["seal_quality"], x["seal_amount"]), reverse=True)
    industries: dict[str, int] = {}
    for item in first_boards:
        industry = item.get("industry") or "未分类"
        industries[industry] = industries.get(industry, 0) + 1

    return {
        "date": f"{target[:4]}-{target[4:6]}-{target[6:]}",
        "count": len(items),
        "items": items[: max(1, min(100, top))],
        "industries": [
            {"name": name, "count": count}
            for name, count in sorted(industries.items(), key=lambda kv: kv[1], reverse=True)[:8]
        ],
        "source": "akshare",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

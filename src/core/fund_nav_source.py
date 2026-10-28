"""基金数据适配器(阶段六批次 A,T-A1)。

职责:
- 统一收口"基金盘中估值 / 披露持仓 / 历史净值"三类数据获取
- 主源: ``data_source_v2.get_fund_realtime_tencent``(腾讯接口,已返回 estimated_nav,
  置信度 high)
- 备源: akshare 披露持仓拟合(前十大重仓 × 个股实时涨跌,置信度 medium),
  仅当主源缺数据时启用
- 历史净值: 主源腾讯/东财历史接口,备源 akshare ``fund_open_fund_info_em``(累计净值)

工程约定(架构文档 §5/§6):
- akshare 一律**惰性导入**——本机 akshare 曾因 pip 502 无法重装,ImportError
  必须转为可识别异常 ``AkshareUnavailable`` 并由调用方降级,绝不 500
- 季报级披露持仓走 DataCache 文件层(TTL 24h),接口抖动可容忍
- 测试 100% mock,本模块不做任何真实网络请求的单测路径
"""
from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from src.core.cache import DataCache
from src.utils.convert import safe_float as _safe_float, safe_str as _safe_str

# ===== 常量(集中定义,便于后续配置化) =====

#: 季报披露持仓缓存 TTL(秒)——季报 ~15 个工作日更新一次,24h 文件缓存足够
HOLDINGS_CACHE_TTL: int = 24 * 3600

#: 历史净值缓存 TTL(秒)——日级更新数据,1h 足够
NAV_HISTORY_CACHE_TTL: int = 3600

#: 披露持仓默认取前十大重仓(内存硬预算约束)
DEFAULT_TOP_N: int = 10

#: 披露持仓缓存条目上限(内存硬预算,架构文档 §6)
_HOLDINGS_CACHE_MAX_ENTRIES: int = 200

# 披露持仓缓存(文件层 data/cache/fund_holdings/,进程重启不丢)
_holdings_cache = DataCache(
    cache_dir="data/cache/fund_holdings",
    default_ttl=HOLDINGS_CACHE_TTL,
    memory_maxsize=_HOLDINGS_CACHE_MAX_ENTRIES,
    max_files=_HOLDINGS_CACHE_MAX_ENTRIES,
)

# 历史净值缓存(文件层 data/cache/fund_nav_history/)
_nav_history_cache = DataCache(
    cache_dir="data/cache/fund_nav_history",
    default_ttl=NAV_HISTORY_CACHE_TTL,
    memory_maxsize=_HOLDINGS_CACHE_MAX_ENTRIES,
    max_files=_HOLDINGS_CACHE_MAX_ENTRIES,
)

# akshare 惰性导入结果进程内复用(线程安全:to_thread 并发调用)
_akshare_lock = threading.Lock()
_akshare_module: Any = None


class AkshareUnavailable(RuntimeError):
    """akshare 不可用(未安装/损坏/导入失败),调用方应走降级路径。"""


def _import_akshare() -> Any:
    """惰性导入 akshare(进程内缓存导入结果)。

    Raises:
        AkshareUnavailable: 导入失败时抛出,绝不向上传播裸 ImportError。
    """
    global _akshare_module
    if _akshare_module is not None:
        return _akshare_module
    with _akshare_lock:
        if _akshare_module is not None:
            return _akshare_module
        try:
            import akshare as ak  # 惰性导入:冷启动不加载 2-5s 的重依赖
        except Exception as e:  # ImportError 及损坏包的各种异常一律转义
            raise AkshareUnavailable(f"akshare 不可用: {e}") from e
        _akshare_module = ak
        return _akshare_module


# ===== 数据结构 =====


@dataclass
class FundHoldings:
    """基金披露持仓(最新一期季报前十大重仓)。"""

    fund_code: str
    as_of: str = ""  # 数据截止期,如 "2026Q3"(季报有 ~15 个工作日滞后)
    holdings: list[dict] = field(default_factory=list)  # [{stock_code, stock_name, weight}]
    source: str = "akshare"

    def to_cache_dict(self) -> dict:
        return {
            "fund_code": self.fund_code,
            "as_of": self.as_of,
            "holdings": self.holdings,
            "source": self.source,
        }

    @classmethod
    def from_cache_dict(cls, data: dict) -> "FundHoldings":
        return cls(
            fund_code=_safe_str(data.get("fund_code")),
            as_of=_safe_str(data.get("as_of")),
            holdings=list(data.get("holdings") or []),
            source=_safe_str(data.get("source"), "akshare"),
        )


# ===== 工具函数 =====


def is_qdii(fund_name: str) -> bool:
    """按基金名称判断是否 QDII(QDII 因海外市场时差,盘中估值参考意义有限)。"""
    return "QDII" in (fund_name or "").upper()


def _latest_quarter(today: _dt.date | None = None) -> str:
    """推断最近已披露季报期(Q1 披露 ~4/22,Q2 ~7/21,Q3 ~10/22,Q4 次年 ~1/21)。

    采用保守滞后:季度结束月 +1 月的 25 日后视为已披露,取上一完整季度。
    """
    d = today or _dt.date.today()
    # 每季披露截止: Q1→4/25, Q2→7/25, Q3→10/25, Q4→次年1/25
    boundaries = [(4, 25), (7, 25), (10, 25), (12, 31)]
    if (d.month, d.day) <= (1, 25):
        return f"{d.year - 1}Q4"
    quarter = 0
    for i, (m, day) in enumerate(boundaries, start=1):
        if (d.month, d.day) >= (m, day):
            quarter = i
    if quarter == 0:
        return f"{d.year - 1}Q4"
    return f"{d.year}Q{quarter}"


# ===== 披露持仓(备源核心数据) =====


def get_fund_holdings(code: str, top: int = DEFAULT_TOP_N) -> FundHoldings | None:
    """获取基金最新季报前十大重仓股(akshare,24h 文件缓存)。

    Returns:
        FundHoldings;数据不可用(akshare 缺失/接口失败/无持仓数据)时返回 **None**,
        由调用方标记 degraded,绝不抛出网络异常。

    测试注意:缓存实例可通过 monkeypatch ``fund_nav_source._holdings_cache`` 指向 tmp。
    """
    code = _safe_str(code).strip()
    if not code or not code.isdigit() or len(code) != 6:
        return None
    cache_key = f"fund_holdings_{code}_{top}"
    cached = _holdings_cache.get(cache_key)
    if cached is not None:
        return FundHoldings.from_cache_dict(cached)

    try:
        ak = _import_akshare()
        # akshare fund_portfolio_hold_em(symbol, date): date 为年份,返回该年各季度或最新季度
        df = ak.fund_portfolio_hold_em(symbol=code, date=str(_dt.date.today().year))
    except AkshareUnavailable as e:
        logger.warning(f"get_fund_holdings {code}: akshare 不可用,降级 ({e})")
        return None
    except Exception as e:
        logger.warning(f"get_fund_holdings {code}: 接口失败,降级 ({e})")
        return None

    try:
        if df is None or df.empty:
            return None
        # 部分版本按季度返回多期数据,取"季度"列最后一期(最新披露)
        if "季度" in df.columns and len(df) > 0:
            latest_q = str(df["季度"].iloc[-1])
            df = df[df["季度"].astype(str) == latest_q]
            as_of = latest_q
        else:
            as_of = _latest_quarter()
        holdings: list[dict] = []
        for _, row in df.iterrows():
            stock_code = _safe_str(row.get("股票代码")).zfill(6)
            weight = _safe_float(row.get("占净值比例"))
            if not stock_code or weight <= 0:
                continue
            holdings.append({
                "stock_code": stock_code,
                "stock_name": _safe_str(row.get("股票名称")),
                "weight": round(weight, 4),
            })
        if not holdings:
            return None
        holdings.sort(key=lambda h: h["weight"], reverse=True)
        info = FundHoldings(fund_code=code, as_of=_safe_str(as_of), holdings=holdings[:top])
        _holdings_cache.set(cache_key, info.to_cache_dict(), ttl=HOLDINGS_CACHE_TTL)
        return info
    except Exception as e:
        logger.warning(f"get_fund_holdings {code}: 解析失败,降级 ({e})")
        return None


def fit_estimate_from_holdings(
    holdings: FundHoldings | None,
    stock_pct_map: dict[str, float],
    index_pct: float = 0.0,
) -> tuple[float, float] | None:
    """基于披露持仓拟合盘中估算涨跌(备源口径,架构文档 §5)。

    公式: est_pct = Σ(w_i × pct_i) + (1 − Σw_i) × pct_index,w_i 为披露权重(归一)。

    Args:
        holdings: 披露持仓(None/空 → 数据不足返回 None)
        stock_pct_map: {stock_code: 当日涨跌%}
        index_pct: 未覆盖权重的基准指数涨跌%(近似填充)

    Returns:
        (est_pct, coverage) 或 None(数据不足,调用方标 low 置信度,不编数据)。
        coverage ∈ (0,1],为已覆盖权重占比,供置信度说明。
    """
    if holdings is None or not holdings.holdings:
        return None
    covered = 0.0
    acc = 0.0
    for h in holdings.holdings:
        w = _safe_float(h.get("weight")) / 100.0
        pct = stock_pct_map.get(_safe_str(h.get("stock_code")))
        if pct is None:
            continue  # 个股涨跌拿不到,归入未覆盖部分
        covered += w
        acc += w * _safe_float(pct)
    if covered <= 0.0:
        return None
    est_pct = acc + (1.0 - covered) * _safe_float(index_pct)
    return round(est_pct, 3), round(min(covered, 1.0), 4)


# ===== 历史净值(风险指标数据源) =====

_HISTORY_PERIOD_DAYS: dict[str, int] = {
    "1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "all": 0,
}


def _period_start(period: str) -> str:
    """period → 窗口起始日(YYYY-MM-DD);all 返回空串表示不限。"""
    days = _HISTORY_PERIOD_DAYS.get((period or "1y").strip().lower(), 365)
    if days <= 0:
        return ""
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


def _sorted_rows(rows: list[dict]) -> list[dict]:
    """统一按日期升序(上游分页/备源返回顺序不可信,风险计算依赖时间方向)。"""
    return sorted(
        (r for r in rows if _safe_str(r.get("date"))),
        key=lambda r: _safe_str(r.get("date")),
    )


def get_fund_nav_history(code: str, period: str = "1y") -> list[dict]:
    """获取基金历史净值(主源东财,备源 akshare 累计净值)。

    Returns:
        按日期**升序**、限定在 period 窗口内的 [{"date", "nav", "acc_nav", "change_pct"}];
        无数据返回 [](调用方需将样本 <2 点标记为降级,不编数据)。

    测试注意:缓存实例可通过 monkeypatch ``fund_nav_source._nav_history_cache`` 指向 tmp。
    """
    code = _safe_str(code).strip()
    if not code or not code.isdigit() or len(code) != 6:
        return []
    cache_key = f"fund_nav_history_{code}_{period}"
    cached = _nav_history_cache.get(cache_key)
    if cached is not None:
        return list(cached)

    # 主源: 东财历史净值(内部已按窗口过滤并升序;失败时降级到腾讯单点)
    from src.core.data_source_v2 import get_fund_history_tencent

    try:
        data = _sorted_rows(get_fund_history_tencent(code, period))
    except Exception as e:
        logger.warning(f"get_fund_nav_history {code} 主源失败: {e}")
        data = []
    if data:
        _nav_history_cache.set(cache_key, data, ttl=NAV_HISTORY_CACHE_TTL)
        return list(data)

    # 备源: akshare 累计净值(复权口径,返回全量历史,需本地按窗口裁剪)
    try:
        ak = _import_akshare()
        df = ak.fund_open_fund_info_em(symbol=code, indicator="累计净值")
    except AkshareUnavailable as e:
        logger.warning(f"get_fund_nav_history {code}: 备源 akshare 不可用 ({e})")
        return []
    except Exception as e:
        logger.warning(f"get_fund_nav_history {code}: 备源接口失败 ({e})")
        return []

    try:
        if df is None or df.empty:
            return []
        start = _period_start(period)
        points: list[tuple[str, float]] = []
        for _, row in df.iterrows():
            date_str = _safe_str(row.get("净值日期"))[:10]
            acc = _safe_float(row.get("累计净值"))
            if not date_str or acc <= 0:
                continue
            if start and date_str < start:
                continue
            points.append((date_str, acc))
        points.sort(key=lambda x: x[0])
        result: list[dict] = []
        prev_acc: float | None = None
        for date_str, acc in points:
            change_pct = 0.0
            if prev_acc is not None and prev_acc > 0:
                change_pct = round((acc - prev_acc) / prev_acc * 100, 2)
            result.append({"date": date_str, "nav": acc, "acc_nav": acc, "change_pct": change_pct})
            prev_acc = acc
        if result:
            _nav_history_cache.set(cache_key, result, ttl=NAV_HISTORY_CACHE_TTL)
        return result
    except Exception as e:
        logger.warning(f"get_fund_nav_history {code}: 备源解析失败 ({e})")
        return []

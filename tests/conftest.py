"""测试配置与公共fixtures"""
import datetime as _dt
import hashlib
import inspect
import os
import re
import sys
import warnings
from pathlib import Path

import pytest

# 确保项目根目录与本地依赖在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_pylibs = str(Path(_project_root) / "pylibs")
if _pylibs not in sys.path:
    sys.path.insert(0, _pylibs)

# ===== 阶段六批次A:CI 级红线守卫 =====
# web/user_fund_positions.json 是用户真实持仓文件,测试绝不允许触碰。
_PROTECTED_POSITIONS_FILE = Path(_project_root) / "web" / "user_fund_positions.json"


def _snapshot_protected_file() -> dict | None:
    """对受保护持仓文件做 mtime + sha256 快照(只读,不修改)。"""
    p = _PROTECTED_POSITIONS_FILE
    if not p.exists():
        return None
    import os as _os
    st = p.stat()
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha256": digest}


@pytest.fixture(scope="session", autouse=True)
def _guard_real_positions_file():
    """CI 级断言(阶段六批次A):整个测试会话前后,真实持仓文件 mtime 与 sha256 不变。

    任何测试若读写 web/user_fund_positions.json(哪怕只是 mtime 变化),
    全量回归将在会话结束时失败,防止真实持仓数据被测试污染。
    """
    before = _snapshot_protected_file()
    yield
    after = _snapshot_protected_file()
    if before != after:
        raise AssertionError(
            "测试运行修改了 web/user_fund_positions.json(真实持仓文件)!\n"
            f"before={before}\nafter={after}\n"
            "铁律:测试一律使用 tmp_path/mock,绝不触碰该文件。"
        )

# P1 修复(2026-07-29)配套:测试环境全局设为 dev 模式
# 原因:auth.require_admin 在生产环境(APP_ENV != "dev")未配置 ADMIN_TOKEN 时抛 500(fail-closed)
# 测试默认不配置 ADMIN_TOKEN,需设为 dev 模式才能放行
# 单独的 test_auth.py 测试 fail-closed 行为时,会显式覆盖 APP_ENV=production
os.environ.setdefault("APP_ENV", "dev")


@pytest.fixture(autouse=True)
def _clear_global_caches():
    """每个测试前清理全局缓存,避免测试间状态共享

    P0 新增:LLM 响应缓存(_llm_cache)和 AI 端点缓存(_ai_cache)是全局单例,
    若不清理会导致测试间相互污染(如 test_chat_with_retries 命中前一个测试的缓存)。
    """
    try:
        from src.core.llm_router import _llm_cache
        _llm_cache.clear()
    except Exception:
        pass
    try:
        from web.routes.agent import _ai_cache
        _ai_cache.clear()
    except Exception:
        pass
    # 阶段四:限流计数也属于全局状态,逐个测试清零(写端点 10/min、LLM 3/min),
    # 避免不同测试累计触发 429 造成随机失败。
    try:
        from web.rate_limiter import limiter
        limiter._storage.reset()
    except Exception:
        pass
    # 阶段二 R-06:匿名读请求指纹/全站安全阀也是进程内全局状态,逐个测试清零
    try:
        from web.rate_limiter import _anon_read_guard
        _anon_read_guard.reset()
    except Exception:
        pass
    yield
    # 测试后也清理(防止副作用泄露到下一个测试)
    try:
        from src.core.llm_router import _llm_cache
        _llm_cache.clear()
    except Exception:
        pass
    try:
        from web.routes.agent import _ai_cache
        _ai_cache.clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_request_metrics():
    """每个测试前重置全局请求指标单例（2026-10-26 新增测试隔离）

    `web.middleware.metrics.metrics` 是模块级单例，所有经由 TestClient 的请求都会累加。
    test_metrics.py::test_health_metrics_records_this_request 用的是相对断言
    （`status_distribution["200"] >= request_count`），隐含"本测试期内所有请求都是 200"。
    而其他文件里大量鉴权/校验用例会刻意产生 401/422，使 request_count 增长快于 200 计数，
    于是该用例在**全量运行**时随机失败、单独运行必过——属于典型的全局单例未隔离。

    这里在每次测试前清零（只影响全局单例；test_metrics 中其余用例自带
    RequestMetrics() 局部实例，不受影响）。
    """
    try:
        from web.middleware.metrics import metrics as _global_metrics
        _global_metrics.reset()
    except Exception:
        pass
    yield


@pytest.fixture
def sample_quote():
    """示例行情数据"""
    return {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1688.00,
        "change": 18.50,
        "change_pct": 1.11,
        "volume": 32567890,
        "amount": 5498765432.0,
        "high": 1695.00,
        "low": 1670.00,
        "open": 1675.00,
        "prev_close": 1669.50,
        "turnover": 2.59,
        "pe_ttm": 28.5,
        "pb": 9.2,
        "total_market_value": 2120000000000,
        "circ_market_value": 2120000000000,
        "high_limit": 1836.45,
        "low_limit": 1502.55,
    }


@pytest.fixture
def sample_kline():
    """示例K线数据"""
    return [
        {"date": "2026-06-01", "open": 1660, "close": 1670, "high": 1680, "low": 1655, "volume": 28000000, "amount": 4660000000},
        {"date": "2026-06-02", "open": 1672, "close": 1685, "high": 1690, "low": 1668, "volume": 31000000, "amount": 5210000000},
        {"date": "2026-06-03", "open": 1683, "close": 1678, "high": 1692, "low": 1675, "volume": 29500000, "amount": 4960000000},
        {"date": "2026-06-04", "open": 1680, "close": 1695, "high": 1700, "low": 1678, "volume": 33000000, "amount": 5580000000},
        {"date": "2026-06-05", "open": 1693, "close": 1688, "high": 1698, "low": 1685, "volume": 30500000, "amount": 5150000000},
    ]


@pytest.fixture
def sample_capital_flow():
    """示例资金流向数据"""
    return {
        "main_inflow": 500000000,
        "main_outflow": 450000000,
        "main_net_inflow": 50000000,
        "main_inflow_pct": 52.6,
        "super_large_net_inflow": 30000000,
        "large_net_inflow": 20000000,
        "medium_net_inflow": -15000000,
        "small_net_inflow": -35000000,
    }


@pytest.fixture
def sample_financial():
    """示例财务数据"""
    return {
        "pe_ttm": 28.5,
        "pb": 9.2,
        "roe": 31.2,
        "gross_margin": 91.5,
        "net_margin": 52.3,
        "revenue_yoy": 15.8,
        "profit_yoy": 18.2,
    }


# ===== 确定性日期辅助(消除"日期炸弹")=====
# 背景: PredictionStore.list_predictions(days=N) / list_recommendations(days=N)
# 的过滤基准是 SQLite 的 date('now')(真实时钟)。测试若用硬编码日期 seed, 该日期
# 会随时间滑出滑动窗口, 造成"今天必过、某个具体日期起必挂"的日历型失败。
# 这类失败不抖动、只到期引爆, 比随机 flaky 更难排查。
#
# 约定: 凡是断言依赖"滑动窗口"的用例, seed 日期必须与查询基准同源 ——
# 即用下面的辅助函数按真实今天取相对偏移, 而不是写死某个日历日。
# 注意: 这不是"把日期写死成更远的未来", 那只是把炸弹推远。

_CST = _dt.timezone(_dt.timedelta(hours=8))


def trading_days_ago(n: int) -> str:
    """返回 n 个交易日前的日期字符串(YYYY-MM-DD, 跳过周末)。

    n=0 表示今天。用于给 store 的 seed 数据提供"永远落在滑动窗口内"的日期。
    """
    if n < 0:
        raise ValueError(f"n 必须 >= 0, 收到 {n}")
    d = _dt.date.today()
    remaining = n
    while remaining > 0:
        d -= _dt.timedelta(days=1)
        if d.weekday() < 5:  # 0=周一 ... 4=周五
            remaining -= 1
    return d.strftime("%Y-%m-%d")


def fixed_cst(y: int, m: int, d: int, hh: int = 15, mm: int = 40) -> _dt.datetime:
    """显式构造北京时间 datetime, 替代依赖真实时钟的 beijing_now()。

    服务层编排函数(如 verify_daily)都接受 now 入参, 应直接传入本函数的结果,
    而不是 monkeypatch 时钟或依赖运行时的真实时刻。
    """
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_CST)


def cst_on(date_str: str, hh: int = 15, mm: int = 40) -> _dt.datetime:
    """由 'YYYY-MM-DD' 构造当日北京时间 datetime(与 trading_days_ago 配合)。

    典型用法: 让"注入的 now"与"seed 的日期"来自同一个真实时钟基准。
        today = trading_days_ago(0)
        now = cst_on(today)
    """
    d = _dt.date.fromisoformat(date_str)
    return fixed_cst(d.year, d.month, d.day, hh, mm)


# ===== 启发式告警钩子(只 warn, 不 fail)=====
# 两条检查都是启发式的, 做成失败会误伤合法用例(如专门测历史/未来日期边界的用例),
# 最终一定会被人用 noqa 关掉而失去作用。故只 warnings.warn, 并提供豁免标记:
#   @pytest.mark.allow_future_date     —— 允许用例包含未来日期字面量
#   @pytest.mark.allow_hardcoded_date  —— 允许"硬编码日期 + 滑动窗口查询"共存

_DATE_LITERAL_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
# 只有带 days= 的调用才是"按真实时钟滑动"的查询; 带显式 trade_date 的不算。
_SLIDING_WINDOW_RE = re.compile(
    r"list_(?:predictions|recommendations)\([^)]{0,200}?days\s*=", re.DOTALL
)


def _date_literals(text: str) -> list[tuple[str, _dt.date]]:
    """提取文本中的 YYYY-MM-DD 字面量(忽略非法日期)。"""
    out: list[tuple[str, _dt.date]] = []
    for m in _DATE_LITERAL_RE.finditer(text):
        try:
            out.append((m.group(0), _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            continue
    return out


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "allow_future_date: 允许用例包含未来日期字面量(启发式检查豁免)"
    )
    config.addinivalue_line(
        "markers",
        "allow_hardcoded_date: 允许硬编码日期与按真实时钟滑动的查询共存(启发式检查豁免)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """收集后扫描用例源码, 对两类"日期炸弹"风险发出告警(不失败)。

    检查 A(未来日期): 用例源码里出现晚于今天的 YYYY-MM-DD 字面量。
        风险: 用例隐含"今天是某个更早的日期"的假设, 到期后语义会变。
    检查 B(硬编码日期 + 滑动窗口查询): 同一用例里既有 days= 形式的
        list_predictions/list_recommendations 查询, 又有硬编码日期。
        风险: 查询基准是 date('now'), 硬编码日期会随时间滑出窗口 → 日期炸弹。
    """
    today = _dt.date.today()
    file_text: dict[str, str] = {}
    reported: set[tuple[str, str, str]] = set()

    def _text(path: str) -> str:
        if path not in file_text:
            try:
                file_text[path] = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                file_text[path] = ""
        return file_text[path]

    for item in items:
        path = str(getattr(item, "fspath", ""))
        if not path.endswith(".py"):
            continue
        raw = _text(path)
        # 便宜的预筛: 文件里没有任何日期字面量或不含滑动窗口查询 → 跳过
        if "20" not in raw or not _DATE_LITERAL_RE.search(raw):
            continue
        func = getattr(item, "function", None)
        if func is None:
            continue
        try:
            src = inspect.getsource(func)
        except (OSError, TypeError):
            continue
        markers = {m.name for m in item.iter_markers()}
        literals = _date_literals(src)

        # 检查 A: 未来日期
        if literals and "allow_future_date" not in markers:
            future = [s for s, d in literals if d > today]
            key = (path, item.name, "future")
            if future and key not in reported:
                reported.add(key)
                warnings.warn(
                    f"[日期炸弹/未来日期] {item.name} 含晚于今天({today})的日期字面量 "
                    f"{sorted(set(future))}。若用例隐含『今天是更早日期』的假设, "
                    "请改用 conftest.trading_days_ago()/cst_on() 取相对基准; "
                    "确属边界用例请加 @pytest.mark.allow_future_date。",
                    stacklevel=2,
                )

        # 检查 B: 硬编码日期 + 滑动窗口查询
        if literals and "allow_hardcoded_date" not in markers:
            if _SLIDING_WINDOW_RE.search(src):
                key = (path, item.name, "hardcoded")
                if key not in reported:
                    reported.add(key)
                    warnings.warn(
                        f"[日期炸弹/滑动窗口] {item.name} 同时含 days= 形式的"
                        "list_predictions/list_recommendations 查询与硬编码日期 "
                        f"{sorted({s for s, _ in literals})}。查询基准是 date('now'), "
                        "硬编码日期会随时间滑出窗口而静默丢行; 请改用 "
                        "conftest.trading_days_ago() 让 seed 与查询同源, "
                        "或加 @pytest.mark.allow_hardcoded_date。",
                        stacklevel=2,
                    )

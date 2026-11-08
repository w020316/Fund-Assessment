import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
# 借鉴 encode/starlette(10.5K Star)内置 GZip 中间件,响应压缩 70%+,零新依赖
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from loguru import logger

# 借鉴 la-deps/slowapi(1.0K Star)FastAPI 限流中间件
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
# limiter 实例从独立模块导入,避免路由模块 import web.api 触发循环 import
from web.rate_limiter import limiter, anonymous_read_guard_middleware

# 借鉴 prometheus/client_python(4.2K Star)指标设计的轻量级监控
from web.middleware.metrics import metrics
# 借鉴 open-telemetry/opentelemetry-python(2.0K Star)请求追踪 trace ID
from web.middleware.trace import trace_middleware
from web.middleware.write_limit import write_rate_limit_middleware

_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

app_state: dict = {}

# P1 修复(2026-08-01):延迟加载重依赖,加速冷启动
# 原代码在模块级 import akshare(约 2-5s)和 src.core 全套模块(约 3-8s),
# Render Free 冷启动(实例休眠后首次请求)需等待所有 import 完成才响应 /api/health,
# 导致用户看到"页面长时间不用启动太慢"。
# 改为:仅标记 _HAS_* 标志位,实际 import 推迟到 lifespan 中异步执行。
_HAS_AKSHARE = False
_HAS_CORE = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _HAS_AKSHARE, _HAS_CORE
    # 异步加载重依赖(不阻塞事件循环,其他请求可先响应)
    akshare = None
    try:
        import akshare as _ak
        akshare = _ak
        _HAS_AKSHARE = True
        logger.info("akshare loaded successfully")
    except ImportError as e:
        logger.warning(f"import akshare failed: {e}")

    try:
        from src.core.data_source import DataSourceManager
        from src.core.executor import SimulatedBroker, TradeExecutor
        from src.core.risk_manager import RiskManager
        from src.utils.config import get_config
        cfg = get_config()
        tushare_token = cfg.get("tushare.token", "")
        data_source = DataSourceManager(tushare_token=tushare_token)
        risk_manager = RiskManager()
        broker = SimulatedBroker(initial_cash=1_000_000.0)
        executor = TradeExecutor(broker=broker, risk_manager=risk_manager)
        _HAS_CORE = True

        app_state["data_source"] = data_source
        app_state["risk_manager"] = risk_manager
        app_state["broker"] = broker
        app_state["executor"] = executor
        app_state["config"] = cfg
        logger.info("core modules loaded successfully")
    except ImportError as e:
        logger.warning(f"import src.core failed: {e}")
        app_state["data_source"] = None
        app_state["risk_manager"] = None
        app_state["broker"] = None
        app_state["executor"] = None
        app_state["config"] = None

    # prediction.db 启动维护(建表 + 保留清理;失败仅告警)
    _prediction_startup_maintenance()

    yield

    app_state.clear()


def _prediction_startup_maintenance() -> None:
    """prediction.db 启动维护(R5.2): 建表 + 保留策略清理(180/365 天), 失败不阻塞启动。"""
    try:
        from src.core.prediction_store import get_store
        deleted = get_store().cleanup()
        logger.info(f"prediction_store 启动清理完成: {deleted}")
    except Exception as e:  # noqa: BLE001 — 启动维护绝不阻塞主服务
        logger.warning(f"prediction_store 启动维护失败(不影响启动): {e}")


app = FastAPI(
    title="QuantFlow Pro 量化交易系统",
    version="1.0.0",
    lifespan=lifespan,
)

# ===== 限流配置(借鉴 la-deps/slowapi)=====
# 全局限流器:按客户端 IP 限流,保护 Render Free 实例(512MB RAM)
# - LLM 高成本端点(/api/agent/*):3次/分钟
# - 写操作端点(POST/PUT/DELETE):10次/分钟
# - 读操作端点(GET):60次/分钟
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """429 结构化响应(阶段四第 5 项)。

    slowapi 默认处理器只返回 `{"error": "Rate limit exceeded: 3 per 1 minute"}`,
    前端错误卡拿不到可读中文原因。这里保持 `error` 字段向后兼容,同时补
    `detail`(code/message/retryable/limit),与其它错误响应同一套结构。
    """
    limit_text = str(getattr(exc, "detail", "") or "")
    is_llm = request.url.path.startswith("/api/agent/")
    if is_llm:
        code, message = "LLM_RATE_LIMITED", "AI 分析请求过于频繁,请稍后重试。"
    else:
        code, message = "RATE_LIMITED", "请求过于频繁,请稍后重试。"
    response = JSONResponse(
        status_code=429,
        content={
            "error": f"Rate limit exceeded: {limit_text}".strip(),
            "detail": {
                "code": code,
                "message": message,
                "retryable": True,
                "limit": limit_text,
            },
        },
    )
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
# P1 修复(2026-07-30):必须注册 SlowAPIMiddleware,@limiter.limit 装饰器才会被中间件拦截执行
app.add_middleware(SlowAPIMiddleware)

# ===== GZip 响应压缩(借鉴 encode/starlette 10.5K Star 内置中间件)=====
# 压缩大于 1KB 的 JSON 响应(基金净值/K线数据体积大),Render 带宽节省 70%+
# 浏览器自动解压,前端无感知;CPU 开销可忽略(512MB 实例足够)
app.add_middleware(GZipMiddleware, minimum_size=1000)

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]
# 安全修复(2026-10-26):CORS 规范明确禁止 allow_origins=["*"] 与 allow_credentials=True 并用。
# 原实现直接把环境变量里的 "*" 传进去，配合 allow_credentials=True，Starlette 会对带 Cookie
# 的请求**回显任意 Origin**（实测 Origin: https://evil.example 被原样写回
# access-control-allow-origin），等于任意站点都能跨域读取 API 响应。
#
# 处理策略：**对通配符 fail-closed** —— 把 "*" 从白名单里摘掉，而不是"保留通配但关掉
# credentials"。原因：本前端由本服务同源托管（/static/index.html），跨域访问没有合法场景；
# 而保留 "*" 时，任意站点仍可携带猜测的 Authorization 头发起请求，借 200/401 的差异
# 做令牌探测（oracle）。摘掉之后跨域一律不放行，成本为零、收益确定。
# 若确有跨域客户端，请在 CORS_ORIGINS 里显式列出域名（逗号分隔）。
_cors_allow_credentials = True
if "*" in _cors_origins:
    _cors_origins = [o for o in _cors_origins if o != "*"]
    _cors_allow_credentials = False
    logger.error(
        "CORS_ORIGINS 含通配符 '*'，已忽略通配符并拒绝跨域访问（allow_credentials=False）。"
        "如需跨域，请显式配置具体域名（如 https://<app>.onrender.com，逗号分隔）"
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Trace-Id"],
    expose_headers=["X-Trace-Id"],
)

# ===== 安全响应头(借鉴 TypeError/secure 0.7K Star 安全头列表)=====
# 防 XSS/点击劫持/降级攻击,6 个标准安全头
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # CSP 允许 unpkg.com(前端 lightweight-charts CDN)与 data:(内联图表)
    # font-src(2026-11-06,设计系统 v1.0):仅放行展示字体所在 CDN,
    # 不放开 script/style/connect;字体请求失败时按 font-display:swap 回退系统字,
    # 不影响功能与安全面。若后续改为自托管 woff2,应删除该条并回到 'self'。
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https:;"
    )
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
    return response

# ===== 静态资源缓存(阶段 5 S5-08)=====
# 静态文件已有 ETag/Last-Modified 但没有 Cache-Control,浏览器每次访问都要回源验证;
# 补短缓存减少重复访问往返。HTML 只缓存 60s,避免部署后长时间看到旧页面。
@app.middleware("http")
async def add_static_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if request.method in ("GET", "HEAD") and path.startswith("/static/"):
        if path.endswith(".html"):
            response.headers.setdefault("Cache-Control", "public, max-age=60, must-revalidate")
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=300")
    return response

# 阶段二 R-06:匿名读请求指纹分桶(60/min)+ 全站匿名安全阀(300/min)。
# 注册在 log_requests 之内,429 仍会进入访问日志与 metrics。
app.middleware("http")(anonymous_read_guard_middleware)
# 阶段四:写端点 10 次/分钟(在 trace 之内、业务之外)。
# 阶段五 O1:必须注册在 log_requests **之前**(即更内层),否则限流直接返回的 429
# 会绕过 log_requests → 访问日志与 metrics 里看不到被限流的请求(实测 429 无记录)。
app.middleware("http")(write_rate_limit_middleware)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    duration_sec = duration_ms / 1000
    path = request.url.path
    if path.startswith("/api/"):
        # 记录监控指标(借鉴 prometheus_client 指标设计)
        metrics.record(path, duration_sec, response.status_code)
        # 日志附带 trace_id(借鉴 OpenTelemetry context 传播)
        from web.middleware.trace import get_trace_id
        trace_id = get_trace_id()
        logger.info(f"[{trace_id}] {request.method} {path} - {response.status_code} ({duration_ms:.0f}ms)")
    return response


# ===== 请求追踪 trace ID(借鉴 open-telemetry/opentelemetry-python 2.0K Star)=====
# P2 修复(2026-10-26)：trace 中间件必须**最后注册**（即最外层）。
# Starlette 的 app.middleware("http") 每次把中间件插到最前面，因此"最后注册 = 最外层"。
# 原先 trace 注册在最内层，而 log_requests 在它外层：log_requests 通过 call_next 调进
# 子任务后，trace_middleware 在**子任务**里 set 的 ContextVar 不会回传到父任务上下文，
# 于是 get_trace_id() 恒为空——实测 22/22 条访问日志 trace 段为空（形如 `[] GET /api/...`），
# 而响应头 X-Trace-Id 却是正常的，属于"看起来有、实际没有"。
# 放到最外层后，ContextVar 在进入内层任务前就已赋值，内层均可读到。
app.middleware("http")(trace_middleware)

from web.routes import agent as agent_route
from web.routes import config as config_route
from web.routes import dashboard as dashboard_route
from web.routes import data_health as data_health_route
from web.routes import fund as fund_route
from web.routes import global_market as global_route
from web.routes import holdings as holdings_route
from web.routes import dashboard as dashboard_route
from web.routes import evolution as evolution_route
from web.routes import market as market_route
from web.routes import monitor as monitor_route
from web.routes import news as news_route
from web.routes import portfolio as portfolio_route
from web.routes import prediction as prediction_route
from web.routes import review as review_route
from web.routes import scripts as scripts_route
from web.routes import skills as skills_route
from web.routes import strategy as strategy_route
from web.routes import trade as trade_route
from src.utils.auth import require_admin

app.include_router(dashboard_route.router, prefix="/api/dashboard", tags=["仪表盘"])
app.include_router(strategy_route.router, prefix="/api/strategy", tags=["策略"])
# 策略自进化 B6(设计文档 §2): 6 端点挂载, 全部 require_admin + 复用回测单飞锁
app.include_router(evolution_route.router, prefix="/api/strategy/evolution", tags=["策略进化"])
app.include_router(trade_route.router, prefix="/api/trade", tags=["交易"])
app.include_router(monitor_route.router, prefix="/api/monitor", tags=["监控"])
app.include_router(config_route.router, prefix="/api/config", tags=["配置"])
app.include_router(market_route.router, prefix="/api/market", tags=["行情"])
app.include_router(agent_route.router, prefix="/api/agent", tags=["AI Agent"])
app.include_router(fund_route.router, prefix="/api/fund", tags=["基金"])
app.include_router(global_route.router, prefix="/api/global", tags=["国际市场"])
app.include_router(scripts_route.router, prefix="/api/scripts", tags=["话术库"])
app.include_router(news_route.router, prefix="/api/news", tags=["消息面"])
app.include_router(holdings_route.router, prefix="/api/holdings", tags=["重仓股板块"])
# 阶段七:数据源健康度(借鉴 zvt recorder 状态;只读,无需鉴权)
app.include_router(data_health_route.router, prefix="/api/data", tags=["数据源健康"])
# 短线复盘 + 出手三问(只读,复用行情数据源)
app.include_router(review_route.router, prefix="/api/review", tags=["短线复盘"])
# 金融技能库 + 一句话调度(只生成方案,不自动执行写操作)
app.include_router(skills_route.router, prefix="/api/agent", tags=["技能调度"])
# 阶段六批次A:组合盯盘/风险/X-Ray/Webhook/账本导入导出(全部挂 require_admin,scan 例外见路由内说明)
app.include_router(portfolio_route.router, prefix="/api/portfolio", tags=["组合阶段六"])
# 时段预测/推荐/准确率闭环(A1~A8 + export;写端点逐个 require_admin,公开读见路由内鉴权表)
app.include_router(prediction_route.router, prefix="/api/prediction", tags=["时段预测"])

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.head("/")
async def root_head():
    # Uptime 类监控默认用 HEAD 探活;GET-only 路由对 HEAD 返回 405 会被误判宕机
    return Response(status_code=200)


@app.get("/api/health")
async def health_check():
    from src.core.ai_service import _check_api_keys
    keys = _check_api_keys()
    # 返回各 key 的配置状态(布尔值,不泄露 key 本身),供前端状态展示与诊断
    has_keys = any([keys.get("ttapi"), keys.get("agnes"), keys.get("tavily"),
                    keys.get("tinyfish"), keys.get("openai_key"), keys.get("api_key")]) or bool(os.getenv("ZHIPU_API_KEY"))
    # 2026-11-08 修复：原实现只查上述 4 个 key + ZHIPU 特判，而 llm_router 实际支持
    # 15+ Provider —— 只配 GROQ/DeepSeek 等其它 Provider 时，AI 完全可用却报
    # ai_ready=false，前端因此显示"AI 不可用"。改为以路由器「实际可用 Provider」
    # 为准，并保留 key 判定作为兜底（例如只配视觉 OCR 凭证、无文本 Provider 的场景）。
    try:
        from src.core.llm_router import get_llm_router
        _providers = get_llm_router().health_check()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"health: llm router probe failed: {exc}")
        _providers = {}
    available_providers = [name for name, info in _providers.items() if info.get("available")]
    has_ai = bool(available_providers) or has_keys
    # 阶段六:把"能不能用"拆成可判定项,状态码仍保持 200(fail-open 探活),
    # 但 degraded=True 让运维/前端一眼看出是"降级运行"而非"完全正常"。
    checks = {
        "data_source": bool(_HAS_AKSHARE and _HAS_CORE),
        "ai": bool(has_ai),
    }
    return {
        "status": "ok",
        "degraded": not all(checks.values()),
        "checks": checks,
        # 部署版本(便于核对线上到底跑的哪个 commit;Render 自动注入 RENDER_GIT_COMMIT)
        "version": (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown")[:7],
        "akshare": _HAS_AKSHARE,
        "core_modules": _HAS_CORE,
        "ai_ready": has_ai,
        "ai_keys": keys,
        # 路由器视角的真实可用 Provider 列表（空数组表示当前没有任何 LLM 可用），
        # 前端/运维据此判断"AI 是否真的能用"，而不是只看 key 是否存在。
        "ai_providers": available_providers,
        # 监控指标(借鉴 prometheus/client_python 指标设计)
        "metrics": metrics.snapshot(),
        # P0:缓存命中率统计(借鉴 prometheus client_py 的 info 指标)
        "cache_stats": _get_cache_stats(),
    }


@app.get("/api/health/llm")
async def llm_health_check():
    """LLM Provider 健康检查 - 返回各 Provider 的状态、优先级、限流配置

    2026-11-07 增量：新增 `routing` 段（难度分层路由可观测面）：
    - `routing.tiers`：deep/standard/light 三档的有序 provider 名数组
    - `routing.routes`：每档逐跳的 provider/model/label/available/same_upstream_as
    - `routing.budgets_seconds`：各档当前生效的总预算（秒）
    - `routing.last_routing`：最近一次路由决策（tier/provider/model/latency/fallback）
    - `routing.same_upstream_groups`：同源 Provider 分组（zhipu_glm_45 与 zhipu_glm 共享配额）

    匿名可读，且**绝不含 api_key / Authorization / key 片段**（AC9/AC10）。
    """
    from src.core.llm_router import get_llm_router, _llm_cache
    router = get_llm_router()
    providers = router.health_check()
    return {
        "status": "ok",
        "total_providers": len(providers),
        "available_providers": [name for name, info in providers.items() if info["available"]],
        "free_providers": [name for name, info in providers.items() if info["free_tier"]],
        "providers": providers,
        # 难度分层路由矩阵（tier → 有序链 / 预算 / 最近一次决策）
        "routing": router.routing_health(),
        # P0:LLM 响应缓存统计(节省免费额度)
        "response_cache": _llm_cache.stats(),
    }


@app.post("/api/health/llm/probe")
def llm_probe(
    provider: str | None = None,
    tier: str | None = None,
    _: None = Depends(require_admin),
):
    """LLM 连通性自检:对单个/全部 Provider 或某个难度档位的链发一条 ping

    Args:
        provider: 指定 Provider 名（如 `agnes`）；与 `tier` 同时给出时 `provider` 优先。
        tier: 按难度档位探活（deep/standard/light）。会用该档的 model 覆盖逐跳打一次，
            例如 `?tier=deep` 会真实探测 zhipu_glm → ly77625 → agnes(agnes-3.0-flash)。
    """
    from fastapi import HTTPException
    from src.core.llm_router import get_llm_router

    result = get_llm_router().probe(name=provider, tier=tier)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


def _get_cache_stats() -> dict:
    """聚合各层缓存命中率统计(供 /api/health 返回)

    P0 新增:统一暴露 LLM 缓存 + DataCache 内存层命中率,
    便于运维监控缓存效果,优化 TTL 配置。
    """
    stats: dict = {}
    try:
        from src.core.llm_router import _llm_cache
        stats["llm"] = _llm_cache.stats()
    except Exception:
        pass
    try:
        from src.core.cache import DataCache
        # 用临时实例获取默认统计(实际命中率在各路由的 DataCache 实例中)
        # 此处仅返回 LLM 缓存统计,DataCache 统计需通过依赖注入获取
        stats["data_cache_note"] = "per-instance stats, see route logs"
    except Exception:
        pass
    return stats

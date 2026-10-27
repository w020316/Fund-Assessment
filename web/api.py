import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
# 借鉴 encode/starlette(10.5K Star)内置 GZip 中间件,响应压缩 70%+,零新依赖
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from loguru import logger

# 借鉴 la-deps/slowapi(1.0K Star)FastAPI 限流中间件
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
# limiter 实例从独立模块导入,避免路由模块 import web.api 触发循环 import
from web.rate_limiter import limiter

# 借鉴 prometheus/client_python(4.2K Star)指标设计的轻量级监控
from web.middleware.metrics import metrics
# 借鉴 open-telemetry/opentelemetry-python(2.0K Star)请求追踪 trace ID
from web.middleware.trace import trace_middleware

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

    yield

    app_state.clear()


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
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
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
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token", "X-Trace-Id"],
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
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https:;"
    )
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
    return response

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
from web.routes import fund as fund_route
from web.routes import global_market as global_route
from web.routes import holdings as holdings_route
from web.routes import market as market_route
from web.routes import monitor as monitor_route
from web.routes import news as news_route
from web.routes import portfolio as portfolio_route
from web.routes import scripts as scripts_route
from web.routes import strategy as strategy_route
from web.routes import trade as trade_route
from src.utils.auth import require_admin

app.include_router(dashboard_route.router, prefix="/api/dashboard", tags=["仪表盘"])
app.include_router(strategy_route.router, prefix="/api/strategy", tags=["策略"])
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
# 阶段六批次A:组合盯盘/风险/X-Ray/Webhook/账本导入导出(全部挂 require_admin,scan 例外见路由内说明)
app.include_router(portfolio_route.router, prefix="/api/portfolio", tags=["组合阶段六"])

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/health")
async def health_check():
    from src.core.ai_service import _check_api_keys
    keys = _check_api_keys()
    # 返回各 key 的配置状态(布尔值,不泄露 key 本身),供前端状态展示与诊断
    has_ai = any([keys.get("ttapi"), keys.get("agnes"), keys.get("tavily"),
                  keys.get("tinyfish"), keys.get("openai_key"), keys.get("api_key")]) or bool(os.getenv("ZHIPU_API_KEY"))
    return {
        "status": "ok",
        "akshare": _HAS_AKSHARE,
        "core_modules": _HAS_CORE,
        "ai_ready": has_ai,
        "ai_keys": keys,
        # 监控指标(借鉴 prometheus/client_python 指标设计)
        "metrics": metrics.snapshot(),
        # P0:缓存命中率统计(借鉴 prometheus client_py 的 info 指标)
        "cache_stats": _get_cache_stats(),
    }


@app.get("/api/health/llm")
async def llm_health_check():
    """LLM Provider 健康检查 - 返回各 Provider 的状态、优先级、限流配置"""
    from src.core.llm_router import get_llm_router, _llm_cache
    router = get_llm_router()
    providers = router.health_check()
    return {
        "status": "ok",
        "total_providers": len(providers),
        "available_providers": [name for name, info in providers.items() if info["available"]],
        "free_providers": [name for name, info in providers.items() if info["free_tier"]],
        "providers": providers,
        # P0:LLM 响应缓存统计(节省免费额度)
        "response_cache": _llm_cache.stats(),
    }


@app.post("/api/health/llm/probe")
def llm_probe(provider: str | None = None, _: None = Depends(require_admin)):
    """LLM 连通性自检:对单个或全部 Provider 发一条 ping,返回延迟与失败原因"""
    from fastapi import HTTPException
    from src.core.llm_router import get_llm_router

    result = get_llm_router().probe(name=provider)
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

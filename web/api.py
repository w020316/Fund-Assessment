import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from loguru import logger

_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

app_state: dict = {}

_HAS_AKSHARE = False
_HAS_CORE = False

try:
    import akshare
    _HAS_AKSHARE = True
except ImportError as e:
    logger.warning(f"import akshare failed: {e}")

try:
    from src.core.data_source import DataSourceManager
    from src.core.executor import SimulatedBroker, TradeExecutor
    from src.core.risk_manager import RiskManager
    from src.utils.config import get_config
    _HAS_CORE = True
except ImportError as e:
    logger.warning(f"import src.core failed: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if _HAS_CORE:
        cfg = get_config()
        tushare_token = cfg.get("tushare.token", "")
        data_source = DataSourceManager(tushare_token=tushare_token)
        risk_manager = RiskManager()
        broker = SimulatedBroker(initial_cash=1_000_000.0)
        executor = TradeExecutor(broker=broker, risk_manager=risk_manager)

        app_state["data_source"] = data_source
        app_state["risk_manager"] = risk_manager
        app_state["broker"] = broker
        app_state["executor"] = executor
        app_state["config"] = cfg
    else:
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

_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    if request.url.path.startswith("/api/"):
        from loguru import logger
        logger.info(f"{request.method} {request.url.path} - {response.status_code} ({duration_ms:.0f}ms)")
    return response

from web.routes import agent as agent_route
from web.routes import config as config_route
from web.routes import dashboard as dashboard_route
from web.routes import fund as fund_route
from web.routes import global_market as global_route
from web.routes import holdings as holdings_route
from web.routes import market as market_route
from web.routes import monitor as monitor_route
from web.routes import news as news_route
from web.routes import scripts as scripts_route
from web.routes import strategy as strategy_route
from web.routes import trade as trade_route

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
    }


@app.get("/api/health/llm")
async def llm_health_check():
    """LLM Provider 健康检查 - 返回各 Provider 的状态、优先级、限流配置"""
    from src.core.llm_router import get_llm_router
    router = get_llm_router()
    providers = router.health_check()
    return {
        "status": "ok",
        "total_providers": len(providers),
        "available_providers": [name for name, info in providers.items() if info["available"]],
        "providers": providers,
    }

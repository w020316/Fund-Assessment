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

_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

app_state: dict = {}

_HAS_AKSHARE = False
_HAS_CORE = False

try:
    import akshare
    _HAS_AKSHARE = True
except ImportError:
    pass

try:
    from src.core.data_source import DataSourceManager
    from src.core.executor import SimulatedBroker, TradeExecutor
    from src.core.risk_manager import RiskManager
    from src.utils.config import get_config
    _HAS_CORE = True
except ImportError:
    pass


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
    allow_headers=["*"],
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
from web.routes import market as market_route
from web.routes import monitor as monitor_route
from web.routes import strategy as strategy_route
from web.routes import trade as trade_route

app.include_router(dashboard_route.router, prefix="/api/dashboard", tags=["仪表盘"])
app.include_router(strategy_route.router, prefix="/api/strategy", tags=["策略"])
app.include_router(trade_route.router, prefix="/api/trade", tags=["交易"])
app.include_router(monitor_route.router, prefix="/api/monitor", tags=["监控"])
app.include_router(config_route.router, prefix="/api/config", tags=["配置"])
app.include_router(market_route.router, prefix="/api/market", tags=["行情"])
app.include_router(agent_route.router, prefix="/api/agent", tags=["AI Agent"])

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/health")
async def health_check():
    from src.core.ai_service import _check_api_keys
    return {
        "status": "ok",
        "akshare": _HAS_AKSHARE,
        "core_modules": _HAS_CORE,
        "ai_keys": _check_api_keys(),
    }

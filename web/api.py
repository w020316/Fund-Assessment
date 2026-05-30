import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from src.core.data_source import DataSourceManager
from src.core.executor import SimulatedBroker, TradeExecutor
from src.core.risk_manager import RiskManager
from src.utils.config import get_config

from web.routes import config, dashboard, market, monitor, strategy, trade

app_state: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
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

    yield

    app_state.clear()


app = FastAPI(
    title="OpenClaw 量化交易系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router, prefix="/api/dashboard", tags=["仪表盘"])
app.include_router(strategy.router, prefix="/api/strategy", tags=["策略"])
app.include_router(trade.router, prefix="/api/trade", tags=["交易"])
app.include_router(monitor.router, prefix="/api/monitor", tags=["监控"])
app.include_router(config.router, prefix="/api/config", tags=["配置"])
app.include_router(market.router, prefix="/api/market", tags=["行情"])

from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

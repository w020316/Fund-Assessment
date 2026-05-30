from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_HAS_STRATEGIES = False
try:
    from src.strategies.a_stock_analyst import AStockAnalyst
    from src.strategies.bspro_quant import BSProQuant
    from src.strategies.cb_t0_sniper import CBT0Sniper
    from src.strategies.limit_up import LimitUpAnalyzer
    from src.strategies.trading_quant import TradingQuant
    _HAS_STRATEGIES = True
except ImportError:
    pass


class AnalyzeRequest(BaseModel):
    stock_code: str
    strategy_type: str = "comprehensive"


class AnalyzeResponse(BaseModel):
    stock_code: str
    strategy_type: str
    result: dict[str, Any]


class NewHighItem(BaseModel):
    stock_code: str
    stock_name: str
    change_pct: float
    volume: float
    amount: float


class LimitUpItem(BaseModel):
    stock_code: str
    stock_name: str
    level: str
    reason: str
    seal_time: str
    open_count: int
    seal_volume: float
    quality_score: float


class CBItem(BaseModel):
    cb_code: str
    cb_name: str
    stock_code: str
    stock_name: str
    cb_price: float
    stock_price: float
    conversion_price: float
    conversion_value: float
    premium_rate: float
    is_limit_up: bool
    volume_ratio: float
    turnover_rate: float


class BacktestRequest(BaseModel):
    strategy: str = "bspro_quant"
    stock_code: str = ""
    start_date: str = ""
    end_date: str = ""


class BacktestResponse(BaseModel):
    strategy: str
    stock_code: str
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    trades: int


class StrategyInfo(BaseModel):
    name: str
    display_name: str
    description: str
    enabled: bool


def _mock_analysis(stock_code: str, strategy_type: str) -> dict[str, Any]:
    return {
        "stock_code": stock_code,
        "strategy_type": strategy_type,
        "scores": {
            "technical": round(random.uniform(30, 90), 1),
            "capital": round(random.uniform(30, 90), 1),
            "fundamental": round(random.uniform(30, 90), 1),
            "news": round(random.uniform(30, 90), 1),
            "sentiment": round(random.uniform(30, 90), 1),
        },
        "total_score": round(random.uniform(30, 90), 1),
        "signal": random.choice(["STRONG_BUY", "BUY", "WATCH", "HOLD", "SELL"]),
        "recommendation": "模拟数据 - 请安装 akshare 后获取真实分析",
    }


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    if not _HAS_STRATEGIES:
        return AnalyzeResponse(
            stock_code=req.stock_code,
            strategy_type=req.strategy_type,
            result=_mock_analysis(req.stock_code, req.strategy_type),
        )
    try:
        match req.strategy_type:
            case "comprehensive":
                analyst = AStockAnalyst()
                result = analyst.comprehensive_analysis(req.stock_code)
            case "trading_quant":
                quant = TradingQuant()
                result = quant.stock_analysis(req.stock_code)
            case "bspro_quant":
                quant = BSProQuant()
                result = quant.compute_factors(req.stock_code)
            case _:
                analyst = AStockAnalyst()
                result = analyst.comprehensive_analysis(req.stock_code)
        return AnalyzeResponse(
            stock_code=req.stock_code,
            strategy_type=req.strategy_type,
            result=result,
        )
    except Exception as e:
        return AnalyzeResponse(
            stock_code=req.stock_code,
            strategy_type=req.strategy_type,
            result=_mock_analysis(req.stock_code, req.strategy_type),
        )


@router.get("/scan/new_high", response_model=list[NewHighItem])
async def scan_new_high():
    if not _HAS_STRATEGIES:
        stocks = [("300750", "宁德时代"), ("002594", "比亚迪"), ("601012", "隆基绿能"),
                   ("300059", "东方财富"), ("300015", "爱尔眼科")]
        return [NewHighItem(stock_code=c, stock_name=n, change_pct=round(random.uniform(5, 15), 2),
                            volume=random.uniform(1e6, 1e8), amount=random.uniform(1e8, 1e10))
                for c, n in stocks]
    try:
        import akshare as ak
        items: list[NewHighItem] = []
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            df["涨跌幅"] = df["涨跌幅"].astype(float)
            df["成交额"] = df["成交额"].astype(float)
            high_df = df[df["涨跌幅"] > 5].sort_values("涨跌幅", ascending=False).head(20)
            for _, row in high_df.iterrows():
                items.append(NewHighItem(
                    stock_code=str(row.get("代码", "")),
                    stock_name=str(row.get("名称", "")),
                    change_pct=float(row.get("涨跌幅", 0)),
                    volume=float(row.get("成交量", 0)),
                    amount=float(row.get("成交额", 0)),
                ))
        return items
    except Exception:
        return []


@router.get("/scan/limit_up", response_model=list[LimitUpItem])
async def scan_limit_up():
    if not _HAS_STRATEGIES:
        stocks = [("000001", "平安银行"), ("600519", "贵州茅台")]
        return [LimitUpItem(stock_code=c, stock_name=n, level="首板", reason="题材",
                            seal_time="09:35", open_count=0, seal_volume=random.uniform(1e7, 1e9),
                            quality_score=round(random.uniform(60, 95), 1))
                for c, n in stocks]
    try:
        analyzer = LimitUpAnalyzer()
        results = analyzer.scan_limit_up()
        return [LimitUpItem(
            stock_code=r.stock_code, stock_name=r.stock_name, level=r.level.value,
            reason=r.reason.value, seal_time=r.seal_time, open_count=r.open_count,
            seal_volume=r.seal_volume, quality_score=r.quality_score,
        ) for r in results]
    except Exception:
        return []


@router.get("/scan/cb", response_model=list[CBItem])
async def scan_cb():
    if not _HAS_STRATEGIES:
        return [CBItem(
            cb_code="123456", cb_name="模拟转债", stock_code="000001",
            stock_name="平安银行", cb_price=round(random.uniform(90, 130), 2),
            stock_price=round(random.uniform(10, 50), 2),
            conversion_price=round(random.uniform(10, 50), 2),
            conversion_value=round(random.uniform(90, 130), 2),
            premium_rate=round(random.uniform(-5, 25), 2),
            is_limit_up=True, volume_ratio=round(random.uniform(1, 5), 2),
            turnover_rate=round(random.uniform(5, 30), 2),
        )]
    try:
        sniper = CBT0Sniper()
        results = sniper.scan_cb_opportunities()
        return [CBItem(
            cb_code=r.cb_code, cb_name=r.cb_name, stock_code=r.stock_code,
            stock_name=r.stock_name, cb_price=r.cb_price, stock_price=r.stock_price,
            conversion_price=r.conversion_price, conversion_value=r.conversion_value,
            premium_rate=r.premium_rate, is_limit_up=r.is_limit_up,
            volume_ratio=r.volume_ratio, turnover_rate=r.turnover_rate,
        ) for r in results]
    except Exception:
        return []


@router.post("/backtest", response_model=BacktestResponse)
async def backtest(req: BacktestRequest):
    if not _HAS_STRATEGIES:
        return BacktestResponse(
            strategy=req.strategy, stock_code=req.stock_code,
            total_return=round(random.uniform(-20, 40), 2),
            annualized_return=round(random.uniform(-10, 20), 2),
            max_drawdown=round(random.uniform(5, 25), 2),
            sharpe_ratio=round(random.uniform(-0.5, 2.0), 2),
            win_rate=round(random.uniform(40, 70), 2),
            trades=random.randint(20, 100),
        )
    try:
        quant = BSProQuant()
        factor_combo = {"pe": -0.2, "roe_change": 0.3, "return_3m": 0.3, "sharpe": 0.2}
        result = quant.backtest_strategy(factor_combo=factor_combo, period=60)
        return BacktestResponse(
            strategy=req.strategy, stock_code=req.stock_code,
            total_return=result["total_return"],
            annualized_return=result["annualized_return"],
            max_drawdown=result["max_drawdown"],
            sharpe_ratio=result["sharpe_ratio"],
            win_rate=result["win_rate"],
            trades=result["trades"],
        )
    except Exception:
        return BacktestResponse(
            strategy=req.strategy, stock_code=req.stock_code,
            total_return=0, annualized_return=0, max_drawdown=0,
            sharpe_ratio=0, win_rate=0, trades=0,
        )


@router.get("/list", response_model=list[StrategyInfo])
async def strategy_list():
    return [
        StrategyInfo(name="comprehensive", display_name="综合分析", description="基本面+技术面+行业面综合评分", enabled=True),
        StrategyInfo(name="trading_quant", display_name="量化交易", description="多维度量化评分与信号", enabled=True),
        StrategyInfo(name="bspro_quant", display_name="因子量化", description="多因子模型与因子选股", enabled=True),
        StrategyInfo(name="limit_up", display_name="涨停板", description="涨停板扫描与晋级预测", enabled=True),
        StrategyInfo(name="cb_t0", display_name="可转债T+0", description="可转债日内T+0狙击", enabled=True),
        StrategyInfo(name="new_high", display_name="创业板新高", description="创业板新高突破扫描", enabled=True),
    ]

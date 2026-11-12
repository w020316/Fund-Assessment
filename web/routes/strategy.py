from typing import Any, Literal

import asyncio
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.core import data_source_v2 as ds2
from src.core.validation import validate_returns
from src.utils.auth import require_admin
from web.rate_limiter import limiter

router = APIRouter()

# R-03(2026-11-05): 回测逐票拉取历史行情, 无上限时会长时间挂起(实测 >150s)。
# R-06(2026-11-05): 根因是候选池用 ak.stock_zh_a_spot_em()(59 页/约 70s)且逐票
# 重复拉全市场快照;修复后实测 12~15s 可完成。上限从 45s 收紧到 30s:
# 正常路径有充足余量,异常路径也更早给出可读失败(不靠"拉长时间"掩盖问题)。
BACKTEST_TIMEOUT_SECONDS = 30

# 阶段五 R1:回测同时只允许一个任务。
# 单次回测已用 4 个因子线程 + 20s 预算;并发再来几次会同时打满上游与 512MB 实例,
# 表现为"所有请求都超时"。这里用非阻塞锁快速拒绝,而不是排队(排队只会更糟)。
_BACKTEST_GUARD = threading.Lock()


def _single_backtest_guard():
    if not _BACKTEST_GUARD.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="上一次回测仍在进行,请稍后再试")
    try:
        yield
    finally:
        _BACKTEST_GUARD.release()


# 策略自进化 B6(设计文档 §2): 公开别名——锁唯一真源仍是 _BACKTEST_GUARD,
# evolution 等新重型任务复用同一把锁(进化与回测互斥), 禁止另开新锁。
single_backtest_guard = _single_backtest_guard


_HAS_STRATEGIES = False
try:
    from src.strategies.a_stock_analyst import AStockAnalyst
    from src.strategies.bspro_quant import BSProQuant
    from src.strategies.cb_t0_sniper import CBT0Sniper
    from src.strategies.limit_up import LimitUpAnalyzer
    from src.strategies.trading_quant import TradingQuant
    _HAS_STRATEGIES = True
except ImportError as e:
    logger.warning(f"import strategy modules failed: {e}")


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
    warning: str = "假设性回测：因子为当前快照，存在未来函数偏差，且未扣交易成本"
    partial: bool = False


class StrategyInfo(BaseModel):
    name: str
    display_name: str
    description: str
    enabled: bool


class FactorResearchRequest(BaseModel):
    csv_data: str = Field(..., min_length=1, max_length=2_000_000)
    window: int = Field(20, ge=2, le=120, strict=True)
    horizon: int = Field(20, ge=1, le=120, strict=True)
    groups: int = Field(5, ge=2, le=10, strict=True)
    min_listing_days: int = Field(120, ge=0, le=1000, strict=True)
    min_average_amount: float = Field(5_000_000, ge=0, le=1e12, allow_inf_nan=False)
    direction: Literal[-1, 1] = 1
    train_ratio: float = Field(0.7, ge=0.5, le=0.9, allow_inf_nan=False)


class ValidateRequest(BaseModel):
    returns: list[float] = Field(..., min_length=1, max_length=5000)
    train_size: int = Field(60, ge=5, le=1000)
    test_size: int = Field(20, ge=5, le=500)
    step: int | None = Field(None, ge=1, le=500)
    runs: int = Field(1000, ge=100, le=5000)


def _fallback_analysis(stock_code: str, strategy_type: str, error_msg: str = "") -> dict[str, Any]:
    """策略不可用时的降级响应(显式空值,不冒充真实评分)"""
    return {
        "stock_code": stock_code,
        "strategy_type": strategy_type,
        "scores": {
            "technical": 0.0,
            "capital": 0.0,
            "fundamental": 0.0,
            "news": 0.0,
            "sentiment": 0.0,
        },
        "total_score": 0.0,
        "signal": "N/A",
        "recommendation": f"策略模块不可用{(':' + error_msg) if error_msg else ''},请检查依赖",
        "available": False,
    }


def _lightweight_analysis(stock_code: str, strategy_type: str) -> dict[str, Any]:
    """策略模块未启用时,用基础数据源做轻量分析(不冒充完整策略)"""
    try:
        quote = ds2.get_realtime_quote_tencent([stock_code])
        financial = ds2.get_financial_snapshot(stock_code)
        kline = ds2.get_kline_mootdx(stock_code, count=30)
        flow = ds2.get_capital_flow_detail(stock_code)

        tech_score = 50.0
        if kline and len(kline) >= 5:
            recent = kline[-5:]
            closes = [d.get("close", 0) for d in recent]
            if closes[0] > 0:
                trend = (closes[-1] - closes[0]) / closes[0] * 100
                tech_score = max(10, min(90, 50 + trend * 5))

        fund_score = 50.0
        if financial:
            roe = financial.get("roe", 0)
            pe = financial.get("pe_ttm", 0)
            if pe > 0:
                fund_score = max(10, min(90, 50 + roe * 2 - (pe - 20) * 0.5))

        capital_score = 50.0
        if flow:
            main_pct = flow.get("main_inflow_pct", 0)
            capital_score = max(10, min(90, 50 + main_pct * 3))

        news_score = 50.0
        sentiment_score = 50.0

        if quote:
            change_pct = quote[0].get("change_pct", 0)
            sentiment_score = max(10, min(90, 50 + change_pct * 3))

        scores = {
            "technical": round(tech_score, 1),
            "capital": round(capital_score, 1),
            "fundamental": round(fund_score, 1),
            "news": round(news_score, 1),
            "sentiment": round(sentiment_score, 1),
        }
        total = sum(scores.values()) / len(scores)

        if total >= 70:
            signal = "BUY"
        elif total >= 55:
            signal = "WATCH"
        elif total >= 40:
            signal = "HOLD"
        else:
            signal = "SELL"

        name = quote[0].get("name", stock_code) if quote else stock_code
        price = quote[0].get("price", 0) if quote else 0

        return {
            "stock_code": stock_code,
            "strategy_type": strategy_type,
            "scores": scores,
            "total_score": round(total, 1),
            "signal": signal,
            "recommendation": f"{name}({stock_code}) 当前价{price},综合评分{total:.1f},信号:{signal}",
            "available": True,
            "source": "lightweight",
        }
    except Exception as e:
        logger.warning(f"_lightweight_analysis {stock_code} failed: {e}")
        return _fallback_analysis(stock_code, strategy_type)


def _normalize_analysis(result: dict) -> dict:
    if "scores" in result and isinstance(result.get("scores"), dict):
        return result
    scores = {}
    signal = "HOLD"
    recommendation = ""
    total = result.get("composite_score", 50)
    fund = result.get("fundamental", {})
    tech = result.get("technical", {})
    ind = result.get("industry", {})
    scores["fundamental"] = fund.get("score", 50) if isinstance(fund, dict) else 50
    scores["technical"] = tech.get("score", 50) if isinstance(tech, dict) else 50
    scores["capital"] = ind.get("score", 50) if isinstance(ind, dict) else 50
    scores["news"] = 50.0
    scores["sentiment"] = 50.0
    if total >= 70:
        signal = "BUY"
    elif total >= 55:
        signal = "WATCH"
    elif total < 40:
        signal = "SELL"
    parts = []
    if isinstance(fund, dict) and fund.get("roe"):
        parts.append(f"ROE={fund['roe']:.1f}%")
    if isinstance(tech, dict) and tech.get("trend"):
        parts.append(f"趋势={tech['trend']}")
    recommendation = f"综合评分{total:.1f}，信号：{signal}" + (f"，{','.join(parts)}" if parts else "")
    return {
        "stock_code": result.get("stock_code", ""),
        "strategy_type": result.get("strategy_type", ""),
        "scores": scores,
        "total_score": total,
        "signal": signal,
        "recommendation": recommendation,
    }


@router.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(require_admin)])
async def analyze(req: AnalyzeRequest):
    if not _HAS_STRATEGIES:
        # 策略模块未启用时,用基础数据源做轻量分析(不冒充完整策略)
        return AnalyzeResponse(
            stock_code=req.stock_code,
            strategy_type=req.strategy_type,
            result=_lightweight_analysis(req.stock_code, req.strategy_type),
        )
    try:
        match req.strategy_type:
            case "comprehensive":
                analyst = AStockAnalyst()
                # P1 修复(2026-07-30):同步重型方法包裹到线程池,避免阻塞事件循环
                result = await asyncio.to_thread(analyst.comprehensive_analysis, req.stock_code)
            case "trading_quant":
                quant = TradingQuant()
                result = await asyncio.to_thread(quant.stock_analysis, req.stock_code)
            case "bspro_quant":
                quant = BSProQuant()
                result = await asyncio.to_thread(quant.compute_factors, req.stock_code)
            case _:
                analyst = AStockAnalyst()
                result = await asyncio.to_thread(analyst.comprehensive_analysis, req.stock_code)
        return AnalyzeResponse(
            stock_code=req.stock_code,
            strategy_type=req.strategy_type,
            result=_normalize_analysis(result),
        )
    except Exception as e:
        # P2 修复(2026-07-30):补 logger.exception 保留完整堆栈,便于排查
        logger.exception(f"analyze {req.stock_code} ({req.strategy_type}) failed")
        return AnalyzeResponse(
            stock_code=req.stock_code,
            strategy_type=req.strategy_type,
            result=_fallback_analysis(req.stock_code, req.strategy_type),
        )


def _scan_unavailable(operation: str) -> HTTPException:
    """阶段二 R-13(2026-11-06):扫描失败必须显式 503, 不能返回 [] 冒充“今天没有机会”。"""
    return HTTPException(
        status_code=503,
        detail={
            "code": "SCAN_DATA_UNAVAILABLE",
            "message": f"{operation}数据源暂不可用,请稍后重试。",
            "retryable": True,
        },
    )


@router.get("/scan/new_high", response_model=list[NewHighItem])
async def scan_new_high():
    if not _HAS_STRATEGIES:
        top = []
        source_failed = False
        try:
            # P1 修复(2026-07-30):ds2 同步调用包裹到线程池
            top = await asyncio.to_thread(ds2.get_stock_ranking_em, sort_field="f3", sort_order=0, count=30)
        except Exception as e:
            source_failed = True
            logger.warning(f"get_stock_ranking_em failed: {e}")
        if not top:
            try:
                top = await asyncio.to_thread(ds2._get_stock_ranking_sina, "f3", 0, 30)
            except Exception as e:
                source_failed = True
                logger.warning(f"get_stock_ranking_sina failed: {e}")
        if not top and source_failed:
            raise _scan_unavailable("新高扫描")
        items: list[NewHighItem] = []
        for s in top:
            if s.get("change_pct", 0) >= 3:
                items.append(NewHighItem(
                    stock_code=s.get("code", ""),
                    stock_name=s.get("name", ""),
                    change_pct=s.get("change_pct", 0),
                    volume=s.get("volume", 0),
                    amount=s.get("amount", 0),
                ))
        return items
    try:
        import akshare as ak
        items: list[NewHighItem] = []
        # P1 修复(2026-07-30):akshare 同步调用包裹到线程池
        df = await asyncio.to_thread(ak.stock_zh_a_spot_em)
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
        # P2 修复(2026-07-30):补日志; R-13:显式 503 而非 return []
        logger.exception("scan_new_high akshare path failed")
        raise _scan_unavailable("新高扫描")


@router.get("/scan/limit_up", response_model=list[LimitUpItem])
async def scan_limit_up():
    if not _HAS_STRATEGIES:
        return []
    try:
        analyzer = LimitUpAnalyzer()
        # P1 修复(2026-07-30):同步方法包裹到线程池
        results = await asyncio.to_thread(analyzer.scan_limit_up)
        return [LimitUpItem(
            stock_code=r.stock_code, stock_name=r.stock_name, level=r.level.value,
            reason=r.reason.value, seal_time=r.seal_time, open_count=r.open_count,
            seal_volume=r.seal_volume, quality_score=r.quality_score,
        ) for r in results]
    except Exception:
        # P2 修复(2026-07-30):补日志; R-13:显式 503 而非 return []
        logger.exception("scan_limit_up failed")
        raise _scan_unavailable("涨停扫描")


@router.get("/scan/cb", response_model=list[CBItem])
async def scan_cb():
    if not _HAS_STRATEGIES:
        return []
    try:
        sniper = CBT0Sniper()
        # P1 修复(2026-07-30):同步方法包裹到线程池
        results = await asyncio.to_thread(sniper.scan_cb_opportunities)
        return [CBItem(
            cb_code=r.cb_code, cb_name=r.cb_name, stock_code=r.stock_code,
            stock_name=r.stock_name, cb_price=r.cb_price, stock_price=r.stock_price,
            conversion_price=r.conversion_price, conversion_value=r.conversion_value,
            premium_rate=r.premium_rate, is_limit_up=r.is_limit_up,
            volume_ratio=r.volume_ratio, turnover_rate=r.turnover_rate,
        ) for r in results]
    except Exception:
        # P2 修复(2026-07-30):补日志; R-13:显式 503 而非 return []
        logger.exception("scan_cb failed")
        raise _scan_unavailable("可转债扫描")


@router.post("/backtest", response_model=BacktestResponse,
             dependencies=[Depends(require_admin), Depends(_single_backtest_guard)])
async def backtest(req: BacktestRequest):
    if not _HAS_STRATEGIES:
        raise HTTPException(status_code=503, detail="策略模块不可用，未执行回测")
    try:
        quant = BSProQuant()
        factor_combo = {"pe": -0.2, "roe_change": 0.3, "return_3m": 0.3, "sharpe": 0.2}
        # P1 修复(2026-07-30):
        # (1) 同步方法包裹到线程池
        # (2) 原代码异常时静默返回全零,用户无法区分"回测结果恰好全零"与"回测彻底失败",
        #     可能据此做出错误投资决策。改为抛 HTTPException 503 明确告知失败。
        # R-03(2026-11-05): 该回测逐票拉取历史行情, 外部数据源抖动时可无限挂起
        # (实测本机 >150s 无响应), 前端入口因此不可用 → 增加 45s 上限并明确报超时。
        result = await asyncio.wait_for(
            asyncio.to_thread(quant.backtest_strategy, factor_combo=factor_combo, period=60),
            timeout=BACKTEST_TIMEOUT_SECONDS,
        )
        # R-06:候选池取不到时上游明确失败,不返回全零冒充"回测完成"
        if result.get("error") == "DATA_SOURCE_UNAVAILABLE":
            raise HTTPException(status_code=503, detail=result.get("error_message") or "行情数据源暂不可用")
        return BacktestResponse(
            strategy=req.strategy, stock_code=req.stock_code,
            total_return=result["total_return"],
            annualized_return=result["annualized_return"],
            max_drawdown=result["max_drawdown"],
            sharpe_ratio=result["sharpe_ratio"],
            win_rate=result["win_rate"],
            trades=result["trades"],
            warning=result.get("warning") or BacktestResponse.model_fields["warning"].default,
            partial=result.get("partial", False),
        )
    except asyncio.TimeoutError:
        logger.warning(f"backtest {req.strategy} 超时({BACKTEST_TIMEOUT_SECONDS}s)")
        raise HTTPException(
            status_code=503,
            detail=f"回测超时({BACKTEST_TIMEOUT_SECONDS}s), 行情数据源暂不可用, 请稍后重试",
        )
    except HTTPException:
        # R-06:内部抛出的是有明确语义的 503(如候选池不可用),不要被下面的
        # 通用兜底改写成"回测执行失败",否则真实原因丢失。
        raise
    except Exception as e:
        logger.exception(f"backtest {req.strategy} failed")
        raise HTTPException(status_code=503, detail="回测执行失败，请稍后重试")


@router.post("/factor-research", dependencies=[Depends(require_admin), Depends(_single_backtest_guard)])
@limiter.limit("3/minute")
async def factor_research(request: Request, req: FactorResearchRequest) -> dict[str, Any]:
    from src.core.factor_research import research_alpha144

    # F-2(2026-11-12): 与 backtest/optimize 对齐补超时保护。输入虽有界(2MB/面板
    # 10 万格)最坏耗时秒级, 但单飞锁会被占住, 兜底防止异常环境下的无限挂起。
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(research_alpha144, **req.model_dump()),
            timeout=BACKTEST_TIMEOUT_SECONDS * 2,
        )
    except asyncio.TimeoutError:
        logger.warning("factor_research 超时")
        raise HTTPException(
            status_code=503, detail="因子检验超时，请缩小 CSV 日期范围或股票池后重试"
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"data": result}


class OptimizeMaCrossRequest(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=12)
    start_date: str = Field("", max_length=10, description="YYYY-MM-DD,空则默认近一年")
    end_date: str = Field("", max_length=10, description="YYYY-MM-DD,空则默认今天")


@router.post("/optimize-ma-cross", dependencies=[Depends(require_admin), Depends(_single_backtest_guard)])
@limiter.limit("3/minute")
async def optimize_ma_cross(request: Request, req: OptimizeMaCrossRequest) -> dict[str, Any]:
    """MA 金叉策略参数网格优化(视频链路 STEP 15)。

    K线单次取数,对每组 (MA_short, MA_long) 复用同一份数据模拟,
    网格按信息比率降序,前端据此画热力图。与回测共用单飞锁。
    """
    from datetime import date as _date, timedelta as _timedelta

    from src.core.backtest import BacktestEngine

    stock_code = req.stock_code.strip()
    # F-4(2026-11-12): 六位数字 A 股代码服务端校验, 莫让非法代码走完上游取数
    # 才以"网格为空"的笼统提示失败, 用户无从知道错在输入。
    if len(stock_code) != 6 or not stock_code.isdigit():
        raise HTTPException(status_code=422, detail="股票代码必须是 6 位数字的 A 股代码")
    try:
        end = _date.fromisoformat(req.end_date.strip()) if req.end_date.strip() else _date.today()
        start = _date.fromisoformat(req.start_date.strip()) if req.start_date.strip() else _date.today() - _timedelta(days=365)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="日期格式应为 YYYY-MM-DD") from exc
    if start >= end:
        raise HTTPException(status_code=422, detail="start_date 必须早于 end_date")

    engine = BacktestEngine()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(engine.optimize_ma_cross, stock_code, start.isoformat(), end.isoformat()),
            timeout=BACKTEST_TIMEOUT_SECONDS * 2,
        )
    except asyncio.TimeoutError:
        logger.warning(f"optimize_ma_cross {stock_code} 超时")
        raise HTTPException(status_code=503, detail="参数优化超时，请缩小日期范围后重试")
    except Exception as e:
        logger.exception(f"optimize_ma_cross {stock_code} 执行失败: {e}")
        raise HTTPException(status_code=503, detail="参数优化执行失败，请稍后重试")
    return {
        "data": result,
        "warning": "量化策略存在失效可能，历史回测不代表未来收益；网格最优点通常是过拟合幸存者，实盘请先小资金验证。",
    }


@router.post("/validate", dependencies=[Depends(require_admin)])
@limiter.limit("20/minute")
async def validate_backtest(request: Request, req: ValidateRequest) -> dict[str, Any]:
    """回测结果验证:滚动样本外 + 蒙特卡洛

    输入为逐期收益率序列(如日收益,小数形式),不会触发任何交易或写操作。

    2026-11-05 R-13: 与 /analyze、/backtest 对齐挂 require_admin。
    该端点是纯计算密集 POST(自带 runs/step 可放大计算量), 仅靠 20/min 限流
    不足以防止被当作免费算力; 鉴权只在配置了 ADMIN_TOKEN 时生效, 本地 dev 仍放行。
    """
    result = await asyncio.to_thread(
        validate_returns,
        req.returns,
        train_size=req.train_size,
        test_size=req.test_size,
        step=req.step,
        runs=req.runs,
    )
    return {"data": result}


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

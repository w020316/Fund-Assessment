"""出手三问闸门(龙头弱转强模式)

参考短线交易中的"三问全过才出手,一问不过直接放弃"纪律:
1. 问身份:是不是热点板块的龙头票?能不能带动板块?
2. 问断板:断板当天收盘价是否高于前一日收盘价?
3. 问竞价:次日集合竞价是否高开?

实现原则:
- 只做模式校验,不预测涨跌,不构成投资建议
- 任一问数据缺失 → 按"不通过"处理(fail-closed),避免用不确定的数据下单
- 自动取数模式的数据来自项目既有行情接口;手动模式允许用户填写三项证据
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from loguru import logger

from src.analysis.market_review import fetch_limit_pool
from src.core import data_source_v2 as ds2


@dataclass
class GateQuestion:
    key: str
    title: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)


@dataclass
class GateResult:
    stock_code: str
    stock_name: str
    passed: bool
    verdict: str
    questions: list[GateQuestion]
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    mode: str = "manual"
    checked_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    risk_note: str = "本工具仅做交易模式校验,不构成投资建议;实盘请自行控制仓位与止损。"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = bool(self.passed)
        return data


class ThreeQuestionGate:
    """三问闸门:全部通过才返回可出手结论"""

    def check(
        self,
        stock_code: str,
        *,
        stock_name: str = "",
        sector: str = "",
        prev_close: float | None = None,
        break_close: float | None = None,
        auction_price: float | None = None,
        sector_change_pct: float | None = None,
        sector_limit_up_count: int | None = None,
        is_sector_leader: bool | None = None,
        mode: str = "manual",
    ) -> GateResult:
        questions: list[GateQuestion] = []
        missing: list[str] = []
        failed: list[str] = []

        # ── 第一问:身份 ──
        identity_missing = (
            sector_change_pct is None
            and sector_limit_up_count is None
            and is_sector_leader is None
        )
        if identity_missing:
            q1_passed = False
            missing.append("身份")
            q1_detail = "缺少板块热度/龙头地位数据,按不通过处理"
        else:
            hot = (sector_limit_up_count or 0) >= 3 or (sector_change_pct or 0) >= 3.0
            leader = bool(is_sector_leader)
            q1_passed = hot and leader
            heat_text = []
            if sector_limit_up_count is not None:
                heat_text.append(f"板块涨停 {sector_limit_up_count} 家")
            if sector_change_pct is not None:
                heat_text.append(f"板块涨幅 {sector_change_pct:.2f}%")
            q1_detail = (
                f"{'、'.join(heat_text) or '板块热度未知'};"
                f"{'是' if leader else '不是'}板块龙头 → {'通过' if q1_passed else '不通过'}"
            )
            if not q1_passed:
                failed.append("身份")
        questions.append(GateQuestion(
            key="identity",
            title="第一问 · 身份",
            passed=q1_passed,
            detail=q1_detail,
            evidence={
                "sector": sector,
                "sector_change_pct": sector_change_pct,
                "sector_limit_up_count": sector_limit_up_count,
                "is_sector_leader": is_sector_leader,
            },
        ))

        # ── 第二问:断板 ──
        if prev_close is None or break_close is None or prev_close <= 0 or break_close <= 0:
            q2_passed = False
            missing.append("断板")
            q2_detail = "缺少断板日收盘价/前一日收盘价,按不通过处理"
            change_pct = None
        else:
            q2_passed = break_close > prev_close
            change_pct = round((break_close / prev_close - 1) * 100, 2)
            q2_detail = (
                f"断板日收盘 {break_close:.2f} vs 前一日收盘 {prev_close:.2f}"
                f"({change_pct:+.2f}%) → {'通过' if q2_passed else '不通过'}"
            )
            if not q2_passed:
                failed.append("断板")
        questions.append(GateQuestion(
            key="break_board",
            title="第二问 · 断板",
            passed=q2_passed,
            detail=q2_detail,
            evidence={
                "prev_close": prev_close,
                "break_close": break_close,
                "change_pct": change_pct,
            },
        ))

        # ── 第三问:竞价 ──
        if auction_price is None or break_close is None or auction_price <= 0 or break_close <= 0:
            q3_passed = False
            missing.append("竞价")
            q3_detail = "缺少竞价价格/断板日收盘价,按不通过处理"
            gap_pct = None
        else:
            q3_passed = auction_price > break_close
            gap_pct = round((auction_price / break_close - 1) * 100, 2)
            q3_detail = (
                f"竞价 {auction_price:.2f} vs 断板日收盘 {break_close:.2f}"
                f"({gap_pct:+.2f}%) → {'通过' if q3_passed else '不通过'}"
            )
            if not q3_passed:
                failed.append("竞价")
        questions.append(GateQuestion(
            key="auction",
            title="第三问 · 竞价",
            passed=q3_passed,
            detail=q3_detail,
            evidence={
                "auction_price": auction_price,
                "break_close": break_close,
                "gap_pct": gap_pct,
            },
        ))

        passed = all(q.passed for q in questions)
        if passed:
            verdict = "三问全过:符合龙头弱转强模式,可按计划出手(仍需仓位与止损约束)"
        elif missing:
            verdict = f"放弃:{'、'.join(missing)}数据不足,按未通过处理"
        else:
            verdict = f"放弃:{'、'.join(failed)}不通过,不属于该模式"

        return GateResult(
            stock_code=stock_code,
            stock_name=stock_name,
            passed=passed,
            verdict=verdict,
            questions=questions,
            missing=missing,
            failed=failed,
            mode=mode,
        )

    def auto_check(self, stock_code: str, *, sector: str = "") -> GateResult:
        """自动取数校验

        - 断板/竞价:最近两根已完成日 K + 实时行情的开盘价
        - 身份:当日涨停池中的同行业涨停家数,以及个股在行业内的连板/封单排名
        """
        quote: dict = {}
        klines: list[dict] = []
        pool: list[dict] = []
        try:
            quotes = ds2.get_realtime_quote_tencent([stock_code])
            quote = quotes[0] if quotes else {}
        except Exception as e:
            logger.warning(f"three_question quote failed: {e}")
        try:
            klines = ds2.get_kline_mootdx(stock_code, period="daily", count=6) or []
        except Exception as e:
            logger.warning(f"three_question kline failed: {e}")
        try:
            pool = fetch_limit_pool(date.today().strftime("%Y%m%d"))
        except Exception as e:
            logger.warning(f"three_question limit pool failed: {e}")

        stock_name = str(quote.get("name", "")) or stock_code

        # 只保留已完成交易日(若最后一根是今日盘中 K,则剔除)
        today_str = date.today().strftime("%Y-%m-%d")
        completed = [k for k in klines if str(k.get("date", ""))[:10] < today_str]
        completed.sort(key=lambda k: str(k.get("date", "")))
        prev_close = float(completed[-2].get("close", 0)) if len(completed) >= 2 else None
        break_close = float(completed[-1].get("close", 0)) if completed else None

        # 竞价价:盘前 09:25 后 quote.open 即为集合竞价成交价;盘前未产生则为空
        auction_price = float(quote.get("open", 0) or 0) or None

        row = next((item for item in pool if item.get("code") == stock_code), None)
        sector_name = sector or (row.get("industry", "") if row else "")
        sector_rows = [item for item in pool if item.get("industry") == sector_name] if sector_name else []
        sector_count = len(sector_rows) if sector_rows else None

        is_leader: bool | None = None
        if row and sector_rows:
            ranked = sorted(
                sector_rows,
                key=lambda x: (x.get("streak", 1), x.get("seal_amount", 0)),
                reverse=True,
            )
            is_leader = ranked[0].get("code") == stock_code
        elif row:
            is_leader = False

        return self.check(
            stock_code,
            stock_name=stock_name,
            sector=sector_name,
            prev_close=prev_close,
            break_close=break_close,
            auction_price=auction_price,
            sector_change_pct=None,
            sector_limit_up_count=sector_count,
            is_sector_leader=is_leader,
            mode="auto",
        )


def check_three_questions(
    stock_code: str,
    *,
    mode: str = "auto",
    sector: str = "",
    stock_name: str = "",
    prev_close: float | None = None,
    break_close: float | None = None,
    auction_price: float | None = None,
    sector_change_pct: float | None = None,
    sector_limit_up_count: int | None = None,
    is_sector_leader: bool | None = None,
) -> dict:
    """供路由直接调用的便捷函数"""
    gate = ThreeQuestionGate()
    if mode == "auto":
        return gate.auto_check(stock_code, sector=sector).to_dict()
    return gate.check(
        stock_code,
        stock_name=stock_name,
        sector=sector,
        prev_close=prev_close,
        break_close=break_close,
        auction_price=auction_price,
        sector_change_pct=sector_change_pct,
        sector_limit_up_count=sector_limit_up_count,
        is_sector_leader=is_sector_leader,
        mode="manual",
    ).to_dict()

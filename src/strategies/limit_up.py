from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd
import akshare as ak


class LimitLevel(str, Enum):
    FIRST = "首板"
    SECOND = "二板"
    THIRD_PLUS = "三板+"


class LimitReason(str, Enum):
    THEME = "题材"
    PERFORMANCE = "业绩"
    CAPITAL = "资金"


@dataclass
class LimitUpInfo:
    stock_code: str
    stock_name: str
    level: LimitLevel
    reason: LimitReason
    seal_time: str
    open_count: int
    seal_volume: float
    quality_score: float


class LimitUpAnalyzer:
    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = {}

    def _load_history(self) -> None:
        try:
            df = ak.stock_zt_pool_em(date=pd.Timestamp.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get("代码", ""))
                    if code not in self._history:
                        self._history[code] = []
                    self._history[code].append({
                        "date": pd.Timestamp.now().strftime("%Y-%m-%d"),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "seal_time": str(row.get("首次封板时间", "")),
                        "open_count": int(row.get("炸板次数", 0)),
                        "seal_amount": float(row.get("封板资金", 0)),
                    })
        except Exception:
            pass

    def _determine_level(self, stock_code: str) -> LimitLevel:
        count = len(self._history.get(stock_code, []))
        if count >= 3:
            return LimitLevel.THIRD_PLUS
        if count == 2:
            return LimitLevel.SECOND
        return LimitLevel.FIRST

    def _determine_reason(self, stock_code: str) -> LimitReason:
        try:
            df = ak.stock_board_concept_name_em()
            if df is not None and not df.empty:
                return LimitReason.THEME
        except Exception:
            pass
        try:
            df_fin = ak.stock_financial_abstract_ths(symbol=stock_code)
            if df_fin is not None and not df_fin.empty:
                latest = df_fin.iloc[0]
                net_profit_growth = float(latest.get("净利润同比增长(%)", 0))
                if net_profit_growth > 30:
                    return LimitReason.PERFORMANCE
        except Exception:
            pass
        return LimitReason.CAPITAL

    def _calc_quality_score(
        self,
        seal_time: str,
        open_count: int,
        seal_volume: float,
        level: LimitLevel,
    ) -> float:
        score = 50.0
        if seal_time:
            try:
                hour = int(seal_time.split(":")[0])
                minute = int(seal_time.split(":")[1])
                total_min = hour * 60 + minute
                if total_min <= 570:
                    score += 25.0
                elif total_min <= 600:
                    score += 15.0
                elif total_min <= 630:
                    score += 5.0
                else:
                    score -= 5.0
            except (ValueError, IndexError):
                pass
        if open_count == 0:
            score += 15.0
        elif open_count == 1:
            score += 5.0
        elif open_count >= 3:
            score -= 10.0
        if seal_volume > 1e8:
            score += 10.0
        elif seal_volume > 5e7:
            score += 5.0
        elif seal_volume < 1e7:
            score -= 5.0
        if level == LimitLevel.THIRD_PLUS:
            score += 10.0
        elif level == LimitLevel.SECOND:
            score += 5.0
        return max(0.0, min(100.0, score))

    def scan_limit_up(self) -> list[LimitUpInfo]:
        self._load_history()
        results: list[LimitUpInfo] = []
        try:
            df = ak.stock_zt_pool_em(date=pd.Timestamp.now().strftime("%Y%m%d"))
            if df is None or df.empty:
                return results
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                seal_time = str(row.get("首次封板时间", ""))
                open_count = int(row.get("炸板次数", 0))
                seal_volume = float(row.get("封板资金", 0))
                level = self._determine_level(code)
                reason = self._determine_reason(code)
                quality = self._calc_quality_score(seal_time, open_count, seal_volume, level)
                results.append(LimitUpInfo(
                    stock_code=code,
                    stock_name=name,
                    level=level,
                    reason=reason,
                    seal_time=seal_time,
                    open_count=open_count,
                    seal_volume=seal_volume,
                    quality_score=round(quality, 2),
                ))
        except Exception:
            pass
        return results

    def analyze_limit_up(self, stock_code: str) -> dict:
        self._load_history()
        result: dict = {
            "stock_code": stock_code,
            "level": LimitLevel.FIRST.value,
            "reason": LimitReason.CAPITAL.value,
            "seal_time": "",
            "open_count": 0,
            "seal_volume": 0.0,
            "quality_score": 0.0,
        }
        try:
            df = ak.stock_zt_pool_em(date=pd.Timestamp.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                row = df[df["代码"] == stock_code]
                if not row.empty:
                    r = row.iloc[0]
                    seal_time = str(r.get("首次封板时间", ""))
                    open_count = int(r.get("炸板次数", 0))
                    seal_volume = float(r.get("封板资金", 0))
                    level = self._determine_level(stock_code)
                    reason = self._determine_reason(stock_code)
                    quality = self._calc_quality_score(seal_time, open_count, seal_volume, level)
                    result.update({
                        "level": level.value,
                        "reason": reason.value,
                        "seal_time": seal_time,
                        "open_count": open_count,
                        "seal_volume": seal_volume,
                        "quality_score": round(quality, 2),
                    })
        except Exception:
            pass
        return result

    def predict_promotion(self, stock_code: str) -> dict:
        self._load_history()
        level = self._determine_level(stock_code)
        base_prob: dict[LimitLevel, float] = {
            LimitLevel.FIRST: 25.0,
            LimitLevel.SECOND: 35.0,
            LimitLevel.THIRD_PLUS: 45.0,
        }
        prob = base_prob.get(level, 25.0)
        try:
            df = ak.stock_zt_pool_em(date=pd.Timestamp.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                row = df[df["代码"] == stock_code]
                if not row.empty:
                    r = row.iloc[0]
                    open_count = int(r.get("炸板次数", 0))
                    seal_volume = float(r.get("封板资金", 0))
                    seal_time = str(r.get("首次封板时间", ""))
                    if open_count == 0:
                        prob += 15.0
                    elif open_count >= 3:
                        prob -= 10.0
                    if seal_volume > 2e8:
                        prob += 10.0
                    elif seal_volume > 1e8:
                        prob += 5.0
                    if seal_time:
                        try:
                            hour = int(seal_time.split(":")[0])
                            minute = int(seal_time.split(":")[1])
                            total_min = hour * 60 + minute
                            if total_min <= 570:
                                prob += 15.0
                            elif total_min <= 600:
                                prob += 8.0
                            elif total_min > 660:
                                prob -= 5.0
                        except (ValueError, IndexError):
                            pass
        except Exception:
            pass
        try:
            df_ind = ak.stock_individual_fund_flow(
                stock=stock_code, market="sh" if stock_code.startswith("6") else "sz"
            )
            if df_ind is not None and not df_ind.empty:
                net_pct = float(df_ind.iloc[-1].get("主力净流入-净占比", 0))
                if net_pct > 5:
                    prob += 10.0
                elif net_pct < -5:
                    prob -= 10.0
        except Exception:
            pass
        prob = max(0.0, min(100.0, prob))
        return {
            "stock_code": stock_code,
            "current_level": level.value,
            "promotion_prob": round(prob, 2),
            "confidence": "high" if prob > 60 else ("medium" if prob > 40 else "low"),
        }

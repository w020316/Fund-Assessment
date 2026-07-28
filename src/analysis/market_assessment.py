"""大盘研判增强

基于4个维度计算大盘温度计(0-100),并给出综合研判:
1. 指数趋势(30%): 上证/深证/创业板当日涨跌幅
2. 板块轮动(25%): 上涨板块数 / 总板块数
3. 资金流向(25%): 北向资金净流入 + 板块主力净流入
4. 市场情绪(20%): 涨跌家数比

温度计区间:
- 80-100: 极热(注意风险)
- 60-80:  偏热(谨慎乐观)
- 40-60:  中性(观望)
- 20-40:  偏冷(逢低布局)
- 0-20:   极冷(恐慌底部)
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

from src.core import data_source_v2 as ds2


def _calc_index_trend_score(index_data: list[dict]) -> dict[str, Any]:
    """指数趋势评分(30%)

    基于上证/深证/创业板涨跌幅平均,映射到0-100
    """
    if not index_data:
        return {"score": 50, "change_pct": 0, "indices": [], "note": "指数数据不可用"}

    changes = []
    indices_info = []
    for idx in index_data:
        try:
            chg = float(idx.get("change_pct", 0))
            changes.append(chg)
            indices_info.append({
                "name": idx.get("name", ""),
                "change_pct": round(chg, 2),
            })
        except (ValueError, TypeError):
            continue

    if not changes:
        return {"score": 50, "change_pct": 0, "indices": [], "note": "无有效数据"}

    avg_change = sum(changes) / len(changes)
    # 涨跌幅映射: -5% → 0, 0% → 50, +5% → 100
    score = max(0, min(100, 50 + avg_change * 10))

    return {
        "score": round(score, 1),
        "change_pct": round(avg_change, 2),
        "indices": indices_info,
        "weight": 0.30,
    }


def _calc_sector_rotation_score(sector_ranking: list[dict]) -> dict[str, Any]:
    """板块轮动评分(25%)

    基于上涨板块数 / 总板块数
    """
    if not sector_ranking:
        return {"score": 50, "note": "板块数据不可用"}

    total = len(sector_ranking)
    gain_count = sum(1 for s in sector_ranking if float(s.get("change_pct", 0)) > 0)
    fall_count = sum(1 for s in sector_ranking if float(s.get("change_pct", 0)) < 0)

    # 上涨占比映射: 0% → 0, 50% → 50, 100% → 100
    ratio = gain_count / total if total > 0 else 0.5
    score = ratio * 100

    # 板块涨跌强度(平均涨跌幅)
    avg_change = sum(float(s.get("change_pct", 0)) for s in sector_ranking) / total if total else 0

    return {
        "score": round(score, 1),
        "total_sectors": total,
        "gain_count": gain_count,
        "fall_count": fall_count,
        "avg_change_pct": round(avg_change, 2),
        "weight": 0.25,
    }


def _calc_capital_flow_score(northbound: dict, sector_ranking: list[dict]) -> dict[str, Any]:
    """资金流向评分(25%)

    综合北向资金净流入 + 板块主力净流入
    """
    # 北向资金(单位: 元,通常为亿级别)
    nb_net = float(northbound.get("total_net_inflow", 0) or 0) if northbound else 0
    # 北向资金评分: 净流入100亿 → 100, 0 → 50, -100亿 → 0
    # 1亿 = 100000000 元
    nb_score = max(0, min(100, 50 + nb_net / 1_0000_0000 * 0.5))

    # 板块主力净流入(单位: 元)
    sector_main_net = sum(float(s.get("main_net_inflow", 0) or 0) for s in sector_ranking) if sector_ranking else 0
    # 板块主力净流入评分: 100亿 → 100, 0 → 50, -100亿 → 0
    sector_score = max(0, min(100, 50 + sector_main_net / 1_0000_0000 * 0.5))

    # 综合: 北向 50% + 板块主力 50%
    score = (nb_score + sector_score) / 2

    return {
        "score": round(score, 1),
        "northbound_net": round(nb_net / 1_0000_0000, 2),  # 转为亿元
        "sector_main_net": round(sector_main_net / 1_0000_0000, 2),
        "northbound_score": round(nb_score, 1),
        "sector_score": round(sector_score, 1),
        "weight": 0.25,
    }


def _calc_sentiment_score(sentiment: dict) -> dict[str, Any]:
    """市场情绪评分(20%)

    基于涨跌家数比
    """
    if not sentiment or sentiment.get("sentiment") == "--":
        return {"score": 50, "note": "情绪数据不可用"}

    rise = int(sentiment.get("rise_count", 0))
    fall = int(sentiment.get("fall_count", 0))
    total = rise + fall
    if total == 0:
        return {"score": 50, "note": "无涨跌家数数据"}

    ratio = rise / total
    score = ratio * 100

    return {
        "score": round(score, 1),
        "rise_count": rise,
        "fall_count": fall,
        "ratio": round(ratio, 3),
        "sentiment": sentiment.get("sentiment", ""),
        "weight": 0.20,
    }


def _calc_thermometer(
    index_score: dict,
    sector_score: dict,
    capital_score: dict,
    sentiment_score: dict,
) -> dict[str, Any]:
    """综合计算大盘温度计(0-100)"""
    total_score = (
        index_score.get("score", 50) * index_score.get("weight", 0.30) +
        sector_score.get("score", 50) * sector_score.get("weight", 0.25) +
        capital_score.get("score", 50) * capital_score.get("weight", 0.25) +
        sentiment_score.get("score", 50) * sentiment_score.get("weight", 0.20)
    )

    # 温度等级
    if total_score >= 80:
        level = "极热"
        action = "注意风险,防范回调"
    elif total_score >= 60:
        level = "偏热"
        action = "谨慎乐观,持有为主"
    elif total_score >= 40:
        level = "中性"
        action = "观望,等待方向"
    elif total_score >= 20:
        level = "偏冷"
        action = "逢低布局,分批建仓"
    else:
        level = "极冷"
        action = "恐慌底部,可考虑定投"

    return {
        "score": round(total_score, 1),
        "level": level,
        "action": action,
        "components": {
            "index_trend": index_score,
            "sector_rotation": sector_score,
            "capital_flow": capital_score,
            "sentiment": sentiment_score,
        },
    }


async def get_market_thermometer() -> dict[str, Any]:
    """大盘温度计主入口"""
    # 并行抓取4源数据
    funcs = [
        ("index", ds2.get_index_realtime),
        ("sectors", ds2.get_sector_ranking),
        ("northbound", ds2.get_northbound_flow_realtime),
        ("sentiment", ds2.get_market_sentiment),
    ]
    fetched = ds2._parallel_fetch(funcs)

    index_data = fetched.get("index") or []
    sector_ranking = fetched.get("sectors") or []
    northbound = fetched.get("northbound") or {}
    sentiment = fetched.get("sentiment") or {}

    index_score = _calc_index_trend_score(index_data)
    sector_score = _calc_sector_rotation_score(sector_ranking)
    capital_score = _calc_capital_flow_score(northbound, sector_ranking)
    sentiment_score = _calc_sentiment_score(sentiment)

    thermometer = _calc_thermometer(index_score, sector_score, capital_score, sentiment_score)
    thermometer["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return thermometer


async def get_capital_flow_overview() -> dict[str, Any]:
    """资金流向总览(北向+板块主力)"""
    funcs = [
        ("northbound", ds2.get_northbound_flow_realtime),
        ("sectors", ds2.get_sector_ranking),
    ]
    fetched = ds2._parallel_fetch(funcs)

    northbound = fetched.get("northbound") or {}
    sector_ranking = fetched.get("sectors") or []

    # 板块主力净流入Top5(正)
    sectors_with_main = [s for s in sector_ranking if s.get("main_net_inflow")]
    top_inflow = sorted(sectors_with_main, key=lambda x: float(x.get("main_net_inflow", 0)), reverse=True)[:5]
    top_outflow = sorted(sectors_with_main, key=lambda x: float(x.get("main_net_inflow", 0)))[:5]

    total_sector_main_net = sum(float(s.get("main_net_inflow", 0) or 0) for s in sector_ranking)

    return {
        "northbound": {
            "total_net_inflow": round(float(northbound.get("total_net_inflow", 0) or 0) / 1_0000_0000, 2),
            "sh_net_inflow": round(float(northbound.get("sh_net_inflow", 0) or 0) / 1_0000_0000, 2),
            "sz_net_inflow": round(float(northbound.get("sz_net_inflow", 0) or 0) / 1_0000_0000, 2),
            "date": northbound.get("date", ""),
        },
        "sector_main_flow": {
            "total_net_inflow": round(total_sector_main_net / 1_0000_0000, 2),
            "top_inflow": [
                {
                    "name": s.get("name", ""),
                    "change_pct": round(float(s.get("change_pct", 0)), 2),
                    "main_net_inflow": round(float(s.get("main_net_inflow", 0) or 0) / 1_0000_0000, 2),
                }
                for s in top_inflow
            ],
            "top_outflow": [
                {
                    "name": s.get("name", ""),
                    "change_pct": round(float(s.get("change_pct", 0)), 2),
                    "main_net_inflow": round(float(s.get("main_net_inflow", 0) or 0) / 1_0000_0000, 2),
                }
                for s in top_outflow
            ],
        },
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def get_market_assessment() -> dict[str, Any]:
    """大盘综合研判(温度计+板块轮动+资金流向)

    整合多个维度,给出大盘整体判断
    """
    # 并行获取温度计+板块轮动
    thermometer, capital_overview = await asyncio.gather(
        get_market_thermometer(),
        get_capital_flow_overview(),
    )

    score = thermometer.get("score", 50)
    level = thermometer.get("level", "中性")

    # 综合研判
    if score >= 70:
        assessment = "市场情绪过热,短期可能面临回调压力,建议控制仓位"
    elif score >= 55:
        assessment = "市场偏强,可适度参与,关注景气板块"
    elif score >= 45:
        assessment = "市场震荡,方向不明,观望为主"
    elif score >= 30:
        assessment = "市场偏弱,等待企稳信号,可小额定投"
    else:
        assessment = "市场恐慌,可能接近底部区域,适合分批布局"

    # 板块轮动信号
    sectors = thermometer.get("components", {}).get("sector_rotation", {})
    if sectors.get("gain_count", 0) > sectors.get("fall_count", 0) * 2:
        sector_signal = "板块普涨"
    elif sectors.get("fall_count", 0) > sectors.get("gain_count", 0) * 2:
        sector_signal = "板块普跌"
    else:
        sector_signal = "板块分化"

    # 资金信号
    nb_net = capital_overview.get("northbound", {}).get("total_net_inflow", 0)
    if nb_net > 50:
        capital_signal = "北向资金大幅净流入"
    elif nb_net > 0:
        capital_signal = "北向资金小幅净流入"
    elif nb_net > -50:
        capital_signal = "北向资金小幅净流出"
    else:
        capital_signal = "北向资金大幅净流出"

    return {
        "thermometer": thermometer,
        "capital_overview": capital_overview,
        "assessment": assessment,
        "sector_signal": sector_signal,
        "capital_signal": capital_signal,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

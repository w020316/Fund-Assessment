import numpy as np
import pandas as pd
import akshare as ak


def analyze_capital_flow(stock_code: str) -> dict:
    result: dict = {
        "main_net_inflow": 0.0,
        "large_order_ratio": 0.0,
        "medium_order_ratio": 0.0,
        "small_order_ratio": 0.0,
        "northbound_change": 0.0,
    }

    try:
        flow_df = ak.stock_individual_fund_flow(stock=stock_code, market="sh" if stock_code.startswith("6") else "sz")
        if flow_df is not None and not flow_df.empty:
            latest = flow_df.iloc[-1]
            col_map = {c: c.strip() for c in flow_df.columns}
            flow_df.columns = [c.strip() for c in flow_df.columns]

            for key, possible_cols in [
                ("main_net_inflow", ["主力净流入-净额", "主力净流入", "净流入-净额"]),
                ("large_order_ratio", ["超大单净流入-净占比", "超大单净占比", "大单净流入-净占比"]),
                ("medium_order_ratio", ["中单净流入-净占比", "中单净占比"]),
                ("small_order_ratio", ["小单净流入-净占比", "小单净占比"]),
            ]:
                for col in possible_cols:
                    if col in flow_df.columns:
                        val = latest[col]
                        if pd.notna(val):
                            result[key] = float(val)
                        break
    except Exception:
        pass

    try:
        north_df = ak.stock_hsgt_individual_em(stock=stock_code)
        if north_df is not None and not north_df.empty:
            north_df.columns = [c.strip() for c in north_df.columns]
            latest_north = north_df.iloc[-1]
            for col in ["持股数量", "持股", "持股变化"]:
                if col in north_df.columns:
                    val = latest_north[col]
                    if pd.notna(val):
                        result["northbound_change"] = float(val)
                        break
    except Exception:
        pass

    return result


def _score_main_inflow(main_net_inflow: float) -> float:
    if main_net_inflow > 0:
        if main_net_inflow > 5e8:
            return 40.0
        elif main_net_inflow > 1e8:
            return 32.0
        elif main_net_inflow > 5e7:
            return 24.0
        else:
            return 16.0
    else:
        if main_net_inflow < -5e8:
            return 4.0
        elif main_net_inflow < -1e8:
            return 10.0
        elif main_net_inflow < -5e7:
            return 14.0
        else:
            return 18.0


def _score_northbound(northbound_change: float) -> float:
    if northbound_change > 0:
        if northbound_change > 1e6:
            return 30.0
        elif northbound_change > 1e5:
            return 24.0
        else:
            return 18.0
    elif northbound_change < 0:
        if northbound_change < -1e6:
            return 4.0
        elif northbound_change < -1e5:
            return 10.0
        else:
            return 14.0
    return 15.0


def _score_large_order(large_order_ratio: float) -> float:
    if large_order_ratio > 5:
        return 30.0
    elif large_order_ratio > 2:
        return 24.0
    elif large_order_ratio > 0:
        return 18.0
    elif large_order_ratio > -2:
        return 12.0
    elif large_order_ratio > -5:
        return 8.0
    else:
        return 4.0


def score_capital(stock_code: str) -> float:
    data = analyze_capital_flow(stock_code)

    main_score = _score_main_inflow(data["main_net_inflow"])
    north_score = _score_northbound(data["northbound_change"])
    large_score = _score_large_order(data["large_order_ratio"])

    total = main_score + north_score + large_score
    return round(total, 2)

"""持仓重叠 X-Ray(阶段六批次 A,T-A3,简化版)—— 纯计算,零网络请求。

口径(架构文档 §3.4 #4):
- 数据: 每只基金最新季报前十大重仓(由 fund_nav_source.get_fund_holdings 提供)
- 两两重叠度 = Σ min(w_i_stock, w_j_stock)(min-weight overlap,仅共同重仓股)
- 单股暴露 = 跨基金合计隐含权重(Σ 持有该股的基金权重,未经基金市值再加权)
- 集中度预警: 单股隐含权重 > 15%;单基金市值占比 > 40%

数据可得性降级(主理人拍板):
- 披露持仓拿不到的基金 → 计入 missing_funds,report.degraded=True;
  全部基金都拿不到 → status="insufficient_data"、message="数据不足",
  返回显式说明而非空结果(不撒谎、不 fabrication)。
"""
from __future__ import annotations

from typing import Any

from src.core.fund_nav_source import FundHoldings

#: 单股隐含权重预警阈值(0~1)
STOCK_CONCENTRATION_THRESHOLD: float = 0.15

#: 单基金市值占比预警阈值(0~1)
FUND_CONCENTRATION_THRESHOLD: float = 0.40

SCHEMA_VERSION: int = 1


def build_xray(
    holdings_map: dict[str, FundHoldings | None],
    fund_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """构建 X-Ray 报告。

    Args:
        holdings_map: {fund_code: FundHoldings | None}——None 表示披露持仓不可得
        fund_weights: {fund_code: 市值占比 0~1}(用于单基金集中度预警,可缺省)

    Returns:
        {
          "schema_version": 1,
          "funds": [fund_code...],           # 参与矩阵的基金(按传入顺序)
          "overlap_matrix": [[0~1]],         # 两两重叠度(与 funds 同序)
          "stock_exposure": [ {stock_code, stock_name, fund_count, total_weight, funds:[{fund_code, weight}]} ],
          "concentration_warnings": [str],
          "missing_funds": [fund_code],      # 持仓数据缺失的基金
          "data_as_of": {fund_code: "2026Q3"},
          "degraded": bool,
          "status": "ok" | "insufficient_data",
          "message": str,
        }
    """
    fund_weights = fund_weights or {}
    funds = [c for c, h in holdings_map.items() if h is not None and h.holdings]
    missing_funds = [c for c, h in holdings_map.items() if c not in funds]

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "funds": funds,
        "overlap_matrix": [],
        "stock_exposure": [],
        "concentration_warnings": [],
        "missing_funds": missing_funds,
        "data_as_of": {c: (holdings_map[c].as_of or "") for c in funds},
        "degraded": bool(missing_funds),
        "status": "ok",
        "message": "",
    }

    # 数据可得性降级:全部基金都拿不到披露持仓 → 显式"数据不足",不返回空结果
    if not funds:
        report["status"] = "insufficient_data"
        report["message"] = "数据不足:所有持仓基金的披露持仓均不可得(接口失败或基金类型不支持)"
        report["degraded"] = True
        return report

    # ===== 两两重叠度矩阵(min-weight overlap) =====
    weight_maps = {
        c: {h["stock_code"]: float(h.get("weight", 0.0)) / 100.0 for h in holdings_map[c].holdings}
        for c in funds
    }
    n = len(funds)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0  # 自身重叠度定义为 1(与自身完全重合)
        for j in range(i + 1, n):
            wi, wj = weight_maps[funds[i]], weight_maps[funds[j]]
            common = set(wi) & set(wj)
            overlap = sum(min(wi[s], wj[s]) for s in common)
            matrix[i][j] = round(overlap, 4)
            matrix[j][i] = round(overlap, 4)
    report["overlap_matrix"] = matrix

    # ===== 单股跨基金暴露 =====
    exposure: dict[str, dict[str, Any]] = {}
    for c in funds:
        for h in holdings_map[c].holdings:
            code = h["stock_code"]
            w = float(h.get("weight", 0.0)) / 100.0
            ent = exposure.setdefault(code, {
                "stock_code": code,
                "stock_name": h.get("stock_name", ""),
                "fund_count": 0,
                "total_weight": 0.0,
                "funds": [],
            })
            ent["fund_count"] += 1
            ent["total_weight"] += w
            ent["funds"].append({"fund_code": c, "weight": round(w, 4)})
    stock_exposure = sorted(exposure.values(), key=lambda e: (-e["total_weight"], e["stock_code"]))
    for e in stock_exposure:
        e["total_weight"] = round(e["total_weight"], 4)
    report["stock_exposure"] = stock_exposure

    # ===== 集中度预警 =====
    warnings: list[str] = []
    for e in stock_exposure:
        if e["total_weight"] > STOCK_CONCENTRATION_THRESHOLD:
            warnings.append(
                f"单股 {e['stock_name'] or e['stock_code']} 跨基金隐含权重 "
                f"{e['total_weight'] * 100:.1f}% 超过 {STOCK_CONCENTRATION_THRESHOLD * 100:.0f}% 预警线"
            )
    for c in funds:
        w = float(fund_weights.get(c, 0.0))
        if w > FUND_CONCENTRATION_THRESHOLD:
            warnings.append(f"单基金 {c} 市值占比 {w * 100:.1f}% 超过 {FUND_CONCENTRATION_THRESHOLD * 100:.0f}% 预警线")
    report["concentration_warnings"] = warnings
    return report

"""测试配置与公共fixtures"""
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@pytest.fixture
def sample_quote():
    """示例行情数据"""
    return {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1688.00,
        "change": 18.50,
        "change_pct": 1.11,
        "volume": 32567890,
        "amount": 5498765432.0,
        "high": 1695.00,
        "low": 1670.00,
        "open": 1675.00,
        "prev_close": 1669.50,
        "turnover": 2.59,
        "pe_ttm": 28.5,
        "pb": 9.2,
        "total_market_value": 2120000000000,
        "circ_market_value": 2120000000000,
        "high_limit": 1836.45,
        "low_limit": 1502.55,
    }


@pytest.fixture
def sample_kline():
    """示例K线数据"""
    return [
        {"date": "2026-06-01", "open": 1660, "close": 1670, "high": 1680, "low": 1655, "volume": 28000000, "amount": 4660000000},
        {"date": "2026-06-02", "open": 1672, "close": 1685, "high": 1690, "low": 1668, "volume": 31000000, "amount": 5210000000},
        {"date": "2026-06-03", "open": 1683, "close": 1678, "high": 1692, "low": 1675, "volume": 29500000, "amount": 4960000000},
        {"date": "2026-06-04", "open": 1680, "close": 1695, "high": 1700, "low": 1678, "volume": 33000000, "amount": 5580000000},
        {"date": "2026-06-05", "open": 1693, "close": 1688, "high": 1698, "low": 1685, "volume": 30500000, "amount": 5150000000},
    ]


@pytest.fixture
def sample_capital_flow():
    """示例资金流向数据"""
    return {
        "main_inflow": 500000000,
        "main_outflow": 450000000,
        "main_net_inflow": 50000000,
        "main_inflow_pct": 52.6,
        "super_large_net_inflow": 30000000,
        "large_net_inflow": 20000000,
        "medium_net_inflow": -15000000,
        "small_net_inflow": -35000000,
    }


@pytest.fixture
def sample_financial():
    """示例财务数据"""
    return {
        "pe_ttm": 28.5,
        "pb": 9.2,
        "roe": 31.2,
        "gross_margin": 91.5,
        "net_margin": 52.3,
        "revenue_yoy": 15.8,
        "profit_yoy": 18.2,
    }

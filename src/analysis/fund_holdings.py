"""基金重仓股板块分析

功能:
1. 基金持仓抓取(东方财富 fundf10 接口,前10大重仓股)
2. 板块映射(重仓股 → 行业板块 → 板块涨跌幅)
3. 持仓集中度(HHI 指数 + Top5/Top10 集中度)
4. 净值影响预估(重仓股当日涨跌 → 基金预估涨跌)
5. 板块轮动信号(重仓股所在板块涨跌排名)

设计原则:
- 数据可降级:东方财富不可用时,akshare 兜底
- 不造假:抓取失败返回空列表,不编造数据
- 可解释:每个指标附带计算来源
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import datetime
from typing import Any

import requests
from loguru import logger

from src.core import data_source_v2 as ds2

try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False


_EM_FUND_SESSION = requests.Session()
_EM_FUND_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://fundf10.eastmoney.com/",
})

# P0 诊断(2026-07-29 v1.3):记录最后一次重仓股抓取失败原因
# 用于区分"基金无重仓股数据"与"数据源不可达"
# 典型场景:Render Free 国外 IP 被 eastmoney fundf10 封禁,本地可访问但部署环境不可达
# P2 修复(2026-07-30):原为单一全局 str,并发调用不同基金时会互相覆盖:
#   线程A(161725)失败设reason→线程B(110022)成功清空reason→A读到空reason误判为"基金无数据"
# 改为以 fund_code 为 key 的 dict,并加锁保护,避免并发写冲突。
_last_fetch_reason_map: dict[str, str] = {}
_last_fetch_reason_lock = threading.Lock()


def _set_fetch_reason(fund_code: str, reason: str) -> None:
    """设置指定基金的抓取失败原因(空字符串表示成功/重置)"""
    with _last_fetch_reason_lock:
        if reason:
            _last_fetch_reason_map[fund_code] = reason
        else:
            _last_fetch_reason_map.pop(fund_code, None)


def _get_fetch_reason(fund_code: str) -> str:
    """读取指定基金的抓取失败原因(空字符串表示无失败)"""
    with _last_fetch_reason_lock:
        return _last_fetch_reason_map.get(fund_code, "")


def _current_year_month() -> tuple[str, str]:
    """当前年份和月份(用于东方财富持仓接口)"""
    now = datetime.now()
    # 基金季报披露: Q1 1月底-4月底, Q2 7月-8月底, Q3 10月-10月底, Q4 1月-3月底
    # 简单处理: 当前月份即为最新持仓月份(接口会自动回退到最近一期)
    return str(now.year), f"{now.month:02d}"


def _fetch_fund_holdings_em(fund_code: str, topline: int = 10) -> list[dict]:
    """东方财富基金重仓股抓取

    Args:
        fund_code: 6位基金代码
        topline: 返回前N大重仓股

    Returns:
        [{"code", "name", "weight", "hold_amount", "hold_value", "quarter", "change"}, ...]
    """
    year, month = _current_year_month()
    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    params = {
        "type": "jjcc",
        "code": fund_code,
        "topline": topline,
        "year": year,
        "month": "",
    }
    try:
        resp = _EM_FUND_SESSION.get(url, params=params, timeout=8)
        text = resp.text
        # 接口返回 var apidata={ content:"<html>...</html>",arryear:[...],... };
        m = re.search(r'apidata\s*=\s*({.*?})\s*;', text, re.DOTALL)
        if not m:
            _reason = (
                f"em接口响应格式异常(resp_len={len(text)}, 可能被IP封禁或接口变更)"
            )
            _set_fetch_reason(fund_code, _reason)
            logger.warning(f"fund_holdings_em: {_reason} fund_code={fund_code}")
            return []
        # JS对象转JSON(单引号→双引号, 去除注释)
        js_obj = m.group(1)
        # 提取content字段(HTML)
        content_m = re.search(r'content\s*:\s*"(.*?)"\s*,', js_obj, re.DOTALL)
        if not content_m:
            _set_fetch_reason(fund_code, "em接口返回但无content字段(基金可能无持仓披露)")
            return []
        html = content_m.group(1)
        # 反转义
        html = html.replace("\\/", "/").replace('\\"', '"').replace("\\\\", "\\")
        # 解析HTML表格
        result: list[dict] = []
        # 匹配每行: <tr>...<td>1</td>...<td>600519</td>...<td>贵州茅台</td>...<td>9.85%</td>...</tr>
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) < 8:
                continue
            # 去除HTML标签
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            try:
                # 序号 | 股票代码 | 股票名称 | 占净值比例 | 持股数 | 持仓市值 | 季报 | 持仓变化
                code = cells[1]
                name = cells[2]
                weight_str = cells[3].replace('%', '')
                weight = float(weight_str) if weight_str else 0.0
                hold_amount = float(cells[4].replace(',', '').replace('--', '0')) if cells[4] else 0.0
                hold_value = float(cells[5].replace(',', '').replace('--', '0')) if cells[5] else 0.0
                quarter = cells[6] if len(cells) > 6 else ""
                change_str = cells[7] if len(cells) > 7 else ""
                # 变化: 新进/增持/减持/不变
                change = "不变"
                if "新进" in change_str:
                    change = "新进"
                elif "增持" in change_str:
                    change = "增持"
                elif "减持" in change_str:
                    change = "减持"
                result.append({
                    "code": code,
                    "name": name,
                    "weight": round(weight, 3),
                    "hold_amount": hold_amount,
                    "hold_value": hold_value,
                    "quarter": quarter,
                    "change": change,
                })
            except (ValueError, IndexError) as e:
                logger.debug(f"fund_holdings_em: 解析行失败: {e}, cells={cells}")
                continue
        return result
    except Exception as e:
        _set_fetch_reason(fund_code, f"em接口请求异常: {e}")
        logger.warning(f"fund_holdings_em failed fund_code={fund_code}: {e}")
        return []


def _fetch_fund_holdings_ak(fund_code: str) -> list[dict]:
    """akshare 兜底:基金十大重仓股"""
    if not _HAS_AKSHARE:
        _set_fetch_reason(fund_code, "akshare未安装")
        return []
    try:
        # 季度日期
        now = datetime.now()
        year = now.year
        # akshare 季度参数: 20241, 20242, 20243, 20244
        quarter_month = now.month
        if quarter_month <= 3:
            quarter = f"{year - 1}4"
        elif quarter_month <= 6:
            quarter = f"{year}1"
        elif quarter_month <= 9:
            quarter = f"{year}2"
        else:
            quarter = f"{year}3"
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=quarter)
        if df is None or df.empty:
            return []
        result: list[dict] = []
        for _, row in df.iterrows():
            code = str(row.get("股票代码", "")).strip()
            name = str(row.get("股票名称", "")).strip()
            weight = float(row.get("占净值比例", 0) or 0)
            hold_amount = float(row.get("持股数", 0) or 0)
            hold_value = float(row.get("持仓市值", 0) or 0)
            quarter_str = str(row.get("季度", ""))
            result.append({
                "code": code,
                "name": name,
                "weight": round(weight, 3),
                "hold_amount": hold_amount,
                "hold_value": hold_value,
                "quarter": quarter_str,
                "change": "不变",
            })
        return result[:10]
    except Exception as e:
        _set_fetch_reason(fund_code, f"akshare兜底失败: {e}(底层亦调eastmoney,同源封禁)")
        logger.warning(f"fund_holdings_ak failed fund_code={fund_code}: {e}")
        return []


def get_fund_holdings(fund_code: str) -> list[dict]:
    """获取基金前10大重仓股(东方财富优先,akshare兜底)"""
    cache_key = f"fund_holdings:{fund_code}"
    cached = ds2._cache_get(cache_key)
    if cached is not None:
        return cached

    _set_fetch_reason(fund_code, "")  # 重置:新一轮抓取
    holdings = _fetch_fund_holdings_em(fund_code)
    if not holdings:
        holdings = _fetch_fund_holdings_ak(fund_code)
    if holdings:
        _set_fetch_reason(fund_code, "")  # 成功:清除失败原因

    # 缓存12小时(季报数据变化慢)
    ds2._cache_set(cache_key, holdings, ttl=12 * 3600)
    return holdings


# ===== 板块映射 =====

# 行业板块关键词 → 东方财富行业板块名
# 用于从重仓股名称推断其所属行业板块
# 包含两类: (1) 行业通用关键字 (2) 龙头股名(因部分股票名不含行业关键字)
_STOCK_SECTOR_MAP: list[tuple[str, str]] = [
    # === 龙头股名(优先级高,放在前面) ===
    # 白酒龙头
    ("茅台", "白酒"), ("五粮液", "白酒"), ("泸州老窖", "白酒"),
    ("洋河", "白酒"), ("汾酒", "白酒"), ("古井贡", "白酒"),
    ("今世缘", "白酒"), ("舍得", "白酒"), ("水井坊", "白酒"),
    # 半导体龙头
    ("中芯", "半导体"), ("韦尔", "半导体"), ("北方华创", "半导体"),
    ("中微", "半导体"), ("长电", "半导体"), ("华天", "半导体"),
    ("兆易", "半导体"), ("紫光国微", "半导体"), ("圣邦", "半导体"),
    # 医药龙头
    ("恒瑞", "医药"), ("药明", "医药"), ("迈瑞", "医药"),
    ("片仔癀", "中药"), ("云南白药", "中药"), ("同仁堂", "中药"),
    ("复星", "医药"), ("智飞", "医药"), ("长春高新", "医药"),
    ("爱尔", "医药"), ("通策", "医药"), ("泰格", "医药"),
    # 新能源龙头
    ("宁德时代", "电池"), ("比亚迪", "汽车整车"),
    ("隆基", "光伏"), ("通威", "光伏"), ("阳光", "光伏"),
    ("中环", "光伏"), ("天合", "光伏"),
    # 银行/券商龙头
    ("招商银行", "银行"), ("工商银行", "银行"), ("建设银行", "银行"),
    ("农业银行", "银行"), ("中国银行", "银行"), ("兴业银行", "银行"),
    ("平安银行", "银行"), ("宁波银行", "银行"),
    # 军工龙头
    ("中航", "军工"), ("航发", "军工"), ("沈飞", "军工"),
    ("成飞", "军工"),
    # === 行业通用关键字 ===
    ("银行", "银行"),
    ("证券", "证券"), ("券商", "证券"),
    ("保险", "保险"),
    ("房地产", "房地产"), ("置业", "房地产"), ("地产", "房地产"),
    ("白酒", "白酒"),
    ("啤酒", "酿酒行业"),
    ("食品", "食品饮料"),
    ("乳业", "食品饮料"), ("乳", "食品饮料"),
    ("医药", "医药"), ("医疗", "医药"), ("生物", "医药"), ("药业", "医药"), ("制药", "医药"),
    ("中药", "中药"),
    ("新能源", "新能源"), ("光伏", "光伏"), ("风电", "风电"), ("核电", "电力"),
    ("电池", "电池"),
    ("汽车", "汽车整车"), ("客车", "汽车整车"),
    ("半导体", "半导体"), ("芯片", "半导体"), ("集成电路", "半导体"),
    ("电子", "电子元件"),
    ("软件", "软件"), ("信息", "软件"), ("计算机", "软件"),
    ("通信", "通信"), ("通讯", "通信"),
    ("传媒", "传媒"), ("游戏", "游戏"), ("影视", "传媒"),
    ("煤炭", "煤炭"),
    ("钢铁", "钢铁"),
    ("有色", "有色金属"), ("黄金", "黄金"), ("铜业", "有色金属"), ("铝业", "有色金属"),
    ("石油", "石油"), ("石化", "石油"), ("化工", "化工"),
    ("电力", "电力"),
    ("军工", "军工"), ("航天", "航天航空"), ("航空", "航天航空"),
    ("机械", "机械"), ("装备", "专用设备"),
    ("建材", "水泥建材"), ("水泥", "水泥建材"),
    ("农业", "农牧饲渔"),
    ("环保", "环保"),
    ("物流", "物流"), ("交运", "交运物流"),
    ("旅游", "旅游酒店"), ("酒店", "旅游酒店"),
    ("商业", "商业百货"),
]


def _infer_sector(stock_name: str) -> str:
    """根据股票名称推断所属行业板块"""
    for keyword, sector in _STOCK_SECTOR_MAP:
        if keyword in stock_name:
            return sector
    return ""


def _map_holdings_to_sectors(holdings: list[dict]) -> list[dict]:
    """将重仓股映射到行业板块"""
    sector_map: dict[str, dict] = {}
    for h in holdings:
        name = h.get("name", "")
        code = h.get("code", "")
        weight = h.get("weight", 0)
        sector = _infer_sector(name)
        if not sector:
            continue
        if sector not in sector_map:
            sector_map[sector] = {
                "sector": sector,
                "total_weight": 0.0,
                "stocks": [],
            }
        sector_map[sector]["total_weight"] += weight
        sector_map[sector]["stocks"].append({
            "code": code,
            "name": name,
            "weight": weight,
        })

    result = list(sector_map.values())
    # 按权重排序
    result.sort(key=lambda x: x["total_weight"], reverse=True)
    # 权重四舍五入
    for s in result:
        s["total_weight"] = round(s["total_weight"], 3)
    return result


# ===== 集中度计算 =====

def _calc_concentration(holdings: list[dict]) -> dict[str, Any]:
    """计算持仓集中度指标

    - top5_weight: 前五大重仓股占净值比例合计
    - top10_weight: 前十大重仓股占净值比例合计
    - hhi: Herfindahl-Hirschman Index(赫芬达尔指数),衡量集中度
           HHI = Σ(weight_i)^2,值越大表示越集中
           一般: <1000 低集中, 1000-1800 中等, >1800 高集中
    """
    if not holdings:
        return {
            "top5_weight": 0.0,
            "top10_weight": 0.0,
            "hhi": 0,
            "level": "无数据",
        }

    weights = [h.get("weight", 0) for h in holdings]
    top5 = sum(weights[:5])
    top10 = sum(weights[:10])

    # HHI 计算时权重单位是百分比,标准HHI用占比平方和×10000
    # 这里 weights 已经是百分比数值(如 9.85 表示 9.85%),HHI = Σ(w/100)^2 * 10000 = Σ(w^2)
    hhi = sum(w * w for w in weights)

    if hhi < 1000:
        level = "低集中"
    elif hhi < 1800:
        level = "中等集中"
    else:
        level = "高集中"

    return {
        "top5_weight": round(top5, 3),
        "top10_weight": round(top10, 3),
        "hhi": round(hhi, 2),
        "level": level,
    }


# ===== 净值影响预估 =====

def _estimate_nav_impact(holdings: list[dict], quotes: list[dict]) -> dict[str, Any]:
    """基于重仓股当日涨跌幅预估基金净值影响

    Args:
        holdings: 重仓股列表
        quotes: 重仓股实时行情列表

    Returns:
        {"estimated_change_pct", "contributors", "draggers"}
    """
    if not holdings or not quotes:
        return {
            "estimated_change_pct": 0.0,
            "contributors": [],
            "draggers": [],
            "note": "数据不足",
        }

    quote_map = {q.get("code", ""): q for q in quotes}
    estimated_pct = 0.0
    contributors: list[dict] = []
    draggers: list[dict] = []

    for h in holdings:
        code = h.get("code", "")
        weight = h.get("weight", 0)
        name = h.get("name", "")
        q = quote_map.get(code)
        if not q:
            continue
        change_pct = float(q.get("change_pct", 0) or 0)
        # 重仓股权重×涨跌幅 = 对基金净值的贡献
        contribution = round(weight * change_pct / 100, 4)
        estimated_pct += contribution

        item = {
            "code": code,
            "name": name,
            "weight": weight,
            "change_pct": change_pct,
            "contribution": contribution,
        }
        if change_pct > 0:
            contributors.append(item)
        elif change_pct < 0:
            draggers.append(item)

    # 贡献排序
    contributors.sort(key=lambda x: x["contribution"], reverse=True)
    draggers.sort(key=lambda x: x["contribution"])

    return {
        "estimated_change_pct": round(estimated_pct, 4),
        "contributors": contributors[:5],
        "draggers": draggers[:5],
        "note": f"基于前{len(holdings)}大重仓股预估,实际净值以基金公司公布为准",
    }


# ===== 板块轮动信号 =====

def _build_sector_rotation_signal(sector_exposure: list[dict], sector_ranking: list[dict]) -> list[dict]:
    """根据重仓股所在板块的涨跌排名,生成板块轮动信号

    Args:
        sector_exposure: 重仓股板块暴露度
        sector_ranking: 当日板块涨跌排名

    Returns:
        [{"sector", "weight", "change_pct", "rank", "signal"}, ...]
    """
    if not sector_exposure or not sector_ranking:
        return []

    # 板块按涨跌幅排序,找到排名
    sorted_sectors = sorted(sector_ranking, key=lambda x: float(x.get("change_pct", 0)), reverse=True)
    total_sectors = len(sorted_sectors)
    sector_rank_map = {s.get("name", ""): idx + 1 for idx, s in enumerate(sorted_sectors)}

    result: list[dict] = []
    for se in sector_exposure:
        sector_name = se.get("sector", "")
        weight = se.get("total_weight", 0)
        change_pct = 0.0
        rank = 0
        # 模糊匹配板块名
        for s_name, r in sector_rank_map.items():
            if sector_name in s_name or s_name in sector_name:
                rank = r
                # 找对应的change_pct
                for s in sorted_sectors:
                    if s.get("name") == s_name:
                        change_pct = float(s.get("change_pct", 0))
                        break
                break

        # 信号判断
        if rank == 0:
            signal = "无数据"
        elif rank <= total_sectors // 5:  # 前20%
            signal = "强势" if change_pct > 0 else "抗跌"
        elif rank >= total_sectors * 4 // 5:  # 后20%
            signal = "弱势" if change_pct < 0 else "滞涨"
        else:
            signal = "中性"

        result.append({
            "sector": sector_name,
            "weight": weight,
            "change_pct": round(change_pct, 2),
            "rank": rank,
            "total_sectors": total_sectors,
            "signal": signal,
        })
    return result


# ===== 主入口 =====

async def analyze_fund_holdings(fund_code: str) -> dict[str, Any]:
    """基金重仓股板块分析(主入口)

    Args:
        fund_code: 6位基金代码

    Returns:
        {
            "fund_code": "110022",
            "holdings": [...],            # 前十大重仓股
            "sector_exposure": [...],     # 板块暴露度
            "concentration": {...},       # 集中度指标
            "nav_impact": {...},          # 净值影响预估
            "sector_rotation": [...],     # 板块轮动信号
        }
    """
    # 1. 抓取重仓股
    holdings = await asyncio.to_thread(get_fund_holdings, fund_code)
    if not holdings:
        # P0 诊断(2026-07-29 v1.3):区分"基金无数据"与"数据源不可达"
        # P2 修复(2026-07-30):改为按 fund_code 读取失败原因,避免并发互相覆盖
        fetch_reason = _get_fetch_reason(fund_code)
        reason = fetch_reason or "基金无重仓股数据披露(可能为新基金或债基)"
        # 数据源可达性判断:有失败原因 → 数据源问题;无原因 → 基金本身无数据
        source_unreachable = bool(fetch_reason)
        return {
            "fund_code": fund_code,
            "holdings": [],
            "sector_exposure": [],
            "concentration": {"top5_weight": 0, "top10_weight": 0, "hhi": 0, "level": "无数据"},
            "nav_impact": {"estimated_change_pct": 0, "contributors": [], "draggers": [], "note": "无重仓股数据"},
            "sector_rotation": [],
            "note": reason,
            "diagnostic": {
                "source_unreachable": source_unreachable,
                "reason": reason,
                "hint": (
                    "数据源在当前部署环境不可达,建议:1)本地运行验证 2)迁移至国内可访问eastmoney的部署环境 "
                    "3)配置TUSHARE_TOKEN启用第三数据源"
                    if source_unreachable else
                    "该基金可能确实无重仓股披露(债基/新基金/QDII等)"
                ),
            },
        }

    # 2. 板块映射
    sector_exposure = _map_holdings_to_sectors(holdings)

    # 3. 集中度
    concentration = _calc_concentration(holdings)

    # 4. 净值影响预估(并行抓取重仓股行情+板块排名)
    stock_codes = [h["code"] for h in holdings if h.get("code")]
    funcs = [
        ("quotes", lambda: ds2.get_realtime_quote_tencent(stock_codes) if stock_codes else []),
        ("sectors", ds2.get_sector_ranking),
    ]
    fetched = ds2._parallel_fetch(funcs)
    quotes = fetched.get("quotes") or []
    sector_ranking = fetched.get("sectors") or []

    nav_impact = _estimate_nav_impact(holdings, quotes)
    sector_rotation = _build_sector_rotation_signal(sector_exposure, sector_ranking)

    return {
        "fund_code": fund_code,
        "holdings": holdings,
        "sector_exposure": sector_exposure,
        "concentration": concentration,
        "nav_impact": nav_impact,
        "sector_rotation": sector_rotation,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def get_sector_rotation() -> dict[str, Any]:
    """板块轮动总览(全局)

    Returns:
        {"top_gainers", "top_losers", "hot_sectors", "rotation_signal"}
    """
    sector_ranking = await asyncio.to_thread(ds2.get_sector_ranking)
    if not sector_ranking:
        return {
            "top_gainers": [],
            "top_losers": [],
            "hot_sectors": [],
            "rotation_signal": "无数据",
        }

    sorted_by_change = sorted(sector_ranking, key=lambda x: float(x.get("change_pct", 0)), reverse=True)
    top_gainers = sorted_by_change[:5]
    top_losers = sorted_by_change[-5:][::-1]  # 反转,让最差的在前

    # 热门板块(成交额Top5)
    sorted_by_amount = sorted(sector_ranking, key=lambda x: float(x.get("amount", 0) or 0), reverse=True)
    hot_sectors = sorted_by_amount[:5]

    # 轮动信号
    gain_count = sum(1 for s in sector_ranking if float(s.get("change_pct", 0)) > 0)
    fall_count = sum(1 for s in sector_ranking if float(s.get("change_pct", 0)) < 0)
    if gain_count > fall_count * 2:
        rotation = "普涨"
    elif fall_count > gain_count * 2:
        rotation = "普跌"
    else:
        rotation = "分化"

    return {
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "hot_sectors": hot_sectors,
        "rotation_signal": rotation,
        "total_sectors": len(sector_ranking),
        "gain_count": gain_count,
        "fall_count": fall_count,
    }

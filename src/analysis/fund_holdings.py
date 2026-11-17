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


# C-02(2026-11-17):"最近一个**已披露**的报告期"的唯一口径。
# 旧实现两条链路各算一套: em 源固定取"今年"(`year=<今年> month=""`), 1~4 月时
# 当年的 Q1 还没披露、上游直接返回空 → `[]`, 而失败文案还写成"基金可能无持仓披露"
# (把口径问题说成基金问题); akshare 兜底却把 1~3 月映射到 `{year-1}4`。两源不一致。
# 这里按披露节奏给出保守映射(与 akshare 兜底原有映射一致), 两源共用。
_QUARTER_LABEL = {1: "1季度", 2: "2季度", 3: "3季度", 4: "4季度"}


def _latest_disclosed_quarter(now: datetime | None = None) -> tuple[int, int]:
    """返回 (年, 季) —— 当前时点**最近一个已披露**的定期报告期。

    披露节奏(法定截止日): Q1 4/30、Q2 8/31、Q3 10/31、Q4(年报) 次年 4/30。
    用保守的月份分档(与 akshare 兜底一致), 保证两条链路取同一个报告期。
    """
    today = now or datetime.now()
    month = today.month
    if month <= 3:
        return today.year - 1, 4
    if month <= 6:
        return today.year, 1
    if month <= 9:
        return today.year, 2
    return today.year, 3


def _quarter_text(year: int, quarter: int) -> str:
    return f"{year}年{_QUARTER_LABEL[quarter]}"


# C-02:按**表头文字**定位列, 不再用"第一个以 % 结尾的 cell 就是占净值比例"。
# 那个启发式只因为真实页面「涨跌幅」列为空才成立;一旦上游把涨跌幅渲染成 `1.77%`,
# weight_idx 会命中涨跌幅 → weight/hold_amount/hold_value 三字段整体错位,
# 而且错位后的数值仍然"看起来合理", 极难发现。
_HEADER_KEYS: dict[str, tuple[str, ...]] = {
    "code": ("股票代码", "代码"),
    "name": ("股票名称", "名称"),
    "weight": ("占净值比例", "占净值", "净值比例"),
    "hold_amount": ("持股数", "持股数量", "持股"),
    "hold_value": ("持仓市值", "市值"),
}


def _resolve_columns(header_cells: list[str]) -> dict[str, int]:
    """按表头文字解析列下标;任一必需列缺失则返回 {} (调用方退回旧版定位)。"""
    resolved: dict[str, int] = {}
    for field, keys in _HEADER_KEYS.items():
        for index, raw in enumerate(header_cells):
            text = raw.replace(" ", "").replace("\u3000", "")
            if any(key in text for key in keys):
                resolved[field] = index
                break
    if len(resolved) < len(_HEADER_KEYS):
        return {}
    return resolved


def _fetch_fund_holdings_em(fund_code: str, topline: int = 10) -> list[dict]:
    """东方财富基金重仓股抓取

    Args:
        fund_code: 6位基金代码
        topline: 返回前N大重仓股

    Returns:
        [{"code", "name", "weight", "hold_amount", "hold_value", "quarter", "change"}, ...]
    """
    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
    # C-02: 先按"最近已披露报告期的年份"取;该年为空再退一年。
    # 旧实现无条件取"今年", 1~4 月（当年 Q1 尚未披露）必然拿到空 → []。
    target_year, _target_quarter = _latest_disclosed_quarter()
    try:
        text = ""
        for year in (target_year, target_year - 1):
            resp = _EM_FUND_SESSION.get(url, params={
                "type": "jjcc",
                "code": fund_code,
                "topline": topline,
                "year": str(year),
                "month": "",
            }, timeout=8)
            text = resp.text
            if "apidata" in text and "content" in text and re.search(r'boxitem|<tr', text):
                break
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
            _set_fetch_reason(
                fund_code,
                "em接口返回但无content字段(已按最近披露报告期 "
                f"{_quarter_text(target_year, _target_quarter)} 及上一年各请求一次)",
            )
            return []
        html = content_m.group(1)
        # 反转义
        html = html.replace("\\/", "/").replace('\\"', '"').replace("\\\\", "\\")
        # 2026-11-15 实测:东财表格新增「最新价/涨跌幅/相关资讯」列, 且不再逐行带
        # 「季报/持仓变化」列(季度改到每张表的 h4 标题里), 固定下标解析全部失败。
        # 改为按 quarter 分块 + 按「百分号列」定位比例/持股/市值。
        result: list[dict] = []
        blocks = re.findall(
            r"<div class='boxitem[^']*'>(.*?)(?=<div class='boxitem|$)",
            html, re.DOTALL,
        )
        if not blocks:
            blocks = [html]
        for block in blocks:
            qm = re.search(r"(\d{4}年\d季度)", block)
            quarter = qm.group(1) if qm else ""
            # C-02: 有表头就按列名定位(权威); 老版/无表头响应退回旧启发式, 保持兼容
            header = [
                re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<th[^>]*>(.*?)</th>", block, re.DOTALL)
            ]
            columns = _resolve_columns(header)
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', block, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) < 5:
                    continue
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                try:
                    if columns:
                        code_idx, name_idx = columns["code"], columns["name"]
                        weight_idx = columns["weight"]
                        amount_idx, value_idx = columns["hold_amount"], columns["hold_value"]
                    else:
                        code_idx, name_idx = 1, 2
                        # 旧版列序(序号|代码|名称|比例|持股|市值);无表头时才用 % 猜
                        weight_idx = next(
                            (i for i, c in enumerate(cells) if c.endswith("%")), -1
                        )
                        if weight_idx < 0:
                            weight_idx = 3
                        amount_idx, value_idx = weight_idx + 1, weight_idx + 2
                    if max(code_idx, name_idx, weight_idx, amount_idx, value_idx) >= len(cells):
                        continue
                    code, name = cells[code_idx], cells[name_idx]
                    if not quarter:
                        for c in cells:
                            if re.search(r"\d{4}年\d季度|\d{4}Q\d", c):
                                quarter = c
                                break
                    weight = float(cells[weight_idx].replace("%", "") or 0)
                    hold_amount = float(cells[amount_idx].replace(",", "").replace("--", "0") or 0)
                    hold_value = float(cells[value_idx].replace(",", "").replace("--", "0") or 0)
                    # D-05(2026-11-17):东财改版后「持仓变化」不再是逐行文本列, 而是
                    # 「相关资讯」列里的一个 `变动详情` 链接;旧实现匹配不到
                    # 新进/增持/减持 就**无条件落到 "不变"** —— 把"无法判定"写成了
                    # "确认没变化"(实测 110022 / 161725 各 10 只全部返回 不变)。
                    # 现在三态:
                    #   匹配到 新进/增持/减持/不变 → 用该值(上游真的披露了);
                    #   出现 `变动详情` 但解析不出关键词 → "未披露"(有这个入口但没给文字);
                    #   两者都没有 → None(无法判定, 前端渲染「—」)。
                    change: str | None = None
                    for c in cells:
                        if "新进" in c:
                            change = "新进"
                            break
                        if "增持" in c:
                            change = "增持"
                            break
                        if "减持" in c:
                            change = "减持"
                            break
                        if "不变" in c:
                            change = "不变"
                            break
                    if change is None and any("变动详情" in c for c in cells):
                        change = "未披露"
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
        if result:
            # C-02: 保留最新一期时按 (年, 季) **数值比较**取最大, 不再依赖
            # "接口分块顺序由新到旧"这个未写进契约的隐式约定。
            def _quarter_key(text: str) -> tuple[int, int]:
                matched = re.match(r"(\d{4})年(\d)季度", text or "")
                return (int(matched.group(1)), int(matched.group(2))) if matched else (0, 0)

            newest = max(_quarter_key(row["quarter"]) for row in result)
            if newest != (0, 0):
                result = [row for row in result if _quarter_key(row["quarter"]) == newest]
        return result[:topline]
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
        # akshare 季度参数: 20241, 20242, 20243, 20244
        # C-02: 与 em 源共用同一个"最近已披露报告期"口径, 不再各算一套。
        quarter_year, quarter_no = _latest_disclosed_quarter()
        quarter = f"{quarter_year}{quarter_no}"
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
                # D-05:akshare 的 fund_portfolio_hold_em 表里**没有**持仓变化列,
                # 旧实现固定写 "不变" 属于凭空断言 → 无法判定一律 None。
                "change": None,
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

    # C-02: 有数据才按季报节奏长缓存(12h);**空结果只放 10 分钟** ——
    # 旧实现空结果也缓存 12h, 用户一上午都看到空重仓股(季报本身不会一天变两次)。
    ds2._cache_set(cache_key, holdings, ttl=12 * 3600 if holdings else 600)
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

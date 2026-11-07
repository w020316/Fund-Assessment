#!/usr/bin/env python3
"""A股每日复盘报告生成脚本。

每个交易日 15:35(北京时间)由 GitHub Actions
(.github/workflows/daily-report.yml)自动调用, 生成自包含单文件
HTML 复盘报告, 输出到 daily_reports/ 目录。

设计要点:
- 只复用 src/core/data_source_v2.py 的既有取数函数, 不重复实现 HTTP 抓取;
- 交易日守卫: 以 018957(A股基金, T+0 净值)的 update_time 日期与北京时间
  当天比对, 不一致视为非交易日, 打印提示后 exit 0, 不写任何文件;
- 任一数据源失败只影响对应区块(显示"数据源未覆盖"), 绝不编造数据,
  各区块独立 try/except, 不因单源失败整体崩溃;
- 报告仅含公开市场数据(指数/板块/涨跌家数/公开基金净值), 严禁包含
  任何个人持仓/份额/成本/盈亏信息;
- 红涨绿跌(A股惯例), 浅底深字, 无外部 CDN 依赖, 涨跌条用纯 CSS div 实现。

用法:
    python scripts/gen_daily_report.py                 # 默认输出 daily_reports/A股复盘_YYYYMMDD.html
    python scripts/gen_daily_report.py --out 路径.html  # 指定输出路径
"""
from __future__ import annotations

import os
import sys

# ---- sys.path 引导(照抄 launch.py 模式): 保证从任何 cwd 可运行 ----
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYLIBS = os.path.join(_ROOT, "pylibs")
for _p in (_PYLIBS, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse  # noqa: E402
import html  # noqa: E402
import re  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from loguru import logger  # noqa: E402

from src.core import data_source_v2 as ds2  # noqa: E402

# 报告覆盖的公开基金标的(基金代码是公开标识, 允许出现在报告中)
FUND_CODES: list[str] = [
    "002891", "008984", "006503", "011611", "018957",
    "013470", "017811", "018816", "026211",
]

# 交易日守卫探针: 018957 为 A股基金, 当日 15:00 后净值即时可见(T+0),
# 其 update_time 日期 ≠ 北京时间今天 即判定为非交易日。
# 注: 需求原文写 "FUND_CODES[1] (018957)", 但 FUND_CODES[1] 实为 008984,
# 此处以括号内明确的代码 018957 为准。
TRADE_GUARD_FUND = "018957"

_DEFAULT_OUT_DIR = os.path.join(_ROOT, "daily_reports")

# 财经快讯区块参数: 最多展示条数 / 摘要截断长度
_NEWS_MAX_ITEMS = 8
_NEWS_SUMMARY_LEN = 80

# 北京时间 UTC+8(Render/CI 为 UTC, 一律显式换算, 不依赖系统 TZ)
_CST = timezone(timedelta(hours=8))

_DATE_DASHED_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_COMPACT_RE = re.compile(r"\d{8}")


def beijing_now() -> datetime:
    """返回当前北京时间(UTC+8)。"""
    return datetime.now(_CST)


def _extract_date_part(update_time: str) -> str:
    """从 update_time 字符串中提取 YYYY-MM-DD 日期部分。

    兼容两种上游格式:
    - 腾讯: "2026-10-28 15:00:00"
    - fundgz: "2026-11-06 15:00"
    提取不到时返回空串(调用方按"非交易日"处理)。
    """
    if not update_time:
        return ""
    m = _DATE_DASHED_RE.search(update_time)
    if m:
        return m.group(0)
    m = _DATE_COMPACT_RE.search(update_time)
    if m:
        raw = m.group(0)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def check_trading_day(now: datetime | None = None) -> bool:
    """交易日守卫: 用 018957 的净值 update_time 日期判断今天是否交易日。

    - update_time 日期 == 北京时间今天 → 交易日(True);
    - 其余情况(日期不一致 / 探针取数为空 / 网络异常)一律判为非交易日,
      fail-closed: 宁可漏发一份报告, 也不发一份"净值还是昨天的"假日报。
    """
    target = (now or beijing_now()).strftime("%Y-%m-%d")
    try:
        rows = ds2.get_fund_realtime_tencent([TRADE_GUARD_FUND])
    except Exception as exc:  # noqa: BLE001 — 守卫函数绝不允许抛出
        logger.warning(f"交易日守卫探针取数失败: {exc}")
        return False
    if not rows:
        logger.warning("交易日守卫: 探针基金无返回数据, 按非交易日处理")
        return False
    date_part = _extract_date_part(str(rows[0].get("update_time", "")))
    if date_part != target:
        logger.info(f"交易日守卫: 净值日期 {date_part or '未知'} != 今天 {target}")
        return False
    return True


def collect_indices() -> list[dict] | None:
    """主要指数实时行情; 失败返回 None(渲染为占位)。"""
    try:
        rows = ds2.get_index_realtime()
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"指数数据获取失败: {exc}")
    return None


def collect_sentiment() -> dict | None:
    """市场广度(涨/跌/平家数); 失败或全零返回 None。"""
    try:
        result = ds2.get_market_sentiment()
        if not result:
            return None
        total = (
            int(result.get("rise_count", 0))
            + int(result.get("fall_count", 0))
            + int(result.get("flat_count", 0))
        )
        if total > 0:
            return result
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"市场广度数据获取失败: {exc}")
    return None


def collect_sectors() -> dict | None:
    """板块涨跌 TOP10(涨/跌两列); 失败返回 None。"""
    try:
        rows = ds2.get_sector_ranking()
        if rows:
            ranked = sorted(
                rows, key=lambda r: float(r.get("change_pct", 0.0) or 0.0),
                reverse=True,
            )
            top_up = ranked[:10]
            top_down = list(reversed(ranked[-10:]))
            # 极端行情下可能不足 10 个板块, 去掉与涨幅榜重复的尾巴
            top_down = [r for r in top_down if r not in top_up][:10]
            return {"up": top_up, "down": top_down}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"板块数据获取失败: {exc}")
    return None


def collect_news() -> list[dict] | None:
    """财经快讯(复用 ds2.get_global_news 的东财→新浪双上游); 失败返回 None。

    - 目标取前 _NEWS_MAX_ITEMS(8)条;
    - title 为空的条目跳过; 单条数据结构异常(None/非 dict)直接跳过, 不致命;
    - 清洗后为空或上游抛异常 → 返回 None(渲染为"数据源未覆盖"占位)。
    """
    try:
        rows = ds2.get_global_news()
        cleaned: list[dict] = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            if not str(item.get("title", "") or "").strip():
                continue
            cleaned.append(item)
            if len(cleaned) >= _NEWS_MAX_ITEMS:
                break
        if cleaned:
            return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"财经快讯数据获取失败: {exc}")
    return None


def collect_funds() -> list[dict] | None:
    """持仓基金公开净值(双上游); 失败返回 None。"""
    try:
        rows = ds2.get_fund_realtime_tencent(FUND_CODES)
        if rows:
            # 按 FUND_CODES 声明顺序稳定输出
            order = {c: i for i, c in enumerate(FUND_CODES)}
            return sorted(rows, key=lambda r: order.get(str(r.get("code")), len(order)))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"基金净值数据获取失败: {exc}")
    return None


def build_summary(indices: list[dict] | None, sentiment: dict | None) -> str:
    """一句话定性: 根据指数涨跌与涨跌家数占比做中性规则拼接, 不含买卖建议。"""
    parts: list[str] = []
    if indices:
        changes = [float(i.get("change_pct", 0.0) or 0.0) for i in indices]
        avg = sum(changes) / len(changes)
        if avg > 0.05:
            word = "集体上涨"
        elif avg < -0.05:
            word = "集体下跌"
        else:
            word = "窄幅震荡"
        detail = "、".join(
            f"{i.get('name', '')}{_fmt_pct(float(i.get('change_pct', 0.0) or 0.0))}"
            for i in indices
        )
        parts.append(f"主要指数{word}（{detail}）")
    if sentiment:
        rise = int(sentiment.get("rise_count", 0))
        fall = int(sentiment.get("fall_count", 0))
        flat = int(sentiment.get("flat_count", 0))
        total = rise + fall + flat
        if total > 0:
            ratio = rise / total * 100
            label = str(sentiment.get("sentiment", "中性"))
            parts.append(
                f"全市场{total}只个股中上涨{rise}家、下跌{fall}家、平盘{flat}家，"
                f"上涨占比{ratio:.1f}%，市场情绪“{label}”"
            )
    if not parts:
        return "今日数据源未覆盖，暂无市场综述。"
    return "截至收盘，" + "；".join(parts) + "。"


# ===== HTML 渲染 =====

def _pct_cls(value: float) -> str:
    """涨跌幅对应的 CSS 类: 红涨绿跌(A股惯例), 0 为平。"""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _fmt_price(value: float) -> str:
    return f"{value:.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


_CSS = """
:root { --up:#d92b2b; --down:#0a8f4c; --flat:#8a8f99; --line:#e4e7ec; --muted:#6b7280; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  background:#f6f7f9; color:#1f2329; max-width:980px; margin:0 auto; padding:24px 16px 40px; }
h1 { font-size:22px; margin-bottom:4px; }
h2 { font-size:17px; margin:28px 0 12px; border-left:4px solid #2f6fed; padding-left:8px; }
.meta { color:var(--muted); font-size:13px; margin-bottom:20px; }
.card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px 20px; }
.summary { font-size:15px; line-height:1.8; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }
.idx { background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.idx .name { font-size:13px; color:var(--muted); }
.idx .price { font-size:20px; font-weight:600; margin:4px 0 2px; }
.idx .pct { font-size:14px; font-weight:600; }
.up { color:var(--up); }
.down { color:var(--down); }
.flat { color:var(--flat); }
.breadth-bar { display:flex; height:18px; border-radius:9px; overflow:hidden; margin:10px 0 8px; }
.seg-up { background:var(--up); }
.seg-flat { background:#d9dce1; }
.seg-down { background:var(--down); }
.breadth-legend { display:flex; gap:18px; font-size:13px; color:var(--muted); flex-wrap:wrap; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; }
table { width:100%; border-collapse:collapse; background:#fff; font-size:14px; }
th, td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:right; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--muted); font-weight:500; font-size:13px; background:#fafbfc; }
.tables { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:720px) { .tables { grid-template-columns:1fr; } }
.tables td:last-child { font-weight:600; }
.news-sum { font-size:12px; color:var(--muted); margin-top:2px; line-height:1.5; }
td a { color:#2f6fed; text-decoration:none; }
td a:hover { text-decoration:underline; }
.placeholder { color:var(--muted); font-size:14px; padding:18px; text-align:center;
  background:#fff; border:1px dashed var(--line); border-radius:10px; }
.note { font-size:13px; color:var(--muted); line-height:1.9; }
footer { margin-top:32px; padding-top:14px; border-top:1px solid var(--line); }
"""


def _placeholder(text: str = "数据源未覆盖") -> str:
    return f'<div class="placeholder">{html.escape(text)}</div>'


def _render_indices(indices: list[dict] | None) -> str:
    if not indices:
        return _placeholder()
    cards = []
    for i in indices:
        price = float(i.get("price", 0.0) or 0.0)
        change = float(i.get("change", 0.0) or 0.0)
        pct = float(i.get("change_pct", 0.0) or 0.0)
        cls = _pct_cls(pct)
        sign = "+" if change > 0 else ("-" if change < 0 else "")
        cards.append(
            f'<div class="idx"><div class="name">{html.escape(str(i.get("name", "")))}</div>'
            f'<div class="price {cls}">{_fmt_price(price)}</div>'
            f'<div class="pct {cls}">{_fmt_pct(pct)}（{sign}{_fmt_price(abs(change))}）</div></div>'
        )
    return f'<div class="grid">{"".join(cards)}</div>'


def _render_breadth(sentiment: dict | None) -> str:
    if not sentiment:
        return _placeholder()
    rise = int(sentiment.get("rise_count", 0))
    fall = int(sentiment.get("fall_count", 0))
    flat = int(sentiment.get("flat_count", 0))
    total = rise + fall + flat
    if total <= 0:
        return _placeholder()
    w_up = rise / total * 100
    w_flat = flat / total * 100
    w_down = fall / total * 100
    bar = (
        '<div class="breadth-bar">'
        f'<div class="seg-up" style="width:{w_up:.2f}%"></div>'
        f'<div class="seg-flat" style="width:{w_flat:.2f}%"></div>'
        f'<div class="seg-down" style="width:{w_down:.2f}%"></div>'
        "</div>"
    )
    legend = (
        '<div class="breadth-legend">'
        f'<span><span class="dot seg-up"></span>上涨 {rise}</span>'
        f'<span><span class="dot seg-flat"></span>平盘 {flat}</span>'
        f'<span><span class="dot seg-down"></span>下跌 {fall}</span>'
        f'<span>合计 {total} 只 · 上涨占比 {w_up:.1f}%</span>'
        "</div>"
    )
    return f'<div class="card">{bar}{legend}</div>'


def _sector_rows(rows: list[dict]) -> str:
    trs = []
    for r in rows:
        pct = float(r.get("change_pct", 0.0) or 0.0)
        cls = _pct_cls(pct)
        trs.append(
            f'<tr><td>{html.escape(str(r.get("name", "")))}</td>'
            f'<td>{html.escape(str(r.get("code", "")))}</td>'
            f'<td class="{cls}">{_fmt_pct(pct)}</td></tr>'
        )
    return "".join(trs)


def _render_sectors(sectors: dict | None) -> str:
    if not sectors or not (sectors.get("up") or sectors.get("down")):
        return _placeholder()
    head = "<tr><th>板块名称</th><th>板块代码</th><th>涨跌幅</th></tr>"
    up_html = f"<table><thead>{head}</thead><tbody>{_sector_rows(sectors.get('up') or [])}</tbody></table>"
    down_html = f"<table><thead>{head}</thead><tbody>{_sector_rows(sectors.get('down') or [])}</tbody></table>"
    return (
        '<div class="tables">'
        f'<div><h3>涨幅 TOP10</h3>{up_html}</div>'
        f'<div><h3>跌幅 TOP10</h3>{down_html}</div>'
        "</div>"
    )


def _render_news(news: list[dict] | None) -> str:
    """财经快讯表: 时间 | 来源 | 标题(url 非空则可点击, 标题下灰色小字摘要)。"""
    if not news:
        return _placeholder()
    trs = []
    for n in news:
        # 单条结构异常(None/非 dict)直接跳过, 不让整块渲染失败
        if not isinstance(n, dict):
            continue
        title = str(n.get("title", "") or "").strip()
        if not title:
            continue
        url = str(n.get("url", "") or "").strip()
        source = html.escape(str(n.get("source", "") or ""))
        # 时间缺失或为空串 → 显示占位符 "—"(该条目本身仍展示)
        pub = html.escape(str(n.get("publish_time", "") or "").strip()) or "—"
        esc_title = html.escape(title)
        if url:
            title_html = (
                f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
                f"{esc_title}</a>"
            )
        else:
            title_html = esc_title
        # 摘要不强制展示: content 为空则不渲染, 非空截断到 _NEWS_SUMMARY_LEN
        summary = str(n.get("content", "") or "").strip()
        summary_html = (
            f'<div class="news-sum">{html.escape(summary[:_NEWS_SUMMARY_LEN])}</div>'
            if summary
            else ""
        )
        trs.append(
            f"<tr><td>{pub}</td><td>{source}</td><td>{title_html}{summary_html}</td></tr>"
        )
    if not trs:
        return _placeholder()
    head = "<tr><th>时间</th><th>来源</th><th>标题</th></tr>"
    return f'<table><thead>{head}</thead><tbody>{"".join(trs)}</tbody></table>'


def _render_funds(funds: list[dict] | None) -> str:
    if not funds:
        return _placeholder()
    trs = []
    for f in funds:
        nav = float(f.get("nav", 0.0) or 0.0)
        pct = float(f.get("change_pct", 0.0) or 0.0)
        cls = _pct_cls(pct)
        nav_date = _extract_date_part(str(f.get("update_time", ""))) or "—"
        trs.append(
            f'<tr><td>{html.escape(str(f.get("code", "")))}</td>'
            f'<td>{html.escape(str(f.get("name", "")))}</td>'
            f'<td>{_fmt_price(nav)}</td>'
            f'<td class="{cls}">{_fmt_pct(pct)}</td>'
            f"<td>{html.escape(nav_date)}</td></tr>"
        )
    head = "<tr><th>基金代码</th><th>基金名称</th><th>最新净值</th><th>涨跌幅</th><th>净值日期</th></tr>"
    return f'<table><thead>{head}</thead><tbody>{"".join(trs)}</tbody></table>'


def render_html(data: dict, now: datetime | None = None) -> str:
    """把 collect_data 的结果渲染成自包含单文件 HTML。"""
    now = now or data.get("now") or beijing_now()
    date_str = now.strftime("%Y-%m-%d")
    title = f"A股复盘 · {date_str}"
    summary = str(data.get("summary", ""))
    generated_at = now.strftime("%Y-%m-%d %H:%M")
    body = (
        "<h1>A股每日复盘</h1>"
        f'<div class="meta">报告日期：{date_str} · 生成时间（北京时间）：{generated_at}</div>'
        "<h2>一句话定性</h2>"
        f'<div class="card summary">{html.escape(summary)}</div>'
        "<h2>主要指数</h2>" + _render_indices(data.get("indices"))
        + "<h2>市场广度</h2>" + _render_breadth(data.get("sentiment"))
        + "<h2>板块涨跌 TOP10</h2>" + _render_sectors(data.get("sectors"))
        + "<h2>财经快讯</h2>" + _render_news(data.get("news"))
        + "<h2>基金净值</h2>" + _render_funds(data.get("funds"))
        + "<footer><h2>数据说明与风险提示</h2>"
        '<div class="note">'
        "· 数据来源：腾讯行情、东方财富、天天基金（fundgz）等公开渠道，由 Fund-Assessment "
        "数据层自动抓取生成；<br>"
        "· 各数据源独立容错，显示「数据源未覆盖」表示该来源当日暂不可用，不代表市场无数据；"
        "所有数字均为上游返回的真实值，未经任何人工修改；<br>"
        "· 净值与估值为公开基金行情数据，不包含任何个人账户信息；<br>"
        "· 本报告由程序自动生成，以上基于公开数据，不构成投资建议。"
        "</div></footer>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def collect_data(now: datetime) -> dict:
    """并行/容错地收集全部区块数据; 任一区块失败只记 None, 不抛出。"""
    indices = collect_indices()
    sentiment = collect_sentiment()
    sectors = collect_sectors()
    news = collect_news()
    funds = collect_funds()
    return {
        "now": now,
        "indices": indices,
        "sentiment": sentiment,
        "sectors": sectors,
        "news": news,
        "funds": funds,
        "summary": build_summary(indices, sentiment),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回进程退出码(0 表示成功或非交易日跳过)。"""
    parser = argparse.ArgumentParser(description="生成 A股每日复盘 HTML 报告")
    parser.add_argument(
        "--out",
        default=None,
        help="输出 HTML 路径(默认 daily_reports/A股复盘_YYYYMMDD.html, YYYYMMDD 为北京时间当天)",
    )
    args = parser.parse_args(argv)

    now = beijing_now()
    if not check_trading_day(now):
        print("非交易日，跳过报告生成")
        return 0

    data = collect_data(now)
    html_text = render_html(data, now)

    if args.out:
        out_path = args.out
    else:
        out_path = os.path.join(_DEFAULT_OUT_DIR, f"A股复盘_{now.strftime('%Y%m%d')}.html")
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)

    print(f"报告已生成: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

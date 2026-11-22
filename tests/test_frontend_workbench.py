"""STAGE7 工作台回归(2026-11-14)。

无 JS 测试运行器，沿用 test_frontend_xss.py 的静态契约思路：
- 工作台是行情默认落地页（页面 / 二级 tab / IA 映射 / 样式脚本全部接线）；
- 新的 innerHTML 装配点必须对上游文本调用 esc()；
- 昨日封板率必须由炸板率换算（100 − x），不得把炸板率当封板率展示；
- 缺失字段显示「—」并给原因，判读文案标注非投资建议。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web/static/index.html"
WB_JS = ROOT / "web/static/js/workbench.js"
WB_CSS = ROOT / "web/static/css/workbench.css"


def _index() -> str:
    return INDEX.read_text(encoding="utf-8")


def _js() -> str:
    return WB_JS.read_text(encoding="utf-8")


def test_workbench_assets_exist():
    assert WB_JS.is_file() and WB_JS.stat().st_size > 1000
    assert WB_CSS.is_file() and WB_CSS.stat().st_size > 1000


def test_workbench_is_default_market_landing():
    text = _index()
    assert '<div class="page active" id="page-workbench">' in text
    assert '<span class="hud-subtab active" data-page="workbench"' in text
    assert "workbench: { parent: 'hud-market' }," in text
    assert "'hud-market': 'workbench'," in text
    assert '<link rel="stylesheet" href="/static/css/workbench.css">' in text
    assert re.search(r'<script src="/static/js/workbench\.js(?:\?v=[^"]+)?"></script>', text)
    # 旧总览页不再默认 active，避免两页同时显示
    assert '<div class="page active" id="page-market">' not in text


def test_workbench_escapes_upstream_text():
    js = _js()
    for expr in (
        "esc(it.name || '—')",
        "esc(s.sector || '—')",
        "esc(s.name || '—')",
        "esc(p.fund_name || '—')",
        "esc(hint)",
        "esc(verdict)",
        "esc(note)",
    ):
        assert expr in js, f"工作台缺少转义: {expr}"
    for bare in (
        "+ (it.name || '—')",
        "+ (s.sector || '—')",
        "+ (s.name || '—')",
        "+ (p.fund_name || '—')",
    ):
        assert bare not in js, f"工作台仍存在裸插值: {bare}"


def test_yesterday_seal_rate_is_derived_from_broken_rate():
    """昨日 API 只给 broken_rate_pct；封板率必须换算，否则会把 36.2% 炸板率当封板率。"""
    js = _js()
    assert "num(100 - yBroken, 1)" in js
    assert "esc(yBroken == null ? '—' : num(yBroken, 1) + '%')" not in js


def test_missing_fields_are_explicit_not_zero():
    js = _js()
    assert "— / — / —" in js
    assert "上游未返回计数（指数代理）" in js
    assert "「缺失」维度不计入结论" in js
    assert "非投资建议" in js


def test_refresh_respects_trading_session():
    """休市只刷新交易时段徽标，不再持续打上游；自动刷新偏好需持久化。"""
    js = _js()
    text = _index()
    assert "/api/market/status" in js
    assert "st.is_open" in js
    assert "qf_workbench_auto_v1" in js
    assert "休市暂停刷新" in js
    assert "is-closed" in WB_CSS.read_text(encoding="utf-8")
    assert 'id="wbSessionTag"' in text
    assert 'id="wbRefreshHint"' in text


def test_hot_rows_are_keyboard_buttons_with_delegation():
    """热门股行用真实 button + 事件委托，不把代码拼进内联 onclick。"""
    js = _js()
    assert '<button type="button" class="wb-hot-row" data-code="' in js
    assert "closest('.wb-hot-row')" in js
    assert 'onclick="Workbench.openStock' not in js
    assert "loadStockDetail(String(code))" in js


def test_deep_link_and_source_health():
    """刷新/分享保持当前子页；每个数据源的成败逐项可见。"""
    js = _js()
    text = _index()
    assert "history.replaceState(null, '', hash)" in text
    assert "hashchange" in text
    assert 'id="wbSourceStrip"' in text
    assert 'id="wbUpstreamTime"' in text
    assert "renderHealth([" in js
    assert "需Token" in js
    assert "clockText(" in js


def test_ddx_streak_panel_is_wired_and_non_blocking():
    """资金连增：独立异步补拉（不阻塞首屏），行可点击，口径与非投资建议必须写明。"""
    js = _js()
    text = _index()
    assert 'id="wbDdxBody"' in text
    assert 'id="wbDdxMeta"' in text
    assert "wb-span-7\" aria-label=\"资金连增\"" in text
    assert "/api/market/ddx_streak" in js
    assert "loadDdx(suffix)" in js
    assert "renderDdx(r)" in js
    assert "bindRowClicks('wbDdxBody')" in js
    assert "updateSourceHealth('资金'" in js
    assert "非通达信 DDX 大单净量" in js
    assert "非买入信号" in js
    # 限流/冷却不能被说成"筛选无结果"
    assert "data.cooldown" in js
    assert "上游限流冷却中" in js
    assert "取数失败" in js
    # 上游名称必须转义
    assert "esc(it.name || '—')" in js
    assert "+ (it.name || '—')" not in js


def test_discipline_panel_rules_are_explicit():
    """开仓纪律：5 项阈值写死、缺失项不计入、结论三档、标注非投资建议。"""
    js = _js()
    text = _index()
    assert 'id="wbDisciplineBody"' in text
    assert "renderDiscipline(board, thermo)" in js
    assert "disciplineChecks(" in js
    for rule in ("温度 ≥ 50", "炸板率 ≤ 30%", "最高连板 ≥ 3", "指数均值 ≥ 0%", "封板质量较昨日改善"):
        assert rule in js, f"缺少纪律检查项: {rule}"
    assert "缺失项只标注、不计入判定" in js
    assert "可正常参与" in js and "降级参与" in js and "空仓等待" in js
    assert "非投资建议" in js


def test_flow_streak_and_discipline_surface_on_other_pages():
    """Batch 5：资金连增与开仓纪律在行情/详情/复盘/出手三问页复用同一口径。"""
    text = _index()
    # 行情总览：个股资金连增 Top5
    assert 'id="capitalDdxBody"' in text
    assert "/api/market/ddx_streak?limit=5" in text
    assert "loadCapitalDdx()" in text
    # 个股详情：单票连增端点，区分历史与当日快照
    assert 'id="stockFlowStreak"' in text
    assert "/api/market/stock_flow_streak?code=" in text
    assert "history_available" in text
    assert "新浪当日快照" in text
    assert "不能据此计算连增" in text
    # 复盘页 / 出手三问页：共用工作台同一套规则，不复制阈值
    assert 'id="reviewDisciplineBody"' in text
    assert 'id="gateDisciplineBody"' in text
    assert "Workbench.disciplineChecks" in text
    assert "Workbench.disciplineVerdict" in text
    assert "loadReviewDiscipline()" in text
    assert "loadGateDiscipline()" in text
    assert "loadReviewDiscipline();" in text
    assert "loadGateDiscipline();" in text
    # 跨页复用样式不锁死在 #page-workbench 作用域
    css = WB_CSS.read_text(encoding="utf-8")
    assert ".qf-disc {" in css and ".qf-flow-row {" in css


def test_panel_tokens_are_global_not_page_scoped():
    """通用化 token：面板色/线条/强调色上收 tokens.css，workbench 只做别名。"""
    tokens = (ROOT / "web/static/css/tokens.css").read_text(encoding="utf-8")
    for tok in (
        "--qf-panel-bg", "--qf-panel-bg-2", "--qf-panel-line",
        "--qf-panel-line-soft", "--qf-panel-accent", "--qf-panel-faint",
        "--qf-panel-tint", "--qf-glow-panel",
    ):
        assert tok in tokens, f"tokens.css 缺少通用 token: {tok}"
    css = WB_CSS.read_text(encoding="utf-8")
    for alias in (
        "--wb-surface: var(--qf-panel-bg);",
        "--wb-surface-2: var(--qf-panel-bg-2);",
        "--wb-line: var(--qf-panel-line);",
        "--wb-faint: var(--qf-panel-faint);",
    ):
        assert alias in css, f"workbench 未复用通用 token: {alias}"
    cross = css[css.index("跨页共享组件"):]
    assert "rgba(148, 163, 184" not in cross, "跨页组件仍写死线条色"
    assert "#0B111B" not in cross, "跨页组件仍写死面板色"


def test_market_subpages_share_page_head_and_kpi():
    """行情子页统一页头 / KPI / 说明条，不再每页各写一套内联样式。"""
    text = _index()
    for page in ("page-market", "page-review", "page-gate", "page-dispatch"):
        assert f'id="{page}"' in text
    assert text.count('class="qf-page-head"') >= 4
    for cls in ('class="qf-page-title"', 'class="qf-page-sub"', 'class="qf-page-actions"'):
        assert cls in text
    assert 'class="qf-kpi-grid mb-16"' in text
    assert 'class="qf-kpi-value"' in text
    assert 'class="qf-callout mb-16"' in text


def test_primary_pages_share_page_head_and_kpi():
    """一级页同样复用共享页头与 KPI 瓦片，避免每页各写一份内联标题。"""
    text = _index()
    expected = {
        "page-fund": "我的基金",
        "page-position": "持仓管理",
        "page-strategy": "策略中心",
        "page-ai": "智能分析",
        "page-monitor": "监控预警",
        "page-config": "系统配置",
        "page-global": "国际市场",
        "page-data": "深度数据",
        "page-sector-board": "板块行情",
    }
    for page, title in expected.items():
        start = text.index(f'id="{page}"')
        seg = text[start:start + 1200]
        assert 'class="qf-page-head"' in seg, f"{page} 缺少共享页头"
        assert f'class="qf-page-title">{ title }</div>' in seg, f"{page} 标题不符合 {title}"
        assert 'class="qf-page-sub"' in seg, f"{page} 缺少页头说明"
    # 基金/持仓的四个账户指标走同一套瓦片（大号金额档）
    assert text.count('class="qf-kpi-value is-lg"') >= 8
    # B2-8(2026-11-17) 收口: 原来这里是 `assert "fund-head" not in text` ——
    # 全局子串检查会把**任何**含该词的新类名/文案判红(与本次改动无关), 而且
    # 它并不能证明"基金页用了共享页头"。改为只看该页自身的片段:
    fund_start = text.index('id="page-fund"')
    fund_seg = text[fund_start:fund_start + 3000]
    assert "fund-head" not in fund_seg, "基金页仍在用旧的自建标题类"
    stage6 = (ROOT / "web/static/css/stage6.css").read_text(encoding="utf-8")
    # 同理: 只针对"作用在基金页上"的旧类规则, 不做全文件子串匹配
    assert "#page-fund .fund-head" not in stage6


# ===== C-01(2026-11-17):工作台假死的机制契约 =====
# 背景:fetchJSON 原先是裸 fetch,没有 AbortController / 超时;load() 用 Promise.all
# 串起 7 个请求,任一上游永不 settle → await 不返回 → state.loading 卡在 true,
# 刷新按钮永久置灰、60s 定时器每次静默 return(用户感知为"工作台假死")。
# 这里不全文子串匹配,而是**先定位具体函数体**再做结构断言。


def _function_body(js: str, signature: str) -> str:
    """按花括号配平截取某个函数体(含签名),避免"全文命中"式的伪断言。"""
    start = js.index(signature)
    brace = js.index("{", start)
    depth = 0
    for i in range(brace, len(js)):
        ch = js[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    raise AssertionError(f"{signature} 花括号未配平")


def test_fetch_json_has_abort_and_timeout():
    """fetchJSON 必须自带 deadline:AbortController + setTimeout + clearTimeout。"""
    body = _function_body(_js(), "async function fetchJSON(url) {")
    assert "AbortController" in body, "fetchJSON 缺少 AbortController, 上游挂起时无法中断"
    assert "setTimeout(" in body, "fetchJSON 缺少超时计时器"
    assert "abort()" in body, "计时器到点必须调用 abort()"
    assert "clearTimeout(" in body, "请求结束后必须 clearTimeout, 否则计时器泄漏"


def test_fetch_json_timeout_budgets_are_graded():
    """超时预算分级:复盘面板 30s,其余 15s;两档都必须在 URL 分发函数里。"""
    js = _js()
    assert "var FETCH_TIMEOUT_MS = 15000;" in js
    assert "var FETCH_TIMEOUT_BOARD_MS = 30000;" in js
    body = _function_body(js, "function timeoutFor(url) {")
    assert "/api/review/board" in body
    assert "FETCH_TIMEOUT_BOARD_MS" in body
    assert "FETCH_TIMEOUT_MS" in body


def test_load_uses_allsettled_not_promise_all():
    """load() 不能再让单个面板拖死全局:必须 allSettled, 且不得残留 Promise.all。"""
    body = _function_body(_js(), "async function load(force, statusIn) {")
    assert "Promise.allSettled(" in body, "load() 仍用 Promise.all, 单点挂起会阻塞全部面板"
    assert "Promise.all(" not in body, "load() 里不得再出现裸 Promise.all"


def test_load_resets_loading_and_button_after_failure():
    """反向断言:失败/超时路径必须回到可用态 —— finally 里复位 loading 与按钮。"""
    body = _function_body(_js(), "async function load(force, statusIn) {")
    finally_at = body.index("} finally {")
    tail = body[finally_at:]
    assert "state.loading = false" in tail, "finally 未复位 state.loading → 刷新按钮永久置灰"
    assert "btn.disabled = false" in tail, "finally 未恢复刷新按钮可点击"
    assert "btn.textContent = '刷新'" in tail


def test_load_reports_partial_failure_instead_of_silence():
    """单面板失败要在页脚明说失败的是哪几块, 不能让"少一块"被读成"今天没数据"。"""
    body = _function_body(_js(), "async function load(force, statusIn) {")
    assert "failedNames" in body
    assert "部分面板取数失败" in body


# ===== C-03(2026-11-17):降级时不得渲染「¥0.00」 =====

def test_render_portfolio_handles_null_pnl():
    """`total_est_day_pnl` / `est_day_pnl` 为 null 时必须渲染「—」并标降级样式。

    旧实现 `fmtMoney(Number(p.est_day_pnl))` 会把 null 变成 `Number(null)=0` → ¥0.00,
    与同项 `est_pct: null`(显示「—」)自相矛盾, 且 0 会被当成真实盈亏读走。
    """
    body = _function_body(_js(), "function renderPortfolio(r) {")

    # 必须有显式的 null 分支(而不是靠 fmtMoney 的隐式转换)
    assert "totalKnown" in body
    assert "total !== null" in body
    assert "p.est_day_pnl !== null" in body
    # 降级要整块可见, 不只小字
    assert "is-degraded" in body
    assert "估算不可用" in body
    # 反向断言: 不得再把 null 兜成 0, 取值必须在"已知/未知"三元里
    assert "total_est_day_pnl || 0" not in body
    assert "pnlKnown ? fmtMoney(Number(p.est_day_pnl)) : '—'" in body
    assert "totalKnown ? fmtMoney(Number(total)) : '—'" in body


# ===== C-04(2026-11-17):移动端触摸目标 ≥48px =====

def _media_block(css: str, query: str = "@media (max-width: 640px)", occurrence: int = 0) -> str:
    """按花括号配平截取某个 @media 块的内容(只断言块内规则, 不做全文匹配)。

    ``occurrence`` 取第几个匹配(0=首个, -1=最后一个): hud.css 里同一个断点出现多次。
    """
    if occurrence == 0:
        start = css.index(query)
    else:
        positions = []
        idx = css.find(query)
        while idx != -1:
            positions.append(idx)
            idx = css.find(query, idx + 1)
        start = positions[occurrence]
    brace = css.index("{", start)
    depth = 0
    for i in range(brace, len(css)):
        ch = css[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[brace:i + 1]
    raise AssertionError(f"{query} 花括号未配平")


def test_mobile_touch_targets_are_at_least_48px():
    """工作台是默认落地页, 移动端 .wb-btn / .wb-hot-row / .wb-link 必须 ≥48px。

    改前: .wb-btn 只有 40px(低于门槛), .wb-hot-row(≈29px)与 .wb-link(≈19px)
    完全没有移动端放大规则。
    """
    block = _media_block(WB_CSS.read_text(encoding="utf-8"))

    assert "min-height: 48px" in block
    for selector, extra in (
        ("#page-workbench .wb-btn", "min-height: 48px"),
        ("#page-workbench .wb-hot-row", "min-height: 48px"),
        ("#page-workbench .wb-link", "min-height: 48px"),
    ):
        assert selector in block, f"媒体查询块里缺少 {selector}"
        seg = block[block.index(selector):]
        seg = seg[:seg.index("}")] if "}" in seg else seg
        assert extra in seg, f"{selector} 未设 {extra}"
    # 反向断言: 不得残留旧的低门槛值
    assert "min-height: 40px" not in block


# ===== B2-8(2026-11-17):测试有效性 —— 模板字符串插值审计 =====
# 原来的 14 个用例全是 `assert "字面量" in text`, 锁的是"某段字符串还在文件里":
#   ① 抓不到经 helper 产生的 +0.00% 这类问题;
#   ② 反例检查只覆盖拼接式 `+ (it.name || '—')`, 对模板字符串 `${it.name}` **完全免疫**;
#   ③ 全局子串断言(如旧的 fund-head)与改动无关却会误报。
# 下面三条补上"结构/行为"层面的护栏。

_TEMPLATE_EXPR = re.compile(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")

#: 视为"已安全"的函数:要么转义,要么纯数值/格式化(结果只能是数字或受控字面量)。
_SAFE_WRAPPERS = ("esc(", "escapeHtml(", "_numOrDash(", "_pctOrDash(", "_ratioOrDash(")
#: 纯数值表达式的白名单函数名(返回值只会是数字/文字, 不携带上游文本)
_NUMERIC_CALLS = (
    "fmtNum(", "fmtPct(", "fmtMoney(", "fmtPctNum(", "pctText(", "num(",
    "changeColor(", "changeSign(", "clsOf(", "corrColor(", "nbColor(", "nbText(",
    "Math.", "Number(", "parseFloat(", "parseInt(", "toFixed(", "toLocaleString(",
    "signedPct(", "signalLabel(", "decisionLabel(", "statusMap[", "dotMap[", "textMap[",
)
#: 纯数值/常量的变量名(在所属函数里由数字运算得出, 不是上游文本)
_NUMERIC_VARS = frozenset({
    "dims", "totalDims", "sc", "c", "i", "j", "k", "n", "v", "w", "x", "y",
    "width", "height", "left", "top", "pad", "size", "count", "idx", "total",
    "value", "score", "pct", "ratio", "weight", "fillPct", "pointerLeft",
})


def _template_exprs(text: str) -> list[str]:
    return [m.group(1).strip() for m in _TEMPLATE_EXPR.finditer(text)]


#: 本轮改动块内**逐条审查过**的安全表达式 → 每条都写明为什么安全。
#: 规则: 只能加"结果不含上游文本"的表达式; 任何可能带入用户/上游字符串的都必须
#: 在代码里 esc()/escapeHtml() 包起来, 而不是加进这里。
_SCOPED_SAFE_EXPRS = {
    # 同一函数内由 `x >= 60 ? 'var(--a)' : 'var(--b)'` 这类**字面量三元**赋值的颜色串
    "levelColor": "CSS 颜色, 由数值比较在字面量之间选择, 不含上游文本",
    # compRows 的 label 是代码里写死的字面量数组('指数趋势'/'板块轮动'…)
    "item.label": "标签来自代码内字面量数组 compRows, 非上游数据",
    # 该三项是在本函数内、字段已 escapeHtml/esc 之后拼好的 HTML 片段
    "compRows": "逐行 HTML 片段, 字段值为字面量 label 与数值 sc",
    "corrTable": "逐格 escapeHtml(codes[i]) + 数值; 见 fundsRow 上方实现",
    "fundsRow": "逐列 escapeHtml(f.fund_code)/escapeHtml(f.fund_name) + 数值",
    # 纯布尔/数值分支: 两支都是字面量或数字拼接
    'dimOk ? \'\' : \'<span style="color:var(--text-muted);">(无数据)</span>\'':
        "两支都是字面量, 无变量插值",
    'f.degraded ? \'<span style="color:var(--accent-blue);">数据不足</span>\' : f.nav_points + \' 点\'':
        "字面量 / 数字(f.nav_points 为 int)拼 「点」, 不含文本字段",
    "d.funds.length": "数组长度, 数字",
}


def _is_safe_expr(expr: str) -> bool:
    """判断一个 ${...} 表达式是否"不可能是未转义的上游文本"。"""
    if expr in _SCOPED_SAFE_EXPRS:
        return True
    if any(w in expr for w in _SAFE_WRAPPERS):
        return True
    if any(call in expr for call in _NUMERIC_CALLS):
        return True
    # 纯三元里两支都是字符串字面量(不掺变量)
    if re.fullmatch(r"[\w$.\[\]'\"?:=!<>&|\s]+", expr) and "'" in expr and "." not in expr:
        return True
    # 裸的数值型局部变量
    if expr in _NUMERIC_VARS:
        return True
    return False


def test_workbench_js_template_interpolations_are_escaped():
    """workbench.js 里每个模板插值都必须转义或是纯数值。

    该文件目前用字符串拼接而非模板字面量(所以本用例是"新增护栏"), 但一旦有人
    改成 `${it.name}` 这类写法, 这里会立刻拦住 —— 旧的字符串断言对这种写法免疫。
    """
    unsafe = [e for e in _template_exprs(_js()) if not _is_safe_expr(e)]

    assert unsafe == [], f"workbench.js 存在未转义的模板插值: {unsafe}"


#: 本轮(B1/B2)新增或改动的 index.html 片段所在函数。
#: 只审计这些作用域: 全文件另有 539 处**既有**未转义插值(历史技术债),
#: 一次性冻结成基线没有意义(那等于把 XSS 技术债标成"安全"), 已记入遗留。
_R6_INDEX_SCOPES = ("async function loadMarketThermometer()", "async function loadPortfolioDiversification()")


def _js_function_body(text: str, signature: str) -> str:
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"{signature} 花括号未配平")


@pytest.mark.parametrize("signature", _R6_INDEX_SCOPES)
def test_this_round_index_html_blocks_escape_interpolations(signature):
    """本轮新增的 index.html 渲染块:每个插值都必须转义或纯数值。"""
    body = _js_function_body(_index(), signature)
    unsafe = [e for e in _template_exprs(body) if not _is_safe_expr(e)]

    assert unsafe == [], f"{signature} 存在未转义的模板插值: {unsafe}"


# ===== 语法护栏: 前端 JS 至少要是合法语法 =====

def test_frontend_js_files_pass_node_syntax_check():
    """`node --check`: 抓括号/模板字面量不配平这类"改坏了"的问题。

    本机与 CI runner 都带 node;没有则 skip(汇总里会报是否真的跑了)。
    """
    node = shutil.which("node")
    if node is None:  # pragma: no cover - 取决于环境
        pytest.skip("环境无 node, 跳过语法校验")

    targets = [
        "web/static/js/workbench.js",
        "web/static/js/stage6-portfolio.js",
        "web/static/js/ma-optimizer.js",
        "web/static/js/recommendations.js",
    ]
    for rel in targets:
        path = ROOT / rel
        assert path.is_file(), f"缺少前端文件 {rel}"
        proc = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, timeout=60, check=False,
        )
        assert proc.returncode == 0, f"{rel} 语法错误: {proc.stderr}"


# ===== M-04(2026-11-17):缺失值不得在前端被 Number() 归一成 0 =====

def test_hot_rows_do_not_coerce_missing_pct_to_zero():
    """`Number(null) === 0` → `clsOf(0)` 空类 + `pctText(0)` 返回 `+0.00%`,
    把"没拿到涨跌幅"画成"今天不涨不跌"。函数体内不得再出现 `Number(s.change_pct)`。"""
    for signature in ("function hotGainRow(s) {", "function hotVolRow(s) {"):
        body = _function_body(_js(), signature)
        assert "Number(s.change_pct)" not in body, f"{signature} 仍把 null 归一成 0"
        assert "var v = s.change_pct;" in body
        assert "pctText(v)" in body
        assert "clsOf(v)" in body

    # 成交额同理(缺失时 fmtYuanAmount 自己会渲染「—」)
    vol_body = _function_body(_js(), "function hotVolRow(s) {")
    assert "fmtYuanAmount(Number(s.amount))" not in vol_body
    assert "fmtYuanAmount(s.amount)" in vol_body


def test_sector_rows_keep_null_and_width_zero():
    body = _function_body(_js(), "function sectorRows(list, maxAbs) {")
    assert "Number(s.change_pct)" not in body
    assert "var v = s.change_pct;" in body
    assert "isNum(v) ?" in body, "柱宽必须对缺失值单独处理, 不能按 0 算"


def test_sector_sorting_puts_missing_last():
    """缺失值按 0 参与排序会被排进"领跌"; 必须显式分离并固定排末尾。"""
    body = _function_body(_js(), "function renderSectors(items, meta) {")
    assert "isNum(s.change_pct)" in body
    assert "hasPct" in body and "noPct" in body
    assert ".concat(noPct)" in body, "缺失项必须固定排末尾"
    assert "Number(s.change_pct || 0)" not in body


# ===== C-06(2026-11-17):相关性不得借用"红=涨"的语义色 =====

def _corr_block(html: str) -> str:
    """截取 corrColor 定义 + 紧随的图例段落(到相关性表格开始为止)。

    刻意不用全文子串: 同一个文件里别的图表确实该用涨跌色, 只有这一块不该。
    """
    start = html.index("const corrColor =")
    # 到本函数的 catch 块为止: 覆盖 corrColor + 相关矩阵 + 图例 + 口径脚注
    end = html.index("} catch (e)", start)
    return html[start:end]


def test_corr_color_uses_risk_scale_not_up_down_semantics():
    """`--color-up` 的站内语义是"涨"。用它表示"相关性高=风险高"会让用户按
    红涨绿跌的直觉把高相关读成利好; `#FBBF24` 还是写死 hex(违背 token 规范)。"""
    block = _corr_block(_index())

    assert "--color-up" not in block, "相关性仍在使用「涨」的语义色"
    assert "--color-down" not in block
    assert "--qf-sem-critical" in block
    assert "--qf-sem-warn" in block
    assert "#FBBF24" not in block, "仍存在写死 hex(应为 var(--qf-sem-warn))"


def test_corr_card_explains_colors_are_not_gain_loss():
    """换色不够, 还要**主动澄清** —— 同一屏里权重是青色、相关是暖色, 容易被误读。"""
    block = _corr_block(_index())

    assert "不表示涨跌" in block
    assert "分散度风险" in block


def test_qf_sem_tokens_exist_in_tokens_css():
    """断言的 token 必须真的存在(否则改成了未定义变量 = 无色)。"""
    tokens = (ROOT / "web/static/css/tokens.css").read_text(encoding="utf-8")

    assert "--qf-sem-warn" in tokens
    assert "--qf-sem-critical" in tokens


# ===== D(2026-11-17):移动端触摸目标 48px 收口(4 页) =====
# 真机矩阵(4 页 × 6 断点 × 2 主题)实测出的真实缺口只有两类: 三个辅助页的
# input/select/button/nav a 是 44~46px, 首页 .hud-subtab 是 44px(且宽度 44)。
# 这些是纯 CSS 门槛, 用结构断言钉住, 防止将来改样式时又退回 44。

_AUX_PAGES = ("web/static/stock-compare.html", "web/static/ma-optimizer.html",
              "web/static/factor-research.html")


@pytest.mark.parametrize("rel", _AUX_PAGES)
def test_aux_pages_mobile_touch_targets_are_48(rel):
    html = (ROOT / rel).read_text(encoding="utf-8")
    block = _media_block(html, "@media (max-width: 640px)")

    assert "min-height: 48px" in block
    assert "min-height: 44px" not in block, f"{rel} 仍有 44px 门槛"
    # select 必须在选择器里(#direction / #trainRatio 是 select, 曾被漏掉)
    assert "input, button, select { min-height: 48px; }" in block, f"{rel} 未覆盖 select"
    assert "nav a" in block and "min-height: 48px" in block


def test_hud_subtab_and_btn_sm_are_48_on_mobile():
    """首页: `.hud-subtab` 被 aurora 的固定 height:44px 压住(固定值会盖掉 min-height),
    且 aurora 在 hud.css 之后加载 —— 所以 hud.css 必须用更高特异性 + height:auto
    覆盖;浅色回退档(body 去掉 hud-theme)还要有一份不带前缀的版本。"""
    css = (ROOT / "web/static/css/hud.css").read_text(encoding="utf-8")
    block = _media_block(css, "@media (max-width: 768px)", occurrence=-1)

    flat = " ".join(block.split())
    # 深色档(压过 body.hud-theme .hud-subtab) 与浅色档(无前缀)都要有
    assert "body.hud-theme .hud-subtabs > .hud-subtab { height: auto; min-height: 48px; min-width: 48px; }" in flat
    assert ".hud-subtabs > .hud-subtab { height: auto; min-height: 48px; min-width: 48px; }" in flat
    # .btn-sm 在 aurora 里是 height:40/min-height:44 → 两档都提到 48
    assert "body.hud-theme .btn.btn-sm { height: auto; min-height: 48px; }" in flat
    assert ".btn.btn-sm { height: auto; min-height: 48px; }" in flat
    # height:auto 必须显式写(否则固定 height 会压住 min-height)
    assert "height: auto" in flat


# ===== A-08 漏网(2026-11-17):score_available=false 时不得把占位 50 当结论 =====
# 后端在四维全缺时返回 `score: 50` + `score_available: false`。旧实现只看
# `isNum(thermo.score)` → 标签渲染成「大盘温度 50.0 · 数据不足」并着 is-warn
# (50 落在 40~60), 用户读到的是"温度中性", 而实际是四维全缺 —— 比纯空态更误导。

def _thermo_usable_source() -> str:
    return _function_body(_js(), "function thermoUsable(thermo) {")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行该判据")
def test_thermo_usable_gate_is_behaviourally_correct():
    """真跑一遍判据(不是子串匹配): 只看 score_available 与 available_dims。"""
    script = (
        _thermo_usable_source()
        + """
const cases = [
  null,
  {score: 50, score_available: false, data_quality: {available_dims: 0}},
  {score: 50, score_available: true, data_quality: {available_dims: 0}},
  {score: 73.8, score_available: true, data_quality: {available_dims: 4}},
  {score: 60, data_quality: {available_dims: 3}},
];
console.log(JSON.stringify(cases.map(thermoUsable)));
"""
    )
    proc = subprocess.run([shutil.which("node"), "-e", script],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == [False, False, False, True, True]


def test_temp_tag_does_not_show_placeholder_score():
    """标签分支必须先过 thermoUsable; false 分支不输出数字、不着结论色。"""
    body = _function_body(_js(), "function renderHead(board, thermo) {")

    assert "thermoUsable(thermo) && isNum(thermo.score)" in body, "标签未接 score_available"
    # false 分支那段(从说明文案的组装到 live 点之前)不得出现三种结论色
    seg = body[body.index("var why"): body.index("var live =")]
    for cls in ("is-up", "is-down", "is-warn"):
        assert cls not in seg, f"不可用分支仍给了结论色 {cls}"
    assert "数据不足" in seg, "不可用时必须写明数据不足"
    assert "tempTag.className = 'wb-tag';" in seg, "不可用时应保持中性单类"


def test_radar_marks_unavailable_components_as_missing():
    """维度对象存在但 available=false 时也算缺失, 否则占位 50 会被画成正常的点。"""
    body = _function_body(_js(), "function radarDims(board, thermo) {")

    assert body.count("available === false") >= 4, "四个维度都要判 available"


def test_radar_center_and_points_ignore_unavailable():
    body = _function_body(_js(), "function renderRadar(board, thermo) {")

    assert "thermoUsable(thermo) && isNum(thermo.score)" in body, "雷达中心分未接 score_available"
    assert "if (d.missing || !isNum(d.value)) return;" in body, "缺失维仍被画成 50 的点"


def test_discipline_score_does_not_use_placeholder():
    """占位 50 会让「温度 ≥ 50」判成通过 —— 缺失数据伪造出一个通过的纪律项。"""
    body = _function_body(_js(), "function disciplineChecks(board, thermo) {")

    assert "thermoUsable(thermo) && isNum(thermo.score)" in body


def test_health_dot_not_green_when_thermo_unavailable():
    body = _function_body(_js(), "async function load(force, statusIn) {")

    assert "thermo.score_available !== false" in body, "四维全缺时健康点仍是绿的"


# ===== XSS(2026-11-17): 上游文本 → innerHTML 的机制级审计 =====
#
# 为什么不用 `assert "escapeHtml(msg)" in text` 这种写法：
# 那种断言锁的是"某段字面量还在文件里"，抓不到新增的漏网（本仓库第五轮已因此漏过）。
# 这里改为**按上下文重扫描**：先定位所有 innerHTML / insertAdjacentHTML 的语句范围
# （命中行 -2 ~ +22 行），取出该范围内所有 `${...}`，再断言「最末属性名像上游文本」
# 的插值**全部**被转义包装。新增一处裸插值这条就会红。
#
# 两道显式豁免（都写明依据，不做"全放过"）：
#   1) signalLabel() / decisionLabel() —— 定义处即为 `return escapeHtml(...)`（见 index.html:3195），
#      调用点再包一层会双重转义；
#   2) 纯字面量三元 —— 如 `${type === 'number' ? 'number' : 'text'}`，两支都是代码内字面量。
_INDEX_RISKY_FIELD = re.compile(
    r"(?:^|\.)(name|reason|signal|message|msg|text|title|label|note|desc|remark"
    r"|buyer|seller|industry|concept|fund_name|stock_name)$", re.I)
_INDEX_SAFE_WRAPPERS = (
    "esc(", "escapeHtml(", "_numOrDash(", "_pctOrDash(", "_ratioOrDash(", "_esc(",
    "signalLabel(", "decisionLabel(",  # 定义处已 return escapeHtml(...)
)
_INDEX_TEMPLATE_EXPR = re.compile(r"\$\{([^{}]*)\}")


def _is_literal_ternary(expr: str) -> bool:
    """`a === 'x' ? 'y' : 'z'` —— 两支都是引号字面量、且不取任何对象属性。"""
    return "'" in expr and "?" in expr and not re.search(r"[A-Za-z_$][\w$]*\s*\.", expr)


def _inner_html_spans(lines: list[str]) -> set[int]:
    """定位 innerHTML / insertAdjacentHTML 语句覆盖的行号集合。"""
    span: set[int] = set()
    for i, line in enumerate(lines):
        if re.search(r"innerHTML\s*=|insertAdjacentHTML\(", line):
            span.update(range(max(0, i - 2), min(len(lines), i + 22)))
    return span


def test_index_upstream_text_into_innerhtml_is_escaped():
    """index.html + js/*.js：进入 innerHTML 的上游文本字段必须被转义包装。"""
    files = [INDEX] + sorted((ROOT / "web/static/js").glob("*.js"))
    offenders: list[str] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        span = _inner_html_spans(lines)
        for i in sorted(span):
            for m in _INDEX_TEMPLATE_EXPR.finditer(lines[i]):
                expr = m.group(1).strip()
                if any(w in expr for w in _INDEX_SAFE_WRAPPERS):
                    continue
                if _is_literal_ternary(expr):
                    continue
                idents = re.findall(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", expr)
                if not idents:
                    continue
                if _INDEX_RISKY_FIELD.match(idents[-1].split(".")[-1]):
                    offenders.append(f"{path.name}:L{i + 1} ${{{expr[:60]}}}")
    assert not offenders, (
        "以下位置把上游文本直接插进 innerHTML（未转义，可被上游注入脚本）：\n  "
        + "\n  ".join(offenders)
    )


def test_index_escape_helper_covers_html_metachars():
    """escapeHtml 必须覆盖 & < > " ' 五个字符（少一个就留一条注入缝）。"""
    html = INDEX.read_text(encoding="utf-8")
    start = html.index("function escapeHtml(")
    body = html[start:start + 600]
    for pat, name in ((r"&/g", "&"), (r"</g", "<"), (r">/g", ">"),
                      ('"/g', '"'), (r"'/g", "'")):
        assert pat in body, f"escapeHtml 未处理 {name}"


# ===== C-02 收尾(2026-11-17):stock_realtime / index_realtime / sector_flow /
# heatmap 四端点数值改 nullable 后, 前端消费点不得把 null 归一成 0 =====

def test_render_indices_keeps_null_change_pct():
    """workbench 指数卡: change_pct 保持原值交给 isNum 系 helper,
    `Number(null)===0` 会把"没拿到涨跌幅"画成 0.00%(M-04 同款缺陷)。"""
    js = _js()
    assert "var pct = Number(it.change_pct);" not in js, "renderIndices 仍在 Number() 归一 change_pct"
    assert "var pct = it.change_pct;" in js


def test_index_bar_and_cards_do_not_render_dash_percent():
    """指数栏/卡片: 涨跌幅缺失(null)不得拼出「—%」残渣, 价格缺失不得进数字滚动
    (data-countup="null" → Number("null")=NaN 靠侥幸落「—」, 必须显式守卫)。"""
    html = _index()
    # 只锁定 loadIndexData 函数体(消费 /api/market/index_realtime);
    # loadGlobalIndexBar 消费 /api/global/indices, 属另一条链, 不在本轮范围。
    start = html.index("async function loadIndexData()")
    end = html.index("async function loadGlobalIndexBar()")
    body = html[start:end]
    assert '${sign}${fmtNum(idx.change_pct)}%' not in body, "指数栏/卡片仍无条件拼接「%」"
    assert 'class="qf-count" data-countup="${idx.price}"' not in body, "价格缺失时仍无条件进数字滚动"
    # 新守卫必须存在
    assert "pctKnown" in body
    assert "priceKnown" in body


def test_index_sparkline_guards_null_change_pct():
    """`null >= 0` 在 JS 里为 true(null 被强转为 0), 缺失涨跌幅不得画成"上涨"走势。"""
    html = _index()
    assert "drawSparkline(svgId, idx.change_pct >= 0" not in html, "sparkline 仍在裸比较 null >= 0"
    assert "drawSparkline(svgId, idx.change_pct != null && idx.change_pct >= 0" in html


def test_watchlist_does_not_coerce_missing_quote_to_zero():
    """自选股行情缺失不得 `|| 0`(会画成"价格 0.00 / 涨幅 0.00%"), 必须保持 null 渲染「—」。"""
    html = _index()
    assert "price: q.price || 0," not in html
    assert "change_pct: q.change_pct || 0," not in html
    assert "price: q.price ?? null," in html
    assert "change_pct: q.change_pct ?? null," in html


def test_sector_board_sort_puts_missing_last_and_fmtYi_guards_null():
    """板块二级页: 缺失涨跌幅不得 `Number(x || 0)` 归一后参与排序(会被排进中段/
    领跌区, 口径与 workbench renderSectors 的 M-04 一致); fmtYi 对 null/空串
    不得经 Number(null)===0 画成 "0.00 亿"(2026-11-22 三视图重写后统一走 isNum)。"""
    js = (ROOT / "web/static/js/sector-board.js").read_text(encoding="utf-8")
    assert "Number(b.change_pct || 0)" not in js, "排序仍把缺失涨跌幅当 0"
    assert ".concat(noPct);" in js
    assert "if (!isNum(v)) return '—';" in js
    assert "function isNum(v) { return v !== null && v !== undefined && v !== ''" in js


def test_data_health_tab_wires_existing_quality_endpoint():
    """深度数据「数据体检」: 复用既有 /api/market/data-quality/{code},
    必须展示四个维度与缺失/问题明细, 且缺失维度不按 0 分表述。"""
    html = _index()
    assert "switchDataTab('health')" in html
    assert 'id="dataHealthPanel"' in html
    assert "/api/market/data-quality/" in html
    assert "function loadDataHealth" in html
    for label in ("行情", "K线", "资金流", "财务"):
        assert label in html
    assert "不按 0 分计算" in html


def test_sector_board_drilldown_wires_constituent_endpoint():
    """板块下钻: 行/气泡需可点击并调用新增成分股端点, 拿不到时明确不可用。"""
    js = (ROOT / "web/static/js/sector-board.js").read_text(encoding="utf-8")
    html = _index()
    assert "/api/market/sector_stocks?" in js
    assert "data-sb-sector=" in js
    assert "data-sb-stock=" in js
    assert 'id="sbDrawer"' in html
    assert "该板块暂未返回成分股" in js
    assert "缺失数据不补零" in js


def test_data_caliber_card_names_stop_and_fallback_paths():
    """数据口径卡: 必须写明北向停披、板块回退、净值时效与「—」语义,
    防止后续改版把诚实性说明删掉。"""
    html = _index()
    assert 'id="dataCaliberCard"' in html
    assert "2024-08" in html
    assert "成交额" in html and "冒充净流入" in html
    assert "新浪行业资金榜" in html and "前 50" in html
    assert "QDII/FOF" in html
    assert "未自动复权" in html
    assert "unavailable_fields" in html


def test_aurora_theme_stock_hex_are_tokenized():
    """STAGE5 遗留#2 分片一: aurora-theme.css 存量字面色 → token(同值替换)。
    锁定已 token 化的 8 个 qf 色不再回退成裸 hex(注释里的引用不算)。"""
    css = (ROOT / "web/static/css/aurora-theme.css").read_text(encoding="utf-8")
    # 去掉块注释后再断言, 注释中的色值引用(对比度记录等)合法保留
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for h in ("#1C2A3D", "#2A3D55", "#0C131F", "#22E3FF", "#3D7BFF",
              "#04070E", "#070C15", "#121C2B"):
        assert h not in stripped, f"aurora-theme.css 仍有裸 hex {h}(应引用对应 --qf-* token)"
    for tok in ("var(--qf-line-1)", "var(--qf-line-2)", "var(--qf-bg-surface)",
                "var(--qf-neon-cyan)", "var(--qf-ink)"):
        assert tok in css, f"aurora-theme.css 缺少 {tok}"


def test_stage6_bare_warning_amber_is_tokenized():
    """stage6.css 三处裸 #f59e0b 改为 var(--color-warning, #f59e0b)
    (沿用本文件「token + 字面量回退」既有约定, 回退值保持同色)。"""
    css = (ROOT / "web/static/css/stage6.css").read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for bad in ("background: #f59e0b", "color: #f59e0b", "dashed #f59e0b", "solid #f59e0b"):
        assert bad not in stripped, f"stage6.css 仍有裸警告色: {bad}"
    assert css.count("var(--color-warning, #f59e0b)") >= 5


def test_layout_spacing_on_4px_grid_slice():
    """STAGE5 遗留#1 分片一: 布局级间距(卡片/区块/面板/列表行的 padding/margin)
    10/14/18/6 → 12/16/16/8。锁定本分片已改的声明不回退;
    内联控件(.btn/.tab/.badge/.kbd/input)与 2px 细调按文档口径有意保留。"""
    checks = {
        "web/static/css/prediction.css": [
            "padding: 12px 16px;", "margin: 0 0 12px;", "padding: 16px 0;",
        ],
        "web/static/css/stage6.css": [
            "padding: 16px 16px;", "margin-bottom: 16px;", "padding: 12px 12px;",
        ],
        "web/static/css/workbench.css": [
            "padding: 12px 12px 12px;", "padding: 16px 8px;", "padding: 16px 8px; min-height: 48px;",
        ],
    }
    for rel, needles in checks.items():
        css = (ROOT / rel).read_text(encoding="utf-8")
        for n in needles:
            assert n in css, f"{rel} 缺少 {n}(布局级间距应已上 4px 网格)"
    # 旧值不得回归
    regressions = {
        "web/static/css/prediction.css": ["padding: 12px 14px;", "padding: 14px 0;", "margin: 0 0 10px;"],
        "web/static/css/stage6.css": ["padding: 14px 16px;", "padding: 10px 12px;"],
        "web/static/css/workbench.css": ["padding: 18px 8px;", "padding: 14px 6px;"],
        "web/static/index.html": ["margin-bottom:14px; }", "margin-bottom:10px; }"],
    }
    for rel, bads in regressions.items():
        css = (ROOT / rel).read_text(encoding="utf-8")
        for b in bads:
            assert b not in css, f"{rel} 出现已下网的旧值: {b}"


# ===== 推荐可执行 / 自选开关 / 净值走势增强(2026-11-22 第三批) =====


def test_recommendation_cards_expose_actions():
    """推荐卡必须能直达行情/重仓并加入自选, 而不是只展示文字。"""
    js = (ROOT / "web/static/js/recommendations.js").read_text(encoding="utf-8")
    for needle in (
        'data-rec-action="chart"',
        'data-rec-action="holdings"',
        'data-rec-action="watch"',
        "/api/monitor/watchlist",
        "window.loadStockDetail",
        "window.viewHoldings",
        "pred-rec-actions",
    ):
        assert needle in js, f"recommendations.js 缺少 {needle}"


def test_stock_detail_watch_toggle_reads_existing_state():
    """个股详情的自选按钮必须先读列表状态, 再决定加/移除, 不能盲写。"""
    text = _index()
    for needle in (
        'id="stockWatchBtn"',
        "toggleStockWatch(this)",
        "syncStockWatchState(",
        "async function toggleStockWatch(",
        "/api/monitor/watchlist/",
        "dataset.state === 'on'",
    ):
        assert needle in text, f"index.html 缺少 {needle}"
    # 移除走 DELETE、新增走 POST, 不能只做单向写入
    assert "method: 'DELETE'" in text
    assert "method: 'POST',\n        body: JSON.stringify({ stock_code: code, rules: ['price_surge'] })" in text


def test_fund_chart_has_period_switch_and_cost_line():
    """基金走势: 周期切换 + 成本虚线; 上游 0 值必须剔除而不是画成贴底。"""
    text = _index()
    for needle in (
        "data-fund-period=\"${k}\"",
        "'3m': '近3月'",
        "setFundChartPeriod(",
        "FUND_CHART_PERIODS",
        "series.createPriceLine(",
        "成本 ",
        "_fundPositionsByCode",
        "上游未返回，不补数",
    ):
        assert needle in text, f"index.html 缺少 {needle}"
    assert "period=1y`" not in text, "净值走势不应再把周期硬编码为 1y"


def test_stock_flow_series_uses_single_fetch_and_switches_period():
    """个股资金流时间序列(2026-11-22 第四批)。

    历史序列复用 /api/market/stock_flow_streak 的同一次取数(history),
    前端按 5/10/20/60 日切片, 不再额外请求上游; 缺失保持「不补数」口径。
    """
    text = _index()
    for needle in (
        "STOCK_FLOW_DAYS = [5, 10, 20, 60]",
        'data-flow-days="${n}"',
        "onStockFlowDaysClick(this)",
        "addHistogramSeries(",
        "addLineSeries(",
        "priceScaleId: 'left'",
        "Array.isArray(d.history) ? d.history : []",
        "上游仅返回主力净额，超大/大单拆分缺失",
        "缺失不补数",
        "这是取数问题，不是没有资金流",
    ):
        assert needle in text, f"index.html 缺少 {needle}"
    # 切片只发生在本地, 不能为切换周期再发一次上游请求
    assert "stock_flow_history" not in text
    assert "stock_flow_streak/history" not in text


def test_stock_flow_chart_survives_render_cleanup():
    """重新渲染个股详情时必须先销毁旧图, 否则 lightweight-charts 会叠图/泄漏。"""
    text = _index()
    assert "function _disposeStockFlowChart(" in text or "_stockFlowChart.remove()" in text
    idx = text.find("function renderStockDetail(")
    assert idx != -1
    body = text[idx: idx + 2000]
    assert "_stockFlowChart" in body, "renderStockDetail 开头应销毁旧资金流图"

"""STAGE7 工作台回归(2026-11-14)。

无 JS 测试运行器，沿用 test_frontend_xss.py 的静态契约思路：
- 工作台是行情默认落地页（页面 / 二级 tab / IA 映射 / 样式脚本全部接线）；
- 新的 innerHTML 装配点必须对上游文本调用 esc()；
- 昨日封板率必须由炸板率换算（100 − x），不得把炸板率当封板率展示；
- 缺失字段显示「—」并给原因，判读文案标注非投资建议。
"""
from __future__ import annotations

from pathlib import Path

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
    assert '<script src="/static/js/workbench.js"></script>' in text
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
    # 旧的基金页单独标题类已随内联样式一起移除
    assert "fund-head" not in text
    stage6 = (ROOT / "web/static/css/stage6.css").read_text(encoding="utf-8")
    assert ".fund-head" not in stage6

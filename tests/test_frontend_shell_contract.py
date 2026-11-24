"""STAGE8 终端化骨架（qf-shell）静态契约测试（2026-11-22，QA 新增）。

本文件只做**静态字符串/结构断言**，不执行 JS、不依赖 node：
- 加载顺序（qf-shell.css 必须在 terminal-ui.css 之后且唯一；两个新 script 在 version-modal.js
  之后、</body> 之前）；
- 回滚开关（?shell=off / localStorage['qf.shell.off']）的判定必须早于任何 DOM 操作；
- QF_NAV 必须覆盖 **20** 个二级页，且必须包含 STAGE5_PAGE_MAP 缺失的
  watchboard / risk / evolution（防「按 17 键枚举」的改法回退）；
- 新 JS 不得使用 innerHTML 拼接（列表/面板渲染一律 textContent）；
- 数据诚实性：index.html 中不得残留 sparkline（合成走势已按产品决策删除）。

设计依据：docs/design-ui-terminal-2026-11-22.md §1.3 / §3.1-3.2 / §4.1 / §15.2 / §16.1。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web/static/index.html"
SHELL_JS = ROOT / "web/static/js/qf-shell.js"
CMDK_JS = ROOT / "web/static/js/qf-cmdk.js"
SHELL_CSS = ROOT / "web/static/css/qf-shell.css"
TERMINAL_CSS = ROOT / "web/static/css/terminal-ui.css"
TOKENS_CSS = ROOT / "web/static/css/tokens.css"

# STAGE5_PAGE_MAP（index.html 唯一权威映射）缺这 3 页，由 QF_NAV 兜住（BUG-STAGE5-MAP-INCOMPLETE）
PAGES_MISSING_IN_STAGE5_MAP = ("watchboard", "risk", "evolution")
# QF_NAV 真源：5 组 20 页（design §3.2）
EXPECTED_PAGE_COUNT = 20


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _index() -> str:
    return _read(INDEX)


def _nav_block() -> str:
    """截取 window.QF_NAV 常量块（到行首两空格的 `};` 为止）。"""
    js = _read(SHELL_JS)
    start = js.index("window.QF_NAV = window.QF_NAV || {")
    end = js.index("\n  };", start)
    return js[start:end]


# QF_NAV 里的 group 对象：{ id: 'hud-market', label: '行情', key: '1', defaultPage: 'workbench',
_GROUP_RE = re.compile(r"\{\s*id:\s*'([\w-]+)',\s*label:\s*'([^']*)',\s*key:\s*'(\d)'")
# QF_NAV 里的 item 对象：{ id: 'workbench', label: '工作台' } —— 紧跟 label 就结束，不带 key
_ITEM_RE = re.compile(r"\{\s*id:\s*'([\w-]+)',\s*label:\s*'([^']*)'\s*\}")


def _nav_groups() -> list[tuple[str, str, str]]:
    return _GROUP_RE.findall(_nav_block())


def _nav_items() -> list[tuple[str, str]]:
    return _ITEM_RE.findall(_nav_block())


# ---------------------------------------------------------------- 加载顺序


def test_shell_assets_exist():
    for f in (SHELL_CSS, SHELL_JS, CMDK_JS):
        assert f.is_file(), f"缺少文件 {f}"
    assert SHELL_CSS.stat().st_size > 8000, "qf-shell.css 过小，骨架层疑似未落地"
    assert SHELL_JS.stat().st_size > 20000, "qf-shell.js 过小，骨架层疑似未落地"


def test_qf_shell_css_loads_after_terminal_ui_exactly_once():
    """qf-shell.css 必须排在 terminal-ui.css 之后（否则被 terminal-ui 的 78px 轨道压掉），且唯一。

    注意：本断言只数 `<link ... terminal-ui.css>`，不数字面量出现次数 ——
    index.html 的 STAGE8 注释里也写了 "terminal-ui.css" 这个文件名（说明性引用，不加载）。
    """
    html = _index()
    terminal_links = re.findall(r'<link[^>]+href="[^"]*terminal-ui\.css[^"]*"', html)
    shell_links = re.findall(r'<link[^>]+href="[^"]*qf-shell\.css[^"]*"', html)
    assert len(terminal_links) == 1, f"terminal-ui.css 必须只加载 1 次，实际 {len(terminal_links)}"
    assert len(shell_links) == 1, f"qf-shell.css 必须只加载 1 次，实际 {len(shell_links)}"

    pos_terminal = html.index(terminal_links[0])
    pos_shell = html.index(shell_links[0])
    assert pos_shell > pos_terminal, "qf-shell.css 必须排在 terminal-ui.css 之后"

    # qf-shell.css 必须是最后一个样式表层（后面不得再有 stylesheet，否则覆盖关系失效）
    after = html[pos_shell + len(shell_links[0]):]
    assert 'rel="stylesheet"' not in after, (
        "qf-shell.css 之后还有 <link rel=stylesheet>，会破坏「骨架层最后加载」的覆盖关系"
    )


def test_shell_scripts_load_after_version_modal_before_body_close():
    """qf-shell.js / qf-cmdk.js 必须是最后两个 script（链上最外层 switchPage 包装）。"""
    html = _index()
    pos_modal = html.index('<script src="/static/js/version-modal.js"')
    pos_shell = html.index('src="/static/js/qf-shell.js')
    pos_cmdk = html.index('src="/static/js/qf-cmdk.js')
    pos_body_close = html.rindex("</body>")
    assert pos_modal < pos_shell < pos_cmdk, "script 顺序必须是 version-modal → qf-shell → qf-cmdk（先 shell 后 cmdk）"
    assert pos_cmdk < pos_body_close, "qf-cmdk.js 必须在 </body> 之前"

    # qf-shell.js 之后除 qf-cmdk.js 外不得再有其它的 js script（否则包装层级不再最外）
    tag_start = html.rindex("<script", 0, pos_shell)
    tail = html[tag_start:pos_body_close]
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', tail)
    assert scripts == ["/static/js/qf-shell.js?v=20261122a",
                       "/static/js/qf-cmdk.js?v=20261122a"], f"尾部 script 顺序异常: {scripts}"


# ---------------------------------------------------------------- 回滚开关


def test_shell_rollback_switch_is_decided_before_any_dom_operation():
    """?shell=off / localStorage['qf.shell.off'] 的判定必须在文件最前面，早于任何 DOM 操作。

    结构断言（不执行 JS）：
    ① isShellOff 定义在前 60 行；
    ② if (isShellOff()) 早于第一处 document.body / querySelector 调用；
    ③ 早退分支把 QF.shell.enabled 置 false（qf-cmdk.js 依赖它跳过初始化）。
    """
    js = _read(SHELL_JS)
    lines = js.splitlines()

    def first_line(pred) -> int:
        for i, ln in enumerate(lines, start=1):
            if pred(ln):
                return i
        return -1

    line_fn = first_line(lambda ln: "function isShellOff()" in ln)
    line_check = first_line(lambda ln: "if (isShellOff())" in ln)
    assert 0 < line_fn <= 60, f"isShellOff 应定义在文件前 60 行内，实际第 {line_fn} 行"
    assert 0 < line_check <= 60, f"isShellOff() 判定应在前 60 行内，实际第 {line_check} 行"
    assert line_check > line_fn, "必须先定义 isShellOff 再判定"

    # 判定必须早于任何 DOM 读写
    for marker in ("document.body.classList.add", "document.body.setAttribute", "document.body.appendChild",
                   "document.querySelector", "document.getElementById"):
        pos = js.find(marker)
        if pos < 0:
            continue
        line_dom = js[:pos].count("\n") + 1
        assert line_check < line_dom, f"isShellOff 判定(第{line_check}行)晚于 DOM 操作 {marker}(第{line_dom}行)"

    # 两条开关来源都在
    assert "shell=off" in js, "缺少 ?shell=off URL 开关"
    assert "qf.shell.off" in js, "缺少 localStorage['qf.shell.off'] 开关"
    block = js[js.index("if (isShellOff())"):js.index("if (isShellOff())") + 400]
    assert "QF.shell.enabled = false" in block, "回滚分支必须把 QF.shell.enabled 置 false"
    assert "return" in block, "回滚分支必须直接 return（不建节点、不搬 DOM）"


def test_cmdk_skips_initialisation_when_shell_disabled():
    """qf-cmdk.js 必须在 QF.shell.enabled === false 时整体不初始化（回滚粒度到文件级）。"""
    js = _read(CMDK_JS)
    head = js[:js.index("var RECENT_KEY")]
    assert "QF.shell.enabled === false" in head, "qf-cmdk.js 顶部缺少 QF.shell.enabled === false 的短路"


# ---------------------------------------------------------------- 导航模型


def test_qf_nav_covers_20_pages_including_the_three_missing_from_stage5_map():
    """QF_NAV 必须是 5 组 20 页，且收录 STAGE5_PAGE_MAP 缺失的 watchboard/risk/evolution。

    这条断言的目的：锁死「20 页」这个真源。任何按 STAGE5_PAGE_MAP 的 17 个键来枚举
    页面（命令面板 / ctxbar tabs / 走查清单）的改法都会让命令面板漏 3 页
    （BUG-STAGE5-MAP-INCOMPLETE，design §3.1）。
    """
    groups = _nav_groups()
    items = _nav_items()
    group_ids = [g[0] for g in groups]
    item_ids = [i[0] for i in items]

    assert group_ids == ["hud-market", "hud-holdings", "hud-strategy", "hud-monitor", "hud-settings"], (
        f"一级分组异常: {group_ids}"
    )
    assert len(item_ids) == EXPECTED_PAGE_COUNT, (
        f"QF_NAV 必须覆盖 {EXPECTED_PAGE_COUNT} 个二级页，实际 {len(item_ids)}: {item_ids}"
    )
    assert len(set(item_ids)) == EXPECTED_PAGE_COUNT, f"QF_NAV 二级页 id 重复: {item_ids}"
    for pid in PAGES_MISSING_IN_STAGE5_MAP:
        assert pid in item_ids, (
            f"QF_NAV 缺少 {pid}（STAGE5_PAGE_MAP 本身缺它，只能由 QF_NAV 兜住，design §3.1）"
        )


def test_stage5_page_map_still_has_only_17_keys_and_was_not_modified():
    """PRD §1.4 硬约束「不动 STAGE5_PAGE_MAP」：它仍应是 17 键（缺 3 页是既存缺陷）。

    本断言不是"要求缺页"，而是锁住「本轮没有偷偷改映射」这一事实：
    如果下一轮把 3 页并入映射，这条会红 —— 那时应同时删除设计文档 §16 的登记项，
    属于有意的契约变更，不应该被无意触发。
    """
    html = _index()
    start = html.index("const STAGE5_PAGE_MAP = {")
    end = html.index("};", start)
    block = html[start:end]
    keys = re.findall(r"^\s*'?([\w-]+)'?:\s*\{\s*parent:", block, re.M)
    assert len(keys) == 17, f"STAGE5_PAGE_MAP 应为 17 键（3 页缺失由 QF_NAV 兜住），实际 {len(keys)}"
    for pid in PAGES_MISSING_IN_STAGE5_MAP:
        assert pid not in keys, f"{pid} 意外出现在 STAGE5_PAGE_MAP，需同步更新 design §16 登记"


# ---------------------------------------------------------------- 无 innerHTML


def test_cmdk_js_never_uses_innerHTML():
    """命令面板结果行必须 textContent 渲染（design §4.1 / 既有 innerHTML 插值扫描契约）。"""
    js = _read(CMDK_JS)
    assert "innerHTML" not in js, "qf-cmdk.js 出现 innerHTML（结果行必须 createElement + textContent）"
    assert "insertAdjacentHTML" not in js, "qf-cmdk.js 出现 insertAdjacentHTML"
    assert "outerHTML" not in js, "qf-cmdk.js 出现 outerHTML"


def test_shell_js_never_uses_innerHTML():
    """qf-shell.js 同样不得拼接 HTML（面板三态/指数条/排序/工作台模板全部 DOM API）。"""
    js = _read(SHELL_JS)
    assert "innerHTML" not in js, "qf-shell.js 出现 innerHTML"
    assert "insertAdjacentHTML" not in js, "qf-shell.js 出现 insertAdjacentHTML"
    assert "outerHTML" not in js, "qf-shell.js 出现 outerHTML"


# ---------------------------------------------------------------- 数据诚实性


def test_index_html_has_no_sparkline_left():
    """数据诚实性（design §15.2）：指数卡的合成 PRNG 走势必须彻底删除。

    与 tests/test_frontend_workbench.py::test_index_cards_drop_synthetic_sparkline_and_explain_missing_amount
    重叠（那条同时锁真实字段钩子）；这里保留一条最小断言，用于「删了一个字就红」的变异验证。
    """
    html = _index().lower()
    assert "sparkline" not in html, "index.html 仍残留 sparkline（合成走势未彻底删除）"
    assert "drawsparkline" not in html, "index.html 仍残留 drawSparkline"


def test_marked_panels_are_exactly_six():
    """M4 的**范围口径**：本轮 data-qf-panel 标记面板 = 6 个（design §5.3 / §16 P2-PANEL-COVERAGE）。"""
    html = _index()
    assert html.count("data-qf-panel") == 6, (
        f"本轮标记面板应为 6 个（工作台 4 + 策略页 2），实际 {html.count('data-qf-panel')}"
    )


# ---------------------------------------------------------------- 不许碰的文件


def test_terminal_ui_css_and_tokens_untouched_by_shell_layer():
    """design §13.1：不改 terminal-ui.css（最后存量样式层）与 tokens.css 既有 token。"""
    assert "qf-shell" not in _read(TERMINAL_CSS), "terminal-ui.css 被写入了 qf-shell 相关规则"
    assert "--qf-rail-w" not in _read(TOKENS_CSS), "新增布局 token 应写在 qf-shell.css 的 body.qf-shell 里"


def test_shell_css_scopes_skeleton_rules_under_body_qf_shell():
    """骨架规则必须挂在 body.qf-shell 下（移除该类即回滚），不得凭空改写全局 .sidebar/.topbar。"""
    css = _read(SHELL_CSS)
    for selector in (r".sidebar\s*\{", r".topbar\s*\{", r"\.nav-item\s*\{"):
        for m in re.finditer(selector, css):
            # 取该规则的选择器起点（向前找到最近的 '}' 或空行）
            head = css[: m.start()]
            rule_start = max(head.rfind("}"), head.rfind("\n\n"))
            sel = head[rule_start + 1: m.start()].strip().splitlines()[-1]
            assert "body.qf-shell" in sel, f"未作用域化的骨架规则: {sel.strip()!r} -> {selector}"

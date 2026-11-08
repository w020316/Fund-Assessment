"""前端错误态契约测试（QF3-27）——静态源码断言，无需 JS 运行器。

背景
----
`fetchJSON` 早已返回 `{ok, status, body}`，但 3 个调用点写成
`const rows = (r.ok && ...) || [];`，把**请求失败归一成空数组**，随后走进
"暂无数据/未触发"的正常空态分支 —— 用户看到的是"没有"，而实际是"取不到"。

本测试锁定修复形态，且**能区分三种改法**：

| 改法 | 结果 |
|---|---|
| 先判 `ok` 再判空，`|| []` 保留在 ok 判断之后 | ✅ 通过 |
| 只把 `if (!r.ok)` 加上、但删掉 `|| []` | ❌ 失败（`undefined.length` 会崩，不是修复） |
| 删掉空态分支（"暂无预测"等） | ❌ 失败（真实无数据时反而无提示） |
| 回退到 `(r.ok && ...) || []` 旧写法 | ❌ 失败（旧模式断言命中） |

约束：只读源码，不修改任何文件；不依赖浏览器。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "js"

# (文件, 函数名, 空态文案 —— 用于确认"真实无数据"分支未被误删)
SITES = [
    ("prediction.js", "loadSessions", None),
    ("prediction.js", "loadPrediction", "暂无预测"),
    ("recommendations.js", "load", "今日无满足条件的推荐"),
]


def _strip_js_comments(src: str) -> str:
    """去掉注释，避免注释里引用的旧写法被误判为"代码仍是旧写法"。

    只剥离**整行** `//` 注释与 `/* */` 块，不碰行内 `//`
    —— 因为字符串里的 URL（如 'https://...'）含 `//`，行内剥离会截断字符串。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//[^\n]*$", "", src)


def _extract_function(src: str, name: str) -> str:
    """按函数名提取函数体（花括号配对，避免跨函数误取）。注释已剥离。"""
    src = _strip_js_comments(src)
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", src)
    assert m, f"未找到函数 {name}()"
    start = src.find("{", m.end())
    assert start != -1, f"{name}() 缺少函数体"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"{name}() 花括号不配对")


@pytest.mark.parametrize("filename,func,empty_text", SITES)
def test_ok_checked_before_empty_state(filename, func, empty_text):
    """先判 ok 再判空：ok 判断必须存在，且位于 `|| []` 之前。"""
    body = _extract_function((JS_DIR / filename).read_text(encoding="utf-8"), func)

    i_ok = body.find("if (!r.ok)")
    i_fallback = body.find("|| []")

    assert i_ok != -1, f"{filename}:{func} 缺少 `if (!r.ok)` —— 失败会被归一成空态"
    assert i_fallback != -1, f"{filename}:{func} 缺少 `|| []` 兜底 —— 删掉会让 undefined.length 崩"
    assert i_ok < i_fallback, (
        f"{filename}:{func} 的 `|| []` 出现在 `if (!r.ok)` 之前 —— 说明仍是旧写法，"
        "失败会先被归一成空数组再判断"
    )


@pytest.mark.parametrize("filename,func,empty_text", SITES)
def test_error_message_is_actionable(filename, func, empty_text):
    """错误文案必须说"加载失败"并给可操作信息，不能是"暂无数据"。"""
    body = _extract_function((JS_DIR / filename).read_text(encoding="utf-8"), func)

    assert "数据加载失败" in body, f"{filename}:{func} 错误分支未使用『数据加载失败』文案"
    assert "HTTP " in body, f"{filename}:{func} 有 status 时未回显状态码"
    assert "请稍后重试" in body, f"{filename}:{func} 有 status 时缺少『请稍后重试』"
    assert "请检查网络后重试" in body, f"{filename}:{func} 无 status 时缺少网络排查提示"

    # 错误分支不得复用"暂无数据"话术
    err_block = body[body.find("if (!r.ok)"):body.find("if (!r.ok)") + 400]
    assert "暂无数据" not in err_block, f"{filename}:{func} 错误分支仍使用『暂无数据』"


@pytest.mark.parametrize("filename,func,empty_text", SITES)
def test_rows_fallback_retained(filename, func, empty_text):
    """`|| []` 必须仍兜住 rows —— 防止"修复"变成删兜底导致 undefined.length 崩溃。"""
    body = _extract_function((JS_DIR / filename).read_text(encoding="utf-8"), func)

    m = re.search(r"const rows = (.+?);", body, re.S)
    assert m, f"{filename}:{func} 未找到 rows 赋值"
    expr = m.group(1).strip()
    assert expr.endswith("|| []"), (
        f"{filename}:{func} 的 rows 表达式未以 `|| []` 结尾（实际：{expr[-60:]}）—— "
        "删掉兜底会在 body 缺失时抛 TypeError"
    )


@pytest.mark.parametrize("filename,func,empty_text", SITES)
def test_legacy_pattern_removed(filename, func, empty_text):
    """旧模式 `(r.ok && ...) || []` 必须消失，否则失败仍会被归一成空数组。"""
    body = _extract_function((JS_DIR / filename).read_text(encoding="utf-8"), func)
    assert not re.search(r"\(\s*r\.ok\s*&&", body), (
        f"{filename}:{func} 仍存在 `(r.ok && ...) || []` 旧模式"
    )


@pytest.mark.parametrize("filename,func,empty_text", SITES)
def test_genuine_empty_state_kept(filename, func, empty_text):
    """真实"无数据"分支必须保留 —— 否则修完变成"两种情况下都不提示"。"""
    if not empty_text:
        pytest.skip("该函数无独立空态文案（直接渲染固定卡片）")
    body = _extract_function((JS_DIR / filename).read_text(encoding="utf-8"), func)
    assert empty_text in body, (
        f"{filename}:{func} 丢失了真实空态文案『{empty_text}』—— "
        "修错误态不应连带删除无数据提示"
    )


def test_no_new_unguarded_empty_data_text():
    """全局护栏：`暂无数据` 不得出现在 catch 块上下文（本轮取证：21 处中 0 处如此）。"""
    files = [JS_DIR.parent / "index.html", *sorted(JS_DIR.glob("*.js"))]
    offenders = []
    for p in files:
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8", errors="ignore").split("\n")
        for i, line in enumerate(lines):
            if "暂无数据" in line:
                ctx = "\n".join(lines[max(0, i - 6):i + 2])
                if re.search(r"catch\s*\(|\}\s*catch", ctx):
                    offenders.append(f"{p.name}:{i + 1}")
    assert not offenders, f"『暂无数据』出现在错误路径上：{offenders}"

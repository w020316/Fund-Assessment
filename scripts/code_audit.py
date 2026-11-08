"""阶段一代码审计探针(只读): OpenAPI 操作面 vs 全仓调用面。

第三轮改进（R3，2026-11-08）—— 解决"30 个假孤儿"的误报：
  1) 引用面从"仅前端"扩展到 `.github/workflows/*.yml` 与 `scripts/*.py`，
     能识别 `$PREDICT_API_BASE/api/...` 这类 CI 定时任务调用；
  2) 自动提取 `const BASE = '/api/...'` 常量并解析 `${BASE}/xxx` 拼接，
     取代原先的硬编码白名单（新增模块不再需要手工登记）；
  3) 同时解析 `'/api/x/' + var + '/y'` 形式的字符串拼接；
  4) 每个操作输出 `evidence`: browser / workflow / script / none，真孤儿一眼可辨。

用途:
    python scripts/code_audit.py

输出(JSON):
    - api: 路径数/操作数
    - sources: 各来源解析出的路径数
    - orphans: 未被任何来源引用的操作（evidence == "none"）
    - orphan_count / no_ui_writes
    - non_api_paths: 非 /api 路径（如根跳转），不计入孤儿

约束:
    - 只读, 不修改任何业务代码与数据文件
    - 不发起外部网络请求; 仅用 TestClient 读取 /openapi.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ENV", "dev")

METHODS = ("get", "post", "put", "delete", "patch")

# 宽松抓取任意 /api/ 字面量（覆盖 api()/apiCached()/fetch()/curl/变量拼接）
API_LITERAL_RE = re.compile(r"/api/[A-Za-z0-9_\-/{}${}.]+")
# const BASE = '/api/strategy/evolution' 这类路径常量
CONST_RE = re.compile(r"""(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*['"`](/api/[^'"`]*)['"`]""")
# '/api/fund/positions/' + code + '/shares' 这类拼接（中间是变量）
CONCAT_VAR_RE = re.compile(
    r"""['"`](/api/[^'"`]*/)['"`]\s*\+\s*[A-Za-z_$][\w$.]*\s*\+\s*['"`]([^'"`]*)['"`]"""
)


def _norm(path: str) -> str:
    """把 ${var} 与 {var} 统一成 {param}，并去掉尾部斜杠，便于跨来源比对。"""
    p = re.sub(r"\$\{[^}]+\}", "{param}", path)
    p = re.sub(r"\{[^}]+\}", "{param}", p)
    return p.rstrip("/")


def _strip_comments(blob: str) -> str:
    """去掉注释，避免注释里提到的接口被误判为"已引用"。"""
    blob = re.sub(r"//[^\n]*", "", blob)
    blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.S)
    blob = re.sub(r"(?m)^\s*#.*$", "", blob)  # yml / py 行注释
    return blob


def _collect(blob: str) -> set[str]:
    """从一段文本里解析所有 /api 路径：直接字面量 + 常量拼接 + 变量拼接。"""
    found: set[str] = set()

    # 1) 直接字面量
    for m in API_LITERAL_RE.finditer(blob):
        found.add(_norm(m.group(0)))

    # 2) const BASE = '/api/...'  +  ${BASE}/suffix  或  BASE + '/suffix'
    consts = {name: val for name, val in CONST_RE.findall(blob)}
    for name, base in consts.items():
        found.add(_norm(base))
        for m in re.finditer(r"\$\{\s*" + re.escape(name) + r"\s*\}([^'\"`\s)]*)", blob):
            found.add(_norm(base + m.group(1)))
        for m in re.finditer(
            re.escape(name) + r"""\s*\+\s*['"`]([^'"`]*)['"`]""", blob
        ):
            found.add(_norm(base + m.group(1)))

    # 3) '/api/x/' + var + '/y'
    for prefix, suffix in CONCAT_VAR_RE.findall(blob):
        found.add(_norm(prefix + "{param}" + suffix))

    return {p for p in found if p.startswith("/api")}


def _browser_paths() -> set[str]:
    files = [ROOT / "web/static/index.html", *sorted((ROOT / "web/static/js").glob("*.js"))]
    blob = "\n".join(
        _strip_comments(p.read_text(encoding="utf-8", errors="ignore"))
        for p in files if p.exists()
    )
    return _collect(blob)


def _workflow_paths() -> set[str]:
    found: set[str] = set()
    for pat in ("*.yml", "*.yaml"):
        for p in sorted((ROOT / ".github/workflows").glob(pat)):
            found |= _collect(_strip_comments(p.read_text(encoding="utf-8", errors="ignore")))
    return found


def _script_paths() -> set[str]:
    found: set[str] = set()
    for p in sorted((ROOT / "scripts").glob("*.py")):
        if p.name == Path(__file__).name:
            continue
        found |= _collect(_strip_comments(p.read_text(encoding="utf-8", errors="ignore")))
    return found


def main() -> int:
    from fastapi.testclient import TestClient

    from web.api import app

    spec = TestClient(app).get("/openapi.json").json()

    sources = {
        "browser": _browser_paths(),
        "workflow": _workflow_paths(),
        "script": _script_paths(),
    }

    ops, non_api = [], []
    for path, methods in spec["paths"].items():
        for method in methods:
            if method not in METHODS:
                continue
            if not path.startswith("/api"):
                # 根跳转等非 API 路径：不适用"孤儿接口"概念，单独列出
                non_api.append({"method": method.upper(), "path": path})
                continue
            full = _norm(path)
            literal = _norm(path[4:])  # 去掉 /api 前缀，兼容前端只写后缀的旧习惯
            evidence = "none"
            for name in ("browser", "workflow", "script"):  # 优先级：浏览器 > CI > 脚本
                pool = sources[name]
                if full in pool or literal in pool:
                    evidence = name
                    break
            ops.append({"method": method.upper(), "path": path, "evidence": evidence})

    orphans = [o for o in ops if o["evidence"] == "none"]
    result = {
        "api": {"paths": len(spec["paths"]), "operations": len(ops)},
        "sources": {k: len(v) for k, v in sources.items()},
        "evidence_summary": {
            name: sum(1 for o in ops if o["evidence"] == name)
            for name in ("browser", "workflow", "script", "none")
        },
        "orphans": orphans,
        "orphan_count": len(orphans),
        "no_ui_writes": [o for o in orphans if o["method"] in ("POST", "PUT", "DELETE", "PATCH")],
        "non_api_paths": non_api,
        "note": "orphan = evidence 为 none，即前端/CI/脚本三处均无引用；"
                "非 /api 路径已单列，不计入孤儿。",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""阶段一代码审计探针(只读): OpenAPI 操作面 vs 前端调用面。

用途:
    python scripts/code_audit.py

输出(JSON):
    - api: 路径数/操作数
    - frontend: 前端实际调用路径数
    - orphans: 后端已注册但前端未引用的操作
    - no_ui_writes: 写操作中前端未引用的项

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


def _frontend_paths() -> set[str]:
    blob = ""
    for p in [ROOT / "web/static/index.html", *sorted((ROOT / "web/static/js").glob("*.js"))]:
        blob += p.read_text(encoding="utf-8", errors="ignore") + "\n"
    paths: set[str] = set()
    # 去掉注释, 避免注释里提到的接口被误判为“已引用”
    blob = re.sub(r"//[^\n]*", "", blob)
    blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.S)
    # 抓取任意 /api/ 字面量, 覆盖 api()/apiCached()/fetch() 以及先存变量再请求的写法
    for m in re.finditer(r"/api/[A-Za-z0-9_\-/{}${}.]+", blob):
        raw = m.group(0).split("?")[0].rstrip("'\"`),;")
        raw = re.sub(r"\$\{[^}]+\}", "{param}", raw)
        if raw.endswith("/"):
            raw += "{param}"
        paths.add(raw)
    return paths


def main() -> int:
    from fastapi.testclient import TestClient

    from web.api import app

    spec = TestClient(app).get("/openapi.json").json()
    frontend = _frontend_paths()

    ops = []
    for path, methods in spec["paths"].items():
        for method in methods:
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            suffix = path[4:] if path.startswith("/api") else path
            literal = re.sub(r"\{[^}]+\}", "{param}", suffix).rstrip("/")
            full = re.sub(r"\{[^}]+\}", "{param}", path).rstrip("/")
            referenced = full in frontend or literal in frontend
            ops.append({"method": method.upper(), "path": path, "referenced": referenced})

    result = {
        "api": {"paths": len(spec["paths"]), "operations": len(ops)},
        "frontend": {"unique_paths": len(frontend)},
        "orphans": [o for o in ops if not o["referenced"]],
        "orphan_count": sum(1 for o in ops if not o["referenced"]),
        "no_ui_writes": [o for o in ops if not o["referenced"] and o["method"] in ("POST", "PUT", "DELETE", "PATCH")],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

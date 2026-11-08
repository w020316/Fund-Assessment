"""环境来源诊断（只读）：打印关键依赖的版本与真实加载路径，标记"来自项目外路径"。

背景（第三轮阶段 0 发现）：本机 Python 存在两个 sys.path 注入点，
使同一包在不同启动方式下解析到不同版本 ——
  1) 机器级：D:/dev-tools/Python312/python312._pth 硬编码 d:\\xm\\mylibrary-rag
  2) 项目级：tests/conftest.py 与 launch.py 把 <root>/pylibs 插到 sys.path[0]
故 `pip list` 看到的版本不等于实际 import 的版本。本脚本让该差异可复现、可审计。

用法：
    python scripts/check_env_provenance.py            # 裸解释器口径
    python scripts/check_env_provenance.py --pylibs   # 模拟 launch.py / pytest 口径
退出码：0 全部来自项目内；1 存在项目外来源（供 CI 可选接入）。
"""
from __future__ import annotations

import os
import sys

PACKAGES = ("fastapi", "starlette", "uvicorn", "requests", "pandas", "pydantic",
            "yaml", "multipart", "akshare", "tushare", "loguru", "apscheduler",
            "numpy", "bs4")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIRS = (os.path.join(ROOT, "pylibs"), os.path.join(ROOT, "src"), os.path.join(ROOT, "web"))


def classify(path: str) -> str:
    """把加载来源分成三类，避免把正常的 site-packages 与真正的跨项目污染混为一谈。

    PROJECT  = 本项目目录（pylibs/src/web）—— 受 launch.py 与 conftest.py 注入影响
    SITE     = 解释器自带 site-packages —— 正常安装，只需核对版本是否等于声明
    FOREIGN  = 其他项目目录（如 mylibrary-rag）—— 真正的可复现性风险
    """
    if not path:
        return "UNKNOWN"
    p = os.path.abspath(path)
    if any(p.startswith(os.path.abspath(x) + os.sep) for x in PROJECT_DIRS):
        return "PROJECT"
    if f"{os.sep}site-packages{os.sep}" in p or f"{os.sep}dist-packages{os.sep}" in p:
        return "SITE"
    return "FOREIGN"


def main() -> int:
    if "--pylibs" in sys.argv:
        sys.path.insert(0, os.path.join(ROOT, "pylibs"))
        sys.path.insert(0, ROOT)
        mode = "pylibs 口径（模拟 launch.py / pytest）"
    else:
        mode = "裸解释器口径（._pth 生效）"

    print(f"# 环境来源诊断 · {mode}")
    print(f"# interpreter = {sys.executable}")
    print(f"# cwd/root    = {ROOT}")
    print()
    print(f"{'package':<16}{'version':<14}{'source':<10}path")
    print("-" * 104)

    counts = {"PROJECT": 0, "SITE": 0, "FOREIGN": 0, "UNKNOWN": 0}
    for name in PACKAGES:
        try:
            mod = __import__(name)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:<16}{'IMPORT-ERROR':<14}{'-':<10}{type(exc).__name__}: {exc}")
            continue
        ver = getattr(mod, "__version__", None) or getattr(mod, "VERSION", None) or "?"
        path = getattr(mod, "__file__", "") or ""
        src = classify(path)
        counts[src] = counts.get(src, 0) + 1
        print(f"{name:<16}{str(ver):<14}{src:<10}{path}")

    print()
    print(f"# 来源统计: PROJECT={counts['PROJECT']}  SITE={counts['SITE']}  "
          f"FOREIGN={counts['FOREIGN']}  ERROR={counts['UNKNOWN']}  (共 {len(PACKAGES)})")
    if counts["FOREIGN"]:
        print("# ⚠ FOREIGN>0：存在跨项目加载（通常是 python312._pth 硬编码了其他项目目录）。")
        print("#   影响：该目录移动/删除即不可运行；测试结论不可平移到干净环境。")
    if counts["PROJECT"]:
        print("# ⚠ PROJECT>0：pylibs 注入生效（launch.py / conftest.py），此处版本可能与声明不符。")
    return 1 if (counts["FOREIGN"] or counts["PROJECT"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fund-Assessment FastAPI 服务启动器。

自动将项目根目录与 pylibs 加入 sys.path，规避系统级 PYTHONPATH
被其他项目覆盖导致 uvicorn worker 子进程找不到依赖的问题。

用法:
    python launch.py              # 生产模式（单进程）
    python launch.py --reload     # 开发模式（热重载）
    python launch.py --port 9000  # 指定端口
    python launch.py --host 0.0.0.0 --port 8000 --reload
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_ROOT)

_PYLIBS = os.path.join(_ROOT, "pylibs")
sys.path.insert(0, _PYLIBS)
sys.path.insert(0, _ROOT)

# 同步写入 PYTHONPATH 环境变量，确保 --reload 模式下 worker 子进程
# (通过 subprocess 启动) 也能继承到正确的依赖路径。
_existing = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [_PYLIBS, _ROOT, _existing]))

import uvicorn  # noqa: E402


def _parse_arg(name: str, default=None, cast=str):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return cast(sys.argv[i + 1])
    return default


if __name__ == "__main__":
    # 单 worker 自检(2026-10-26)：模拟持仓/风控状态/限流计数/缓存均在进程内，
    # 多 worker 会让状态各持一份，表现为"持仓消失、限流翻倍、风控错乱"。
    # 注意：本启动器**不会**把 --workers 转发给 uvicorn.run（始终单进程），
    # 这里只做提示——真正的风险在有人直接改用 `uvicorn web.api:app --workers N`。
    _workers = _parse_arg("--workers", 1, int) or 1
    if _workers > 1:
        print(
            f"[WARN] 检测到 --workers {_workers}：本启动器始终单进程，该参数不会转发给 uvicorn。"
            "本应用的模拟持仓/风控/限流/缓存状态均在进程内，若改用 "
            f"`uvicorn web.api:app --workers {_workers}` 会让状态各持一份"
            "（持仓消失、限流额度翻倍、风控错乱）；确需多进程请先把这些状态外置到 SQLite。",
            file=sys.stderr,
        )

    uvicorn.run(
        "web.api:app",
        host=_parse_arg("--host", "0.0.0.0"),
        port=_parse_arg("--port", 8000, int),
        reload="--reload" in sys.argv,
        log_level="info",
    )

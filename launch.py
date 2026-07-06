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
    uvicorn.run(
        "web.api:app",
        host=_parse_arg("--host", "0.0.0.0"),
        port=_parse_arg("--port", 8000, int),
        reload="--reload" in sys.argv,
        log_level="info",
    )

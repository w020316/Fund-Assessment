#!/bin/bash
set -e

echo "========================================"
echo "  Fund-Assessment 一键启动脚本"
echo "========================================"

PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_CMD="$cmd"
        echo "[OK] 找到 Python: $($cmd --version)"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] 未找到 Python，请先安装 Python 3.10+"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "[1/3] 安装依赖..."
$PYTHON_CMD -m pip install -r requirements.txt --quiet
echo "[OK] 依赖安装完成"

echo ""
echo "[2/3] 启动 FastAPI 服务..."
if [ -f .env ]; then
    set -a
    source .env
    set +a
    echo "[OK] 已加载 .env 配置"
fi

$PYTHON_CMD -m uvicorn web.api:app --host 0.0.0.0 --port 8000 --reload &
SERVER_PID=$!

sleep 3

echo ""
echo "[3/3] 打开浏览器..."
if command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:8000
elif command -v open &>/dev/null; then
    open http://localhost:8000
fi

echo ""
echo "========================================"
echo "  服务已启动: http://localhost:8000"
echo "  按 Ctrl+C 停止服务"
echo "========================================"

wait $SERVER_PID

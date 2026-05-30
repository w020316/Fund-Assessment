#!/bin/bash
# OpenClaw 量化 AI 炒股机器人 - 控制脚本

WORKSPACE=$(cd "$(dirname "$0")" && pwd)
LOG_DIR="$WORKSPACE/data/logs"
PID_DIR="$WORKSPACE/data"
PYTHON=python3

mkdir -p "$LOG_DIR"

start() {
    echo "启动 OpenClaw 量化交易系统..."
    nohup $PYTHON "$WORKSPACE/scripts/quant.py" market_anomaly > "$LOG_DIR/quant.log" 2>&1 &
    echo $! > "$PID_DIR/quant.pid"
    echo "量化分析模块已启动 [PID: $(cat "$PID_DIR/quant.pid")]"

    nohup $PYTHON "$WORKSPACE/scripts/cb_monitor.py" --continuous 30 > "$LOG_DIR/cb_monitor.log" 2>&1 &
    echo $! > "$PID_DIR/cb_monitor.pid"
    echo "可转债监控已启动 [PID: $(cat "$PID_DIR/cb_monitor.pid")]"

    nohup $PYTHON "$WORKSPACE/scripts/limit_up_monitor.py" > "$LOG_DIR/limit_up.log" 2>&1 &
    echo $! > "$PID_DIR/limit_up.pid"
    echo "涨停板监控已启动 [PID: $(cat "$PID_DIR/limit_up.pid")]"

    echo "所有模块已启动"
}

stop() {
    echo "停止 OpenClaw 量化交易系统..."
    for pid_file in "$PID_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            name=$(basename "$pid_file" .pid)
            kill "$pid" 2>/dev/null && echo "$name 已停止 [PID: $pid]" || echo "$name 未运行"
            rm -f "$pid_file"
        fi
    done
    echo "所有模块已停止"
}

status() {
    echo "OpenClaw 量化交易系统状态:"
    for pid_file in "$PID_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            name=$(basename "$pid_file" .pid)
            if kill -0 "$pid" 2>/dev/null; then
                echo "  $name: 运行中 [PID: $pid]"
            else
                echo "  $name: 已停止"
            fi
        fi
    done
}

log() {
    local module=${1:-quant}
    tail -f "$LOG_DIR/${module}.log"
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 2; start ;;
    status)  status ;;
    log)     log "$2" ;;
    *)       echo "用法: $0 {start|stop|restart|status|log [module]}" ;;
esac

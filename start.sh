#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="${PYTHONPATH:-$SCRIPT_DIR}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec uvicorn web.api:app --host "$HOST" --port "$PORT"

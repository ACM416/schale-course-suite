#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

PY_BIN="$APP_DIR/.venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN="$(command -v python3)"

mkdir -p "$APP_DIR/data"

if [ "${1:-}" = "daemon" ]; then
  shift
  LOG_FILE="$APP_DIR/data/suite.log"
  PID_FILE="$APP_DIR/data/suite.pid"
  nohup "$PY_BIN" "$APP_DIR/server.py" "$@" >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "[OK] 已在后台启动 (PID: $(cat "$PID_FILE"))"
  echo "  UOOC   (圣园未花):   http://<你的IP>:51088"
  echo "  智慧树 (小绿&小桃):  http://<你的IP>:51089"
  echo "  学习通 (阿罗娜):     http://<你的IP>:51092"
  echo "  查看日志: tail -f $LOG_FILE"
elif [ "${1:-}" = "stop" ]; then
  PID_FILE="$APP_DIR/data/suite.pid"
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "[OK] 已停止后台服务"
  else
    pkill -f "$APP_DIR/server.py" 2>/dev/null || true
    echo "[OK] 已发送停止信号"
  fi
else
  exec "$PY_BIN" "$APP_DIR/server.py" "$@"
fi

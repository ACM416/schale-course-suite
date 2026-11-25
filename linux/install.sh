#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

echo "============================================================"
echo "  S.C.H.A.L.E. Course Suite — Linux Installer"
echo "  Install Directory: $APP_DIR"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/3] 正在创建独立虚拟环境 .venv ..."
  python3 -m venv .venv || {
    echo "[WARN] venv 模块不可用，尝试直接安装到用户 Python 环境..."
    python3 -m pip install --upgrade pip
    python3 -m pip install -r requirements.txt
  }
fi

if [ -x ".venv/bin/python" ]; then
  echo "[2/3] 正在安装依赖..."
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt
  PY_BIN="$APP_DIR/.venv/bin/python"
else
  PY_BIN="$(command -v python3)"
fi

mkdir -p "$APP_DIR/data"
chmod +x "$APP_DIR/linux/start.sh" "$APP_DIR/server.py"

echo "[3/3] 安装完成！"
echo ""
echo "启动方式："
echo "  前台运行: ./linux/start.sh"
echo "  后台运行: ./linux/start.sh daemon"
echo ""

if [ "$(id -u)" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
  read -r -p "检测到 root 与 systemd，是否立即注册为开机自启系统服务？[y/N] " ans || ans="n"
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    sed \
      -e "s|__APP_DIR__|$APP_DIR|g" \
      -e "s|__PY_BIN__|$PY_BIN|g" \
      "$APP_DIR/linux/schale-course-suite.service" > /etc/systemd/system/schale-course-suite.service
    systemctl daemon-reload
    systemctl enable --now schale-course-suite.service
    systemctl status schale-course-suite.service --no-pager -n 10
  fi
fi

@echo off
chcp 65001 >nul
title S.C.H.A.L.E. Course Suite - Windows Launcher
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未检测到 Python，请先安装 Python 3.10+ 或直接下载编译好的 Windows .exe 版本！
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] 首次运行，正在创建虚拟环境 .venv 并安装依赖...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\pip.exe install -r requirements.txt
)

echo [2/2] 正在启动 S.C.H.A.L.E. 桌面控制面板...
start "" ".venv\Scripts\pythonw.exe" win_gui.py

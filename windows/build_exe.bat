@echo off
chcp 65001 >nul
title Build S.C.H.A.L.E. Course Suite Windows EXE
cd /d "%~dp0.."

if not exist ".build_venv\Scripts\python.exe" (
    echo [1/3] Creating build virtual environment...
    python -m venv .build_venv
)

echo [2/3] Installing runtime dependencies + PyInstaller...
.build_venv\Scripts\python.exe -m pip install --upgrade pip
.build_venv\Scripts\pip.exe install -r requirements.txt pyinstaller

echo [3/3] Packaging standalone SCHALE-Course-Suite.exe ...
.build_venv\Scripts\pyinstaller.exe ^
    --noconfirm --clean --onefile --windowed ^
    --name "SCHALE-Course-Suite" ^
    --add-data "platforms\uooc\templates;platforms\uooc\templates" ^
    --add-data "platforms\uooc\static;platforms\uooc\static" ^
    --add-data "platforms\zhs\templates;platforms\zhs\templates" ^
    --add-data "platforms\zhs\static;platforms\zhs\static" ^
    --add-data "platforms\cx\templates;platforms\cx\templates" ^
    --add-data "platforms\cx\static;platforms\cx\static" ^
    --paths "platforms\zhs" ^
    --hidden-import "platforms.uooc.uooc_app" ^
    --hidden-import "platforms.zhs.zhs_app" ^
    --hidden-import "platforms.cx.cx_app" ^
    --hidden-import "fucker" ^
    --hidden-import "zd_utils" ^
    --hidden-import "sign" ^
    --hidden-import "ObjDict" ^
    --hidden-import "utils" ^
    --hidden-import "logger" ^
    --hidden-import "push" ^
    --hidden-import "Crypto.Cipher.AES" ^
    --hidden-import "PIL.Image" ^
    --hidden-import "bs4" ^
    win_gui.py

if errorlevel 1 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Output binary: %CD%\dist\SCHALE-Course-Suite.exe
pause

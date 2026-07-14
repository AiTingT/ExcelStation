@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
REM ============================================================
REM   Excel 智能助手 - 启动脚本（Windows）
REM   使用：双击 start.bat 运行
REM ============================================================
cd /d "%~dp0"

echo ============================================
echo   Excel 智能助手
echo ============================================
echo.

REM ---------- 查找 Python 3 ----------
set PYTHON=
for %%c in (python python3) do (
    if not defined PYTHON (
        where %%c >nul 2>&1
        if !ERRORLEVEL!==0 (
            for /f "tokens=*" %%v in ('%%c -c "import sys; print(sys.version_info.major)" 2^>nul') do set VER=%%v
            if "!VER!"=="3" set PYTHON=%%c
        )
    )
)
if not defined PYTHON (
    echo.
    echo   [!] 未找到 Python 3
    echo       请先安装：https://www.python.org/downloads/
    echo       安装时请勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PYTHON% --version 2^>^&1') do echo   Python: %%v

REM ---------- 虚拟环境 ----------
if not exist "venv\Scripts\python.exe" (
    echo   ^> 首次运行，创建虚拟环境...
    %PYTHON% -m venv venv
)
call venv\Scripts\activate.bat

REM ---------- 依赖 ----------
if not exist "venv\.deps_ok" (
    echo   ^> 安装依赖（仅首次，请稍候）...
    pip install -q -r requirements.txt
    echo. > venv\.deps_ok
)

REM ---------- 启动 ----------
echo.
python main.py

echo.
pause

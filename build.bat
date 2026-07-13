@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM   Excel 轻量工作站 - 打包脚本（Windows）
REM   打包完成后在 dist\ 目录生成 ExcelStation.exe
REM ============================================================
cd /d "%~dp0"

echo ============================================
echo   Excel 轻量工作站 - 打包
echo ============================================
echo.

REM ---------- 查找 Python ----------
set PYTHON=
where python >nul 2>&1 && set PYTHON=python
if not defined PYTHON (
    where python3 >nul 2>&1 && set PYTHON=python3
)
if not defined PYTHON (
    echo   [!] 未找到 Python 3
    echo       请先安装：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM ---------- 环境 ----------
if not exist "venv\Scripts\python.exe" (
    echo   ^> 创建虚拟环境...
    %PYTHON% -m venv venv
)
call venv\Scripts\activate.bat

echo   ^> 安装依赖...
pip install -q -r requirements.txt pyinstaller

REM ---------- 打包 ----------
echo.
echo   ^> 开始打包（可能需要几分钟）...
echo.
pyinstaller excel_station.spec --clean --noconfirm

echo.
echo ============================================
if exist "dist\ExcelStation.exe" (
    echo   [OK] 打包成功！
    echo   可执行文件: dist\ExcelStation.exe
    echo   运行方式: 双击 dist\ExcelStation.exe
    echo.
    echo   分发方法: 将 dist\ExcelStation.exe 整个文件发给对方即可
    echo             对方无需安装 Python，直接双击运行
) else (
    echo   [!] 打包失败，请检查上方错误信息
)
echo ============================================
echo.
pause

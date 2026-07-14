#!/bin/bash
# ============================================================
#  Excel 智能助手 - 启动脚本（macOS / Linux）
#  使用：双击运行，或在终端执行 ./start.sh
# ============================================================
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Excel 智能助手"
echo "============================================"
echo ""

# ---------- 查找 Python 3 ----------
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        if [ "$ver" = "3" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python 3"
    echo "   请先安装：https://www.python.org/downloads/"
    echo ""
    read -p "按回车键退出..."
    exit 1
fi
echo "  Python: $($PYTHON --version)"

# ---------- 虚拟环境 ----------
if [ ! -d "venv" ]; then
    echo "  → 首次运行，创建虚拟环境..."
    $PYTHON -m venv venv
fi
source venv/bin/activate

# ---------- 依赖 ----------
if [ ! -f "venv/.deps_ok" ]; then
    echo "  → 安装依赖（仅首次，请稍候）..."
    pip install -q -r requirements.txt
    touch venv/.deps_ok
fi

# ---------- 启动 ----------
echo ""
python main.py

echo ""
read -p "按回车键退出..."

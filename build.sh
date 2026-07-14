#!/bin/bash
# ============================================================
#  Excel 智能助手 - 打包脚本（macOS / Linux）
#  打包完成后在 dist/ 目录生成可执行文件
# ============================================================
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Excel 智能助手 - 打包"
echo "============================================"
echo ""

# ---------- 查找 Python 3 ----------
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        if [ "$ver" = "3" ]; then PYTHON="$cmd"; break; fi
    fi
done
if [ -z "$PYTHON" ]; then echo "❌ 未找到 Python 3"; exit 1; fi

# ---------- 环境 ----------
[ ! -d "venv" ] && $PYTHON -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt pyinstaller

# ---------- 打包 ----------
echo "→ 开始打包（可能需要几分钟）..."
echo ""
pyinstaller excel_station.spec --clean --noconfirm

echo ""
echo "============================================"
if [ -f "dist/ExcelStation" ]; then
    SIZE=$(du -sh dist/ExcelStation | cut -f1)
    echo "  ✅ 打包成功！"
    echo "  可执行文件: dist/ExcelStation（$SIZE）"
    echo "  运行方式: 双击 dist/ExcelStation 或 ./dist/ExcelStation"
    echo ""
    echo "  分发方法: 将 dist/ExcelStation 整个文件发给对方即可"
    echo "            对方无需安装 Python，直接双击运行"
else
    echo "  ❌ 打包失败，请检查上方错误信息"
fi
echo "============================================"

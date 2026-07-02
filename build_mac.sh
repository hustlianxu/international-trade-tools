#!/usr/bin/env bash
# ═══════ macOS 打包脚本 ═══════
# 在 MacBook 上运行此脚本生成 .app
# 用法:
#   bash build_mac.sh              # 精简版（~80M，用云端 ASR）
#   INCLUDE_MLX=1 bash build_mac.sh  # 完整版（~600M，含本地 MLX Whisper）
set -e

echo "=== 外贸助手 macOS 打包 ==="

# ─── 检查 Python（支持 python3 / python3.11 / python3.12）───
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &> /dev/null; then
        PYTHON=$cmd
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "错误: 未找到 python3（需 3.10+），请先安装"
    echo "  推荐 python.org 官方安装包（自带 tkinter）:"
    echo "    https://www.python.org/downloads/macos/"
    exit 1
fi
echo "Python: $($PYTHON --version)"

# ─── 关键检查：tkinter ───
# Mac 上 Homebrew/pyenv 安装的 Python 经常不带 tkinter，会导致打包后 .app 闪退
echo ""
echo "检查 tkinter..."
if ! $PYTHON -c "import tkinter" 2>/dev/null; then
    echo "═══════════════════════════════════════════════════════════"
    echo "错误: 当前 Python 缺少 tkinter 模块！"
    echo "  打包后的 .app 会闪退（报 ModuleNotFoundError: No module named 'tkinter'）"
    echo ""
    echo "原因: Homebrew / pyenv 安装的 Python 默认不带 tkinter"
    echo ""
    echo "解决方案（任选其一）:"
    echo "  方案A（推荐，最简单）: 安装 python.org 官方 Python"
    echo "    1. 访问 https://www.python.org/downloads/macos/"
    echo "    2. 下载 Python 3.11 或 3.12 的 macOS 64-bit universal2 installer"
    echo "    3. 安装后用 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
    echo "    4. 重新运行本脚本"
    echo ""
    echo "  方案B: Homebrew 用户安装 tcl-tk 并重装 python"
    echo "    brew install tcl-tk"
    echo "    brew uninstall python@3.11"
    echo "    brew install python-tk@3.11"
    echo "    brew install python@3.11"
    echo ""
    echo "  方案C: pyenv 用户编译时带 tcl-tk"
    echo "    brew install tcl-tk"
    echo "    env PYTHON_CONFIGURE_OPTS=\"--with-tcl-tk\" pyenv install 3.11.9"
    echo "═══════════════════════════════════════════════════════════"
    exit 1
fi
echo "  ✓ tkinter 可用"

# 检查架构
ARCH=$(uname -m)
echo "系统架构: $ARCH"

# 构建模式
if [ "$INCLUDE_MLX" = "1" ]; then
    echo "构建模式: 完整版（含本地 MLX Whisper，体积约 600M）"
    BUILD_MODE="full"
else
    echo "构建模式: 精简版（云端 ASR，体积约 80M）"
    echo "  如需本地 MLX Whisper（免费），运行: INCLUDE_MLX=1 bash build_mac.sh"
    BUILD_MODE="lite"
fi
export BUILD_MODE

# 清理旧的构建产物
rm -rf .venv build dist *.spec.bak

# ─── 生成应用图标 ───
echo ""
echo "0. 生成应用图标..."
if [ ! -f assets/icon.png ]; then
    $PYTHON tools/make_icon.py assets/icon.png
fi
# 用 macOS 自带工具把 PNG 转成 .icns
if [ ! -f assets/icon.icns ]; then
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "x86_64" ]; then
        rm -rf assets/icon.iconset
        mkdir -p assets/icon.iconset
        for size in 16 32 128 256 512; do
            sips -z $size $size assets/icon.png --out assets/icon.iconset/icon_${size}x${size}.png > /dev/null 2>&1
        done
        sips -z 32 32 assets/icon.png --out assets/icon.iconset/icon_16x16@2x.png > /dev/null 2>&1
        sips -z 64 64 assets/icon.png --out assets/icon.iconset/icon_32x32@2x.png > /dev/null 2>&1
        sips -z 256 256 assets/icon.png --out assets/icon.iconset/icon_128x128@2x.png > /dev/null 2>&1
        sips -z 512 512 assets/icon.png --out assets/icon.iconset/icon_256x256@2x.png > /dev/null 2>&1
        cp assets/icon.png assets/icon.iconset/icon_512x512@2x.png
        iconutil -c icns assets/icon.iconset -o assets/icon.icns 2>/dev/null && echo "  ✓ icon.icns 生成成功" || echo "  ⚠ iconutil 失败，将使用默认图标"
        rm -rf assets/icon.iconset
    else
        echo "  ⚠ 非 macOS，跳过 .icns 生成"
    fi
fi

# 创建虚拟环境
echo ""
echo "1. 创建虚拟环境..."
$PYTHON -m venv .venv
source .venv/bin/activate
echo "   venv Python: $(which python)"

# 安装依赖
echo ""
echo "2. 安装基础依赖..."
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install pyinstaller

# MLX Whisper（仅完整版）
if [ "$BUILD_MODE" = "full" ] && [ "$ARCH" = "arm64" ]; then
    echo ""
    echo "3. 安装 MLX Whisper (Apple Silicon)..."
    pip install mlx-whisper
    echo "   MLX Whisper 安装完成（首次转写会自动下载模型，约 1.5GB）"
else
    echo ""
    echo "3. 跳过 MLX Whisper（精简模式 / Intel Mac）"
    echo "   精简模式使用云端 ASR（火山豆包/OpenAI），功能完整"
fi

# 验证关键依赖
echo ""
echo "4. 验证依赖..."
python -c "import tkinter; print('   ✓ tkinter 可用')" 2>/dev/null || echo "   ✗ tkinter 不可用（打包后必崩！）"
python -c "import pysilk; print('   ✓ pysilk-mod 可用')" 2>/dev/null || echo "   ✗ pysilk-mod 未安装（语音转写将不可用）"
python -c "import zstandard; print('   ✓ zstandard 可用')" 2>/dev/null || echo "   ✗ zstandard 未安装"
python -c "from Crypto.Cipher import AES; print('   ✓ pycryptodome 可用')" 2>/dev/null || echo "   ✗ pycryptodome 未安装"
if [ "$BUILD_MODE" = "full" ] && [ "$ARCH" = "arm64" ]; then
    python -c "import mlx_whisper; print('   ✓ mlx_whisper 可用')" 2>/dev/null || echo "   ✗ mlx_whisper 未安装"
fi

# 打包
echo ""
echo "5. 开始打包（可能需要 2-5 分钟）..."
pyinstaller trade-tools.spec --noconfirm --clean 2>&1 | tail -20

# 验证产物
echo ""
echo "=== 打包完成 ==="
if [ -d "dist/外贸助手.app" ]; then
    APP_SIZE=$(du -sh "dist/外贸助手.app" | cut -f1)
    echo "应用位置: dist/外贸助手.app"
    echo "应用大小: $APP_SIZE"
    echo ""
    echo "运行方式:"
    echo "  1. Finder 中双击 dist/外贸助手.app"
    echo "  2. 首次运行如提示\"已损坏/无法验证\"，右键 → 打开"
    echo ""
    echo "调试（如闪退）:"
    echo "  终端运行: dist/外贸助手.app/Contents/MacOS/TradeTools"
    echo "  可直接看到错误信息"
    echo ""
    echo "分发给其他 Mac:"
    echo "  cd dist && zip -ry 外贸助手.zip 外贸助手.app"
else
    echo "⚠ 未找到 dist/外贸助手.app，请检查上方打包日志"
    exit 1
fi

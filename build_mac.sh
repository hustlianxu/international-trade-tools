#!/usr/bin/env bash
# ═══════ macOS 打包脚本 ═══════
# 在 MacBook 上运行此脚本生成 .app
# 用法: bash build_mac.sh
#
# 本脚本会:
# 1. 创建独立虚拟环境（不污染系统 Python）
# 2. 安装依赖（pysilk-mod 替代 pilk，因为 pilk 仅 Windows）
# 3. Mac M3 额外安装 mlx-whisper
# 4. PyInstaller 打包为 .app
set -e

echo "=== 外贸助手 macOS 打包 ==="

# 检查 Python（支持 python3 / python3.11 / python3.12）
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &> /dev/null; then
        PYTHON=$cmd
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "错误: 未找到 python3（需 3.10+），请先安装"
    echo "  brew install python@3.11"
    exit 1
fi
echo "Python: $($PYTHON --version)"

# 检查架构
ARCH=$(uname -m)
echo "系统架构: $ARCH"

# 清理旧的构建产物
rm -rf .venv build dist *.spec.bak

# 创建虚拟环境
echo ""
echo "1. 创建虚拟环境..."
$PYTHON -m venv .venv

# 激活虚拟环境（后续所有 pip/python 都用 venv 内的）
source .venv/bin/activate
# 确认用的是 venv 的 python
echo "   venv Python: $(which python)"

# 安装依赖
echo ""
echo "2. 安装基础依赖..."
python -m pip install --upgrade pip setuptools wheel
# requirements.txt 已用环境标记区分平台：Mac 上 pilk 会跳过，pysilk-mod 会安装
pip install -r requirements.txt
pip install pyinstaller

# Mac M3 额外安装 MLX Whisper（Intel Mac 跳过）
if [ "$ARCH" = "arm64" ]; then
    echo ""
    echo "3. 安装 MLX Whisper (Apple Silicon)..."
    pip install mlx-whisper
    echo "   MLX Whisper 安装完成（首次转写会自动下载模型，约 1.5GB）"
else
    echo ""
    echo "3. 跳过 MLX Whisper (Intel Mac，请在配置中选择火山豆包引擎)"
fi

# 验证关键依赖
echo ""
echo "4. 验证依赖..."
python -c "import pysilk; print('   ✓ pysilk-mod 可用')" 2>/dev/null || echo "   ✗ pysilk-mod 未安装（语音转写将不可用）"
python -c "import zstandard; print('   ✓ zstandard 可用')" 2>/dev/null || echo "   ✗ zstandard 未安装"
python -c "import pycryptodome; print('   ✓ pycryptodome 可用')" 2>/dev/null || \
    python -c "from Crypto.Cipher import AES; print('   ✓ pycryptodome 可用')" 2>/dev/null || echo "   ✗ pycryptodome 未安装"
if [ "$ARCH" = "arm64" ]; then
    python -c "import mlx_whisper; print('   ✓ mlx_whisper 可用')" 2>/dev/null || echo "   ✗ mlx_whisper 未安装"
fi

# 打包
echo ""
echo "5. 开始打包（可能需要 3-5 分钟）..."
pyinstaller trade-tools.spec --noconfirm --clean 2>&1 | tail -20

# 验证产物
echo ""
echo "=== 打包完成 ==="
if [ -d "dist/外贸助手.app" ]; then
    echo "应用位置: dist/外贸助手.app"
    APP_SIZE=$(du -sh "dist/外贸助手.app" | cut -f1)
    echo "应用大小: $APP_SIZE"
    echo ""
    echo "运行方式:"
    echo "  1. Finder 中双击 dist/外贸助手.app"
    echo "  2. 首次运行如提示\"已损坏/无法验证\"，右键 → 打开"
    echo "  3. 或终端: open dist/外贸助手.app"
    echo ""
    echo "分发给其他 Mac:"
    echo "  cd dist && zip -ry 外贸助手.zip 外贸助手.app"
else
    echo "⚠ 未找到 dist/外贸助手.app，请检查上方打包日志"
    exit 1
fi

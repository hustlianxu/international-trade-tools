#!/usr/bin/env bash
# ═══════ macOS 打包脚本 ═══════
# 在 MacBook 上运行此脚本生成 .app
# 用法: bash build_mac.sh
set -e

echo "=== 外贸助手 macOS 打包 ==="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

# 检查架构
ARCH=$(uname -m)
echo "系统架构: $ARCH"

# 创建虚拟环境
echo ""
echo "1. 创建虚拟环境..."
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
echo ""
echo "2. 安装依赖..."
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

# Mac M3 额外安装 MLX Whisper
if [ "$ARCH" = "arm64" ]; then
    echo ""
    echo "3. 安装 MLX Whisper (Apple Silicon)..."
    pip install mlx-whisper
else
    echo ""
    echo "3. 跳过 MLX Whisper (Intel Mac，请使用火山豆包引擎)"
fi

# 打包
echo ""
echo "4. 开始打包..."
pyinstaller trade-tools.spec --noconfirm --clean

echo ""
echo "=== 打包完成 ==="
echo "应用位置: dist/外贸助手.app"
echo ""
echo "首次运行可能需要右键 -> 打开（绕过 Gatekeeper）"
echo ""
echo "如需分发给其他 Mac，建议压缩:"
echo "  cd dist && zip -r 外贸助手.zip 外贸助手.app"

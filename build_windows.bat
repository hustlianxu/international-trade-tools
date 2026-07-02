@echo off
chcp 65001 >nul
REM ═══════ Windows 打包脚本 ═══════
REM 在 Windows 上双击或命令行运行此脚本生成 exe 目录
REM 用法: build_windows.bat

echo === 外贸助手 Windows 打包 ===

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 python，请先安装 Python 3.10+
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 创建虚拟环境
echo.
echo 1. 创建虚拟环境...
python -m venv .venv
call .venv\Scripts\activate

REM 安装依赖
echo.
echo 2. 安装依赖...
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

REM Windows 不装 mlx-whisper（仅 Mac M3 支持）
echo.
echo 3. 跳过 MLX Whisper（仅 Mac M3 支持，Windows 使用火山豆包引擎）

REM 打包
echo.
echo 4. 开始打包...
pyinstaller trade-tools.spec --noconfirm --clean

echo.
echo === 打包完成 ===
echo 程序位置: dist\TradeTools\TradeTools.exe
echo.
echo 如需分发给其他 Windows，压缩整个 dist\TradeTools 目录
echo.
pause

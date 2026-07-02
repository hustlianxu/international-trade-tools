@echo off
chcp 65001 >nul
REM ═══════ Windows 打包脚本 ═══════
REM 在 Windows 上双击或命令行运行此脚本生成 exe 目录
REM 用法: build_windows.bat
setlocal enabledelayedexpansion

echo === 外贸助手 Windows 打包 ===

REM 检查 Python（支持 py / python / python3）
set PYTHON=
for %%c in (py python python3) do (
    where %%c >nul 2>&1
    if !errorlevel! equ 0 (
        set PYTHON=%%c
        goto :found_python
    )
)
echo 错误: 未找到 python，请先安装 Python 3.10+
echo 下载: https://www.python.org/downloads/
pause
exit /b 1

:found_python
echo Python:
%PYTHON% --version

REM 检查 tkinter（Microsoft Store 版 Python 可能不带，会导致打包后闪退）
echo.
echo 检查 tkinter...
%PYTHON% -c "import tkinter" 2>nul
if errorlevel 1 (
    echo 错误: 当前 Python 缺少 tkinter 模块！打包后会闪退
    echo 解决方案: 安装 python.org 官方 Python（自带 tkinter）
    echo   https://www.python.org/downloads/windows/
    echo 不要用 Microsoft Store 版本的 Python
    pause
    exit /b 1
)
echo    OK: tkinter 可用

REM 清理旧构建产物
if exist .venv rmdir /s /q .venv
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM 创建虚拟环境
echo.
echo 1. 创建虚拟环境...
%PYTHON% -m venv .venv

REM 激活虚拟环境（后续用 venv 内的 python/pip）
call .venv\Scripts\activate
echo    venv Python: 
where python

REM 安装依赖
echo.
echo 2. 安装基础依赖...
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install pyinstaller

REM Windows 不装 mlx-whisper（仅 Mac M3 支持）
echo.
echo 3. 跳过 MLX Whisper（仅 Mac M3 支持，Windows 使用火山豆包引擎）

REM 验证关键依赖
echo.
echo 4. 验证依赖...
python -c "import pilk; print('    OK: pilk 可用')" 2>nul || echo "    WARN: pilk 未安装"
python -c "import zstandard; print('    OK: zstandard 可用')" 2>nul || echo "    WARN: zstandard 未安装"
python -c "from Crypto.Cipher import AES; print('    OK: pycryptodome 可用')" 2>nul || echo "    WARN: pycryptodome 未安装"
python -c "import pymem; print('    OK: pymem 可用')" 2>nul || echo "    WARN: pymem 未安装（微信密钥扫描需要）"

REM 打包
echo.
echo 5. 开始打包（可能需要 3-5 分钟）...
pyinstaller trade-tools.spec --noconfirm --clean

REM 验证产物
echo.
echo === 打包完成 ===
if exist "dist\TradeTools\TradeTools.exe" (
    echo 程序位置: dist\TradeTools\TradeTools.exe
    echo.
    echo 运行方式: 双击 dist\TradeTools\TradeTools.exe
    echo.
    echo 分发给其他 Windows: 压缩整个 dist\TradeTools 目录
) else (
    echo [警告] 未找到 dist\TradeTools\TradeTools.exe，请检查上方打包日志
)
echo.
pause

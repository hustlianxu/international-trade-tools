# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（跨平台）。

Mac:   bash build_mac.sh                    # 精简版（~80M，云端 ASR）
       INCLUDE_MLX=1 bash build_mac.sh      # 完整版（~600M，含本地 MLX Whisper）
Win:   build_windows.bat
"""
import os
import sys
from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(".").resolve()

# 数据文件：配置模板
datas = [
    ("src/config/config.example.yaml", "src/config"),
]
# 图标（如已生成）
icon_path = None
if Path("assets/icon.icns").exists():
    icon_path = "assets/icon.icns"
elif Path("assets/icon.ico").exists():
    icon_path = "assets/icon.ico"

# 隐藏导入（PyInstaller 无法自动检测的动态导入）
hiddenimports = [
    "yaml",
    "zstandard",
    "psutil",
    "openai",
    "apscheduler",
]
# SILK 后端：Windows 用 pilk，Mac/Linux 用 pysilk-mod
if sys.platform == "win32":
    hiddenimports += ["pilk", "pymem"]
else:
    hiddenimports += ["pysilk"]

# MLX Whisper：仅完整模式打包（精简模式不打包，体积从 600M 降到 80M）
# 用环境变量 BUILD_MODE 或 INCLUDE_MLX 控制
include_mlx = (
    sys.platform == "darwin"
    and (os.environ.get("INCLUDE_MLX") == "1" or os.environ.get("BUILD_MODE") == "full")
)
if include_mlx:
    hiddenimports += ["mlx_whisper", "mlx", "numpy", "scipy", "transformers", "tokenizers"]
    print("[spec] 完整模式：打包 MLX Whisper（体积约 600M）")
else:
    print("[spec] 精简模式：不含 MLX Whisper（体积约 80M，用云端 ASR）")

# 排除不需要的大模块（减小体积）
excludes = [
    "matplotlib",
    "pandas",
    "PIL",
    "tkinter.test",
    "tkinter.tix",
    "test",
    "unittest",
    "pydoc_data",
    "distutils",
    "lib2to3",
]
# 精简模式额外排除 MLX 相关大依赖
if not include_mlx:
    excludes += ["mlx", "mlx_whisper", "mlx_lm", "transformers", "tokenizers", "torch", "tensorflow"]
# Windows 不需要的 Mac 专用
if sys.platform == "win32":
    excludes += ["mlx", "mlx_whisper"]

a = Analysis(
    ["src/gui_app.py"],
    pathex=[str(PROJECT_ROOT)],  # 项目根，确保 from src.xxx import 可解析
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TradeTools",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 模式，不显示控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TradeTools",
)

# Mac 上额外生成 .app 包
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="外贸助手.app",
        icon=icon_path,
        bundle_identifier="com.tradetools.app",
        info_plist={
            "CFBundleName": "外贸助手",
            "CFBundleDisplayName": "外贸助手",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSMicrophoneUsageDescription": "外贸助手需要访问麦克风用于语音转写",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )

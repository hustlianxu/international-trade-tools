# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（跨平台）。

Mac:   pyinstaller trade-tools.spec
Win:   pyinstaller trade-tools.spec
生成 dist/TradeTools/ 目录（--onedir 模式，启动快）
"""
import sys
from pathlib import Path

block_cipher = None

# 数据文件：配置模板
datas = [
    ("src/config/config.example.yaml", "src/config"),
]

# 隐藏导入（PyInstaller 无法自动检测的动态导入）
hiddenimports = [
    "yaml",
    "zstandard",
    "pilk",
    "pymem",
    "psutil",
    "openai",
    "apscheduler",
]
# Mac 专用
if sys.platform == "darwin":
    hiddenimports += ["mlx_whisper", "mlx"]
# Windows 专用
if sys.platform == "win32":
    hiddenimports += ["pymem"]

a = Analysis(
    ["src/gui_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "PIL", "tkinter.test"],
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
    icon=None,  # 可放入 icon.ico / icon.icns
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
        icon=None,
        bundle_identifier="com.tradetools.app",
        info_plist={
            "CFBundleName": "外贸助手",
            "CFBundleDisplayName": "外贸助手",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSMicrophoneUsageDescription": "外贸助手需要访问麦克风用于语音转写",
            "LSMinimumSystemVersion": "11.0",
        },
    )

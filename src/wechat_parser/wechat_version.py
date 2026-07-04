"""微信版本检测（跨平台）。

微信 4.0.x 支持内存扫描提取密钥；
微信 4.1.x 内存扫描已失效，需 DLL 注入或加载 all_keys.json。

检测策略：
  Windows: 读取 WeChat.exe 文件版本资源 / 注册表
  macOS:   读取 WeChat.app/Contents/Info.plist 中的 CFBundleShortVersionString
  Linux:   读取 wechat 包信息
"""
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WeChatVersionInfo:
    """微信版本检测结果。"""
    found: bool = False
    version: str = ""           # 如 "4.0.9"
    supports_memory_scan: bool = True
    platform: str = ""          # win32 / darwin / linux


def _parse_version(version_str: str) -> tuple[int, ...]:
    """把 '4.0.9.26' 解析为 (4, 0, 9, 26)。"""
    parts = []
    for p in version_str.split("."):
        p = p.strip()
        if p.isdigit():
            parts.append(int(p))
    return tuple(parts) or (0,)


def _supports_memory_scan(version_str: str) -> bool:
    """4.0.x 支持内存扫描，4.1.x 及以上失效。"""
    v = _parse_version(version_str)
    if len(v) >= 2:
        return v[0] == 4 and v[1] == 0
    return True  # 未知版本默认支持


def _detect_macos() -> WeChatVersionInfo:
    """macOS: 读取 WeChat.app 的 Info.plist。"""
    info = WeChatVersionInfo(platform="darwin")
    # 微信 4.x: /Applications/WeChat.app
    # 微信 4.x 容器版: ~/Library/Containers/com.tencent.xinWeChat/...
    plist_paths = [
        Path("/Applications/WeChat.app/Contents/Info.plist"),
        Path.home() / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Info.plist",
    ]
    for plist in plist_paths:
        if not plist.exists():
            continue
        try:
            # 用 plutil 读取（不依赖 plistlib 的二进制格式问题）
            r = subprocess.run(
                ["plutil", "-extract", "CFBundleShortVersionString", "raw", str(plist)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                version = r.stdout.strip()
                info.found = True
                info.version = version
                info.supports_memory_scan = _supports_memory_scan(version)
                logger.info("[版本检测] macOS 微信 %s (内存扫描=%s)",
                            version, info.supports_memory_scan)
                return info
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("[版本检测] 读取 %s 失败: %s", plist, e)
    return info


def _detect_windows() -> WeChatVersionInfo:
    """Windows: 读取 WeChat.exe 版本（通过 wmic 或 PowerShell）。"""
    info = WeChatVersionInfo(platform="win32")
    try:
        # 优先从注册表读
        r = subprocess.run(
            ["reg", "query", r"HKCU\Software\Tencent\WeChat", "/v", "Version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "Version" in line and "REG_DWORD" in line:
                    # 0x0400091a → 4.0.9.26
                    val = line.split("0x")[-1].strip()
                    ver_int = int(val, 16)
                    version = f"{ver_int >> 24}.{(ver_int >> 16) & 0xFF}.{(ver_int >> 8) & 0xFF}.{ver_int & 0xFF}"
                    info.found = True
                    info.version = version
                    info.supports_memory_scan = _supports_memory_scan(version)
                    return info
    except (subprocess.SubprocessError, OSError):
        pass
    return info


def _detect_linux() -> WeChatVersionInfo:
    """Linux: 尝试从 wechat 进程或包信息检测。"""
    info = WeChatVersionInfo(platform="linux")
    return info


def detect_wechat_version() -> WeChatVersionInfo:
    """检测微信版本。

    Returns:
        WeChatVersionInfo
    """
    logger.info("[版本检测] 平台: %s", sys.platform)
    if sys.platform == "darwin":
        return _detect_macos()
    elif sys.platform == "win32":
        return _detect_windows()
    else:
        return _detect_linux()

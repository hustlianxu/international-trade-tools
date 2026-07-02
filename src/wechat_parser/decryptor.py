"""微信数据库解密模块。

基于 ylytdeng/wechat-decrypt 的解密原理实现，支持 PC 微信 4.0.x。
- 算法：SQLCipher 4（AES-256-CBC + HMAC-SHA512，PBKDF2 256000 次迭代）
- 密钥：扫描微信进程内存获取 raw key（ASCII: x'<64hex_enc_key><32hex_salt>'）

⚠️ 微信 4.1.x 内存扫描已失效，需用 wx_key DLL 注入方案（见 docs/03）。
"""
import hashlib
import hmac
import logging
import os
import sqlite3
import struct
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# SQLCipher 4 参数
SQLCIPHER_PAGE_SIZE = 4096
SQLCIPHER_KDF_ITER = 256000
SQLCIPHER_HMAC_ALGO = "sha512"
SQLCIPHER_RESERVED = 80  # IV(16) + HMAC(64)
SQLCIPHER_SALT_SIZE = 16


class WeChatDecryptor:
    def __init__(self, enc_key: bytes, salt: bytes):
        """初始化解密器。

        Args:
            enc_key: 32 字节加密密钥（从内存扫描获取）
            salt: 16 字节盐值（.db 文件前 16 字节）
        """
        if len(enc_key) != 32:
            raise ValueError(f"enc_key 必须为 32 字节，当前 {len(enc_key)}")
        if len(salt) != 16:
            raise ValueError(f"salt 必须为 16 字节，当前 {len(salt)}")
        self.enc_key = enc_key
        self.salt = salt
        # 预计算 HMAC 密钥（PBKDF2 派生）
        self.hmac_key = hashlib.pbkdf2_hmac(
            SQLCIPHER_HMAC_ALGO, enc_key, salt, 2, 32
        )

    @classmethod
    def from_raw_key_hex(cls, raw_key_hex: str, db_path: str | Path):
        """从内存扫描到的 raw key（96 字符 hex）创建解密器。

        raw_key 格式: <64 hex chars enc_key><32 hex chars salt>
        salt 也可从 db 文件前 16 字节读取（更可靠）。
        """
        hex_str = raw_key_hex.replace("x'", "").replace("'", "").strip()
        if len(hex_str) != 96:
            raise ValueError(f"raw key hex 应为 96 字符，当前 {len(hex_str)}")

        enc_key = bytes.fromhex(hex_str[:64])
        # salt 优先从 db 文件读取（避免内存中 salt 与文件不匹配）
        salt_from_file = Path(db_path).read_bytes()[:SQLCIPHER_SALT_SIZE]
        salt_from_key = bytes.fromhex(hex_str[64:96])

        # 校验：两者应一致
        if salt_from_file != salt_from_key:
            logger.warning("内存 salt 与文件 salt 不一致，采用文件 salt")
        return cls(enc_key, salt_from_file)

    def _decrypt_page(self, page_data: bytes, page_num: int) -> bytes:
        """解密单个数据库页（4096 字节）。"""
        from Crypto.Cipher import AES

        # 页结构: [加密数据][IV(16)][HMAC(64)]
        # 首页跳过前 16 字节 salt
        offset = SQLCIPHER_SALT_SIZE if page_num == 1 else 0
        encrypted = page_data[offset: SQLCIPHER_PAGE_SIZE - SQLCIPHER_RESERVED]
        iv = page_data[SQLCIPHER_PAGE_SIZE - SQLCIPHER_RESERVED:
                       SQLCIPHER_PAGE_SIZE - SQLCIPHER_RESERVED + 16]
        hmac_val = page_data[SQLCIPHER_PAGE_SIZE - SQLCIPHER_RESERVED + 16:
                             SQLCIPHER_PAGE_SIZE]

        # HMAC 校验（页号大端 1 字节 + 加密数据）
        hmac_data = encrypted + struct.pack(">I", page_num)
        expected_hmac = hmac.new(self.hmac_key, hmac_data, hashlib.sha512).digest()
        if not hmac.compare_digest(expected_hmac, hmac_val):
            raise ValueError(f"页 {page_num} HMAC 校验失败，密钥可能错误")

        # AES-256-CBC 解密
        cipher = AES.new(self.enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)

        # 第一页去掉 salt 后填充
        if page_num == 1:
            # 用 SQLite 头 "SQLite format 3\\x00" 填充原 salt 位置
            sqlite_header = b"SQLite format 3\x00"
            decrypted = sqlite_header + decrypted[len(sqlite_header):]

        # 补齐到页大小
        padding_needed = SQLCIPHER_PAGE_SIZE - len(decrypted) - SQLCIPHER_RESERVED
        if padding_needed > 0:
            decrypted += b"\x00" * padding_needed

        return decrypted

    def decrypt_db(self, db_path: str | Path, output_path: str | Path = None) -> str:
        """解密整个 .db 文件为明文 SQLite。

        Args:
            db_path: 加密的 .db 文件路径
            output_path: 输出路径，默认临时文件

        Returns:
            解密后的明文 db 文件路径
        """
        db_path = Path(db_path)
        if output_path is None:
            output_path = Path(tempfile.gettempdir()) / f"dec_{db_path.name}"
        output_path = Path(output_path)

        raw = db_path.read_bytes()
        total_pages = (len(raw) - SQLCIPHER_SALT_SIZE) // SQLCIPHER_PAGE_SIZE + 1

        with open(output_path, "wb") as f:
            for page_num in range(1, total_pages + 1):
                start = (page_num - 1) * SQLCIPHER_PAGE_SIZE
                page_data = raw[start: start + SQLCIPHER_PAGE_SIZE]
                if len(page_data) < SQLCIPHER_PAGE_SIZE:
                    page_data += b"\x00" * (SQLCIPHER_PAGE_SIZE - len(page_data))
                decrypted_page = self._decrypt_page(page_data, page_num)
                f.write(decrypted_page)

        logger.info("[解密] %s → %s (%d 页)", db_path.name, output_path.name, total_pages)
        return str(output_path)

    def open_sqlite(self, db_path: str | Path):
        """解密并打开为只读 SQLite 连接。"""
        plain_path = self.decrypt_db(db_path)
        conn = sqlite3.connect(f"file:{plain_path}?mode=ro", uri=True)
        return conn


def scan_wechat_key(process_name: str = None) -> str:
    """扫描微信进程内存获取 raw key（跨平台）。

    Windows 4.0.x: 用 pymem 扫描进程内存
    macOS: 用 lldb 附加微信进程提取密钥（需 sudo）
    其他/失败: 抛出异常，引导用户手动输入

    Returns:
        96 字符 hex 的 raw key（x'<64hex_key><32hex_salt>'）
    """
    import sys as _sys
    if _sys.platform == "win32":
        return _scan_wechat_key_windows(process_name or "WeChat.exe")
    elif _sys.platform == "darwin":
        return _scan_wechat_key_macos()
    else:
        raise RuntimeError(
            "当前系统不支持自动扫描微信密钥。\n"
            "请手动输入密钥（96 字符 hex），获取方式见使用手册。"
        )


def _scan_wechat_key_windows(process_name: str = "WeChat.exe") -> str:
    """Windows: 用 pymem 扫描微信进程内存（仅 4.0.x）。"""
    try:
        import pymem
    except ImportError:
        raise RuntimeError(
            "pymem 未安装（Windows 专用）。请运行: pip install pymem\n"
            "或手动输入微信密钥（96 字符 hex）。"
        )
    try:
        import psutil
    except ImportError:
        raise RuntimeError("psutil 未安装。请运行: pip install psutil")

    # 查找微信进程
    target_pid = None
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] and process_name.lower() in proc.info["name"].lower():
            target_pid = proc.info["pid"]
            break

    if not target_pid:
        raise RuntimeError(f"未找到微信进程: {process_name}，请确保微信已登录运行")

    logger.info("[密钥扫描] 进程 %s PID=%d", process_name, target_pid)

    # 扫描内存匹配 x'<96 hex>' 模式
    pm = pymem.Pymem(target_pid)
    pattern = b"x'"
    candidates = []

    for module in pm.list_modules():
        try:
            base = module.lpBaseOfDll
            size = module.SizeOfImage
            CHUNK = 0x100000  # 1MB
            for offset in range(0, size, CHUNK):
                read_size = min(CHUNK, size - offset)
                try:
                    data = pm.read_bytes(base + offset, read_size)
                except Exception:
                    continue
                pos = 0
                while True:
                    idx = data.find(pattern, pos)
                    if idx == -1:
                        break
                    candidate = data[idx: idx + 99]
                    if len(candidate) == 99 and candidate[98:99] == b"'":
                        hex_str = candidate[2:98]
                        try:
                            bytes.fromhex(hex_str.decode("ascii"))
                            candidates.append(hex_str.decode("ascii"))
                        except (ValueError, UnicodeDecodeError):
                            pass
                    pos = idx + 1
        except Exception as e:
            logger.debug("扫描模块 %s 失败: %s", module.name, e)

    if not candidates:
        raise RuntimeError(
            "未在内存中找到微信密钥。可能原因：\n"
            "1. 微信版本为 4.1.x，内存扫描已失效（需 wx_key DLL 注入）\n"
            "2. 微信刚启动未加载数据库（请先打开几个聊天）\n"
            "3. 权限不足（需管理员权限运行）\n"
            "4. 可手动输入密钥（96 字符 hex）"
        )

    logger.info("[密钥扫描] 找到 %d 个候选密钥", len(candidates))
    return candidates[0]


def _scan_wechat_key_macos() -> str:
    """macOS: 尝试用 lldb 附加微信进程提取密钥。

    需要 sudo 权限（lldb 附加进程需要）。
    若失败，抛出异常引导用户手动输入。
    """
    import subprocess
    import tempfile

    # lldb 脚本：附加微信进程，搜索内存中的密钥模式
    # 微信 4.x Mac 密钥在内存中以特定模式存在
    lldb_script = '''
import lldb
import re

def find_key(process):
    """在进程内存中搜索密钥。"""
    target = process.GetTarget()
    for module in target.module_iter():
        for section in module.section_iter():
            if not section.IsValid():
                continue
            data = section.GetSectionData()
            if not data.IsValid():
                continue
            size = data.GetByteSize()
            if size == 0 or size > 100 * 1024 * 1024:
                continue
            buf = bytearray(size)
            data.ReadRawData(0, buf, size)
            # 搜索 96 字符 hex 模式（密钥+盐）
            # 微信 4.x 密钥特征：32 字节密钥后跟 16 字节盐
            text = buf.decode('latin-1', errors='replace')
            # 匹配连续 96 个 hex 字符
            for m in re.finditer(r'[0-9a-fA-F]{96}', text):
                key_hex = m.group()
                # 验证：前 64 字符为密钥，后 32 字符为盐
                try:
                    bytes.fromhex(key_hex)
                    print("FOUND_KEY:" + key_hex)
                    return
                except Exception:
                    pass

process = lldb.target.process
if process and process.IsValid():
    find_key(process)
else:
    print("NO_PROCESS")
'''

    try:
        # 查找微信进程
        result = subprocess.run(
            ["pgrep", "-x", "微信"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["pgrep", "-f", "xinWeChat"],
                capture_output=True, text=True, timeout=5
            )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(
                "未找到微信进程。请确保微信已登录运行。\n"
                "若微信正在运行仍报此错，可手动输入密钥。"
            )
        pid = result.stdout.strip().split("\n")[0]

        # 用 lldb 附加（需要 sudo）
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(lldb_script)
            script_path = f.name

        try:
            result = subprocess.run(
                ["lldb", "-p", pid, "-o", f"command script import {script_path}",
                 "-o", "quit"],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout + result.stderr
            # 解析 FOUND_KEY:xxx
            for line in output.split("\n"):
                if line.startswith("FOUND_KEY:"):
                    key = line[len("FOUND_KEY:"):].strip()
                    logger.info("[密钥扫描] lldb 成功提取密钥")
                    return key
            raise RuntimeError(
                "lldb 未能从内存中提取密钥。\n"
                "可能原因：\n"
                "1. 需要 sudo 权限（终端运行: sudo 外贸助手）\n"
                "2. 微信版本不支持此方法\n"
                "3. 请手动输入密钥（96 字符 hex）"
            )
        finally:
            Path(script_path).unlink(missing_ok=True)
    except FileNotFoundError:
        raise RuntimeError(
            "未找到 lldb 工具。请安装 Xcode Command Line Tools:\n"
            "  xcode-select --install\n"
            "或手动输入密钥（96 字符 hex）"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("lldb 附加超时，请手动输入密钥")


def get_wechat_key_with_fallback(process_name: str = None, manual_key: str = None) -> str:
    """获取微信密钥，支持手动输入回退。

    优先自动扫描，失败时若提供了 manual_key 则使用手动值。
    """
    if manual_key and len(manual_key.strip()) == 96:
        return manual_key.strip()
    try:
        return scan_wechat_key(process_name)
    except RuntimeError as e:
        if manual_key and len(manual_key.strip()) == 96:
            return manual_key.strip()
        raise

"""微信数据库解密模块（多密钥版）。

实现原理对齐 ylytdeng/wechat-decrypt：
- 算法：SQLCipher 4（AES-256-CBC + HMAC-SHA512，PBKDF2 256000 次迭代）
- 微信 4.x 每个 .db 文件有独立的 enc_key + salt（不再是单一密钥）
- 密钥获取（macOS）：用 Mach VM API（task_for_pid + mach_vm_region + mach_vm_read）
  扫描微信进程 RW 内存区域，匹配 ASCII 字面量 ``x'<96hex>'``（64hex key + 32hex salt）
- 密钥↔DB 匹配：以每个 .db 前 16 字节（SQLCipher salt）作为关联键
- 三种密钥来源（按优先级）：
  1. 自动扫描（macOS 需 sudo + WeChat.app 已 ad-hoc 重签名）
  2. 加载 all_keys.json（wechat-decrypt 工具产出的批量密钥文件）
  3. 手动输入单个 96 字符 hex raw key（兼容旧版）

⚠️ 微信 4.1.x 内存扫描已失效，需用 wx_key DLL 注入方案（见 docs/03）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# SQLCipher 4 参数
SQLCIPHER_PAGE_SIZE = 4096
SQLCIPHER_KDF_ITER = 256000
SQLCIPHER_HMAC_ALGO = "sha512"
SQLCIPHER_RESERVED = 80  # IV(16) + HMAC(64)
SQLCIPHER_SALT_SIZE = 16
SQLITE_HEADER = b"SQLite format 3\x00"

# 内存扫描参数（对齐 wechat-decrypt find_all_keys_macos.c）
_MAX_KEYS = 256
_MAX_DBS = 256
_CHUNK_SIZE = 2 * 1024 * 1024  # 2MB
_HEX_PATTERN_LEN = 96  # 64 hex key + 32 hex salt


# ════════════════════════════════════════════════════════════════
#  密钥存储
# ════════════════════════════════════════════════════════════════
@dataclass
class KeyEntry:
    """单个 .db 的密钥条目。"""
    enc_key_hex: str            # 64 字符 hex（32 字节 AES 密钥）
    salt_hex: str               # 32 字符 hex（16 字节 salt）
    db_rel_path: str = ""       # 匹配到的 .db 相对路径（空=未匹配）

    @property
    def enc_key(self) -> bytes:
        return bytes.fromhex(self.enc_key_hex)

    @property
    def salt(self) -> bytes:
        return bytes.fromhex(self.salt_hex)


class WeChatKeyStore:
    """微信多密钥存储：以 salt 为关联键，把内存里扫到的密钥匹配到 .db 文件。

    与 wechat-decrypt 的 all_keys.json 完全兼容：
        {"message/message_0.db": {"enc_key": "64hex"}, ...}
    """

    def __init__(self, db_storage_path: str | Path):
        self.db_storage_path = Path(db_storage_path)
        # salt_hex(小写) -> KeyEntry
        self._by_salt: dict[str, KeyEntry] = {}
        # db_rel_path -> KeyEntry
        self._by_path: dict[str, KeyEntry] = {}

    # ─── 增删 ───
    def add_key(self, enc_key_hex: str, salt_hex: str, db_rel_path: str = "") -> KeyEntry:
        """添加一个密钥条目（自动去重）。"""
        enc_key_hex = enc_key_hex.lower().strip()
        salt_hex = salt_hex.lower().strip()
        if len(enc_key_hex) != 64 or len(salt_hex) != 32:
            raise ValueError(f"密钥格式错误：enc_key 应 64 hex，salt 应 32 hex；"
                             f"got {len(enc_key_hex)}/{len(salt_hex)}")
        # 触发 hex 校验
        bytes.fromhex(enc_key_hex)
        bytes.fromhex(salt_hex)

        entry = self._by_salt.get(salt_hex)
        if entry is None:
            entry = KeyEntry(enc_key_hex, salt_hex, db_rel_path)
            self._by_salt[salt_hex] = entry
        elif db_rel_path and not entry.db_rel_path:
            entry.db_rel_path = db_rel_path
        if db_rel_path:
            self._by_path[db_rel_path] = entry
        return entry

    def add_raw_key(self, raw_key_hex: str) -> KeyEntry:
        """从 96 字符 hex raw key 添加（前 64=enc_key，后 32=salt）。"""
        raw = raw_key_hex.replace("x'", "").replace("'", "").strip().lower()
        if len(raw) != 96:
            raise ValueError(f"raw key 应为 96 字符 hex，当前 {len(raw)}")
        return self.add_key(raw[:64], raw[64:96])

    # ─── 匹配 ───
    def match_to_dbs(self) -> int:
        """扫描 db_storage 目录下所有 .db，按 salt 匹配密钥。

        Returns:
            成功匹配的 .db 数量
        """
        if not self.db_storage_path.exists():
            logger.warning("[KeyStore] db_storage 不存在: %s", self.db_storage_path)
            return 0

        matched = 0
        for db_file in self._iter_db_files():
            try:
                salt = db_file.read_bytes()[:SQLCIPHER_SALT_SIZE]
            except OSError as e:
                logger.debug("[KeyStore] 读取 %s 失败: %s", db_file, e)
                continue
            if not salt or len(salt) < SQLCIPHER_SALT_SIZE:
                continue
            # 跳过已解密的 SQLite 文件
            if db_file.read_bytes()[:15] == b"SQLite format 3":
                continue
            salt_hex = salt.hex()
            entry = self._by_salt.get(salt_hex)
            if entry is None:
                continue
            rel = self._rel_path(db_file)
            entry.db_rel_path = rel
            self._by_path[rel] = entry
            matched += 1
            logger.debug("[KeyStore] 匹配 %s ↔ %s", rel, salt_hex[:8] + "…")
        logger.info("[KeyStore] 共 %d 个密钥，匹配到 %d 个 .db",
                    len(self._by_salt), matched)
        return matched

    def get_key_for_db(self, db_path: str | Path) -> Optional[KeyEntry]:
        """获取指定 .db 的密钥（先按 path 再按 salt 匹配）。"""
        db_path = Path(db_path)
        rel = self._rel_path(db_path)
        # 1) 按 path
        if rel in self._by_path:
            return self._by_path[rel]
        # 2) 按 salt
        try:
            salt = db_path.read_bytes()[:SQLCIPHER_SALT_SIZE]
        except OSError:
            return None
        return self._by_salt.get(salt.hex())

    def has_key_for_db(self, db_path: str | Path) -> bool:
        return self.get_key_for_db(db_path) is not None

    def stats(self) -> dict:
        return {
            "total_keys": len(self._by_salt),
            "matched_dbs": len(self._by_path),
            "db_storage": str(self.db_storage_path),
        }

    def to_all_keys_json(self) -> dict:
        """导出为 wechat-decrypt 兼容的 all_keys.json 格式。"""
        out = {}
        for rel, entry in sorted(self._by_path.items()):
            out[rel] = {"enc_key": entry.enc_key_hex}
        return out

    def save_all_keys_json(self, path: str | Path):
        """保存为 all_keys.json（兼容 wechat-decrypt）。"""
        Path(path).write_text(
            json.dumps(self.to_all_keys_json(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        logger.info("[KeyStore] 已保存 %d 个密钥到 %s", len(self._by_path), path)

    @classmethod
    def load_all_keys_json(cls, path: str | Path, db_storage_path: str | Path) -> "WeChatKeyStore":
        """加载 wechat-decrypt 产出的 all_keys.json。

        格式：{"rel/path.db": {"enc_key": "64hex"}, ...}
        salt 从对应 .db 文件读取（与 wechat-decrypt 一致，更可靠）。

        路径容错：Mac 微信检测可能返回 user_dir 而非 user_dir/db_storage，
        若 db_storage_path/rel 找不到文件，自动尝试 db_storage_path/db_storage/rel。
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        db_base = Path(db_storage_path)
        # 自动探测真实 db_storage 根：若 db_base 下无 .db 但 db_base/db_storage 有，则用后者
        if not any(db_base.rglob("*.db")):
            sub = db_base / "db_storage"
            if sub.exists() and any(sub.rglob("*.db")):
                db_base = sub
                logger.info("[KeyStore] 自动修正 db_storage 路径: %s", db_base)
        store = cls(db_base)
        for rel, info in data.items():
            if rel.startswith("_"):
                continue  # 跳过元数据字段
            enc_key = info.get("enc_key") if isinstance(info, dict) else info
            if not enc_key or len(enc_key) != 64:
                continue
            db_file = db_base / rel
            if not db_file.exists():
                logger.debug("[KeyStore] all_keys.json 引用的 db 不存在: %s", rel)
                continue
            try:
                salt = db_file.read_bytes()[:SQLCIPHER_SALT_SIZE]
            except OSError:
                continue
            store.add_key(enc_key, salt.hex(), db_rel_path=rel)
        # 交叉验证补全未直接匹配的 db
        store.match_to_dbs()
        logger.info("[KeyStore] 从 %s 加载 %d 个密钥", path, len(store._by_salt))
        return store

    # ─── 内部 ───
    def _rel_path(self, db_path: Path) -> str:
        try:
            return str(db_path.relative_to(self.db_storage_path)).replace(os.sep, "/")
        except ValueError:
            return db_path.name

    def _iter_db_files(self):
        """遍历 db_storage 下所有 .db 文件（跳过 -wal/-shm）。"""
        for p in sorted(self.db_storage_path.rglob("*.db")):
            if p.suffix == ".db" and not p.name.endswith(("-wal", "-shm", "-journal")):
                yield p


# ════════════════════════════════════════════════════════════════
#  解密器
# ════════════════════════════════════════════════════════════════
class WeChatDecryptor:
    """微信 SQLCipher 4 数据库解密器。

    支持两种模式：
    1. 单密钥模式：from_raw_key_hex(raw_key, db_path) — 兼容旧版
    2. 多密钥模式：from_key_store(key_store) — 推荐，按 .db 自动选密钥
    """

    def __init__(self, enc_key: bytes, salt: bytes):
        if len(enc_key) != 32:
            raise ValueError(f"enc_key 必须为 32 字节，当前 {len(enc_key)}")
        if len(salt) != 16:
            raise ValueError(f"salt 必须为 16 字节，当前 {len(salt)}")
        self.enc_key = enc_key
        self.salt = salt
        # 预计算 HMAC 密钥（PBKDF2 派生，salt 异或 0x3a，2 次迭代）
        mac_salt = bytes(b ^ 0x3a for b in salt)
        self.hmac_key = hashlib.pbkdf2_hmac(
            SQLCIPHER_HMAC_ALGO, enc_key, mac_salt, 2, 32
        )

    @classmethod
    def from_raw_key_hex(cls, raw_key_hex: str, db_path: str | Path) -> "WeChatDecryptor":
        """从内存扫描到的 raw key（96 字符 hex）创建解密器。

        salt 优先从 db 文件前 16 字节读取（避免内存 salt 与文件不匹配）。
        """
        hex_str = raw_key_hex.replace("x'", "").replace("'", "").strip()
        if len(hex_str) != 96:
            raise ValueError(f"raw key hex 应为 96 字符，当前 {len(hex_str)}")
        enc_key = bytes.fromhex(hex_str[:64])
        salt_from_file = Path(db_path).read_bytes()[:SQLCIPHER_SALT_SIZE]
        salt_from_key = bytes.fromhex(hex_str[64:96])
        if salt_from_file != salt_from_key:
            logger.warning("内存 salt 与文件 salt 不一致，采用文件 salt")
        return cls(enc_key, salt_from_file)

    @classmethod
    def for_db(cls, key_store: WeChatKeyStore, db_path: str | Path) -> "WeChatDecryptor":
        """从 KeyStore 中按 .db 自动选密钥创建解密器。"""
        entry = key_store.get_key_for_db(db_path)
        if entry is None:
            raise KeyError(
                f"KeyStore 中未找到 {db_path} 的密钥。"
                f"请重新扫描或加载 all_keys.json（已匹配 {len(key_store._by_path)} 个 .db）"
            )
        # salt 从文件读（与 wechat-decrypt 一致）
        salt = Path(db_path).read_bytes()[:SQLCIPHER_SALT_SIZE]
        return cls(entry.enc_key, salt)

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

        cipher = AES.new(self.enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)

        if page_num == 1:
            # 用 SQLite 头填充原 salt 位置
            decrypted = SQLITE_HEADER + decrypted[len(SQLITE_HEADER):]

        padding_needed = SQLCIPHER_PAGE_SIZE - len(decrypted) - SQLCIPHER_RESERVED
        if padding_needed > 0:
            decrypted += b"\x00" * padding_needed
        return decrypted

    def decrypt_db(self, db_path: str | Path, output_path: str | Path = None) -> str:
        """解密整个 .db 文件为明文 SQLite。"""
        db_path = Path(db_path)
        if output_path is None:
            output_path = Path(tempfile.gettempdir()) / f"dec_{db_path.name}"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        raw = db_path.read_bytes()
        total_pages = (len(raw) - SQLCIPHER_SALT_SIZE) // SQLCIPHER_PAGE_SIZE + 1

        with open(output_path, "wb") as f:
            for page_num in range(1, total_pages + 1):
                start = (page_num - 1) * SQLCIPHER_PAGE_SIZE
                page_data = raw[start: start + SQLCIPHER_PAGE_SIZE]
                if len(page_data) < SQLCIPHER_PAGE_SIZE:
                    page_data += b"\x00" * (SQLCIPHER_PAGE_SIZE - len(page_data))
                f.write(self._decrypt_page(page_data, page_num))

        # 清理验证残留的 -wal/-shm
        for suffix in ("-wal", "-shm"):
            p = output_path.with_suffix(output_path.suffix + suffix)
            p.unlink(missing_ok=True)

        logger.info("[解密] %s → %s (%d 页)", db_path.name, output_path.name, total_pages)
        return str(output_path)

    def open_sqlite(self, db_path: str | Path):
        """解密并打开为只读 SQLite 连接。"""
        plain_path = self.decrypt_db(db_path)
        conn = sqlite3.connect(f"file:{plain_path}?mode=ro", uri=True)
        return conn


# ════════════════════════════════════════════════════════════════
#  密钥扫描（macOS：Mach VM API，对齐 wechat-decrypt）
# ════════════════════════════════════════════════════════════════
def _find_wechat_pid_macos() -> Optional[int]:
    """查找微信进程 PID。"""
    for name in ("微信", "WeChat"):
        try:
            r = subprocess.run(["pgrep", "-x", name],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return int(r.stdout.strip().split("\n")[0])
        except (subprocess.SubprocessError, ValueError):
            pass
    # 兜底：模糊匹配
    try:
        r = subprocess.run(["pgrep", "-f", "xinWeChat"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().split("\n")[0])
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


def _scan_keys_macos_mach(pid: int) -> list[tuple[str, str]]:
    """用 Mach VM API 扫描微信进程内存，返回 [(enc_key_hex, salt_hex), ...]。

    对齐 wechat-decrypt find_all_keys_macos.c：
    - task_for_pid 拿到 task port（需 root）
    - mach_vm_region 枚举所有 RW 内存区域
    - mach_vm_read 分块（2MB）读取
    - 匹配 ASCII 字面量 x'<96hex>'
    """
    import ctypes
    import ctypes.util

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

    # ─── 类型/常量 ───
    mach_port_t = ctypes.c_uint
    kern_return_t = ctypes.c_int
    mach_vm_address_t = ctypes.c_uint64
    mach_vm_size_t = ctypes.c_uint64
    mach_msg_type_number_t = ctypes.c_uint32  # mach_vm_read 的 count_out 是 4 字节
    natural_t = ctypes.c_uint
    integer_t = ctypes.c_int
    KERN_SUCCESS = 0

    # vm_region_basic_info_data_64_t
    class vm_region_basic_info_data_64(ctypes.Structure):
        _fields_ = [
            ("protection", ctypes.c_int),
            ("max_protection", ctypes.c_int),
            ("inheritance", ctypes.c_uint),
            ("shared", ctypes.c_uint),
            ("reserved", ctypes.c_uint),
            ("offset", ctypes.c_uint64),
            ("behavior", ctypes.c_int),
            ("user_wired_count", ctypes.c_ushort),
        ]

    VM_REGION_BASIC_INFO_64 = 9
    VM_REGION_BASIC_INFO_COUNT_64 = ctypes.sizeof(vm_region_basic_info_64) // 4
    VM_PROT_READ = 1
    VM_PROT_WRITE = 2

    # 函数原型
    libc.task_for_pid.argtypes = [mach_port_t, ctypes.c_int, ctypes.POINTER(mach_port_t)]
    libc.task_for_pid.restype = kern_return_t

    libc.mach_vm_region.argtypes = [
        mach_port_t,
        ctypes.POINTER(mach_vm_address_t),
        ctypes.POINTER(mach_vm_size_t),
        natural_t,  # flavor
        ctypes.c_void_p,  # info
        ctypes.POINTER(natural_t),  # count
        ctypes.c_void_p,  # object_name (mach_port_t*)
    ]
    libc.mach_vm_region.restype = kern_return_t

    # mach_vm_read(task, addr, size, pointer_out, count_out)
    # 注意：count_out 是 mach_msg_type_number_t*（unsigned int, 4字节），
    # 不是 mach_vm_size_t*（8字节）。错类型会导致未定义行为。
    libc.mach_vm_read.argtypes = [
        mach_port_t,
        mach_vm_address_t,
        mach_vm_size_t,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(mach_msg_type_number_t),
    ]
    libc.mach_vm_read.restype = kern_return_t

    libc.mach_vm_deallocate.argtypes = [mach_port_t, ctypes.c_void_p, mach_vm_size_t]
    libc.mach_vm_deallocate.restype = kern_return_t

    # ⚠️ mach_task_self() 在 macOS 是宏，展开为全局变量 mach_task_self_，
    # 不是函数。若用 libc.mach_task_self() 调用会把变量地址当函数跳转 → SIGSEGV。
    mach_self = ctypes.c_uint.in_dll(libc, "mach_task_self_").value

    # 1) task_for_pid
    task = mach_port_t(0)
    kr = libc.task_for_pid(mach_self, pid, ctypes.byref(task))
    if kr != KERN_SUCCESS:
        raise RuntimeError(
            f"task_for_pid 失败 (kern_return={kr})。需要 root 权限，"
            f"且 WeChat.app 必须已 ad-hoc 重签名：\n"
            f"  sudo codesign --force --deep --sign - /Applications/WeChat.app"
        )

    # 2) 枚举内存区域
    keys: list[tuple[str, str]] = []
    seen: set[str] = set()
    addr = mach_vm_address_t(0)
    size = mach_vm_size_t(0)
    info = vm_region_basic_info_data_64()
    count = natural_t(VM_REGION_BASIC_INFO_COUNT_64)
    obj_name = mach_port_t(0)

    hex_re = re.compile(rb"x'([0-9a-fA-F]{96})'")
    # x'<96hex>' 模式最长 101 字节，分块边界需重叠此长度以防漏扫
    _OVERLAP = 101

    while True:
        kr = libc.mach_vm_region(task, ctypes.byref(addr), ctypes.byref(size),
                                 VM_REGION_BASIC_INFO_64, ctypes.byref(info),
                                 ctypes.byref(count), ctypes.byref(obj_name))
        if kr != KERN_SUCCESS:
            break  # 枚举结束

        # size==0 守护：避免无限循环（对齐 find_all_keys_macos.c 第 190 行）
        if size.value == 0:
            addr.value += 1
            continue

        # 只扫描可读可写区域（与 wechat-decrypt 一致）
        if not (info.protection & VM_PROT_READ and info.protection & VM_PROT_WRITE):
            addr.value += size.value
            continue

        # 分块读取
        region_addr = addr.value
        region_end = region_addr + size.value
        off = region_addr
        while off < region_end:
            chunk_size = min(_CHUNK_SIZE, region_end - off)
            data_ptr = ctypes.c_void_p(0)
            # count_out 用 mach_msg_type_number_t（4字节），不是 mach_vm_size_t
            data_size = mach_msg_type_number_t(0)
            kr = libc.mach_vm_read(task, off, chunk_size,
                                   ctypes.byref(data_ptr), ctypes.byref(data_size))
            if kr != KERN_SUCCESS or not data_ptr.value or data_size.value == 0:
                off += chunk_size
                continue
            try:
                buf = ctypes.string_at(data_ptr.value, data_size.value)
            finally:
                libc.mach_vm_deallocate(mach_self, data_ptr, data_size.value)

            # 匹配 x'<96hex>'
            for m in hex_re.finditer(buf):
                hex_str = m.group(1).decode("ascii").lower()
                if hex_str in seen:
                    continue
                seen.add(hex_str)
                enc_key = hex_str[:64]
                salt = hex_str[64:96]
                keys.append((enc_key, salt))
                if len(keys) >= _MAX_KEYS:
                    return keys
            # 重叠推进：回退 _OVERLAP 字节，确保跨块边界的密钥模式被捕获
            advance = chunk_size - _OVERLAP if chunk_size > _OVERLAP else chunk_size
            off += advance
        addr.value = region_end

    return keys


def scan_keys_macos(db_storage_path: str | Path) -> WeChatKeyStore:
    """macOS: 扫描微信进程内存获取所有密钥并匹配到 .db。

    需要：
    1. sudo 权限运行（task_for_pid 需要 root）
    2. WeChat.app 已 ad-hoc 重签名（关闭 hardened runtime）

    失败时抛 RuntimeError，调用方应回退到 all_keys.json 或手动输入。
    """
    pid = _find_wechat_pid_macos()
    if pid is None:
        raise RuntimeError(
            "未找到微信进程。请确保微信已登录运行。\n"
            "若微信正在运行仍报此错，可：\n"
            "  1. 加载 all_keys.json（用 wechat-decrypt 工具生成）\n"
            "  2. 手动输入密钥（96 字符 hex）"
        )

    logger.info("[密钥扫描] 微信 PID=%d，开始 Mach VM 内存扫描", pid)
    raw_keys = _scan_keys_macos_mach(pid)
    logger.info("[密钥扫描] 找到 %d 个候选密钥", len(raw_keys))

    if not raw_keys:
        raise RuntimeError(
            "内存中未找到微信密钥。可能原因：\n"
            "1. 未用 sudo 运行（task_for_pid 需 root）\n"
            "2. WeChat.app 未 ad-hoc 重签名：\n"
            "   sudo codesign --force --deep --sign - /Applications/WeChat.app\n"
            "3. 微信版本为 4.1.x（内存扫描已失效，需 wx_key DLL 注入）\n"
            "4. 微信刚启动未加载数据库（请先打开几个聊天）\n\n"
            "替代方案：加载 all_keys.json 或手动输入密钥"
        )

    store = WeChatKeyStore(db_storage_path)
    for enc_key, salt in raw_keys:
        store.add_key(enc_key, salt)
    matched = store.match_to_dbs()
    if matched == 0:
        raise RuntimeError(
            f"扫描到 {len(raw_keys)} 个密钥，但未匹配到任何 .db 文件。\n"
            f"请确认 db_storage 路径正确：{db_storage_path}\n"
            f"或尝试加载 all_keys.json。"
        )
    return store


# ════════════════════════════════════════════════════════════════
#  sudo 弹窗（osascript）—— GUI 用户无终端时的提权方案
# ════════════════════════════════════════════════════════════════
def scan_keys_macos_with_sudo_dialog(
    db_storage_path: str | Path,
    app_name: str = "外贸助手",
) -> WeChatKeyStore:
    """通过 osascript 弹出系统授权对话框让用户输入 root 密码，
    然后用 sudo 重新执行密钥扫描。

    原理：
    - osascript 的 `do shell script ... with administrator privileges`
      会弹出系统授权对话框，用户输入密码后以 root 执行命令
    - 我们把扫描逻辑写成一个独立 Python 脚本，通过 sudo 调用
    - 输出 JSON 到 stdout，主进程解析

    Returns:
        匹配好 .db 的 KeyStore
    """
    db_storage_path = str(Path(db_storage_path).resolve())
    # 打包模式下 sys.executable 是 .app 二进制（无法执行 .py），改用系统 Python；
    # 扫描脚本只用 stdlib（ctypes/hashlib/hmac/struct/re），/usr/bin/python3 足够。
    # 开发模式用 sys.executable（有完整依赖）。
    if getattr(sys, "frozen", False):
        python_exe = "/usr/bin/python3"
    else:
        python_exe = sys.executable or "python3"

    # 内联扫描脚本（独立运行，sudo 上下文）
    scan_script = f'''
import json, sys
sys.path.insert(0, {repr(str(Path(__file__).resolve().parents[2]))})
from src.wechat_parser.decryptor import scan_keys_macos
import logging
logging.basicConfig(level=logging.INFO, format="[scan] %(message)s", stream=sys.stderr)
try:
    store = scan_keys_macos({repr(db_storage_path)})
    print(json.dumps({{
        "ok": True,
        "stats": store.stats(),
        "keys": store.to_all_keys_json(),
    }}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"ok": False, "error": str(e)}}, ensure_ascii=False))
    sys.exit(1)
'''

    # 写到临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix="_scan.py",
                                     delete=False, encoding="utf-8") as f:
        f.write(scan_script)
        script_path = f.name

    try:
        # osascript 弹窗 + sudo 执行
        # 用 with administrator privileges 会弹系统密码框
        # 路径用单引号包裹，避免空格（macOS "Application Support"）被截断
        apple = (
            f"do shell script \"{python_exe} '{script_path}' 2>&1\" "
            f"with administrator privileges"
        )
        cmd = ["osascript", "-e", apple]
        logger.info("[sudo] 弹出系统授权对话框，等待用户输入 root 密码...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            if "User canceled" in err or "-128" in err:
                raise RuntimeError("用户取消了授权")
            raise RuntimeError(f"sudo 扫描失败：{err}")

        out = result.stdout.strip()
        # osascript 输出可能有 escape，取最后一个 JSON 行
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                break
        else:
            raise RuntimeError(f"无法解析扫描输出：{out[:200]}")

        if not data.get("ok"):
            raise RuntimeError(data.get("error", "未知错误"))

        # 重建 KeyStore（salt 从 .db 文件读）
        store = WeChatKeyStore(db_storage_path)
        for rel, info in data.get("keys", {}).items():
            enc_key = info.get("enc_key") if isinstance(info, dict) else info
            db_file = Path(db_storage_path) / rel
            if not db_file.exists() or not enc_key:
                continue
            salt = db_file.read_bytes()[:SQLCIPHER_SALT_SIZE]
            store.add_key(enc_key, salt.hex(), db_rel_path=rel)
        logger.info("[sudo] 扫描成功：%s", data.get("stats"))
        return store
    finally:
        Path(script_path).unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════
#  统一入口
# ════════════════════════════════════════════════════════════════
def get_key_store(
    db_storage_path: str | Path,
    manual_raw_key: str = None,
    all_keys_json_path: str | Path = None,
    auto_scan: bool = True,
    use_sudo_dialog: bool = False,
) -> WeChatKeyStore:
    """获取微信密钥存储，按优先级回退：

    1. 加载 all_keys.json（显式提供路径）
    2. 手动 raw_key（96 hex）→ 单密钥 store
    3. 自动扫描（macOS: Mach VM；Windows: pymem）
       - use_sudo_dialog=True 时通过 osascript 弹窗提权
       - use_sudo_dialog=False 时直接扫描（需已 sudo 运行）

    Args:
        db_storage_path: db_storage 根目录
        manual_raw_key: 手动输入的 96 hex raw key
        all_keys_json_path: all_keys.json 文件路径
        auto_scan: 是否允许自动扫描
        use_sudo_dialog: macOS 是否通过 osascript 弹窗提权

    Returns:
        匹配好 .db 的 WeChatKeyStore
    """
    # 1) all_keys.json
    if all_keys_json_path and Path(all_keys_json_path).exists():
        logger.info("[密钥] 加载 all_keys.json: %s", all_keys_json_path)
        store = WeChatKeyStore.load_all_keys_json(all_keys_json_path, db_storage_path)
        if store.stats()["matched_dbs"] > 0:
            return store
        logger.warning("[密钥] all_keys.json 加载成功但未匹配到 .db，尝试其他方式")

    # 2) 手动 raw_key
    if manual_raw_key and len(manual_raw_key.strip()) == 96:
        logger.info("[密钥] 使用手动输入的 raw key")
        store = WeChatKeyStore(db_storage_path)
        store.add_raw_key(manual_raw_key)
        matched = store.match_to_dbs()
        if matched > 0:
            return store
        logger.warning("[密钥] 手动 key 未匹配到任何 .db")

    # 3) 自动扫描
    if not auto_scan:
        raise RuntimeError("无可用密钥源（all_keys.json / 手动 key / 自动扫描均未启用）")

    if sys.platform == "darwin":
        if use_sudo_dialog:
            return scan_keys_macos_with_sudo_dialog(db_storage_path)
        return scan_keys_macos(db_storage_path)
    elif sys.platform == "win32":
        return _scan_keys_windows(db_storage_path)
    else:
        raise RuntimeError(
            "当前系统不支持自动扫描微信密钥。\n"
            "请：\n"
            "  1. 加载 all_keys.json（用 wechat-decrypt 工具生成）\n"
            "  2. 手动输入密钥（96 字符 hex）"
        )


def _scan_keys_windows(db_storage_path: str | Path) -> WeChatKeyStore:
    """Windows: 用 pymem 扫描微信进程内存（仅 4.0.x）。"""
    try:
        import pymem
    except ImportError:
        raise RuntimeError(
            "pymem 未安装（Windows 专用）。请运行: pip install pymem\n"
            "或加载 all_keys.json / 手动输入密钥。"
        )
    try:
        import psutil
    except ImportError:
        raise RuntimeError("psutil 未安装。请运行: pip install psutil")

    target_pid = None
    for proc in psutil.process_iter(["pid", "name"]):
        pname = proc.info.get("name") or ""
        if "WeChat".lower() in pname.lower():
            target_pid = proc.info["pid"]
            break
    if not target_pid:
        raise RuntimeError("未找到微信进程 WeChat.exe，请确保微信已登录运行")

    logger.info("[密钥扫描] Windows 进程 PID=%d", target_pid)
    pm = pymem.Pymem(target_pid)
    pattern = b"x'"
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for module in pm.list_modules():
        try:
            base = module.lpBaseOfDll
            size = module.SizeOfImage
            for offset in range(0, size, _CHUNK_SIZE):
                read_size = min(_CHUNK_SIZE, size - offset)
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
                        hex_str = candidate[2:98].decode("ascii", errors="ignore").lower()
                        try:
                            bytes.fromhex(hex_str)
                            if len(hex_str) == 96 and hex_str not in seen:
                                seen.add(hex_str)
                                candidates.append((hex_str[:64], hex_str[64:96]))
                        except ValueError:
                            pass
                    pos = idx + 1
        except Exception as e:
            logger.debug("扫描模块 %s 失败: %s", module.name, e)

    if not candidates:
        raise RuntimeError(
            "未在内存中找到微信密钥。可能原因：\n"
            "1. 微信版本为 4.1.x，内存扫描已失效\n"
            "2. 微信刚启动未加载数据库\n"
            "3. 权限不足（需管理员权限运行）\n"
            "4. 可加载 all_keys.json 或手动输入密钥"
        )

    logger.info("[密钥扫描] 找到 %d 个候选密钥", len(candidates))
    store = WeChatKeyStore(db_storage_path)
    for enc_key, salt in candidates:
        store.add_key(enc_key, salt)
    matched = store.match_to_dbs()
    if matched == 0:
        raise RuntimeError(
            f"扫描到 {len(candidates)} 个密钥，但未匹配到任何 .db 文件。\n"
            f"请确认 db_storage 路径正确：{db_storage_path}"
        )
    return store


# ════════════════════════════════════════════════════════════════
#  兼容旧接口
# ════════════════════════════════════════════════════════════════
def scan_wechat_key(process_name: str = None) -> str:
    """[兼容旧接口] 扫描微信进程内存获取单个 raw key（96 hex）。

    ⚠️ 已弃用，新代码请用 get_key_store() / scan_keys_macos()。
    """
    if sys.platform == "darwin":
        # 旧调用方可能没有 db_storage_path，用 cwd 兜底
        store = scan_keys_macos(Path.cwd())
        if not store._by_salt:
            raise RuntimeError("未找到微信密钥")
        first = next(iter(store._by_salt.values()))
        return first.enc_key_hex + first.salt_hex
    elif sys.platform == "win32":
        store = _scan_keys_windows(Path.cwd())
        if not store._by_salt:
            raise RuntimeError("未找到微信密钥")
        first = next(iter(store._by_salt.values()))
        return first.enc_key_hex + first.salt_hex
    else:
        raise RuntimeError("当前系统不支持自动扫描，请用 get_key_store()")


def get_wechat_key_with_fallback(process_name: str = None, manual_key: str = None) -> str:
    """[兼容旧接口] 获取微信密钥，支持手动输入回退。

    ⚠️ 已弃用，新代码请用 get_key_store()。
    """
    if manual_key and len(manual_key.strip()) == 96:
        return manual_key.strip()
    try:
        return scan_wechat_key(process_name)
    except RuntimeError:
        if manual_key and len(manual_key.strip()) == 96:
            return manual_key.strip()
        raise

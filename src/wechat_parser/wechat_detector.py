"""微信数据目录与进程自动检测（跨平台）。

非技术用户无法手动找到微信存储目录，本模块自动扫描：
  Windows: C:\\Users\\<用户>\\Documents\\xwechat_files\\<wxid>_<hash>\\db_storage\\
  macOS:   ~/Library/Containers/com.tencent.xinWeChat/Data/.../Message/
  Linux:   ~/.config/wechat/...（微信 Linux 版）

进程检测：
  Windows: WeChat.exe
  macOS:   微信 / WeChat
  Linux:   wechat
"""
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WeChatDetection:
    """微信检测结果。"""
    found: bool = False
    db_storage_path: str = ""        # db_storage 根目录
    process_name: str = ""           # 微信进程名
    process_running: bool = False    # 微信是否在运行
    wxid: str = ""                   # 当前登录的 wxid
    version: str = ""                # 微信版本
    candidates: list = None          # 多个候选路径

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


def default_process_name() -> str:
    """当前系统的默认微信进程名。"""
    if sys.platform == "win32":
        return "WeChat.exe"
    elif sys.platform == "darwin":
        return "微信"
    else:
        return "wechat"


def detect_process(process_name: str = None) -> tuple[str, bool]:
    """检测微信进程是否在运行。

    Returns:
        (实际进程名, 是否运行)
    """
    name = process_name or default_process_name()
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name"]):
            pname = proc.info.get("name") or ""
            # 模糊匹配：WeChat.exe / 微信 / wechat
            if name.lower() in pname.lower() or "wechat" in pname.lower():
                return pname, True
    except ImportError:
        logger.debug("psutil 未安装，无法检测进程")
    except Exception as e:
        logger.debug("进程检测异常: %s", e)
    return name, False


def _detect_windows() -> WeChatDetection:
    """Windows 检测：扫描 xwechat_files 目录。"""
    result = WeChatDetection(process_name="WeChat.exe")
    home = Path.home()
    # 微信 4.x 默认存储在 Documents\xwechat_files
    search_roots = [
        home / "Documents" / "xwechat_files",
        Path(os.environ.get("USERPROFILE", home)) / "Documents" / "xwechat_files",
    ]

    for root in search_roots:
        if not root.exists():
            continue
        # 每个子目录是 <wxid>_<hash> 格式
        for user_dir in sorted(root.iterdir()):
            if not user_dir.is_dir():
                continue
            db_storage = user_dir / "db_storage"
            if db_storage.exists():
                wxid_hash = user_dir.name
                # wxid_<32位hash>，提取 wxid 部分
                wxid = wxid_hash.split("_")[0] if "_" in wxid_hash else wxid_hash
                result.candidates.append({
                    "path": str(db_storage),
                    "wxid": wxid,
                    "label": f"{wxid} ({user_dir.name})",
                })

    if result.candidates:
        # 取最近修改的作为默认
        result.candidates.sort(key=lambda x: Path(x["path"]).stat().st_mtime, reverse=True)
        best = result.candidates[0]
        result.found = True
        result.db_storage_path = best["path"]
        result.wxid = best["wxid"]

    result.process_name, result.process_running = detect_process("WeChat.exe")
    return result


def _resolve_real_home() -> Path:
    """sudo 运行时 HOME 会变成 /var/root，需通过 SUDO_USER 回真实用户家目录。

    对齐 wechat-decrypt config.py::_auto_detect_db_dir_macos / find_all_keys_macos.c
    的 SUDO_USER 处理逻辑。
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            pw = pwd.getpwnam(sudo_user)
            if pw.pw_dir:
                return Path(pw.pw_dir)
        except (KeyError, OSError):
            pass
    return Path.home()


def _has_db_files(db_storage: Path) -> bool:
    """判断目录是否真的含 .db 文件（避免误报空目录）。"""
    try:
        return any(db_storage.rglob("*.db"))
    except OSError:
        return False


def _detect_macos() -> WeChatDetection:
    """macOS 检测：对齐 wechat-decrypt 的目录探测逻辑。

    wechat-decrypt（4.x 标准）使用：
        ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage

    旧版微信 Mac 布局（部分 4.0 早期版本）：
        ~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/
            com.tencent.xinWeChat/<version>/<user_hash>/db_storage

    两者都扫描，返回的 path 始终是 db_storage 子目录（含 .db 文件），
    这样 all_keys.json 的 "message/message_0.db" 相对路径才能正确解析。
    """
    result = WeChatDetection(process_name="微信")
    home = _resolve_real_home()

    # ── 路径1（wechat-decrypt 标准，4.x 推荐）：Data/Documents/xwechat_files ──
    xwechat_root = (home / "Library" / "Containers" / "com.tencent.xinWeChat"
                    / "Data" / "Documents" / "xwechat_files")
    if xwechat_root.exists():
        # 每个子目录是 <wxid>_<hash> 或 <wxid>，下有 db_storage
        for user_dir in sorted(xwechat_root.iterdir(), reverse=True):
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            db_storage = user_dir / "db_storage"
            if db_storage.exists() and _has_db_files(db_storage):
                wxid_hash = user_dir.name
                wxid = wxid_hash.split("_")[0] if "_" in wxid_hash else wxid_hash
                result.candidates.append({
                    "path": str(db_storage),  # 始终返回 db_storage 子目录
                    "wxid": wxid,
                    "label": f"{wxid} ({user_dir.name})",
                    "version": "",
                })

    # ── 路径2（旧版 4.0 早期）：Application Support/<version>/<hash>/db_storage ──
    legacy_roots = [
        home / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Library"
        / "Application Support" / "com.tencent.xinWeChat",
        home / "Library" / "Application Support" / "com.tencent.xinWeChat",
    ]
    for root in legacy_roots:
        if not root.exists():
            continue
        for version_dir in sorted(root.iterdir(), reverse=True):
            if not version_dir.is_dir() or version_dir.name.startswith("."):
                continue
            for user_dir in sorted(version_dir.iterdir()):
                if not user_dir.is_dir() or user_dir.name.startswith("."):
                    continue
                db_storage = user_dir / "db_storage"
                if not db_storage.exists():
                    db_storage = user_dir / "Message"  # 更早版本
                if db_storage.exists() and _has_db_files(db_storage):
                    result.candidates.append({
                        "path": str(db_storage),  # 始终返回 db_storage 子目录
                        "wxid": user_dir.name,
                        "label": f"{user_dir.name} (微信 {version_dir.name})",
                        "version": version_dir.name,
                    })

    if result.candidates:
        # 按 db_storage 目录 mtime 降序，优先最近活跃账号
        result.candidates.sort(key=lambda x: Path(x["path"]).stat().st_mtime, reverse=True)
        best = result.candidates[0]
        result.found = True
        result.db_storage_path = best["path"]
        result.wxid = best.get("wxid", "")
        result.version = best.get("version", "")

    result.process_name, result.process_running = detect_process("微信")
    return result


def _detect_linux() -> WeChatDetection:
    """Linux 检测。"""
    result = WeChatDetection(process_name="wechat")
    home = Path.home()
    search_roots = [
        home / ".config" / "wechat",
        home / ".wechat",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for user_dir in sorted(root.iterdir()):
            if not user_dir.is_dir():
                continue
            db_storage = user_dir / "db_storage"
            if db_storage.exists():
                result.candidates.append({
                    "path": str(db_storage),
                    "wxid": user_dir.name,
                    "label": user_dir.name,
                })
    if result.candidates:
        best = result.candidates[0]
        result.found = True
        result.db_storage_path = best["path"]
        result.wxid = best["wxid"]
    result.process_name, result.process_running = detect_process("wechat")
    return result


def detect_wechat(process_name: str = None) -> WeChatDetection:
    """自动检测微信数据目录和进程状态。

    Args:
        process_name: 指定进程名（None 则用系统默认）

    Returns:
        WeChatDetection 检测结果
    """
    logger.info("[检测] 系统平台: %s", sys.platform)
    if sys.platform == "win32":
        result = _detect_windows()
    elif sys.platform == "darwin":
        result = _detect_macos()
    else:
        result = _detect_linux()

    if process_name:
        result.process_name, result.process_running = detect_process(process_name)

    if result.found:
        logger.info("[检测] 微信目录: %s (wxid=%s, 版本=%s)", result.db_storage_path, result.wxid, result.version)
    else:
        logger.warning("[检测] 未找到微信数据目录")
    logger.info("[检测] 微信进程: %s 运行中=%s", result.process_name, result.process_running)
    return result


def list_contacts_from_db(db_storage_path: str) -> list[dict]:
    """从微信数据库提取联系人/会话列表（用于左侧栏展示）。

    Returns:
        [{"talker": wxid, "name": 昵称, "type": "user"/"group", "last_time": ts}, ...]
    """
    # session.db 加密，密钥由调用方通过 get_key_store() / MessageExtractor 管理
    contacts = []
    try:
        # 先尝试从 session.db 获取会话列表
        db_path = Path(db_storage_path)
        session_db = db_path / "session" / "session.db"
        if not session_db.exists():
            # Mac 微信路径可能不同
            session_db = db_path / "session.db"

        if not session_db.exists():
            logger.warning("[联系人] 未找到 session.db: %s", session_db)
            return contacts

        # session.db 是加密的，需要先解密（但需要密钥）
        # 这里返回原始数据，由调用方解密
        contacts.append({
            "talker": "__session_db__",
            "name": "会话列表",
            "path": str(session_db),
        })
    except Exception as e:
        logger.error("[联系人] 提取失败: %s", e)
    return contacts

"""微信消息提取与准实时监听。

基于 ylytdeng/wechat-decrypt 的 WAL 监听方案：
- 30~100ms 轮询 WAL 文件 mtime（不能用 size，WAL 预分配 4MB 固定大小）
- 检测到变化后 debounce → 增量解密 → 按 msg_svr_id 游标提取新消息
- 端到端延迟约 100ms
"""
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import zstandard

from .decryptor import WeChatDecryptor

logger = logging.getLogger(__name__)


@dataclass
class WeChatMessage:
    """微信消息结构。"""
    local_id: int
    msg_svr_id: int           # 服务器全局唯一 ID（增量游标）
    talker: str               # 会话对方 wxid 或群ID
    type: int                 # 1=文本 3=图片 34=语音 43=视频 49=复合
    is_sender: int            # 0=收到 1=发出
    create_time: int          # Unix 秒级时间戳
    content_text: str = ""    # 文本内容（已 ZSTD 解压）
    blob_data: bytes = b""    # 附件数据（语音/图片）
    talker_name: str = ""     # 对方昵称（从 contact 表查）


# 消息类型常量
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_VIDEO = 43
MSG_TYPE_COMPLEX = 49  # 链接/文件/小程序/引用等


@dataclass
class ParseCursor:
    """增量解析游标，持久化到 SQLite。"""
    talker: str = ""
    last_msg_svr_id: int = 0
    last_create_time: int = 0


class MessageExtractor:
    """从解密后的微信消息库提取消息。"""

    def __init__(self, decryptor: WeChatDecryptor, db_storage_path: str | Path):
        self.decryptor = decryptor
        self.db_storage_path = Path(db_storage_path)
        self._zstd_decompressor = zstandard.ZstdDecompressor()
        self._msg_db_cache: dict[str, sqlite3.Connection] = {}

    def _get_msg_db_connection(self, talker_id: str) -> sqlite3.Connection:
        """根据 talker_id 定位 message_*.db 并返回连接。

        微信 4.x 有 message_0~13.db 共 14 个库，每个库含多个 Msg_<hash> 表。
        talker_id → 表名的映射存在 Name2Id 表。
        """
        # 简化实现：遍历所有 message_*.db 查找含该 talker 表的库
        # 实际应用中可缓存 talker_id → db 的映射
        cache_key = talker_id
        if cache_key in self._msg_db_cache:
            return self._msg_db_cache[cache_key]

        msg_dir = self.db_storage_path / "message"
        for db_file in sorted(msg_dir.glob("message_*.db")):
            plain_path = self.decryptor.decrypt_db(db_file)
            conn = sqlite3.connect(f"file:{plain_path}?mode=ro", uri=True)
            # 查 Name2Id 表确认该 talker 是否在此库
            try:
                cur = conn.execute(
                    "SELECT table_name FROM Name2Id WHERE user_name = ?", (talker_id,)
                )
                row = cur.fetchone()
                if row:
                    self._msg_db_cache[cache_key] = conn
                    return conn
            except sqlite3.OperationalError:
                pass
            conn.close()

        raise KeyError(f"未找到 talker {talker_id} 的消息表")

    def extract_new_messages(
        self,
        talker_id: str,
        cursor: ParseCursor,
        limit: int = 1000,
    ) -> list[WeChatMessage]:
        """提取指定会话的新消息（基于 msg_svr_id 游标增量）。

        Args:
            talker_id: 会话对方 wxid
            cursor: 上次解析的游标
            limit: 最大返回条数

        Returns:
            新消息列表（按时间升序）
        """
        conn = self._get_msg_db_connection(talker_id)
        table_name = self._get_table_name(conn, talker_id)
        if not table_name:
            return []

        cur = conn.execute(
            f"SELECT local_id, msg_svr_id, type, is_sender, create_time, "
            f"message_content, message_blob FROM {table_name} "
            f"WHERE msg_svr_id > ? ORDER BY create_time ASC, local_id ASC LIMIT ?",
            (cursor.last_msg_svr_id, limit),
        )
        messages = self._parse_messages(cur, talker_id)

        if messages:
            last = messages[-1]
            cursor.last_msg_svr_id = last.msg_svr_id
            cursor.last_create_time = last.create_time

        return messages

    def _get_table_name(self, conn: sqlite3.Connection, talker_id: str) -> str:
        """获取 talker 对应的消息表名。"""
        try:
            cur = conn.execute(
                "SELECT table_name FROM Name2Id WHERE user_name = ?", (talker_id,)
            )
            row = cur.fetchone()
            return row[0] if row else ""
        except sqlite3.OperationalError:
            return ""

    def _parse_messages(self, cur, talker_id: str) -> list[WeChatMessage]:
        """解析查询结果为 WeChatMessage 列表（处理 ZSTD 解压）。"""
        messages = []
        for r in cur.fetchall():
            local_id, msg_svr_id, mtype, is_sender, create_time, content, blob = r
            content_text = ""
            if content:
                try:
                    content_text = self._zstd_decompressor.decompress(content).decode("utf-8", errors="replace")
                except Exception:
                    content_text = content.decode("utf-8", errors="replace")
            messages.append(WeChatMessage(
                local_id=local_id,
                msg_svr_id=msg_svr_id,
                talker=talker_id,
                type=mtype,
                is_sender=is_sender,
                create_time=create_time,
                content_text=content_text,
                blob_data=blob or b"",
            ))
        return messages

    def extract_messages_by_time(
        self,
        talker_id: str,
        time_from: int = 0,
        time_to: int = 0,
        limit: int = 500,
    ) -> list[WeChatMessage]:
        """按时间范围提取消息（用于 GUI 时间筛选）。

        Args:
            talker_id: 会话对方 wxid
            time_from: 起始 Unix 时间戳（0=不限制）
            time_to: 结束 Unix 时间戳（0=不限制）
            limit: 最大返回条数

        Returns:
            消息列表（按时间升序）
        """
        conn = self._get_msg_db_connection(talker_id)
        table_name = self._get_table_name(conn, talker_id)
        if not table_name:
            return []

        sql = f"SELECT local_id, msg_svr_id, type, is_sender, create_time, message_content, message_blob FROM {table_name}"
        conditions = []
        params = []
        if time_from > 0:
            conditions.append("create_time >= ?")
            params.append(time_from)
        if time_to > 0:
            conditions.append("create_time <= ?")
            params.append(time_to)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY create_time ASC, local_id ASC LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        return self._parse_messages(cur, talker_id)

    def search_messages(
        self,
        keyword: str,
        talker_id: str = None,
        time_from: int = 0,
        time_to: int = 0,
        limit: int = 50,
    ) -> list[WeChatMessage]:
        """按关键词搜索消息（跨会话）。

        Args:
            keyword: 搜索关键词
            talker_id: 限定会话（None=全部）
            time_from/time_to: 时间范围
            limit: 最大返回条数
        """
        results = []
        talkers = [talker_id] if talker_id else self.list_all_talkers()
        for tid in talkers:
            try:
                msgs = self.extract_messages_by_time(tid, time_from, time_to, limit=1000)
                for m in msgs:
                    if keyword.lower() in m.content_text.lower():
                        results.append(m)
                        if len(results) >= limit:
                            return results
            except Exception as e:
                logger.debug("搜索 %s 失败: %s", tid, e)
        return results

    def list_all_talkers(self) -> list[str]:
        """列出所有会话 talker ID（从 session.db）。"""
        try:
            session_db = self.db_storage_path / "session" / "session.db"
            if not session_db.exists():
                session_db = self.db_storage_path / "session.db"
            if not session_db.exists():
                return []
            plain_path = self.decryptor.decrypt_db(session_db)
            conn = sqlite3.connect(f"file:{plain_path}?mode=ro", uri=True)
            cur = conn.execute("SELECT DISTINCT user_name FROM session ORDER BY update_time DESC LIMIT 200")
            talkers = [r[0] for r in cur.fetchall()]
            conn.close()
            return talkers
        except Exception as e:
            logger.debug("列出会话失败: %s", e)
            return []

    def list_contacts(self) -> list[dict]:
        """列出联系人/会话列表（用于左侧栏）。

        Returns:
            [{"talker": wxid, "name": 昵称, "last_time": ts, "type": "user"/"group"}, ...]
        """
        contacts = []
        try:
            session_db = self.db_storage_path / "session" / "session.db"
            if not session_db.exists():
                session_db = self.db_storage_path / "session.db"
            if not session_db.exists():
                return contacts
            plain_path = self.decryptor.decrypt_db(session_db)
            conn = sqlite3.connect(f"file:{plain_path}?mode=ro", uri=True)
            # session 表字段: user_name, nickname, update_time, ...
            cur = conn.execute(
                "SELECT user_name, nickname, update_time FROM session "
                "ORDER BY update_time DESC LIMIT 200"
            )
            for row in cur.fetchall():
                talker, name, last_time = row
                # 群聊 talker 以 @chatroom 结尾
                ctype = "group" if talker.endswith("@chatroom") else "user"
                contacts.append({
                    "talker": talker,
                    "name": name or talker,
                    "last_time": last_time or 0,
                    "type": ctype,
                })
            conn.close()
        except Exception as e:
            logger.debug("列出联系人失败: %s", e)
        return contacts


class RealtimeMonitor:
    """准实时监听微信数据库变化。

    采用 WAL 文件 mtime 轮询方案（与 wechat-decrypt 一致）：
    - WAL 文件预分配 4MB 固定大小，不能用 size 判断，必须用 mtime
    - 检测到变化后 debounce 等待写入完成，再触发增量提取
    """

    def __init__(
        self,
        db_storage_path: str | Path,
        extractor: MessageExtractor,
        on_new_messages: Callable[[str, list[WeChatMessage]], None],
        poll_interval_ms: int = 100,
        debounce_ms: int = 200,
        watch_talkers: list[str] = None,
        ignore_talkers: list[str] = None,
    ):
        self.db_storage_path = Path(db_storage_path)
        self.extractor = extractor
        self.on_new_messages = on_new_messages
        self.poll_interval = poll_interval_ms / 1000.0
        self.debounce = debounce_ms / 1000.0
        self.watch_talkers = set(watch_talkers or [])
        self.ignore_talkers = set(ignore_talkers or ["filehelper", "weixin"])
        self._running = False
        self._thread = None
        self._wal_mtimes: dict[str, float] = {}
        self._cursors: dict[str, ParseCursor] = {}

    def _scan_wal_files(self) -> dict[str, float]:
        """扫描所有 message_*.db 的 WAL 文件 mtime。"""
        mtimes = {}
        msg_dir = self.db_storage_path / "message"
        if not msg_dir.exists():
            return mtimes
        for db_file in msg_dir.glob("message_*.db"):
            wal_file = db_file.with_suffix(".db-wal")
            if wal_file.exists():
                mtimes[str(wal_file)] = wal_file.stat().st_mtime
        return mtimes

    def _has_changes(self) -> bool:
        """检查 WAL 文件是否有变化。"""
        current = self._scan_wal_files()
        if not self._wal_mtimes:
            self._wal_mtimes = current
            return False
        changed = False
        for path, mtime in current.items():
            if path not in self._wal_mtimes or self._wal_mtimes[path] != mtime:
                changed = True
                self._wal_mtimes[path] = mtime
        return changed

    def _poll_loop(self):
        """轮询主循环。"""
        logger.info("[监听] 准实时监听已启动 (间隔=%dms)", int(self.poll_interval * 1000))
        while self._running:
            try:
                if self._has_changes():
                    # debounce：等待写入完成
                    time.sleep(self.debounce)
                    self._extract_all_new()
            except Exception as e:
                logger.error("[监听] 轮询异常: %s", e, exc_info=True)
            time.sleep(self.poll_interval)
        logger.info("[监听] 已停止")

    def _extract_all_new(self):
        """对所有监听的会话提取新消息。"""
        # 获取最近活跃的会话列表（从 session.db）
        # 简化实现：遍历 watch_talkers，为空则遍历所有最近会话
        talkers_to_check = self.watch_talkers
        if not talkers_to_check:
            # 从 session.db 获取最近会话
            talkers_to_check = self._get_recent_talkers()

        for talker in talkers_to_check:
            if talker in self.ignore_talkers:
                continue
            try:
                cursor = self._cursors.setdefault(talker, ParseCursor(talker=talker))
                messages = self.extractor.extract_new_messages(talker, cursor)
                if messages:
                    logger.info("[监听] %s: %d 条新消息", talker, len(messages))
                    self.on_new_messages(talker, messages)
            except Exception as e:
                logger.debug("[监听] 提取 %s 失败: %s", talker, e)

    def _get_recent_talkers(self) -> list[str]:
        """从 session.db 获取最近活跃会话列表。"""
        try:
            session_db = self.db_storage_path / "session" / "session.db"
            plain_path = self.extractor.decryptor.decrypt_db(session_db)
            conn = sqlite3.connect(f"file:{plain_path}?mode=ro", uri=True)
            cur = conn.execute("SELECT DISTINCT user_name FROM session ORDER BY update_time DESC LIMIT 50")
            talkers = [r[0] for r in cur.fetchall()]
            conn.close()
            return talkers
        except Exception as e:
            logger.debug("[监听] 获取最近会话失败: %s", e)
            return []

    def start(self):
        """启动准实时监听（异步线程）。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="wechat-monitor")
        self._thread.start()

    def stop(self):
        """停止监听。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

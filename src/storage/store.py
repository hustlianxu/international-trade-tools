"""SQLite 存储层：游标持久化、转写结果、分析结果、TODO。"""
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.reminder.todo_manager import TodoItem

logger = logging.getLogger(__name__)


class Store:
    """本地 SQLite 存储。"""

    def __init__(self, db_path: str = "./data/trade_tools.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    def _init_schema(self):
        with self._conn() as c:
            # 增量解析游标
            c.execute("""
                CREATE TABLE IF NOT EXISTS parse_cursor (
                    talker TEXT PRIMARY KEY,
                    last_msg_svr_id INTEGER DEFAULT 0,
                    last_create_time INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)
            # 转写结果
            c.execute("""
                CREATE TABLE IF NOT EXISTS transcription (
                    msg_svr_id INTEGER PRIMARY KEY,
                    talker TEXT,
                    silk_path TEXT,
                    text TEXT,
                    language TEXT,
                    duration_sec REAL,
                    transcribed_at TEXT
                )
            """)
            # 分析结果
            c.execute("""
                CREATE TABLE IF NOT EXISTS analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    talker TEXT,
                    talker_name TEXT,
                    language TEXT,
                    summary TEXT,
                    needs_json TEXT,
                    customer_mood TEXT,
                    raw_text TEXT,
                    analyzed_at TEXT
                )
            """)
            # TODO 待办
            c.execute("""
                CREATE TABLE IF NOT EXISTS todo (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    talker TEXT,
                    talker_name TEXT,
                    content TEXT,
                    category TEXT,
                    urgency TEXT DEFAULT 'normal',
                    deadline TEXT,
                    created_at TEXT,
                    status TEXT DEFAULT 'pending',
                    done_at TEXT
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_todo_status ON todo(status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_todo_talker ON todo(talker)")

    # ═══════ 游标 ═══════
    def get_cursor(self, talker: str):
        with self._conn() as c:
            cur = c.execute(
                "SELECT last_msg_svr_id, last_create_time FROM parse_cursor WHERE talker=?",
                (talker,),
            )
            row = cur.fetchone()
            if row:
                return {"last_msg_svr_id": row[0], "last_create_time": row[1]}
            return {"last_msg_svr_id": 0, "last_create_time": 0}

    def save_cursor(self, talker: str, last_msg_svr_id: int, last_create_time: int):
        with self._conn() as c:
            c.execute("""
                INSERT INTO parse_cursor (talker, last_msg_svr_id, last_create_time, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(talker) DO UPDATE SET
                    last_msg_svr_id=excluded.last_msg_svr_id,
                    last_create_time=excluded.last_create_time,
                    updated_at=excluded.updated_at
            """, (talker, last_msg_svr_id, last_create_time, datetime.now().isoformat()))

    # ═══════ 转写结果 ═══════
    def save_transcription(self, msg_svr_id: int, talker: str, silk_path: str,
                           text: str, language: str, duration_sec: float):
        with self._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO transcription
                (msg_svr_id, talker, silk_path, text, language, duration_sec, transcribed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (msg_svr_id, talker, silk_path, text, language, duration_sec,
                  datetime.now().isoformat()))

    def get_transcription(self, msg_svr_id: int) -> str | None:
        with self._conn() as c:
            cur = c.execute("SELECT text FROM transcription WHERE msg_svr_id=?", (msg_svr_id,))
            row = cur.fetchone()
            return row[0] if row else None

    # ═══════ 分析结果 ═══════
    def save_analysis(self, result):
        """保存 AnalysisResult。"""
        from src.llm.base import AnalysisResult
        needs_json = json.dumps([{
            "category": n.category, "summary": n.summary, "product": n.product,
            "quantity": n.quantity, "deadline": n.deadline, "urgency": n.urgency,
        } for n in result.needs], ensure_ascii=False)
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO analysis (talker, talker_name, language, summary, needs_json, customer_mood, raw_text, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (result.talker, result.talker_name, result.language, result.summary,
                  needs_json, result.customer_mood, result.raw_text, result.analyzed_at))
            return cur.lastrowid

    # ═══════ TODO ═══════
    def save_todo(self, item: TodoItem):
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO todo (talker, talker_name, content, category, urgency, deadline, created_at, status, done_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item.talker, item.talker_name, item.content, item.category,
                  item.urgency, item.deadline, item.created_at, item.status, item.done_at))
            return cur.lastrowid

    def update_todo_status(self, todo_id: int, status: str, done_at: str = ""):
        with self._conn() as c:
            c.execute(
                "UPDATE todo SET status=?, done_at=? WHERE id=?",
                (status, done_at, todo_id),
            )

    def get_todos(self, status: str = "pending", talker: str = None) -> list[TodoItem]:
        sql = "SELECT id, talker, talker_name, content, category, urgency, deadline, created_at, status, done_at FROM todo WHERE status=?"
        params = [status]
        if talker:
            sql += " AND talker=?"
            params.append(talker)
        sql += " ORDER BY CASE urgency WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, created_at ASC"
        with self._conn() as c:
            cur = c.execute(sql, params)
            items = []
            for r in cur.fetchall():
                items.append(TodoItem(
                    id=r[0], talker=r[1], talker_name=r[2], content=r[3],
                    category=r[4], urgency=r[5], deadline=r[6], created_at=r[7],
                    status=r[8], done_at=r[9],
                ))
            return items

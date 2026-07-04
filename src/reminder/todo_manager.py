"""TODO 待办管理器：汇总已办/待办，按天/小时推进提醒。"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.llm.base import AnalysisResult

logger = logging.getLogger(__name__)


@dataclass
class TodoItem:
    """待办事项。"""
    id: int = 0
    talker: str = ""           # 客户 wxid
    talker_name: str = ""
    content: str = ""          # 待办内容（中文）
    category: str = ""         # 需求分类
    urgency: str = "normal"    # high/normal/low
    deadline: str = ""         # 截止日期
    created_at: str = ""
    status: str = "pending"    # pending(待办) / done(已办) / overdue(超期)
    done_at: str = ""


class TodoManager:
    """待办事项管理与提醒。

    - 从分析结果中提取待办，写入存储
    - 按 urgency + deadline 排序
    - 支持按天/小时定时提醒
    - 超期事项标记为 overdue
    """

    def __init__(self, store):
        """Args: store: storage.Store 实例（SQLite 持久化）"""
        self.store = store

    def add_from_analysis(self, result: AnalysisResult):
        """将分析结果中的待办事项写入存储。"""
        now = datetime.now().isoformat()
        for todo_text in result.todo_items:
            item = TodoItem(
                talker=result.talker,
                talker_name=result.talker_name,
                content=todo_text,
                category=result.needs[0].category if result.needs else "other",
                urgency=result.needs[0].urgency if result.needs else "normal",
                deadline=result.needs[0].deadline if result.needs else "",
                created_at=now,
                status="pending",
            )
            self.store.save_todo(item)
            logger.info("[TODO] 新增待办: %s", todo_text)

        # 已办事项记录
        for done_text in result.done_items:
            item = TodoItem(
                talker=result.talker,
                talker_name=result.talker_name,
                content=done_text,
                category=result.needs[0].category if result.needs else "other",
                created_at=now,
                status="done",
                done_at=now,
            )
            self.store.save_todo(item)

    def mark_done(self, todo_id: int):
        """标记待办为已完成。"""
        self.store.update_todo_status(todo_id, "done", done_at=datetime.now().isoformat())
        logger.info("[TODO] #%d 已完成", todo_id)

    def get_pending_todos(self, talker: str = None) -> list[TodoItem]:
        """获取待办事项列表。"""
        return self.store.get_todos(status="pending", talker=talker)

    def get_overdue_todos(self, overdue_days: int = 3) -> list[TodoItem]:
        """获取超期待办。"""
        threshold = datetime.now() - timedelta(days=overdue_days)
        pending = self.get_pending_todos()
        overdue = []
        for t in pending:
            try:
                created = datetime.fromisoformat(t.created_at)
                if created < threshold:
                    t.status = "overdue"
                    overdue.append(t)
            except (ValueError, TypeError):
                pass
        return overdue

    def generate_reminder(self, granularity: str = "daily") -> str:
        """生成提醒文案。

        Args:
            granularity: "daily"(每天) / "hourly"(每小时)

        Returns:
            提醒文案（可直接输出到终端或写入文件）
        """
        now = datetime.now()
        pending = self.get_pending_todos()
        overdue = self.get_overdue_todos()

        lines = []
        header = f"📋 待办提醒 ({now.strftime('%Y-%m-%d %H:%M')})\n"
        header += "=" * 50 + "\n"
        lines.append(header)

        if not pending and not overdue:
            lines.append("✅ 当前无待办事项，保持节奏！\n")
            return "\n".join(lines)

        # 超期事项（红色标记）
        if overdue:
            lines.append(f"🔴 超期待办（{len(overdue)} 项）:\n")
            for t in overdue:
                customer = t.talker_name or t.talker
                lines.append(f"  ⚠ [{customer}] {t.content}")
                try:
                    created = datetime.fromisoformat(t.created_at)
                    days_ago = (now - created).days
                    lines.append(f"     (已过 {days_ago} 天)\n")
                except Exception:
                    lines.append("")

        # 高优先级待办
        high_priority = [t for t in pending if t.urgency == "high" and t.status != "overdue"]
        if high_priority:
            lines.append(f"🔥 高优先级（{len(high_priority)} 项）:\n")
            for t in high_priority:
                customer = t.talker_name or t.talker
                deadline_str = f" 截止:{t.deadline}" if t.deadline else ""
                lines.append(f"  ★ [{customer}] {t.content}{deadline_str}\n")

        # 普通待办
        normal = [t for t in pending if t.urgency != "high" and t.status != "overdue"]
        if normal:
            lines.append(f"📝 待办（{len(normal)} 项）:\n")
            for t in normal:
                customer = t.talker_name or t.talker
                lines.append(f"  · [{customer}] {t.content}\n")

        # 按客户分组汇总
        by_customer: dict[str, list[TodoItem]] = {}
        for t in pending + overdue:
            key = t.talker_name or t.talker
            by_customer.setdefault(key, []).append(t)

        if by_customer:
            lines.append("\n" + "-" * 50)
            lines.append("👥 按客户汇总:\n")
            for customer, todos in sorted(by_customer.items(), key=lambda x: -len(x[1])):
                lines.append(f"  {customer}: {len(todos)} 项待办")

        return "\n".join(lines)

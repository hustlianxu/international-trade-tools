"""大模型 LLM 抽象基类与数据结构。

定义 LLMEngine ABC（各厂商 analyzer 的统一接口）以及 AnalysisResult / CustomerNeed
数据类。这两个数据类历史上位于 deepseek_analyzer.py，已迁移至此以供所有厂商复用，
字段完全保持向后兼容（仅新增可选字段 provider / sub_results）。
"""
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CustomerNeed:
    """客户需求结构。"""
    category: str          # inquiry(询价) / quotation(报价) / sample(样品) / order(订单) / logistics(物流) / payment(付款) / complaint(投诉) / other
    summary: str           # 需求摘要（中文）
    details: str = ""      # 详情
    product: str = ""      # 涉及产品
    quantity: str = ""     # 数量
    deadline: str = ""     # 截止日期（如客户提及）
    urgency: str = "normal"  # high / normal / low


@dataclass
class AnalysisResult:
    """单次对话分析结果。"""
    talker: str
    talker_name: str = ""
    analyzed_at: str = ""
    language: str = ""        # 检测到的主语言 es/zh/en
    summary: str = ""         # 整体摘要（中文）
    needs: list[CustomerNeed] = field(default_factory=list)
    done_items: list[str] = field(default_factory=list)      # 已办
    todo_items: list[str] = field(default_factory=list)      # 待办
    customer_mood: str = ""    # 客户情绪
    raw_text: str = ""         # 原始文本（拼接所有消息）
    # ── 新增可选字段（向后兼容） ──
    provider: str = ""                 # 产出该结果的具体厂商 id
    sub_results: list["AnalysisResult"] = field(default_factory=list)  # 多厂商时保留各子结果


class LLMEngine(ABC):
    """大模型分析引擎抽象接口。

    各厂商 analyzer 继承此类并实现 name() / chat()。
    analyze_dialog() 提供了基于 ANALYSIS_PROMPT 的通用实现，子类一般无需重写。
    """

    @abstractmethod
    def name(self) -> str:
        """厂商显示名，用于日志与 UI。"""
        ...

    @abstractmethod
    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        """通用对话接口，返回模型文本响应。

        Args:
            system: system prompt
            user: user content
            json_mode: 是否请求 JSON 输出格式（厂商支持则启用，不支持则忽略）
        """
        ...

    def analyze_dialog(
        self,
        talker: str,
        talker_name: str,
        messages: list[dict],  # [{"is_sender": 0/1, "text": "...", "time": "..."}]
    ) -> AnalysisResult:
        """分析一段对话，提取需求与待办。

        默认实现：拼接对话文本 → 调用 chat() → 解析 JSON → 构建 AnalysisResult。
        子类通常直接复用，无需重写。

        Args:
            talker: 客户 wxid
            talker_name: 客户昵称
            messages: 消息列表（文字 + 语音转写后的文本）

        Returns:
            AnalysisResult 分析结果
        """
        from src.llm.prompt import ANALYSIS_PROMPT

        # 拼接对话文本
        dialog_lines = []
        for m in messages:
            role = "我方" if m.get("is_sender") else "客户"
            text = m.get("text", "").strip()
            if not text:
                continue
            time_str = m.get("time", "")
            dialog_lines.append(f"[{time_str}] {role}: {text}")
        dialog_text = "\n".join(dialog_lines)

        if not dialog_text.strip():
            return AnalysisResult(
                talker=talker,
                talker_name=talker_name,
                analyzed_at=datetime.now().isoformat(),
                provider=self._provider_id(),
            )

        prompt = ANALYSIS_PROMPT.format(dialog_text=dialog_text)
        logger.info(
            "[%s] 分析 %s 的对话 (%d 字)...",
            self.name(), talker_name or talker, len(dialog_text),
        )

        raw_response = ""
        try:
            raw_response = self.chat(
                system="你是一位严谨的外贸业务分析助手，只输出 JSON，不要任何解释。",
                user=prompt,
                json_mode=True,
            )
            data = self._parse_json_response(raw_response)
        except json.JSONDecodeError as e:
            logger.error("[%s] JSON 解析失败: %s, 原始: %s", self.name(), e, raw_response[:200])
            data = {"summary": "分析失败", "needs": [], "done_items": [], "todo_items": []}
        except Exception as e:
            logger.error("[%s] 调用失败: %s", self.name(), e)
            data = {"summary": f"分析异常: {e}", "needs": [], "done_items": [], "todo_items": []}

        # 解析需求
        needs = []
        for n in data.get("needs", []):
            needs.append(CustomerNeed(
                category=n.get("category", "other"),
                summary=n.get("summary", ""),
                product=n.get("product", ""),
                quantity=n.get("quantity", ""),
                deadline=n.get("deadline", ""),
                urgency=n.get("urgency", "normal"),
            ))

        result = AnalysisResult(
            talker=talker,
            talker_name=talker_name,
            analyzed_at=datetime.now().isoformat(),
            language=data.get("language", ""),
            summary=data.get("summary", ""),
            needs=needs,
            done_items=data.get("done_items", []),
            todo_items=data.get("todo_items", []),
            customer_mood=data.get("customer_mood", ""),
            raw_text=dialog_text,
            provider=self._provider_id(),
        )
        logger.info(
            "[%s] 完成: %d 个需求, %d 待办, %d 已办",
            self.name(), len(needs), len(result.todo_items), len(result.done_items),
        )
        return result

    # ── 子类可重写以提供自己的厂商 id（用于 AnalysisResult.provider） ──
    def _provider_id(self) -> str:
        """返回厂商 id（与配置 key 对应，如 'deepseek' / 'openai'）。"""
        return self.name()

    # ── 共享辅助 ──
    @staticmethod
    def _parse_json_response(text: str) -> dict:
        """从 LLM 文本响应中提取 JSON 对象。

        优先直接 json.loads；失败则尝试提取 ```json``` 代码块或第一个 {...} 段。
        """
        if not text:
            return {}
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # 尝试第一个 {...} 段（贪婪到匹配括号）
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            pass
                        break
        raise json.JSONDecodeError("无法从响应中提取 JSON", text, 0)

"""DeepSeek 大模型客户需求分析。

将微信消息（文字 + 语音转写文本）交给 DeepSeek，提取：
- 客户需求要点（询价/报价/样品/订单/物流/付款等）
- 已办事项（已完成的动作）
- 待办事项（需跟进的任务）
- 客户情绪与紧迫度

DeepSeek V4-Flash 月费 < 1 元（500条×300tokens），西语业务理解能力强。
"""
import json
import logging
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


# 分析 prompt（中英双语，确保 DeepSeek 理解西语原文 + 中文输出）
ANALYSIS_PROMPT = """你是一位资深的外贸业务助手。请分析以下客户微信对话（可能为西班牙语、中文或混合），提取关键信息。

=== 客户对话内容 ===
{dialog_text}

=== 输出要求（严格 JSON，不要 markdown 代码块）===
{{
  "language": "检测到的主语言代码(es/zh/en)",
  "summary": "本次对话的整体摘要（用中文，2-3句话）",
  "needs": [
    {{
      "category": "分类: inquiry/quotation/sample/order/logistics/payment/complaint/other",
      "summary": "需求摘要（中文）",
      "product": "涉及的产品名（原文+中文）",
      "quantity": "数量（如有）",
      "deadline": "截止日期（如有，ISO格式）",
      "urgency": "high/normal/low"
    }}
  ],
  "done_items": ["已完成的动作列表（中文，如：已发送报价单、已确认样品规格）"],
  "todo_items": ["需要跟进的待办事项（中文，如：回复客户关于交期的疑问、准备形式发票）"],
  "customer_mood": "客户情绪（中文，如：积极/急切/不满/平和）"
}}

=== 分析要点 ===
1. 需求分类要准确：询价=inquiry，报价=quotation，要样品=sample，下订单=order，问物流=logistics，问付款=payment
2. 注意西语外贸用语：cotización=报价，pedido=订单，envío=发货，muestra=样品，pago=付款，factura proforma=形式发票
3. 待办事项要具体可执行，每条都是一个明确动作
4. 已办事项是业务员已经做的（发出消息中体现的）
5. 如果对话太短无法分析，needs/todo_items 返回空数组
"""


class DeepSeekAnalyzer:
    """DeepSeek 客户需求分析器。"""

    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "https://api.deepseek.com")
        self.model = config.get("model", "deepseek-chat")
        self.temperature = config.get("temperature", 0.3)
        self.max_tokens = config.get("max_tokens", 2000)
        if not self.api_key:
            raise RuntimeError("DeepSeek 需配置 api_key")

    def _call_deepseek(self, system_prompt: str, user_content: str) -> str:
        """调用 DeepSeek API（兼容 OpenAI 接口）。"""
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""

    def analyze_dialog(
        self,
        talker: str,
        talker_name: str,
        messages: list[dict],  # [{"is_sender": 0/1, "text": "...", "time": "..."}]
    ) -> AnalysisResult:
        """分析一段对话，提取需求与待办。

        Args:
            talker: 客户 wxid
            talker_name: 客户昵称
            messages: 消息列表（文字 + 语音转写后的文本）

        Returns:
            AnalysisResult 分析结果
        """
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
            return AnalysisResult(talker=talker, talker_name=talker_name, analyzed_at=datetime.now().isoformat())

        # 调用 DeepSeek 分析
        prompt = ANALYSIS_PROMPT.format(dialog_text=dialog_text)
        logger.info("[DeepSeek] 分析 %s 的对话 (%d 字)...", talker_name or talker, len(dialog_text))

        try:
            raw_response = self._call_deepseek(
                system_prompt="你是一位严谨的外贸业务分析助手，只输出 JSON，不要任何解释。",
                user_content=prompt,
            )
            data = json.loads(raw_response)
        except json.JSONDecodeError as e:
            logger.error("[DeepSeek] JSON 解析失败: %s, 原始: %s", e, raw_response[:200])
            data = {"summary": "分析失败", "needs": [], "done_items": [], "todo_items": []}
        except Exception as e:
            logger.error("[DeepSeek] 调用失败: %s", e)
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
        )
        logger.info(
            "[DeepSeek] 完成: %d 个需求, %d 待办, %d 已办",
            len(needs), len(result.todo_items), len(result.done_items),
        )
        return result

"""LLM Prompt 常量集中管理。

- ANALYSIS_PROMPT：单厂商分析单段客户对话的 prompt（中英双语，输出 JSON）
- AGGREGATE_PROMPT：多厂商分析结果聚合 prompt（输入各厂商 AnalysisResult JSON，
  输出合并后的单一 AnalysisResult JSON）
"""

# 分析 prompt（中英双语，确保 LLM 理解西语原文 + 中文输出）
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


# 聚合 prompt：把多个厂商对同一段对话的分析结果合并为单一 AnalysisResult
AGGREGATE_PROMPT = """你是一位资深的外贸业务分析专家。下面是对同一段客户微信对话，由 {n} 个不同的大模型分别分析得到的结果。

请综合各家所长（互补的需求点、更准确的待办、更精炼的摘要），合并为单一的分析结果。

=== 各厂商分析结果（JSON 数组） ===
{sub_results_json}

=== 输出要求（严格 JSON，不要 markdown 代码块）===
{{
  "language": "检测到的主语言代码(es/zh/en)",
  "summary": "综合后的整体摘要（用中文，2-3句话）",
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
  "done_items": ["已完成的动作列表（去重后）"],
  "todo_items": ["需要跟进的待办事项（去重后，按优先级排序）"],
  "customer_mood": "客户情绪（中文）"
}}

=== 合并要点 ===
1. needs 要去重合并：相同需求只保留一条，细节取最完整的版本
2. todo_items / done_items 去重，保留可执行性最强的措辞
3. summary 综合各厂商摘要，不丢失任何关键信息
4. 若各厂商 language 判断一致则保持，否则取多数
5. customer_mood 取最具体的描述
"""

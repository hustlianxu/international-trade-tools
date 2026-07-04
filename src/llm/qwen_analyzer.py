"""通义千问 (Qwen / DashScope) 大模型客户需求分析。

DashScope 提供 OpenAI 兼容模式，因此直接继承 OpenAIAnalyzer，仅改默认 base_url 与 model。
官方文档：https://help.aliyun.com/zh/dashscope/developer-reference/compatibility-of-openai-with-dashscope
"""
import logging

from src.llm.openai_analyzer import OpenAIAnalyzer

logger = logging.getLogger(__name__)


class QwenAnalyzer(OpenAIAnalyzer):
    """通义千问客户需求分析器（DashScope OpenAI 兼容模式）。"""

    def __init__(self, config: dict):
        # 注入 Qwen 默认值（用户配置可覆盖）
        config = dict(config)
        config.setdefault("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        config.setdefault("model", "qwen-plus")
        super().__init__(config)

    def name(self) -> str:
        return "Qwen"

    def _provider_id(self) -> str:
        return "qwen"

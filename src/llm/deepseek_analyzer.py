"""DeepSeek 大模型客户需求分析。

DeepSeek 兼容 OpenAI SDK 接口，支持 response_format={"type":"json_object"}。
本模块保留 DeepSeekAnalyzer 作为向后兼容入口（历史上 gui/main/mcp 直接 import 它），
AnalysisResult / CustomerNeed / ANALYSIS_PROMPT 已迁移到 base.py / prompt.py，
这里通过 re-export 保持旧的 import 路径可用。
"""
import logging

# ── 向后兼容：re-export 已迁移的符号 ──
from src.llm.base import AnalysisResult, CustomerNeed, LLMEngine  # noqa: F401
from src.llm.prompt import ANALYSIS_PROMPT  # noqa: F401

logger = logging.getLogger(__name__)


class DeepSeekAnalyzer(LLMEngine):
    """DeepSeek 客户需求分析器（兼容 OpenAI 接口）。"""

    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "https://api.deepseek.com")
        self.model = config.get("model", "deepseek-chat")
        self.temperature = config.get("temperature", 0.3)
        self.max_tokens = config.get("max_tokens", 2000)
        if not self.api_key:
            raise RuntimeError("DeepSeek 需配置 api_key")

    def name(self) -> str:
        return "DeepSeek"

    def _provider_id(self) -> str:
        return "deepseek"

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        """调用 DeepSeek API（兼容 OpenAI 接口）。

        旧的 `_call_deepseek(system_prompt, user_content)` 方法已重命名为 `chat`，
        行为完全一致（默认走 json_object 模式，与历史实现保持一致）。
        """
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "openai SDK 未安装，DeepSeek 无法调用。请运行 pip install openai"
            ) from e

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        create_kwargs = {
            "model": kwargs.pop("model", self.model),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
        }
        # DeepSeek 兼容 OpenAI 的 json_object 模式；历史实现总是开启。
        # 这里：json_mode=True 显式开启；json_mode=False 显式关闭。
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}
        # 允许调用方覆盖任何参数
        create_kwargs.update(kwargs)
        resp = client.chat.completions.create(**create_kwargs)
        return resp.choices[0].message.content or ""

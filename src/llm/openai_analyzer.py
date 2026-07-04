"""OpenAI 大模型客户需求分析。

使用官方 openai SDK，base_url 默认 https://api.openai.com/v1。
支持 response_format={"type":"json_object"}。
"""
import logging

from src.llm.base import LLMEngine

logger = logging.getLogger(__name__)


class OpenAIAnalyzer(LLMEngine):
    """OpenAI 客户需求分析器（GPT 系列）。"""

    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self.model = config.get("model", "gpt-4o-mini")
        self.temperature = config.get("temperature", 0.3)
        self.max_tokens = config.get("max_tokens", 2000)
        if not self.api_key:
            raise RuntimeError("OpenAI 需配置 api_key")

    def name(self) -> str:
        return "OpenAI"

    def _provider_id(self) -> str:
        return "openai"

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "openai SDK 未安装，OpenAI 无法调用。请运行 pip install openai"
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
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}
        create_kwargs.update(kwargs)
        resp = client.chat.completions.create(**create_kwargs)
        return resp.choices[0].message.content or ""

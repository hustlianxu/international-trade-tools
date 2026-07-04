"""Claude (Anthropic) 大模型客户需求分析。

使用 anthropic SDK 的 messages API。

注意：Claude 的 system 是顶层参数（不在 messages 数组内）；
Claude 不支持 response_format json_object，需在 prompt 里强约束 JSON 并用正则兜底。
"""
import logging

from src.llm.base import LLMEngine

logger = logging.getLogger(__name__)


class ClaudeAnalyzer(LLMEngine):
    """Claude 客户需求分析器。"""

    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "claude-3-5-haiku-latest")
        self.max_tokens = config.get("max_tokens", 2000)
        self.temperature = config.get("temperature", 0.3)
        # 可选自定义 endpoint（企业网关等）
        self.base_url = config.get("base_url", "") or ""
        if not self.api_key:
            raise RuntimeError("Claude 需配置 api_key")

    def name(self) -> str:
        return "Claude"

    def _provider_id(self) -> str:
        return "claude"

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK 未安装，Claude 无法调用。请运行 pip install anthropic"
            ) from e

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = anthropic.Anthropic(**client_kwargs)

        # Claude 不支持 response_format json_object，但若调用方请求 json_mode，
        # 在 system 末尾追加强约束提示。
        effective_system = system
        if json_mode:
            effective_system = (
                system
                + "\n\n【强制要求】只输出一个合法的 JSON 对象，不要任何 markdown 代码块、"
                "不要解释文字、不要前后缀。"
            )

        create_kwargs = {
            "model": kwargs.pop("model", self.model),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
            "temperature": kwargs.pop("temperature", self.temperature),
            "system": effective_system,
            "messages": [{"role": "user", "content": user}],
        }
        create_kwargs.update(kwargs)
        resp = client.messages.create(**create_kwargs)
        # 响应 content 是 list[ContentBlock]，提取文本
        text_parts = []
        for block in getattr(resp, "content", []) or []:
            t = getattr(block, "text", None)
            if t:
                text_parts.append(t)
        return "".join(text_parts)

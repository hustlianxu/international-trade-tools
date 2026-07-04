"""Google Gemini 大模型客户需求分析。

使用 google-generativeai SDK（genai.GenerativeModel）。
支持 response_mime_type="application/json" 强制 JSON 输出。
"""
import logging

from src.llm.base import LLMEngine

logger = logging.getLogger(__name__)


class GeminiAnalyzer(LLMEngine):
    """Gemini 客户需求分析器。"""

    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gemini-1.5-flash")
        self.temperature = config.get("temperature", 0.3)
        self.max_tokens = config.get("max_tokens", 2000)
        if not self.api_key:
            raise RuntimeError("Gemini 需配置 api_key")

    def name(self) -> str:
        return "Gemini"

    def _provider_id(self) -> str:
        return "gemini"

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise RuntimeError(
                "google-generativeai SDK 未安装，Gemini 无法调用。"
                "请运行 pip install google-generativeai"
            ) from e

        genai.configure(api_key=self.api_key)

        # system_instruction 通过 system_instruction 参数传入
        generation_config = {
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_output_tokens": kwargs.pop("max_output_tokens", self.max_tokens),
        }
        if json_mode:
            generation_config["response_mime_type"] = "application/json"

        model_name = kwargs.pop("model", self.model)
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system,
            generation_config=generation_config,
        )
        resp = model.generate_content(user)
        # 兼容不同版本：优先取 text 属性
        try:
            return resp.text or ""
        except Exception:
            # 某些版本在 candidates[0].content.parts
            for cand in getattr(resp, "candidates", []) or []:
                content = getattr(cand, "content", None)
                for part in getattr(content, "parts", []) or []:
                    t = getattr(part, "text", None)
                    if t:
                        return t
            return ""

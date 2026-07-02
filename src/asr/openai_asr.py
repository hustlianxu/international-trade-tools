"""OpenAI gpt-4o-mini-transcribe 语音识别（最高准确率）。

成本：$0.003/分钟 ≈ 0.0216 元/分钟，500 分钟/月 ≈ 10.8 元。
西语 WER 约 2.7-3%，适合对准确率要求最高的场景。
"""
import logging
from pathlib import Path

from .base import ASREngine

logger = logging.getLogger(__name__)


class OpenAIASR(ASREngine):
    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o-mini-transcribe")
        if not self.api_key:
            raise RuntimeError("OpenAI ASR 需配置 api_key")

    def transcribe(self, audio_path: str | Path, language: str = "") -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)

        with open(audio_path, "rb") as f:
            kwargs = {"model": self.model, "file": f}
            if language:
                kwargs["language"] = language
            result = client.audio.transcriptions.create(**kwargs)

        text = (result.text or "").strip()
        logger.info("[OpenAI ASR] 完成: %s", text[:100])
        return text

    def name(self) -> str:
        return f"OpenAI({self.model})"

"""本地 MLX Whisper 语音识别（Apple Silicon M3 优化）。

优势：完全免费、离线可用、隐私好（客户语音不上云）。
M3 实测：medium 模型约 7 秒/分钟音频，西语 WER 4-6%。
"""
import logging
from pathlib import Path

from .base import ASREngine

logger = logging.getLogger(__name__)


class MLXWhisperASR(ASREngine):
    def __init__(self, config: dict):
        self.model = config.get("model", "mlx-community/whisper-medium-mlx-8bit")
        self.language = config.get("language", "")
        self.initial_prompt = config.get("initial_prompt", "")
        self._loaded_model = None

    def _ensure_model(self):
        """懒加载模型（首次调用时下载/加载，约 1-2 秒）。"""
        if self._loaded_model is None:
            try:
                import mlx_whisper
                self._loaded_model = mlx_whisper
            except ImportError:
                raise RuntimeError(
                    "mlx-whisper 未安装。Mac M3 请运行: pip install mlx-whisper\n"
                    "Windows/Linux 请改用 volcengine 或 openai 引擎"
                )
        return self._loaded_model

    def transcribe(self, audio_path: str | Path, language: str = "") -> str:
        mlx = self._ensure_model()
        audio_str = str(audio_path)
        lang = language or self.language or None

        logger.info("[MLX Whisper] 转写中: %s (语言=%s)", audio_str, lang or "自动检测")
        result = mlx.transcribe(
            audio_str,
            path_or_hf_repo=self.model,
            language=lang,
            initial_prompt=self.initial_prompt or None,
        )
        text = (result.get("text") or "").strip()
        logger.info("[MLX Whisper] 完成: %s", text[:100])
        return text

    def name(self) -> str:
        return f"MLX-Whisper({self.model.split('/')[-1]})"

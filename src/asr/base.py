"""ASR 语音识别抽象基类。"""
from abc import ABC, abstractmethod
from pathlib import Path


class ASREngine(ABC):
    """语音转文字引擎抽象接口。"""

    @abstractmethod
    def transcribe(self, audio_path: str | Path, language: str = "") -> str:
        """将音频文件转为文字。

        Args:
            audio_path: WAV/MP3 音频文件路径
            language: 语言代码（"es"=西语, "zh"=中文, ""=自动检测）

        Returns:
            转写文本
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """引擎名称，用于日志。"""
        ...


def create_asr(config: dict) -> ASREngine:
    """根据配置创建 ASR 引擎实例。"""
    engine = config.get("engine", "volcengine")
    if engine == "mlx_whisper":
        # 检测 mlx_whisper 是否可用（精简模式打包不含）
        try:
            import mlx_whisper  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "mlx_whisper 未安装。\n"
                "当前为精简版打包（不含 MLX Whisper，体积约 80M）。\n"
                "解决方案（任选其一）:\n"
                "  1. 改用 volcengine 或 openai 引擎（在「配置」页修改）\n"
                "  2. 重新打包完整版: INCLUDE_MLX=1 bash build_mac.sh（体积约 600M，含本地免费 ASR）"
            )
        from .mlx_whisper_asr import MLXWhisperASR
        return MLXWhisperASR(config["mlx_whisper"])
    elif engine == "volcengine":
        from .volcengine_asr import VolcengineASR
        return VolcengineASR(config["volcengine"])
    elif engine == "openai":
        from .openai_asr import OpenAIASR
        return OpenAIASR(config["openai"])
    else:
        raise ValueError(f"不支持的 ASR 引擎: {engine}")

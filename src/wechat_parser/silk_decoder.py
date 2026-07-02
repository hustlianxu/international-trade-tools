"""微信 SILK v3 语音解码为 WAV。

微信 PC 端语音格式为 SILK v3，且在标准 SILK 基础上做了修改：
- 开头插入了 1 字节 b'\\x02'
- 去除了结尾的 b'\\xFF\\xFF'

本模块用 pilk（C 绑定，pip 安装即用）解码为 PCM，再用标准库转 WAV。
"""
import logging
import struct
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

# 微信 SILK 的特殊头字节
WECHAT_SILK_HEADER = b"\x02"
# 标准 SILK 魔数
SILK_V3_HEADER = b"#!SILK_V3"


def is_wechat_silk(data: bytes) -> bool:
    """判断是否为微信格式的 SILK 语音。"""
    return len(data) > 1 and data[0:1] == WECHAT_SILK_HEADER


def normalize_wechat_silk(data: bytes) -> bytes:
    """将微信 SILK 转为标准 SILK 格式（供 pilk 解码）。

    微信格式: [0x02][SILK 数据]
    标准格式: [#!SILK_V3][SILK 数据][\\xFF\\xFF]
    """
    if is_wechat_silk(data):
        # 去掉 0x02 头，加上标准 SILK 头
        silk_body = data[1:]
        return SILK_V3_HEADER + silk_body + b"\xFF\xFF"
    # 已经是标准格式或未知格式，原样返回
    return data


def silk_to_wav(silk_path: str | Path, wav_path: str | Path, sample_rate: int = 24000) -> float:
    """将微信 SILK 语音文件转为 WAV。

    Args:
        silk_path: SILK 文件路径
        wav_path: 输出 WAV 文件路径
        sample_rate: 采样率，微信语音通常 24000Hz

    Returns:
        语音时长（秒）
    """
    try:
        import pilk
    except ImportError:
        raise RuntimeError(
            "pilk 未安装。请运行: pip install pilk\n"
            "pilk 是 SILK v3 的 Python 绑定，自带 x64-win 预编译库"
        )

    silk_path = Path(silk_path)
    wav_path = Path(wav_path)
    raw_data = silk_path.read_bytes()

    # 归一化为标准 SILK 格式
    standard_silk = normalize_wechat_silk(raw_data)

    # 写临时标准 SILK 文件
    tmp_silk = wav_path.with_suffix(".silk.tmp")
    tmp_silk.write_bytes(standard_silk)

    # PCM 临时文件
    pcm_path = wav_path.with_suffix(".pcm")
    try:
        # pilk.decode: SILK → PCM
        duration_ms = pilk.decode(str(tmp_silk), str(pcm_path), sample_rate=sample_rate)

        # PCM → WAV（用标准库 wave，无需 ffmpeg）
        with open(pcm_path, "rb") as pcm_f:
            pcm_data = pcm_f.read()
        with wave.open(str(wav_path), "wb") as wav_f:
            wav_f.setnchannels(1)  # 微信语音单声道
            wav_f.setsampwidth(2)  # 16bit
            wav_f.setframerate(sample_rate)
            wav_f.writeframes(pcm_data)

        duration_sec = duration_ms / 1000.0
        logger.info("[SILK] %s → %s (%.1f秒)", silk_path.name, wav_path.name, duration_sec)
        return duration_sec
    finally:
        tmp_silk.unlink(missing_ok=True)
        pcm_path.unlink(missing_ok=True)


def get_silk_duration(silk_path: str | Path) -> float:
    """获取 SILK 语音时长（秒），不解码。"""
    try:
        import pilk
    except ImportError:
        return 0.0
    return pilk.duration(str(silk_path)) / 1000.0

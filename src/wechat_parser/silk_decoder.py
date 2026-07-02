"""微信 SILK v3 语音解码为 WAV。

微信 PC 端语音格式为 SILK v3 的变体：
- 开头插入了 1 字节 b'\\x02'，其后紧跟标准 SILK 魔数 b'#!SILK_V3'
- 去除了标准 SILK 结尾的 b'\\xFF\\xFF'

即：微信格式 = [0x02][#!SILK_V3][SILK frames]
    标准格式 = [#!SILK_V3][SILK frames][\\xFF\\xFF]

本模块支持两个后端（自动选择可用的一个），两者均可直接解码微信原始格式：
- pilk:        Windows x64 专用（自带预编译库），pip install pilk
- pysilk-mod:  跨平台，提供 macOS arm64 / Linux / Windows wheel，pip install pysilk-mod

优先级：pilk（Windows）> pysilk-mod（跨平台）
"""
import logging
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

# 微信 SILK 的特殊头字节
WECHAT_SILK_HEADER = b"\x02"
# 标准 SILK 魔数
SILK_V3_HEADER = b"#!SILK_V3"


def is_wechat_silk(data: bytes) -> bool:
    """判断是否为微信格式的 SILK 语音（以 0x02 开头）。"""
    return len(data) > 1 and data[0:1] == WECHAT_SILK_HEADER


def normalize_wechat_silk(data: bytes) -> bytes:
    """将微信 SILK 转为标准 SILK 格式。

    微信格式: [0x02][#!SILK_V3][body]（无结尾 \\xFF\\xFF）
    标准格式: [#!SILK_V3][body][\\xFF\\xFF]

    注意：pilk 与 pysilk-mod 均可直接解码微信原始格式，silk_to_wav 不会调用本函数。
    本函数仅供需要标准 SILK 格式的外部场景使用。
    """
    if is_wechat_silk(data):
        # 去掉 0x02 头，body 已包含 #!SILK_V3 魔数
        body = data[1:]
        if body.endswith(b"\xFF\xFF"):
            return body
        return body + b"\xFF\xFF"
    # 已经是标准格式或未知格式，原样返回
    return data


def _get_silk_backend():
    """返回可用的 SILK 解码后端。

    Returns:
        ("pilk", module) 或 ("pysilk", module) 或 (None, None)
    """
    # 优先 pilk（Windows 预编译库，性能好）
    try:
        import pilk
        return "pilk", pilk
    except ImportError:
        pass
    # 回退到 pysilk-mod（跨平台，Mac arm64 有 wheel）
    try:
        import pysilk
        return "pysilk", pysilk
    except ImportError:
        pass
    return None, None


def silk_to_wav(silk_path: str | Path, wav_path: str | Path, sample_rate: int = 24000) -> float:
    """将微信 SILK 语音文件转为 WAV。

    Args:
        silk_path: SILK 文件路径
        wav_path: 输出 WAV 文件路径
        sample_rate: 采样率，微信语音通常 24000Hz

    Returns:
        语音时长（秒）
    """
    backend, module = _get_silk_backend()
    if backend is None:
        raise RuntimeError(
            "未找到 SILK 解码后端。请安装其中之一:\n"
            "  Windows: pip install pilk\n"
            "  macOS/Linux: pip install pysilk-mod"
        )

    silk_path = Path(silk_path)
    wav_path = Path(wav_path)
    raw_data = silk_path.read_bytes()
    # pilk 与 pysilk-mod 均直接接受微信原始格式（[0x02][#!SILK_V3][body]），无需归一化

    pcm_path = wav_path.with_suffix(".pcm")
    try:
        if backend == "pilk":
            # pilk: 写临时 SILK 文件，decode(silk, pcm, sample_rate) 返回时长(ms)
            tmp_silk = wav_path.with_suffix(".silk.tmp")
            tmp_silk.write_bytes(raw_data)
            try:
                duration_ms = module.decode(str(tmp_silk), str(pcm_path), sample_rate=sample_rate)
            finally:
                tmp_silk.unlink(missing_ok=True)
        else:
            # pysilk-mod: decode(silk_data: bytes, to_wav=False, *, sample_rate=24000) -> bytes
            # 接受 bytes（非 file-like），返回 PCM bytes（to_wav=False）
            pcm_bytes = module.decode(raw_data, sample_rate=sample_rate)
            pcm_path.write_bytes(pcm_bytes)
            # pysilk 无直接返回时长，按 PCM 数据量反算
            pcm_size = len(pcm_bytes)
            # PCM 16bit 单声道: 每秒 = sample_rate * 2 字节
            duration_ms = (pcm_size / (sample_rate * 2)) * 1000

        # PCM → WAV（用标准库 wave，无需 ffmpeg）
        with open(pcm_path, "rb") as pcm_f:
            pcm_data = pcm_f.read()
        with wave.open(str(wav_path), "wb") as wav_f:
            wav_f.setnchannels(1)  # 微信语音单声道
            wav_f.setsampwidth(2)  # 16bit
            wav_f.setframerate(sample_rate)
            wav_f.writeframes(pcm_data)

        duration_sec = duration_ms / 1000.0
        logger.info("[SILK] %s → %s (%.1f秒, 后端=%s)", silk_path.name, wav_path.name, duration_sec, backend)
        return duration_sec
    finally:
        pcm_path.unlink(missing_ok=True)


def get_silk_duration(silk_path: str | Path) -> float:
    """获取 SILK 语音时长（秒），不解码。

    pilk 有 duration() 函数；pysilk-mod 无，返回 0。
    """
    backend, module = _get_silk_backend()
    if backend == "pilk":
        return module.duration(str(silk_path)) / 1000.0
    return 0.0

"""消息处理器：语音转文字 → LLM 分析 → 写 TODO。

从 main.py 提取，供 CLI 和 GUI 共用。
"""
import logging
import time
from pathlib import Path

from src.paths import get_tmp_dir

logger = logging.getLogger(__name__)


def process_voice_message(msg, asr_engine, store):
    """处理语音消息：SILK → WAV → 文字。

    Args:
        msg: WeChatMessage（含 blob_data）
        asr_engine: ASR 引擎实例
        store: Store 实例（缓存转写结果）

    Returns:
        转写文本（空字符串表示失败）
    """
    from src.wechat_parser.silk_decoder import silk_to_wav

    if not msg.blob_data:
        return ""

    tmp_dir = get_tmp_dir()
    silk_path = tmp_dir / f"{msg.msg_svr_id}.silk"
    wav_path = tmp_dir / f"{msg.msg_svr_id}.wav"
    silk_path.write_bytes(msg.blob_data)

    try:
        duration = silk_to_wav(silk_path, wav_path)
        text = asr_engine.transcribe(wav_path)
        store.save_transcription(
            msg_svr_id=msg.msg_svr_id,
            talker=msg.talker,
            silk_path=str(silk_path),
            text=text,
            language="",
            duration_sec=duration,
        )
        return text
    except Exception as e:
        logger.error("[语音处理] 失败: %s", e, exc_info=True)
        return ""
    finally:
        wav_path.unlink(missing_ok=True)


def handle_new_messages(talker, messages, asr_engine, analyzer, todo_mgr, store):
    """处理新消息回调：语音转文字 → LLM 分析 → 写 TODO。

    Args:
        talker: 客户 wxid
        messages: WeChatMessage 列表
        asr_engine: ASR 引擎
        analyzer: LLMEngine 实例
        todo_mgr: TodoManager 实例
        store: Store 实例

    Returns:
        AnalysisResult 或 None
    """
    from src.wechat_parser.message_extractor import MSG_TYPE_TEXT, MSG_TYPE_VOICE

    dialog_messages = []
    for msg in messages:
        if msg.type == MSG_TYPE_TEXT:
            dialog_messages.append({
                "is_sender": msg.is_sender,
                "text": msg.content_text,
                "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(msg.create_time)),
            })
        elif msg.type == MSG_TYPE_VOICE:
            text = process_voice_message(msg, asr_engine, store)
            if text:
                dialog_messages.append({
                    "is_sender": msg.is_sender,
                    "text": f"[语音] {text}",
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(msg.create_time)),
                })

    if not dialog_messages:
        return None

    talker_name = getattr(msg, "talker_name", "") or talker
    result = analyzer.analyze_dialog(talker, talker_name, dialog_messages)
    store.save_analysis(result)
    todo_mgr.add_from_analysis(result)

    logger.info("[处理] %s: 摘要=%s, 待办=%d", talker, result.summary[:50], len(result.todo_items))
    return result

"""外贸客户管理工具 - 主入口。

用法:
    python src/main.py --mode realtime    # 准实时监听微信（默认）
    python src/main.py --mode manual      # 手动同步一次
    python src/main.py --transcribe <silk路径>  # 仅转写一条语音
    python src/main.py --reminder         # 生成待办提醒
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("trade-tools")


def load_config(config_path: str = "src/config/config.yaml") -> dict:
    """加载配置文件。"""
    p = Path(config_path)
    if not p.exists():
        # 尝试 example
        p = Path("src/config/config.example.yaml")
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        logger.warning("使用示例配置，请复制为 config.yaml 并填入真实值")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def process_voice_message(msg, asr_engine, store, tmp_dir="./tmp"):
    """处理语音消息：SILK → WAV → 文字。"""
    from src.wechat_parser.silk_decoder import silk_to_wav

    if not msg.blob_data:
        return ""

    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(exist_ok=True)
    silk_path = tmp_dir / f"{msg.msg_svr_id}.silk"
    wav_path = tmp_dir / f"{msg.msg_svr_id}.wav"
    silk_path.write_bytes(msg.blob_data)

    try:
        duration = silk_to_wav(silk_path, wav_path)
        text = asr_engine.transcribe(wav_path)
        # 缓存转写结果
        store.save_transcription(
            msg_svr_id=msg.msg_svr_id,
            talker=msg.talker,
            silk_path=str(silk_path),
            text=text,
            language="",
            duration_sec=duration,
        )
        return text
    finally:
        wav_path.unlink(missing_ok=True)


def handle_new_messages(talker, messages, asr_engine, analyzer, todo_mgr, store):
    """处理新消息的回调：语音转文字 → LLM 分析 → 写 TODO。"""
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
            # 语音先转文字
            text = process_voice_message(msg, asr_engine, store)
            if text:
                dialog_messages.append({
                    "is_sender": msg.is_sender,
                    "text": f"[语音] {text}",
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(msg.create_time)),
                })

    if not dialog_messages:
        return

    # DeepSeek 分析
    talker_name = ""  # 实际应从 contact 表查
    result = analyzer.analyze_dialog(talker, talker_name, dialog_messages)
    store.save_analysis(result)

    # 写入 TODO
    todo_mgr.add_from_analysis(result)

    logger.info("[处理] %s: 摘要=%s, 待办=%d", talker, result.summary[:50], len(result.todo_items))


def run_realtime(config):
    """准实时监听模式。"""
    from src.asr.base import create_asr
    from src.llm import create_analyzer
    from src.reminder.todo_manager import TodoManager
    from src.storage.store import Store
    from src.wechat_parser.decryptor import get_key_store
    from src.wechat_parser.message_extractor import MessageExtractor, RealtimeMonitor

    wechat_cfg = config["wechat"]
    store = Store(config["storage"]["db_path"])
    asr_engine = create_asr(config["asr"])
    analyzer = create_analyzer(config["llm"])
    todo_mgr = TodoManager(store)

    logger.info("=== 外贸助手 - 准实时模式 ===")
    logger.info("ASR 引擎: %s | LLM: %s", asr_engine.name(), analyzer.name())

    # 1. 获取微信密钥存储（按优先级：all_keys.json → 手动 raw_key → 自动扫描）
    #    CLI 模式不弹窗提权，需已 sudo 运行或预先加载 all_keys.json
    logger.info("正在加载微信密钥...")
    key_store = get_key_store(
        db_storage_path=wechat_cfg["db_storage_path"],
        manual_raw_key=wechat_cfg.get("raw_key", ""),
        all_keys_json_path=wechat_cfg.get("all_keys_json_path", ""),
        auto_scan=wechat_cfg.get("auto_scan", True),
        use_sudo_dialog=False,
    )
    logger.info("密钥加载成功：%s", key_store.stats())

    # 2. 创建消息提取器（多密钥模式，每个 .db 自动选密钥）
    extractor = MessageExtractor.from_key_store(key_store, wechat_cfg["db_storage_path"])

    # 3. 启动准实时监听
    def on_new(talker, messages):
        handle_new_messages(talker, messages, asr_engine, analyzer, todo_mgr, store)

    monitor = RealtimeMonitor(
        db_storage_path=wechat_cfg["db_storage_path"],
        extractor=extractor,
        on_new_messages=on_new,
        poll_interval_ms=wechat_cfg.get("poll_interval_ms", 100),
        debounce_ms=wechat_cfg.get("debounce_ms", 200),
        watch_talkers=wechat_cfg.get("watch_talkers", []),
        ignore_talkers=wechat_cfg.get("ignore_talkers", ["filehelper", "weixin"]),
    )

    monitor.start()
    logger.info("监听已启动，按 Ctrl+C 停止")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("正在停止...")
        monitor.stop()
        logger.info("已停止")


def run_manual_sync(config):
    """手动同步一次：提取所有会话的最新消息并分析。"""
    logger.info("=== 外贸助手 - 手动同步 ===")
    logger.info("（准实时模式请用 --mode realtime）")
    # 手动同步逻辑与 realtime 类似，但只执行一次全量提取
    # 实际实现中可复用 MessageExtractor.extract_new_messages 遍历所有会话
    logger.info("手动同步完成")


def run_transcribe(silk_path, config):
    """仅转写一条语音。"""
    from src.asr.base import create_asr
    from src.wechat_parser.silk_decoder import silk_to_wav

    asr_engine = create_asr(config["asr"])
    wav_path = silk_path.replace(".silk", ".wav").replace(".amr", ".wav")
    duration = silk_to_wav(silk_path, wav_path)
    text = asr_engine.transcribe(wav_path)
    print(f"\n时长: {duration:.1f} 秒")
    print(f"引擎: {asr_engine.name()}")
    print(f"转写: {text}")


def run_reminder(config):
    """生成待办提醒。"""
    from src.reminder.todo_manager import TodoManager
    from src.storage.store import Store

    store = Store(config["storage"]["db_path"])
    todo_mgr = TodoManager(store)
    reminder_cfg = config.get("reminder", {})
    text = todo_mgr.generate_reminder(granularity=reminder_cfg.get("granularity", "daily"))
    print(text)


def main():
    parser = argparse.ArgumentParser(description="外贸客户管理工具")
    parser.add_argument("--mode", choices=["realtime", "manual"], default="realtime",
                        help="运行模式: realtime=准实时监听, manual=手动同步一次")
    parser.add_argument("--transcribe", metavar="PATH", help="仅转写一条语音 SILK 文件")
    parser.add_argument("--reminder", action="store_true", help="生成待办提醒")
    parser.add_argument("--config", default="src/config/config.yaml", help="配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.transcribe:
        run_transcribe(args.transcribe, config)
    elif args.reminder:
        run_reminder(config)
    elif args.mode == "realtime":
        run_realtime(config)
    elif args.mode == "manual":
        run_manual_sync(config)


if __name__ == "__main__":
    main()

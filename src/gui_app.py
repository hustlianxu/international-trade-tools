"""外贸助手 GUI 主应用（Tkinter）。

跨平台桌面界面，支持 Windows / macOS / Linux。
启动后自动创建配置目录，首次使用在「配置」页填入 API Key 即可。

用法:
    python src/gui_app.py          # 开发模式
    打包后双击运行                    # 开箱即用
"""
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from src.paths import ensure_default_config, get_app_dir, get_config_path, get_db_path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("trade-tools-gui")


class TradeToolsApp:
    """外贸助手主窗口。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("外贸助手 - 微信 AI 客户管理工具")
        self.root.geometry("900x640")
        self.root.minsize(800, 560)

        # 配置文件
        self.config_path = get_config_path()
        ensure_default_config()
        self.config = self._load_config()

        # 后台监听线程相关
        self.monitor_thread = None
        self.monitor_stop_event = threading.Event()
        self.message_queue: queue.Queue = queue.Queue()
        self.is_monitoring = False

        # 运行时组件（懒加载）
        self._store = None
        self._asr_engine = None
        self._analyzer = None
        self._todo_mgr = None

        # 构建 UI
        self._build_ui()

        # 启动队列轮询
        self._poll_queue()

    # ═══════ 配置加载 ═══════
    def _load_config(self) -> dict:
        try:
            with open(self.config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            # 确保 db_path 指向用户目录
            if not cfg.get("storage", {}).get("db_path"):
                cfg.setdefault("storage", {})["db_path"] = str(get_db_path())
            return cfg
        except Exception as e:
            logger.error("加载配置失败: %s", e)
            return {}

    def _save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.info("配置已保存: %s", self.config_path)
        except Exception as e:
            logger.error("保存配置失败: %s", e)

    # ═══════ 懒加载运行时组件 ═══════
    def _get_store(self):
        if self._store is None:
            from src.storage.store import Store
            db_path = self.config.get("storage", {}).get("db_path") or str(get_db_path())
            self._store = Store(db_path)
        return self._store

    def _get_asr(self):
        if self._asr_engine is None:
            from src.asr.base import create_asr
            self._asr_engine = create_asr(self.config["asr"])
        return self._asr_engine

    def _get_analyzer(self):
        if self._analyzer is None:
            from src.llm.deepseek_analyzer import DeepSeekAnalyzer
            self._analyzer = DeepSeekAnalyzer(self.config["llm"]["deepseek"])
        return self._analyzer

    def _get_todo_mgr(self):
        if self._todo_mgr is None:
            from src.reminder.todo_manager import TodoManager
            self._todo_mgr = TodoManager(self._get_store())
        return self._todo_mgr

    def _reset_runtimes(self):
        """配置变更后重置运行时组件（下次使用时重新创建）。"""
        self._store = None
        self._asr_engine = None
        self._analyzer = None
        self._todo_mgr = None

    # ═══════ UI 构建 ═══════
    def _build_ui(self):
        # 顶部状态栏
        top_frame = ttk.Frame(self.root, padding=(10, 5))
        top_frame.pack(fill=tk.X)
        ttk.Label(top_frame, text="外贸助手", font=("", 14, "bold")).pack(side=tk.LEFT)
        self.status_label = ttk.Label(top_frame, text="状态: 已停止", foreground="gray")
        self.status_label.pack(side=tk.RIGHT)
        self.engine_label = ttk.Label(
            top_frame,
            text=f"ASR: {self.config.get('asr', {}).get('engine', '未配置')}",
            foreground="gray",
        )
        self.engine_label.pack(side=tk.RIGHT, padx=(0, 15))

        # 分隔线
        ttk.Separator(self.root).pack(fill=tk.X, padx=10)

        # 标签页
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.config_tab = ttk.Frame(notebook)
        self.monitor_tab = ttk.Frame(notebook)
        self.transcribe_tab = ttk.Frame(notebook)
        self.todo_tab = ttk.Frame(notebook)

        notebook.add(self.config_tab, text="  配置  ")
        notebook.add(self.monitor_tab, text="  准实时监听  ")
        notebook.add(self.transcribe_tab, text="  语音转写  ")
        notebook.add(self.todo_tab, text="  待办事项  ")

        self._build_config_tab()
        self._build_monitor_tab()
        self._build_transcribe_tab()
        self._build_todo_tab()

    # ──────── 配置页 ────────
    def _build_config_tab(self):
        tab = self.config_tab
        canvas = tk.Canvas(tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        f = scroll_frame
        cfg = self.config

        # ── 微信配置 ──
        ttk.Label(f, text="微信数据配置", font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=tk.W, padx=15, pady=(15, 5)
        )
        ttk.Label(f, text="db_storage 路径:").grid(row=1, column=0, sticky=tk.W, padx=15, pady=3)
        self.cfg_wechat_path = tk.StringVar(value=cfg.get("wechat", {}).get("db_storage_path", ""))
        ttk.Entry(f, textvariable=self.cfg_wechat_path, width=50).grid(row=1, column=1, pady=3)
        ttk.Button(f, text="浏览...", command=self._browse_wechat_path).grid(row=1, column=2, padx=5)

        ttk.Label(f, text="微信进程名:").grid(row=2, column=0, sticky=tk.W, padx=15, pady=3)
        self.cfg_process_name = tk.StringVar(value=cfg.get("wechat", {}).get("process_name", "WeChat.exe"))
        ttk.Entry(f, textvariable=self.cfg_process_name, width=30).grid(row=2, column=1, sticky=tk.W, pady=3)
        ttk.Label(f, text="(Win: WeChat.exe / Mac: 微信)", foreground="gray").grid(
            row=2, column=2, sticky=tk.W
        )

        # ── ASR 配置 ──
        ttk.Label(f, text="语音识别 (ASR) 配置", font=("", 11, "bold")).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, padx=15, pady=(15, 5)
        )
        ttk.Label(f, text="ASR 引擎:").grid(row=4, column=0, sticky=tk.W, padx=15, pady=3)
        self.cfg_asr_engine = tk.StringVar(value=cfg.get("asr", {}).get("engine", "volcengine"))
        engine_combo = ttk.Combobox(
            f, textvariable=self.cfg_asr_engine, width=20, state="readonly",
            values=["volcengine", "mlx_whisper", "openai"],
        )
        engine_combo.grid(row=4, column=1, sticky=tk.W, pady=3)
        engine_combo.bind("<<ComboboxSelected>>", self._on_asr_engine_change)
        ttk.Label(
            f,
            text="Mac M3 选 mlx_whisper(免费)\nWin 选 volcengine(~7元/月)",
            foreground="gray", justify=tk.LEFT,
        ).grid(row=4, column=2, sticky=tk.W, padx=5)

        # 火山豆包
        self.volc_frame = ttk.LabelFrame(f, text="火山豆包 ASR (推荐 Windows)", padding=10)
        self.volc_frame.grid(row=5, column=0, columnspan=3, sticky=tk.EW, padx=15, pady=5)
        ttk.Label(self.volc_frame, text="App ID:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.cfg_volc_appid = tk.StringVar(value=cfg.get("asr", {}).get("volcengine", {}).get("app_id", ""))
        ttk.Entry(self.volc_frame, textvariable=self.cfg_volc_appid, width=40).grid(row=0, column=1, pady=2)
        ttk.Label(self.volc_frame, text="Access Token:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.cfg_volc_token = tk.StringVar(value=cfg.get("asr", {}).get("volcengine", {}).get("access_token", ""))
        ttk.Entry(self.volc_frame, textvariable=self.cfg_volc_token, width=40, show="*").grid(row=1, column=1, pady=2)

        # MLX Whisper
        self.mlx_frame = ttk.LabelFrame(f, text="MLX Whisper (仅 Mac M3, 免费)", padding=10)
        self.mlx_frame.grid(row=6, column=0, columnspan=3, sticky=tk.EW, padx=15, pady=5)
        ttk.Label(self.mlx_frame, text="模型:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.cfg_mlx_model = tk.StringVar(
            value=cfg.get("asr", {}).get("mlx_whisper", {}).get("model", "mlx-community/whisper-medium-mlx-8bit")
        )
        ttk.Entry(self.mlx_frame, textvariable=self.cfg_mlx_model, width=55).grid(row=0, column=1, pady=2)

        # OpenAI
        self.openai_frame = ttk.LabelFrame(f, text="OpenAI (最高准确率, ~11元/月)", padding=10)
        self.openai_frame.grid(row=7, column=0, columnspan=3, sticky=tk.EW, padx=15, pady=5)
        ttk.Label(self.openai_frame, text="API Key:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.cfg_openai_key = tk.StringVar(value=cfg.get("asr", {}).get("openai", {}).get("api_key", ""))
        ttk.Entry(self.openai_frame, textvariable=self.cfg_openai_key, width=50, show="*").grid(row=0, column=1, pady=2)

        self._on_asr_engine_change()  # 初始显示/隐藏

        # ── DeepSeek 配置 ──
        ttk.Label(f, text="DeepSeek 大模型配置 (月费<1元)", font=("", 11, "bold")).grid(
            row=8, column=0, columnspan=3, sticky=tk.W, padx=15, pady=(15, 5)
        )
        ttk.Label(f, text="API Key:").grid(row=9, column=0, sticky=tk.W, padx=15, pady=3)
        self.cfg_deepseek_key = tk.StringVar(value=cfg.get("llm", {}).get("deepseek", {}).get("api_key", ""))
        ttk.Entry(f, textvariable=self.cfg_deepseek_key, width=50, show="*").grid(row=9, column=1, sticky=tk.W, pady=3)
        ttk.Label(f, text="获取: platform.deepseek.com", foreground="gray").grid(row=9, column=2, sticky=tk.W)

        # ── 保存按钮 ──
        ttk.Button(f, text="💾 保存配置", command=self._on_save_config).grid(
            row=10, column=0, columnspan=3, pady=20
        )

    def _on_asr_engine_change(self, event=None):
        engine = self.cfg_asr_engine.get()
        self.volc_frame.grid_remove()
        self.mlx_frame.grid_remove()
        self.openai_frame.grid_remove()
        if engine == "volcengine":
            self.volc_frame.grid()
        elif engine == "mlx_whisper":
            self.mlx_frame.grid()
        elif engine == "openai":
            self.openai_frame.grid()

    def _browse_wechat_path(self):
        path = filedialog.askdirectory(title="选择微信 db_storage 目录")
        if path:
            self.cfg_wechat_path.set(path)

    def _on_save_config(self):
        self.config.setdefault("wechat", {})
        self.config["wechat"]["db_storage_path"] = self.cfg_wechat_path.get()
        self.config["wechat"]["process_name"] = self.cfg_process_name.get()
        self.config.setdefault("asr", {})
        self.config["asr"]["engine"] = self.cfg_asr_engine.get()
        self.config["asr"].setdefault("volcengine", {})
        self.config["asr"]["volcengine"]["app_id"] = self.cfg_volc_appid.get()
        self.config["asr"]["volcengine"]["access_token"] = self.cfg_volc_token.get()
        self.config["asr"].setdefault("mlx_whisper", {})
        self.config["asr"]["mlx_whisper"]["model"] = self.cfg_mlx_model.get()
        self.config["asr"].setdefault("openai", {})
        self.config["asr"]["openai"]["api_key"] = self.cfg_openai_key.get()
        self.config.setdefault("llm", {}).setdefault("deepseek", {})
        self.config["llm"]["deepseek"]["api_key"] = self.cfg_deepseek_key.get()
        self.config.setdefault("storage", {})
        self.config["storage"]["db_path"] = str(get_db_path())

        self._save_config()
        self._reset_runtimes()
        self.engine_label.config(text=f"ASR: {self.cfg_asr_engine.get()}")
        messagebox.showinfo("成功", f"配置已保存到:\n{self.config_path}")

    # ──────── 监听页 ────────
    def _build_monitor_tab(self):
        tab = self.monitor_tab
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        self.btn_start = ttk.Button(btn_frame, text="▶ 启动监听", command=self._start_monitor)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(btn_frame, text="⏹ 停止监听", command=self._stop_monitor, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side=tk.RIGHT, padx=5)

        ttk.Label(tab, text="实时日志:", font=("", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(5, 0))
        self.log_text = tk.Text(tab, height=25, wrap=tk.WORD, font=("Courier", 10))
        log_scroll = ttk.Scrollbar(tab, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=5)

    def _start_monitor(self):
        if self.is_monitoring:
            return
        wechat_path = self.config.get("wechat", {}).get("db_storage_path", "")
        if not wechat_path:
            messagebox.showwarning("提示", "请先在「配置」页填写微信 db_storage 路径")
            return
        deepseek_key = self.config.get("llm", {}).get("deepseek", {}).get("api_key", "")
        if not deepseek_key:
            messagebox.showwarning("提示", "请先在「配置」页填写 DeepSeek API Key")
            return

        self.is_monitoring = True
        self.monitor_stop_event.clear()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.status_label.config(text="状态: 监听中...", foreground="green")
        self._log("启动准实时监听...")

        self.monitor_thread = threading.Thread(
            target=self._monitor_worker, daemon=True, name="wechat-monitor"
        )
        self.monitor_thread.start()

    def _stop_monitor(self):
        if not self.is_monitoring:
            return
        self.is_monitoring = False
        self.monitor_stop_event.set()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.status_label.config(text="状态: 已停止", foreground="gray")
        self._log("正在停止监听...")

    def _monitor_worker(self):
        """后台监听线程：解析微信 → 转写 → 分析 → 通过 queue 通知 UI。"""
        try:
            from src.asr.base import create_asr
            from src.llm.deepseek_analyzer import DeepSeekAnalyzer
            from src.processor import handle_new_messages
            from src.reminder.todo_manager import TodoManager
            from src.storage.store import Store
            from src.wechat_parser.decryptor import WeChatDecryptor, scan_wechat_key
            from src.wechat_parser.message_extractor import MessageExtractor, RealtimeMonitor

            wechat_cfg = self.config["wechat"]
            self.message_queue.put(("log", "正在扫描微信密钥..."))

            raw_key = scan_wechat_key(wechat_cfg["process_name"])
            decryptor = WeChatDecryptor.from_raw_key_hex(raw_key, wechat_cfg["db_storage_path"])
            self.message_queue.put(("log", "密钥获取成功"))

            store = Store(self.config.get("storage", {}).get("db_path") or str(get_db_path()))
            asr_engine = create_asr(self.config["asr"])
            analyzer = DeepSeekAnalyzer(self.config["llm"]["deepseek"])
            todo_mgr = TodoManager(store)

            self.message_queue.put(("log", f"ASR 引擎: {asr_engine.name()}"))
            self.message_queue.put(("log", f"监听目录: {wechat_cfg['db_storage_path']}"))

            extractor = MessageExtractor(decryptor, wechat_cfg["db_storage_path"])

            def on_new(talker, messages):
                if self.monitor_stop_event.is_set():
                    return
                self.message_queue.put(("log", f"收到 {talker} 的 {len(messages)} 条新消息"))
                try:
                    result = handle_new_messages(talker, messages, asr_engine, analyzer, todo_mgr, store)
                    if result:
                        self.message_queue.put(("analysis", result))
                except Exception as e:
                    self.message_queue.put(("log", f"处理失败: {e}"))

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
            self.message_queue.put(("log", "监听已启动，等待新消息..."))

            while not self.monitor_stop_event.is_set():
                time.sleep(0.5)

            monitor.stop()
            self.message_queue.put(("log", "监听已停止"))

        except Exception as e:
            self.message_queue.put(("log", f"监听异常: {e}"))
            logger.error("监听异常", exc_info=True)
        finally:
            self.message_queue.put(("stopped", None))

    # ──────── 转写页 ────────
    def _build_transcribe_tab(self):
        tab = self.transcribe_tab
        input_frame = ttk.Frame(tab)
        input_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(input_frame, text="SILK 语音文件:").grid(row=0, column=0, padx=5)
        self.silk_path_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.silk_path_var, width=50).grid(row=0, column=1, padx=5)
        ttk.Button(input_frame, text="选择文件...", command=self._browse_silk).grid(row=0, column=2, padx=5)

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        self.btn_transcribe = ttk.Button(btn_frame, text="🎙 开始转写", command=self._do_transcribe)
        self.btn_transcribe.pack(side=tk.LEFT, padx=5)
        ttk.Label(btn_frame, text="(支持 .silk / .amr 微信语音文件)", foreground="gray").pack(side=tk.LEFT)

        ttk.Label(tab, text="转写结果:", font=("", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 0))
        result_frame = ttk.Frame(tab)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.transcribe_result = tk.Text(result_frame, height=15, wrap=tk.WORD, font=("", 11))
        tr_scroll = ttk.Scrollbar(result_frame, command=self.transcribe_result.yview)
        self.transcribe_result.configure(yscrollcommand=tr_scroll.set)
        self.transcribe_result.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tr_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _browse_silk(self):
        path = filedialog.askopenfilename(
            title="选择微信语音文件",
            filetypes=[("微信语音", "*.silk *.amr"), ("所有文件", "*.*")],
        )
        if path:
            self.silk_path_var.set(path)

    def _do_transcribe(self):
        silk_path = self.silk_path_var.get().strip()
        if not silk_path or not Path(silk_path).exists():
            messagebox.showwarning("提示", "请选择有效的 SILK 语音文件")
            return
        deepseek_key = self.config.get("llm", {}).get("deepseek", {}).get("api_key", "")
        if not deepseek_key:
            messagebox.showwarning("提示", "请先在「配置」页填写 DeepSeek API Key")
            return

        self.btn_transcribe.config(state=tk.DISABLED, text="转写中...")
        self.transcribe_result.delete("1.0", tk.END)
        self.transcribe_result.insert(tk.END, "正在转写，请稍候...\n")

        threading.Thread(
            target=self._transcribe_worker, args=(silk_path,), daemon=True
        ).start()

    def _transcribe_worker(self, silk_path: str):
        try:
            from src.asr.base import create_asr
            from src.wechat_parser.silk_decoder import silk_to_wav

            asr_engine = create_asr(self.config["asr"])
            tmp_dir = get_app_dir() / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            wav_path = str(tmp_dir / "transcribe_tmp.wav")

            self.message_queue.put(("transcribe_log", f"引擎: {asr_engine.name()}"))
            self.message_queue.put(("transcribe_log", "SILK → WAV 转换中..."))
            duration = silk_to_wav(silk_path, wav_path)
            self.message_queue.put(("transcribe_log", f"时长: {duration:.1f} 秒, 开始识别..."))

            text = asr_engine.transcribe(wav_path)
            Path(wav_path).unlink(missing_ok=True)

            self.message_queue.put(("transcribe_done", {"duration": duration, "text": text, "engine": asr_engine.name()}))

        except Exception as e:
            self.message_queue.put(("transcribe_error", str(e)))

    # ──────── 待办页 ────────
    def _build_todo_tab(self):
        tab = self.todo_tab
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(btn_frame, text="🔄 刷新列表", command=self._refresh_todos).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📋 生成提醒", command=self._generate_reminder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="✓ 标记完成", command=self._mark_todo_done).pack(side=tk.LEFT, padx=5)

        # 待办列表
        list_frame = ttk.LabelFrame(tab, text="待办事项", padding=5)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("id", "customer", "content", "category", "urgency", "created", "status")
        self.todo_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=12)
        self.todo_tree.heading("id", text="ID")
        self.todo_tree.heading("customer", text="客户")
        self.todo_tree.heading("content", text="待办内容")
        self.todo_tree.heading("category", text="分类")
        self.todo_tree.heading("urgency", text="优先级")
        self.todo_tree.heading("created", text="创建时间")
        self.todo_tree.heading("status", text="状态")
        self.todo_tree.column("id", width=40)
        self.todo_tree.column("customer", width=120)
        self.todo_tree.column("content", width=280)
        self.todo_tree.column("category", width=80)
        self.todo_tree.column("urgency", width=70)
        self.todo_tree.column("created", width=140)
        self.todo_tree.column("status", width=60)
        todo_scroll = ttk.Scrollbar(list_frame, command=self.todo_tree.yview)
        self.todo_tree.configure(yscrollcommand=todo_scroll.set)
        self.todo_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        todo_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 提醒文案
        ttk.Label(tab, text="提醒文案:", font=("", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(5, 0))
        reminder_frame = ttk.Frame(tab)
        reminder_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=5)
        self.reminder_text = tk.Text(reminder_frame, height=8, wrap=tk.WORD, font=("Courier", 10))
        rr_scroll = ttk.Scrollbar(reminder_frame, command=self.reminder_text.yview)
        self.reminder_text.configure(yscrollcommand=rr_scroll.set)
        self.reminder_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rr_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _refresh_todos(self):
        try:
            store = self._get_store()
            from src.reminder.todo_manager import TodoManager
            todo_mgr = TodoManager(store)
            todos = todo_mgr.get_pending_todos()

            self.todo_tree.delete(*self.todo_tree.get_children())
            for t in todos:
                urgency_label = {"high": "高", "normal": "普通", "low": "低"}.get(t.urgency, t.urgency)
                cat_label = {
                    "inquiry": "询价", "quotation": "报价", "sample": "样品",
                    "order": "订单", "logistics": "物流", "payment": "付款",
                    "complaint": "投诉", "other": "其他",
                }.get(t.category, t.category)
                created_short = t.created_at[:16].replace("T", " ") if t.created_at else ""
                self.todo_tree.insert("", tk.END, values=(
                    t.id, t.talker_name or t.talker, t.content,
                    cat_label, urgency_label, created_short, t.status,
                ))
            self._log(f"刷新待办: {len(todos)} 项")
        except Exception as e:
            messagebox.showerror("错误", f"刷新失败: {e}")

    def _generate_reminder(self):
        try:
            store = self._get_store()
            from src.reminder.todo_manager import TodoManager
            todo_mgr = TodoManager(store)
            text = todo_mgr.generate_reminder(
                granularity=self.config.get("reminder", {}).get("granularity", "daily")
            )
            self.reminder_text.delete("1.0", tk.END)
            self.reminder_text.insert("1.0", text)
        except Exception as e:
            messagebox.showerror("错误", f"生成提醒失败: {e}")

    def _mark_todo_done(self):
        selected = self.todo_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择一条待办")
            return
        item = self.todo_tree.item(selected[0])
        todo_id = int(item["values"][0])
        try:
            store = self._get_store()
            from src.reminder.todo_manager import TodoManager
            todo_mgr = TodoManager(store)
            todo_mgr.mark_done(todo_id)
            self._refresh_todos()
            self._log(f"标记完成: #{todo_id}")
        except Exception as e:
            messagebox.showerror("错误", f"标记失败: {e}")

    # ═══════ 队列轮询（线程安全更新 UI）═══════
    def _poll_queue(self):
        try:
            while True:
                try:
                    msg_type, data = self.message_queue.get_nowait()
                except queue.Empty:
                    break

                if msg_type == "log":
                    self._log(data)
                elif msg_type == "analysis":
                    self._log(f"分析完成: {data.summary[:60]}")
                    self._log(f"  待办: {len(data.todo_items)} 项, 已办: {len(data.done_items)} 项")
                elif msg_type == "stopped":
                    self.is_monitoring = False
                    self.btn_start.config(state=tk.NORMAL)
                    self.btn_stop.config(state=tk.DISABLED)
                    self.status_label.config(text="状态: 已停止", foreground="gray")
                elif msg_type == "transcribe_log":
                    self.transcribe_result.insert(tk.END, data + "\n")
                    self.transcribe_result.see(tk.END)
                elif msg_type == "transcribe_done":
                    self.transcribe_result.delete("1.0", tk.END)
                    self.transcribe_result.insert(tk.END, f"引擎: {data['engine']}\n")
                    self.transcribe_result.insert(tk.END, f"时长: {data['duration']:.1f} 秒\n")
                    self.transcribe_result.insert(tk.END, "-" * 40 + "\n")
                    self.transcribe_result.insert(tk.END, data["text"] + "\n")
                    self.btn_transcribe.config(state=tk.NORMAL, text="🎙 开始转写")
                elif msg_type == "transcribe_error":
                    self.transcribe_result.delete("1.0", tk.END)
                    self.transcribe_result.insert(tk.END, f"转写失败: {data}\n")
                    self.btn_transcribe.config(state=tk.NORMAL, text="🎙 开始转写")
        finally:
            self.root.after(100, self._poll_queue)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)


def main():
    root = tk.Tk()
    # macOS 适配
    if sys.platform == "darwin":
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "style",
                         root._w, "document", "closeBox collapseBox")
        except Exception:
            pass
    app = TradeToolsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

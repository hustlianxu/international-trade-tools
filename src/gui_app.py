"""外贸助手 GUI 主应用 - 仿微信三栏界面（现代清爽版）。

布局：
  左栏：联系人/群聊列表（可搜索）
  中栏：聊天内容（时间筛选 + 语音自动转录 + 气泡展示）
  右栏：AI 分析结果（总结/需求/待办，支持查看/保存/导出）

底部状态栏：微信连接状态 + ASR 引擎。

密钥管理（设置页）：
  - 自动扫描（macOS 弹出 root 密码框 / Windows 直接扫描）
  - 加载 all_keys.json（用 wechat-decrypt 工具批量生成）
  - 手动输入单个 96 字符 hex raw key

用法:
    python src/gui_app.py          # 开发模式
    python src/gui_app.py --mcp    # MCP server 模式
    打包后双击运行                    # 开箱即用
"""
import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from src.theme import Colors, Fonts, apply_theme, create_bubble_text


# ═══════ 启动诊断：打包模式下重定向日志 ═══════
def _setup_logging_redirect():
    """打包模式下把 stdout/stderr 重定向到日志文件，方便诊断闪退。"""
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        try:
            if sys.platform == "win32":
                base = os.environ.get("APPDATA", str(Path.home()))
                log_dir = Path(base) / "trade-tools"
            elif sys.platform == "darwin":
                log_dir = Path.home() / "Library" / "Application Support" / "trade-tools"
            else:
                xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
                log_dir = Path(xdg) / "trade-tools"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "app.log"
            f = open(log_file, "a", encoding="utf-8", buffering=1)
            f.write(f"\n{'='*60}\n外贸助手启动 @ {datetime.now().isoformat()}\n{'='*60}\n")
            sys.stdout = f
            sys.stderr = f
            logging.basicConfig(level=logging.INFO,
                format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                datefmt="%H:%M:%S", stream=f)
            return log_file
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    return None


_setup_logging_redirect()

from src.paths import ensure_default_config, get_app_dir, get_config_path, get_db_path

logger = logging.getLogger("trade-tools-gui")


def _is_mlx_whisper_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _write_crash_log(exc: Exception) -> Path | None:
    import traceback
    try:
        crash_log = get_app_dir() / "crash.log"
        crash_log.write_text(
            f"外贸助手崩溃 @ {datetime.now().isoformat()}\nPython: {sys.version}\n"
            f"Platform: {sys.platform}\n{'='*60}\n{traceback.format_exc()}",
            encoding="utf-8")
        return crash_log
    except Exception:
        return None


class TradeToolsApp:
    """外贸助手主窗口 - 仿微信三栏布局（现代清爽版）。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("外贸助手 · 微信 AI 客户管理")
        self.root.geometry("1280x800")
        self.root.minsize(1040, 680)

        # 配置
        self.config_path = get_config_path()
        ensure_default_config()
        self.config = self._load_config()

        # 线程安全队列
        self.task_queue: queue.Queue = queue.Queue()

        # 运行时组件（懒加载）
        self._key_store = None           # WeChatKeyStore（多密钥）
        self._extractor = None
        self._asr_engine = None
        self._analyzer = None
        self._todo_mgr = None
        self._store = None
        self._contacts_cache = []
        self._current_talker = None
        self._current_messages = []

        # 构建 UI
        self._build_ui()
        self._auto_detect_wechat()
        self._poll_queue()

    # ═══════ 配置 ═══════
    def _load_config(self) -> dict:
        try:
            with open(self.config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("加载配置失败: %s", e)
            return {}

    def _save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True,
                    default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error("保存配置失败: %s", e)

    # ═══════ 自动检测微信 ═══════
    def _auto_detect_wechat(self):
        """启动时自动检测微信路径和进程。"""
        from src.wechat_parser.wechat_detector import detect_wechat

        def _detect():
            try:
                detection = detect_wechat()
                self.task_queue.put(("detect_done", detection))
            except Exception as e:
                self.task_queue.put(("detect_error", str(e)))

        threading.Thread(target=_detect, daemon=True).start()

    def _on_detect_done(self, detection):
        """检测结果回调（主线程）。"""
        wechat_cfg = self.config.setdefault("wechat", {})
        if detection.found:
            wechat_cfg["db_storage_path"] = detection.db_storage_path
            wechat_cfg["process_name"] = detection.process_name
            self._save_config()
            running = "运行中" if detection.process_running else "未运行"
            self._set_status(f"微信已检测 · 进程{running} · {detection.wxid or 'wxid 未知'}")
            logger.info("自动检测到微信: %s", detection.db_storage_path)
        else:
            self._set_status("未检测到微信，请在「设置」中手动配置")
        # 更新设置页的路径显示
        if hasattr(self, "cfg_wechat_path"):
            self.cfg_wechat_path.set(wechat_cfg.get("db_storage_path", ""))
        if hasattr(self, "cfg_process_name"):
            self.cfg_process_name.set(wechat_cfg.get("process_name", ""))

    # ═══════ 懒加载运行时组件 ═══════
    def _get_key_store(self):
        """获取微信多密钥存储（按配置优先级回退）。"""
        if self._key_store is None:
            from src.wechat_parser.decryptor import get_key_store
            wechat_cfg = self.config["wechat"]
            self._key_store = get_key_store(
                db_storage_path=wechat_cfg["db_storage_path"],
                manual_raw_key=wechat_cfg.get("raw_key", ""),
                all_keys_json_path=wechat_cfg.get("all_keys_json_path", ""),
                auto_scan=wechat_cfg.get("auto_scan", True),
                use_sudo_dialog=wechat_cfg.get("use_sudo_dialog",
                                                sys.platform == "darwin"),
            )
            stats = self._key_store.stats()
            logger.info("[KeyStore] 已加载：%s", stats)
        return self._key_store

    def _get_extractor(self):
        if self._extractor is None:
            from src.wechat_parser.message_extractor import MessageExtractor
            store = self._get_key_store()
            self._extractor = MessageExtractor.from_key_store(
                store, self.config["wechat"]["db_storage_path"])
        return self._extractor

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

    # ═══════ UI 构建 ═══════
    def _build_ui(self):
        apply_theme(self.root)

        # 顶部标题栏
        self._build_topbar()

        # 主体三栏
        body = ttk.Frame(self.root, style="Window.TFrame")
        body.pack(fill=tk.BOTH, expand=True)

        # 左栏：联系人列表
        self.left_panel = ttk.Frame(body, style="Panel.TFrame", width=300)
        self.left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 1))
        self.left_panel.pack_propagate(False)
        self._build_contact_panel(self.left_panel)

        # 中栏：聊天内容
        self.mid_panel = ttk.Frame(body, style="Panel.TFrame")
        self.mid_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 1))
        self._build_chat_panel(self.mid_panel)

        # 右栏：分析结果
        self.right_panel = ttk.Frame(body, style="Panel.TFrame", width=360)
        self.right_panel.pack(side=tk.LEFT, fill=tk.Y)
        self.right_panel.pack_propagate(False)
        self._build_analysis_panel(self.right_panel)

        # 底部状态栏
        self._build_statusbar()

    def _build_topbar(self):
        top = tk.Frame(self.root, bg=Colors.BG_PANEL, height=56)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        # 左侧：Logo + 标题
        left = tk.Frame(top, bg=Colors.BG_PANEL)
        left.pack(side=tk.LEFT, padx=18)
        logo = tk.Label(left, text="◆", fg=Colors.PRIMARY, bg=Colors.BG_PANEL,
                        font=("", 18, "bold"))
        logo.pack(side=tk.LEFT, padx=(0, 10), pady=12)
        title = tk.Label(left, text="外贸助手", fg=Colors.TEXT_PRIMARY,
                         bg=Colors.BG_PANEL, font=Fonts.TITLE)
        title.pack(side=tk.LEFT)
        subtitle = tk.Label(left, text="微信 AI 客户管理", fg=Colors.TEXT_MUTED,
                            bg=Colors.BG_PANEL, font=Fonts.SMALL)
        subtitle.pack(side=tk.LEFT, padx=(8, 0), pady=(14, 0))

        # 右侧：按钮组
        btn_frame = tk.Frame(top, bg=Colors.BG_PANEL)
        btn_frame.pack(side=tk.RIGHT, padx=18)
        ttk.Button(btn_frame, text="设置", style="Ghost.TButton",
                   command=self._show_settings).pack(side=tk.LEFT, padx=4, pady=10)
        ttk.Button(btn_frame, text="待办", style="Ghost.TButton",
                   command=self._show_todos).pack(side=tk.LEFT, padx=4, pady=10)
        ttk.Button(btn_frame, text="MCP", style="Ghost.TButton",
                   command=self._show_mcp_info).pack(side=tk.LEFT, padx=4, pady=10)

        # 分割线
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=Colors.BG_PANEL, height=30)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self.status_label = tk.Label(bar, text="正在检测微信...",
            fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL, font=Fonts.SMALL)
        self.status_label.pack(side=tk.LEFT, padx=16, pady=6)
        self.engine_label = tk.Label(bar,
            text=f"ASR: {self.config.get('asr', {}).get('engine', '未配置')}",
            fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL, font=Fonts.SMALL)
        self.engine_label.pack(side=tk.RIGHT, padx=16, pady=6)

    def _set_status(self, text: str):
        self.status_label.config(text=text)

    # ─── 左栏：联系人 ───
    def _build_contact_panel(self, parent):
        # 顶部标题
        header = tk.Frame(parent, bg=Colors.BG_PANEL)
        header.pack(fill=tk.X, padx=16, pady=(16, 8))
        tk.Label(header, text="会话", fg=Colors.TEXT_PRIMARY, bg=Colors.BG_PANEL,
                 font=Fonts.HEADING).pack(side=tk.LEFT)

        # 搜索框
        search_frame = tk.Frame(parent, bg=Colors.BG_SUBTLE, highlightthickness=0)
        search_frame.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_contacts())
        entry = ttk.Entry(search_frame, textvariable=self.search_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        ttk.Button(search_frame, text="↻", width=3,
                   command=self._load_contacts).pack(side=tk.RIGHT, padx=2, pady=4)

        # 联系人列表
        list_frame = ttk.Frame(parent, style="Panel.TFrame")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.contact_tree = ttk.Treeview(list_frame, columns=("talker", "type"),
            show="tree", selectmode="browse")
        self.contact_tree.heading("#0", text="名称")
        self.contact_tree.column("#0", width=240)
        self.contact_tree.column("talker", width=0, stretch=False)
        self.contact_tree.column("type", width=0, stretch=False)
        cscroll = ttk.Scrollbar(list_frame, command=self.contact_tree.yview)
        self.contact_tree.configure(yscrollcommand=cscroll.set)
        self.contact_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.contact_tree.bind("<<TreeviewSelect>>", self._on_contact_select)

    def _load_contacts(self):
        """加载联系人列表（后台线程）。"""
        def _work():
            try:
                extractor = self._get_extractor()
                contacts = extractor.list_contacts()
                self.task_queue.put(("contacts_loaded", contacts))
            except Exception as e:
                self.task_queue.put(("contacts_error", str(e)))

        self._set_status("正在加载联系人...")
        threading.Thread(target=_work, daemon=True).start()

    def _on_contacts_loaded(self, contacts):
        self._contacts_cache = contacts
        self.contact_tree.delete(*self.contact_tree.get_children())
        for c in contacts:
            name = c.get("name", c["talker"])
            icon = "👥" if c.get("type") == "group" else "👤"
            self.contact_tree.insert("", tk.END, text=f"{icon}  {name}",
                values=(c["talker"], c.get("type", "user")))
        self._set_status(f"已加载 {len(contacts)} 个会话")

    def _filter_contacts(self):
        kw = self.search_var.get().lower()
        self.contact_tree.delete(*self.contact_tree.get_children())
        for c in self._contacts_cache:
            name = c.get("name", c["talker"]).lower()
            if kw and kw not in name and kw not in c["talker"].lower():
                continue
            icon = "👥" if c.get("type") == "group" else "👤"
            self.contact_tree.insert("", tk.END, text=f"{icon}  {c.get('name', c['talker'])}",
                values=(c["talker"], c.get("type", "user")))

    def _on_contact_select(self, event=None):
        sel = self.contact_tree.selection()
        if not sel:
            return
        values = self.contact_tree.item(sel[0], "values")
        talker = values[0]
        self._current_talker = talker
        self._load_chat_history(talker)

    # ─── 中栏：聊天内容 ───
    def _build_chat_panel(self, parent):
        # 顶部：联系人名 + 时间筛选
        header = tk.Frame(parent, bg=Colors.BG_PANEL)
        header.pack(fill=tk.X)
        self.chat_title = tk.Label(header, text="选择左侧联系人查看聊天",
            fg=Colors.TEXT_PRIMARY, bg=Colors.BG_PANEL,
            font=Fonts.HEADING, padx=18, pady=14)
        self.chat_title.pack(side=tk.LEFT)
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # 时间筛选区
        filter_frame = tk.Frame(parent, bg=Colors.BG_PANEL)
        filter_frame.pack(fill=tk.X, padx=18, pady=10)
        tk.Label(filter_frame, text="时间", fg=Colors.TEXT_SECONDARY,
                 bg=Colors.BG_PANEL, font=Fonts.SMALL).pack(side=tk.LEFT, padx=(0, 6))
        self.time_filter_var = tk.StringVar(value="全部")
        time_combo = ttk.Combobox(filter_frame, textvariable=self.time_filter_var, width=10,
            state="readonly", values=["全部", "今天", "近3天", "近7天", "近30天", "自定义"])
        time_combo.pack(side=tk.LEFT, padx=(0, 12))
        time_combo.bind("<<ComboboxSelected>>", self._on_time_filter_change)

        tk.Label(filter_frame, text="从", fg=Colors.TEXT_SECONDARY,
                 bg=Colors.BG_PANEL, font=Fonts.SMALL).pack(side=tk.LEFT, padx=(0, 4))
        self.date_from_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.date_from_var, width=11).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(filter_frame, text="到", fg=Colors.TEXT_SECONDARY,
                 bg=Colors.BG_PANEL, font=Fonts.SMALL).pack(side=tk.LEFT, padx=(0, 4))
        self.date_to_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.date_to_var, width=11).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(filter_frame, text="筛选", style="Accent.TButton",
                   command=self._apply_time_filter).pack(side=tk.LEFT, padx=4)
        ttk.Button(filter_frame, text="AI 分析", style="Accent.TButton",
                   command=self._analyze_current_chat).pack(side=tk.RIGHT, padx=4)

        # 聊天内容区（Canvas + Scroll）
        chat_frame = tk.Frame(parent, bg=Colors.BG_WINDOW)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self.chat_canvas = tk.Canvas(chat_frame, bg=Colors.BG_WINDOW, highlightthickness=0)
        cscroll = ttk.Scrollbar(chat_frame, command=self.chat_canvas.yview)
        self.chat_scroll_frame = tk.Frame(self.chat_canvas, bg=Colors.BG_WINDOW)
        self.chat_scroll_frame.bind("<Configure>",
            lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all")))
        self.chat_canvas.create_window((0, 0), window=self.chat_scroll_frame, anchor="nw")
        self.chat_canvas.configure(yscrollcommand=cscroll.set)
        self.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮绑定
        self.chat_canvas.bind_all("<MouseWheel>",
            lambda e: self.chat_canvas.yview_scroll(int(-e.delta/120), "units"))

        # 底部进度提示
        self.chat_progress = tk.Label(parent, text="", fg=Colors.TEXT_MUTED,
            bg=Colors.BG_PANEL, font=Fonts.SMALL, padx=18, pady=6)
        self.chat_progress.pack(fill=tk.X)

    def _on_time_filter_change(self, event=None):
        val = self.time_filter_var.get()
        if val == "自定义":
            if not self.date_from_var.get():
                self.date_from_var.set(datetime.now().strftime("%Y-%m-%d"))
            if not self.date_to_var.get():
                self.date_to_var.set(datetime.now().strftime("%Y-%m-%d"))
        elif val == "全部":
            self.date_from_var.set("")
            self.date_to_var.set("")

    def _get_time_range(self) -> tuple[int, int]:
        """根据筛选条件返回 (time_from, time_to) Unix 时间戳。"""
        val = self.time_filter_var.get()
        now = datetime.now()
        if val == "今天":
            t0 = now.replace(hour=0, minute=0, second=0)
            return int(t0.timestamp()), 0
        elif val == "近3天":
            return int((now - timedelta(days=3)).timestamp()), 0
        elif val == "近7天":
            return int((now - timedelta(days=7)).timestamp()), 0
        elif val == "近30天":
            return int((now - timedelta(days=30)).timestamp()), 0
        elif val == "自定义":
            t_from, t_to = 0, 0
            if self.date_from_var.get():
                try:
                    t_from = int(datetime.strptime(self.date_from_var.get(), "%Y-%m-%d").timestamp())
                except ValueError:
                    pass
            if self.date_to_var.get():
                try:
                    t_to = int(datetime.strptime(self.date_to_var.get(), "%Y-%m-%d").replace(
                        hour=23, minute=59, second=59).timestamp())
                except ValueError:
                    pass
            return t_from, t_to
        return 0, 0

    def _apply_time_filter(self):
        if not self._current_talker:
            messagebox.showinfo("提示", "请先选择联系人")
            return
        self._load_chat_history(self._current_talker)

    def _load_chat_history(self, talker: str):
        """加载聊天历史（后台线程，含语音自动转录）。"""
        name = ""
        for c in self._contacts_cache:
            if c["talker"] == talker:
                name = c.get("name", talker)
                break
        self.chat_title.config(text=f"{name}  ·  {talker}")

        time_from, time_to = self._get_time_range()

        def _work():
            try:
                extractor = self._get_extractor()
                messages = extractor.extract_messages_by_time(talker, time_from, time_to, limit=500)
                self.task_queue.put(("chat_loaded", messages))
            except Exception as e:
                self.task_queue.put(("chat_error", str(e)))

        self.chat_progress.config(text="正在加载聊天记录...")
        threading.Thread(target=_work, daemon=True).start()

    def _on_chat_loaded(self, messages):
        """渲染聊天消息（仿微信气泡）。"""
        # 清空旧内容
        for w in self.chat_scroll_frame.winfo_children():
            w.destroy()

        self._current_messages = messages
        from src.wechat_parser.message_extractor import MSG_TYPE_TEXT, MSG_TYPE_VOICE

        if not messages:
            empty = tk.Label(self.chat_scroll_frame, text="（无聊天记录）",
                fg=Colors.TEXT_MUTED, bg=Colors.BG_WINDOW, font=Fonts.BODY)
            empty.pack(pady=40)
            self.chat_progress.config(text="")
            return

        voice_count = sum(1 for m in messages if m.type == MSG_TYPE_VOICE)
        self.chat_progress.config(text=f"共 {len(messages)} 条消息 · 语音 {voice_count} 条")

        last_date = ""
        for msg in messages:
            # 日期分隔线
            msg_date = datetime.fromtimestamp(msg.create_time).strftime("%Y-%m-%d")
            if msg_date != last_date:
                last_date = msg_date
                date_label = tk.Label(self.chat_scroll_frame, text=msg_date,
                    font=Fonts.TIMESTAMP, fg=Colors.TEXT_MUTED, bg=Colors.BG_WINDOW)
                date_label.pack(anchor="center", pady=(12, 6))

            ts = datetime.fromtimestamp(msg.create_time).strftime("%H:%M")
            is_self = bool(msg.is_sender)

            if msg.type == MSG_TYPE_TEXT:
                create_bubble_text(self.chat_scroll_frame, msg.content_text or "(空)", is_self, ts)
            elif msg.type == MSG_TYPE_VOICE:
                bubble = create_bubble_text(self.chat_scroll_frame,
                    f"🎤 语音消息 ({msg.msg_svr_id})", is_self, ts)
                store = self._get_store()
                cached = store.get_transcription(msg.msg_svr_id)
                if cached:
                    trans_label = tk.Label(self.chat_scroll_frame, text=f"📝 {cached}",
                        font=Fonts.SMALL, fg=Colors.TEXT_SECONDARY, bg=Colors.BG_WINDOW,
                        wraplength=400, justify="left")
                    trans_label.pack(anchor="e" if is_self else "w", padx=30, pady=(0, 6))
                else:
                    btn = tk.Button(self.chat_scroll_frame, text="转写此语音",
                        font=Fonts.SMALL, fg=Colors.PRIMARY, bg=Colors.BG_WINDOW,
                        relief="flat", cursor="hand2", bd=0,
                        command=lambda m=msg: self._transcribe_one(m))
                    btn.pack(anchor="e" if is_self else "w", padx=30, pady=(0, 6))
            else:
                type_name = {3: "[图片]", 43: "[视频]", 49: "[链接/文件]"}.get(msg.type, f"[类型{msg.type}]")
                create_bubble_text(self.chat_scroll_frame, type_name, is_self, ts)

        # 滚动到底部
        self.root.after(100, lambda: self.chat_canvas.yview_moveto(1.0))

    def _transcribe_one(self, msg):
        """转写单条语音（后台线程）。"""
        def _work():
            try:
                from src.processor import process_voice_message
                asr = self._get_asr()
                store = self._get_store()
                self.task_queue.put(("transcribe_progress", f"正在转写语音 {msg.msg_svr_id}..."))
                text = process_voice_message(msg, asr, store)
                self.task_queue.put(("transcribe_done", {"msg_svr_id": msg.msg_svr_id, "text": text}))
            except Exception as e:
                self.task_queue.put(("transcribe_error", str(e)))

        threading.Thread(target=_work, daemon=True).start()

    def _transcribe_all_voice(self, messages):
        """批量转写所有语音（后台线程）。"""
        from src.wechat_parser.message_extractor import MSG_TYPE_VOICE
        voice_msgs = [m for m in messages if m.type == MSG_TYPE_VOICE]

        def _work():
            try:
                from src.processor import process_voice_message
                asr = self._get_asr()
                store = self._get_store()
                total = len(voice_msgs)
                for i, msg in enumerate(voice_msgs, 1):
                    self.task_queue.put(("transcribe_progress", f"正在转写 {i}/{total}..."))
                    cached = store.get_transcription(msg.msg_svr_id)
                    if cached:
                        continue
                    process_voice_message(msg, asr, store)
                self.task_queue.put(("transcribe_all_done", total))
            except Exception as e:
                self.task_queue.put(("transcribe_error", str(e)))

        if voice_msgs:
            threading.Thread(target=_work, daemon=True).start()

    # ─── 右栏：AI 分析 ───
    def _build_analysis_panel(self, parent):
        # 顶部标题
        header = tk.Frame(parent, bg=Colors.BG_PANEL)
        header.pack(fill=tk.X, padx=18, pady=(16, 8))
        tk.Label(header, text="AI 分析", fg=Colors.TEXT_PRIMARY, bg=Colors.BG_PANEL,
                 font=Fonts.HEADING).pack(side=tk.LEFT)
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # 分析结果区
        result_frame = tk.Frame(parent, bg=Colors.BG_PANEL)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.analysis_text = tk.Text(result_frame, wrap=tk.WORD, font=Fonts.BODY,
            bg=Colors.BG_PANEL, fg=Colors.TEXT_PRIMARY, relief="flat",
            padx=12, pady=12, state=tk.DISABLED,
            highlightthickness=0, borderwidth=0)
        ascroll = ttk.Scrollbar(result_frame, command=self.analysis_text.yview)
        self.analysis_text.configure(yscrollcommand=ascroll.set)
        self.analysis_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ascroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.analysis_text.insert(tk.END,
            "选择联系人后点击「AI 分析」按钮\n\n"
            "将自动：\n"
            "  1. 转写所有语音消息\n"
            "  2. 提取客户需求\n"
            "  3. 生成待办事项")
        self.analysis_text.config(state=tk.DISABLED)

        # 底部操作按钮
        btn_frame = tk.Frame(parent, bg=Colors.BG_PANEL)
        btn_frame.pack(fill=tk.X, padx=18, pady=14)
        ttk.Button(btn_frame, text="保存", style="Ghost.TButton",
                   command=self._save_analysis).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="导出", style="Ghost.TButton",
                   command=self._export_analysis).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="查看待办", style="Ghost.TButton",
                   command=self._show_todos).pack(side=tk.LEFT, padx=4)

        # 当前分析结果缓存
        self._current_analysis = None

    def _analyze_current_chat(self):
        """分析当前聊天（后台线程）：转写语音 → DeepSeek 分析。"""
        if not self._current_talker:
            messagebox.showinfo("提示", "请先选择联系人")
            return
        deepseek_key = self.config.get("llm", {}).get("deepseek", {}).get("api_key", "")
        if not deepseek_key:
            messagebox.showwarning("提示", "请先在「设置」中填写 DeepSeek API Key")
            return

        from src.wechat_parser.message_extractor import MSG_TYPE_TEXT, MSG_TYPE_VOICE

        def _work():
            try:
                messages = self._current_messages
                self.task_queue.put(("analyze_progress", "正在转写语音消息..."))

                # 转写所有语音
                from src.processor import process_voice_message
                asr = self._get_asr()
                store = self._get_store()
                dialog_messages = []
                for msg in messages:
                    if msg.type == MSG_TYPE_TEXT:
                        dialog_messages.append({
                            "is_sender": msg.is_sender,
                            "text": msg.content_text,
                            "time": datetime.fromtimestamp(msg.create_time).strftime("%Y-%m-%d %H:%M"),
                        })
                    elif msg.type == MSG_TYPE_VOICE:
                        cached = store.get_transcription(msg.msg_svr_id)
                        if not cached:
                            self.task_queue.put(("analyze_progress", f"转写语音 {msg.msg_svr_id}..."))
                            cached = process_voice_message(msg, asr, store)
                        if cached:
                            dialog_messages.append({
                                "is_sender": msg.is_sender,
                                "text": f"[语音] {cached}",
                                "time": datetime.fromtimestamp(msg.create_time).strftime("%Y-%m-%d %H:%M"),
                            })

                if not dialog_messages:
                    self.task_queue.put(("analyze_error", "无可用消息（文本或已转写语音）"))
                    return

                self.task_queue.put(("analyze_progress", f"正在用 DeepSeek 分析 {len(dialog_messages)} 条消息..."))

                # DeepSeek 分析
                analyzer = self._get_analyzer()
                talker_name = ""
                for c in self._contacts_cache:
                    if c["talker"] == self._current_talker:
                        talker_name = c.get("name", self._current_talker)
                        break

                result = analyzer.analyze_dialog(self._current_talker, talker_name, dialog_messages)
                store.save_analysis(result)

                # 写入 TODO
                todo_mgr = self._get_todo_mgr()
                todo_mgr.add_from_analysis(result)

                self.task_queue.put(("analyze_done", result))
            except Exception as e:
                self.task_queue.put(("analyze_error", str(e)))

        self.analysis_text.config(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)
        self.analysis_text.insert(tk.END, "正在分析，请稍候...\n")
        self.analysis_text.config(state=tk.DISABLED)

        threading.Thread(target=_work, daemon=True).start()

    def _on_analyze_done(self, result):
        """渲染分析结果。"""
        self._current_analysis = result
        self.analysis_text.config(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)

        self.analysis_text.insert(tk.END, "═══ 客户需求分析 ═══\n\n", "heading")
        self.analysis_text.insert(tk.END, f"客户: {result.talker_name or result.talker}\n", "bold")
        self.analysis_text.insert(tk.END, f"时间: {result.analyzed_at}\n")
        self.analysis_text.insert(tk.END, f"语言: {result.language or '未知'}\n")
        self.analysis_text.insert(tk.END, f"客户情绪: {result.customer_mood or '未知'}\n\n")

        self.analysis_text.insert(tk.END, "── 摘要 ──\n", "heading")
        self.analysis_text.insert(tk.END, f"{result.summary}\n\n")

        if result.needs:
            self.analysis_text.insert(tk.END, f"── 需求 ({len(result.needs)} 项) ──\n", "heading")
            for i, need in enumerate(result.needs, 1):
                self.analysis_text.insert(tk.END, f"\n{i}. [{need.category}] ", "bold")
                if need.urgency == "high":
                    self.analysis_text.insert(tk.END, "🔴高优 ", "danger")
                self.analysis_text.insert(tk.END, f"{need.summary}\n")
                if need.product:
                    self.analysis_text.insert(tk.END, f"   产品: {need.product}")
                    if need.quantity:
                        self.analysis_text.insert(tk.END, f" | 数量: {need.quantity}")
                    self.analysis_text.insert(tk.END, "\n")
                if need.deadline:
                    self.analysis_text.insert(tk.END, f"   截止: {need.deadline}\n")

        if result.todo_items:
            self.analysis_text.insert(tk.END, f"\n── 待办 ({len(result.todo_items)} 项) ──\n", "heading")
            for i, todo in enumerate(result.todo_items, 1):
                self.analysis_text.insert(tk.END, f"  ☐ {todo}\n")

        if result.done_items:
            self.analysis_text.insert(tk.END, f"\n── 已办 ({len(result.done_items)} 项) ──\n", "heading")
            for done in result.done_items:
                self.analysis_text.insert(tk.END, f"  ☑ {done}\n")

        # 配置标签样式
        self.analysis_text.tag_config("heading", font=Fonts.HEADING, foreground=Colors.PRIMARY)
        self.analysis_text.tag_config("bold", font=Fonts.BODY_BOLD)
        self.analysis_text.tag_config("danger", foreground=Colors.DANGER)

        self.analysis_text.config(state=tk.DISABLED)
        self._set_status("分析完成，结果已保存")

    def _save_analysis(self):
        if not self._current_analysis:
            messagebox.showinfo("提示", "暂无分析结果")
            return
        messagebox.showinfo("已保存", "分析结果已保存到本地数据库")

    def _export_analysis(self):
        if not self._current_analysis:
            messagebox.showinfo("提示", "暂无分析结果")
            return
        path = filedialog.asksaveasfilename(
            title="导出分析结果",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("文本", "*.txt"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            r = self._current_analysis
            if path.endswith(".json"):
                data = {
                    "talker": r.talker, "talker_name": r.talker_name,
                    "language": r.language, "summary": r.summary,
                    "customer_mood": r.customer_mood, "analyzed_at": r.analyzed_at,
                    "needs": [{"category": n.category, "summary": n.summary, "product": n.product,
                        "quantity": n.quantity, "deadline": n.deadline, "urgency": n.urgency} for n in r.needs],
                    "todo_items": r.todo_items, "done_items": r.done_items,
                }
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                lines = [f"# 客户需求分析 - {r.talker_name or r.talker}", ""]
                lines.append(f"- 时间: {r.analyzed_at}")
                lines.append(f"- 语言: {r.language or '未知'}")
                lines.append(f"- 客户情绪: {r.customer_mood or '未知'}")
                lines.append(f"\n## 摘要\n\n{r.summary}")
                if r.needs:
                    lines.append(f"\n## 需求 ({len(r.needs)} 项)")
                    for i, n in enumerate(r.needs, 1):
                        lines.append(f"\n{i}. **[{n.category}]** {n.summary}")
                        if n.product:
                            lines.append(f"   - 产品: {n.product} | 数量: {n.quantity or '未知'}")
                        if n.deadline:
                            lines.append(f"   - 截止: {n.deadline}")
                if r.todo_items:
                    lines.append(f"\n## 待办 ({len(r.todo_items)} 项)")
                    for t in r.todo_items:
                        lines.append(f"- [ ] {t}")
                if r.done_items:
                    lines.append(f"\n## 已办 ({len(r.done_items)} 项)")
                    for d in r.done_items:
                        lines.append(f"- [x] {d}")
                Path(path).write_text("\n".join(lines), encoding="utf-8")
            messagebox.showinfo("成功", f"已导出到:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ─── 设置弹窗 ───
    def _show_settings(self):
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.geometry("680x780")
        win.transient(self.root)
        win.grab_set()

        apply_theme(win)

        # 顶部标题
        top = tk.Frame(win, bg=Colors.BG_PANEL)
        top.pack(fill=tk.X)
        tk.Label(top, text="设置", fg=Colors.TEXT_PRIMARY, bg=Colors.BG_PANEL,
                 font=Fonts.TITLE, padx=20, pady=16).pack(side=tk.LEFT)
        ttk.Separator(win, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # 可滚动容器
        canvas = tk.Canvas(win, bg=Colors.BG_WINDOW, highlightthickness=0)
        scroll = ttk.Scrollbar(win, command=canvas.yview)
        container = tk.Frame(canvas, bg=Colors.BG_WINDOW)
        container.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=container, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ─── 微信数据配置 ───
        self._build_settings_wechat(container)
        # ─── 密钥管理 ───
        self._build_settings_keys(container)
        # ─── ASR ───
        self._build_settings_asr(container)
        # ─── DeepSeek ───
        self._build_settings_deepseek(container)

        # 保存按钮
        btn = ttk.Button(container, text="💾  保存配置", style="Accent.TButton",
            command=lambda: self._save_settings(win))
        btn.pack(pady=24)

    def _settings_section_label(self, parent, text):
        """设置区段标题。"""
        tk.Label(parent, text=text, fg=Colors.TEXT_PRIMARY, bg=Colors.BG_WINDOW,
                 font=Fonts.HEADING).pack(anchor=tk.W, padx=20, pady=(20, 8))

    def _build_settings_wechat(self, parent):
        self._settings_section_label(parent, "微信数据目录")
        frame = tk.LabelFrame(parent, text="微信路径", padding=14)
        frame.pack(fill=tk.X, padx=20, pady=(0, 4))

        tk.Label(frame, text="数据目录", font=Fonts.BODY).grid(row=0, column=0, sticky=tk.W, pady=4)
        self.cfg_wechat_path = tk.StringVar(value=self.config.get("wechat", {}).get("db_storage_path", ""))
        ttk.Entry(frame, textvariable=self.cfg_wechat_path, width=46).grid(row=0, column=1, pady=4, padx=8)
        ttk.Button(frame, text="自动检测", style="Ghost.TButton",
                   command=lambda: self._auto_detect_in_settings()).grid(row=0, column=2, padx=2)

        tk.Label(frame, text="进程名", font=Fonts.BODY).grid(row=1, column=0, sticky=tk.W, pady=4)
        self.cfg_process_name = tk.StringVar(value=self.config.get("wechat", {}).get("process_name", ""))
        ttk.Entry(frame, textvariable=self.cfg_process_name, width=30).grid(row=1, column=1, sticky=tk.W, pady=4, padx=8)

    def _build_settings_keys(self, parent):
        """密钥管理区：三种密钥来源。"""
        self._settings_section_label(parent, "微信密钥管理")
        frame = tk.LabelFrame(parent, text="密钥来源（按优先级生效）", padding=14)
        frame.pack(fill=tk.X, padx=20, pady=(0, 4))

        # 当前状态显示
        wechat_cfg = self.config.get("wechat", {})
        self._key_status_var = tk.StringVar(value="尚未加载密钥")
        status_label = tk.Label(frame, textvariable=self._key_status_var,
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_PANEL, font=Fonts.SMALL,
            justify=tk.LEFT, anchor="w", wraplength=560)
        status_label.pack(fill=tk.X, pady=(0, 10))

        # ─── 方式1：加载 all_keys.json ───
        way1 = tk.LabelFrame(frame, text="方式 1 · 加载 all_keys.json（推荐）", padding=10)
        way1.pack(fill=tk.X, pady=6)
        tk.Label(way1, text="用 wechat-decrypt 工具批量生成 all_keys.json 后选择文件",
            fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL, font=Fonts.SMALL,
            justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=(0, 6))
        path_row = tk.Frame(way1, bg=Colors.BG_PANEL)
        path_row.pack(fill=tk.X)
        self.cfg_all_keys_path = tk.StringVar(value=wechat_cfg.get("all_keys_json_path", ""))
        ttk.Entry(path_row, textvariable=self.cfg_all_keys_path, width=46).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(path_row, text="浏览…", style="Ghost.TButton",
                   command=self._browse_all_keys_json).pack(side=tk.LEFT, padx=2)
        ttk.Button(path_row, text="加载", style="Accent.TButton",
                   command=lambda: self._load_all_keys_async(self.cfg_all_keys_path.get())).pack(side=tk.LEFT, padx=2)

        # ─── 方式2：自动扫描（macOS 弹 root 密码框） ───
        way2 = tk.LabelFrame(frame, text="方式 2 · 自动扫描（从微信进程内存提取）", padding=10)
        way2.pack(fill=tk.X, pady=6)
        hint_text = (
            "macOS：会弹出系统授权对话框输入 root 密码。\n"
            "前提：WeChat.app 已 ad-hoc 重签名：\n"
            "  sudo codesign --force --deep --sign - /Applications/WeChat.app\n"
            "Windows：需管理员权限运行"
        ) if sys.platform == "darwin" else "Windows：需管理员权限运行，仅支持微信 4.0.x"
        tk.Label(way2, text=hint_text, fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL,
            font=Fonts.SMALL, justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=(0, 6))
        btn_row = tk.Frame(way2, bg=Colors.BG_PANEL)
        btn_row.pack(fill=tk.X)
        self.cfg_auto_scan = tk.BooleanVar(value=wechat_cfg.get("auto_scan", True))
        ttk.Checkbutton(btn_row, text="允许自动扫描", variable=self.cfg_auto_scan).pack(side=tk.LEFT)
        self.cfg_sudo_dialog = tk.BooleanVar(value=wechat_cfg.get("use_sudo_dialog",
                                                                   sys.platform == "darwin"))
        if sys.platform == "darwin":
            ttk.Checkbutton(btn_row, text="通过弹窗提权（osascript）",
                            variable=self.cfg_sudo_dialog).pack(side=tk.LEFT, padx=12)
        ttk.Button(btn_row, text="立即扫描", style="Accent.TButton",
                   command=self._scan_keys_async).pack(side=tk.RIGHT)

        # ─── 方式3：手动输入单个 raw key ───
        way3 = tk.LabelFrame(frame, text="方式 3 · 手动输入单密钥（兼容旧版）", padding=10)
        way3.pack(fill=tk.X, pady=6)
        tk.Label(way3, text="96 字符 hex（前 64 = enc_key，后 32 = salt）",
            fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL, font=Fonts.SMALL,
            justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=(0, 6))
        self.cfg_raw_key = tk.StringVar(value=wechat_cfg.get("raw_key", ""))
        ttk.Entry(way3, textvariable=self.cfg_raw_key, width=80, show="*").pack(fill=tk.X)

    def _build_settings_asr(self, parent):
        self._settings_section_label(parent, "语音识别（ASR）")
        frame = tk.LabelFrame(parent, text="ASR 引擎", padding=14)
        frame.pack(fill=tk.X, padx=20, pady=(0, 4))

        self.cfg_asr_engine = tk.StringVar(value=self.config.get("asr", {}).get("engine", "volcengine"))
        mlx_ok = _is_mlx_whisper_available()
        engines = ["volcengine", "openai"] + (["mlx_whisper"] if mlx_ok else [])
        tk.Label(frame, text="引擎", font=Fonts.BODY).grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(frame, textvariable=self.cfg_asr_engine, values=engines,
            state="readonly", width=18).grid(row=0, column=1, sticky=tk.W, pady=4, padx=8)
        hint = "Mac M3 可用 mlx_whisper（免费）" if mlx_ok else "精简版用 volcengine / openai"
        tk.Label(frame, text=hint, fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL,
                 font=Fonts.SMALL).grid(row=0, column=2, padx=8)

        tk.Label(frame, text="火山 AppID", font=Fonts.BODY).grid(row=1, column=0, sticky=tk.W, pady=4)
        self.cfg_volc_appid = tk.StringVar(value=self.config.get("asr", {}).get("volcengine", {}).get("app_id", ""))
        ttk.Entry(frame, textvariable=self.cfg_volc_appid, width=36).grid(row=1, column=1, pady=4, padx=8)

        tk.Label(frame, text="火山 Token", font=Fonts.BODY).grid(row=2, column=0, sticky=tk.W, pady=4)
        self.cfg_volc_token = tk.StringVar(value=self.config.get("asr", {}).get("volcengine", {}).get("access_token", ""))
        ttk.Entry(frame, textvariable=self.cfg_volc_token, width=36, show="*").grid(row=2, column=1, pady=4, padx=8)

    def _build_settings_deepseek(self, parent):
        self._settings_section_label(parent, "DeepSeek 大模型")
        frame = tk.LabelFrame(parent, text="DeepSeek", padding=14)
        frame.pack(fill=tk.X, padx=20, pady=(0, 4))

        tk.Label(frame, text="API Key", font=Fonts.BODY).grid(row=0, column=0, sticky=tk.W, pady=4)
        self.cfg_deepseek_key = tk.StringVar(value=self.config.get("llm", {}).get("deepseek", {}).get("api_key", ""))
        ttk.Entry(frame, textvariable=self.cfg_deepseek_key, width=46, show="*").grid(row=0, column=1, pady=4, padx=8)

    # ─── 密钥管理操作 ───
    def _browse_all_keys_json(self):
        path = filedialog.askopenfilename(
            title="选择 all_keys.json",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
            initialdir=str(Path.home()),
        )
        if path:
            self.cfg_all_keys_path.set(path)

    def _load_all_keys_async(self, path: str):
        """异步加载 all_keys.json。"""
        if not path or not Path(path).exists():
            messagebox.showwarning("提示", f"文件不存在：{path}")
            return
        db_path = self.cfg_wechat_path.get()
        if not db_path or not Path(db_path).exists():
            messagebox.showwarning("提示", "请先设置微信数据目录")
            return

        def _work():
            try:
                from src.wechat_parser.decryptor import WeChatKeyStore
                store = WeChatKeyStore.load_all_keys_json(path, db_path)
                matched = store.match_to_dbs()
                stats = store.stats()
                self.task_queue.put(("keys_loaded", {
                    "source": "all_keys.json",
                    "path": path,
                    "stats": stats,
                    "store_dump": store.to_all_keys_json(),
                }))
            except Exception as e:
                self.task_queue.put(("keys_error", f"加载 all_keys.json 失败：{e}"))

        self._key_status_var.set("正在加载 all_keys.json ...")
        threading.Thread(target=_work, daemon=True).start()

    def _scan_keys_async(self):
        """异步触发内存扫描（macOS 通过 osascript 弹窗提权）。"""
        db_path = self.cfg_wechat_path.get()
        if not db_path or not Path(db_path).exists():
            messagebox.showwarning("提示", "请先设置微信数据目录")
            return

        def _work():
            try:
                from src.wechat_parser.decryptor import (
                    scan_keys_macos, scan_keys_macos_with_sudo_dialog, _scan_keys_windows,
                )
                if sys.platform == "darwin":
                    if self.cfg_sudo_dialog.get():
                        store = scan_keys_macos_with_sudo_dialog(db_path)
                    else:
                        store = scan_keys_macos(db_path)
                elif sys.platform == "win32":
                    store = _scan_keys_windows(db_path)
                else:
                    raise RuntimeError("当前平台不支持自动扫描")
                stats = store.stats()
                self.task_queue.put(("keys_loaded", {
                    "source": "scan",
                    "path": "",
                    "stats": stats,
                    "store_dump": store.to_all_keys_json(),
                }))
            except Exception as e:
                self.task_queue.put(("keys_error", str(e)))

        self._key_status_var.set("正在扫描微信进程内存（可能弹出授权框）...")
        threading.Thread(target=_work, daemon=True).start()

    def _on_keys_loaded(self, data):
        """密钥加载成功回调（主线程）。"""
        stats = data["stats"]
        source = data["source"]
        path = data["path"]
        # 缓存到运行时（避免下次再扫）
        from src.wechat_parser.decryptor import WeChatKeyStore
        db_path = self.cfg_wechat_path.get()
        store = WeChatKeyStore(db_path)
        for rel, info in data["store_dump"].items():
            enc_key = info.get("enc_key") if isinstance(info, dict) else info
            db_file = Path(db_path) / rel
            if not db_file.exists() or not enc_key:
                continue
            try:
                salt = db_file.read_bytes()[:16]
                store.add_key(enc_key, salt.hex(), db_rel_path=rel)
            except OSError:
                pass
        self._key_store = store

        if source == "all_keys.json":
            self._key_status_var.set(
                f"✓ 已从 all_keys.json 加载\n"
                f"   文件: {path}\n"
                f"   密钥总数: {stats['total_keys']}，匹配 .db: {stats['matched_dbs']}")
        else:
            self._key_status_var.set(
                f"✓ 内存扫描成功\n"
                f"   密钥总数: {stats['total_keys']}，匹配 .db: {stats['matched_dbs']}")
        messagebox.showinfo("密钥加载成功",
            f"密钥总数：{stats['total_keys']}\n匹配 .db：{stats['matched_dbs']}")

    def _on_keys_error(self, err: str):
        """密钥加载失败回调。"""
        self._key_status_var.set(f"✗ 失败：{err[:80]}")
        messagebox.showerror("密钥加载失败", err)

    def _auto_detect_in_settings(self):
        from src.wechat_parser.wechat_detector import detect_wechat
        detection = detect_wechat()
        if detection.found:
            self.cfg_wechat_path.set(detection.db_storage_path)
            self.cfg_process_name.set(detection.process_name)
            messagebox.showinfo("检测成功",
                f"已检测到微信:\n{detection.db_storage_path}\n\n"
                f"进程: {detection.process_name}\n运行中: {detection.process_running}")
        else:
            messagebox.showwarning("未检测到", "未找到微信数据目录，请手动选择")
            path = filedialog.askdirectory(title="选择微信 db_storage 目录")
            if path:
                self.cfg_wechat_path.set(path)

    def _save_settings(self, win):
        self.config.setdefault("wechat", {})
        self.config["wechat"]["db_storage_path"] = self.cfg_wechat_path.get()
        self.config["wechat"]["process_name"] = self.cfg_process_name.get()
        self.config["wechat"]["raw_key"] = self.cfg_raw_key.get()
        self.config["wechat"]["all_keys_json_path"] = self.cfg_all_keys_path.get()
        self.config["wechat"]["auto_scan"] = bool(self.cfg_auto_scan.get())
        if hasattr(self, "cfg_sudo_dialog"):
            self.config["wechat"]["use_sudo_dialog"] = bool(self.cfg_sudo_dialog.get())
        self.config.setdefault("asr", {})
        self.config["asr"]["engine"] = self.cfg_asr_engine.get()
        self.config["asr"].setdefault("volcengine", {})
        self.config["asr"]["volcengine"]["app_id"] = self.cfg_volc_appid.get()
        self.config["asr"]["volcengine"]["access_token"] = self.cfg_volc_token.get()
        self.config.setdefault("llm", {}).setdefault("deepseek", {})
        self.config["llm"]["deepseek"]["api_key"] = self.cfg_deepseek_key.get()
        self.config.setdefault("storage", {})
        self.config["storage"]["db_path"] = str(get_db_path())
        self._save_config()
        # 重置运行时组件（密钥变更后必须重建）
        self._key_store = None
        self._extractor = None
        self._asr_engine = None
        self._analyzer = None
        self.engine_label.config(text=f"ASR: {self.cfg_asr_engine.get()}")
        win.destroy()
        messagebox.showinfo("成功", "配置已保存")

    # ─── 待办弹窗 ───
    def _show_todos(self):
        win = tk.Toplevel(self.root)
        win.title("待办事项")
        win.geometry("720x540")
        win.transient(self.root)
        apply_theme(win)

        top = tk.Frame(win, bg=Colors.BG_PANEL)
        top.pack(fill=tk.X)
        ttk.Button(top, text="刷新", style="Ghost.TButton",
                   command=lambda: self._load_todos(tree)).pack(side=tk.LEFT, padx=6, pady=10)
        ttk.Button(top, text="标记完成", style="Ghost.TButton",
                   command=lambda: self._mark_done(tree)).pack(side=tk.LEFT, padx=6, pady=10)
        ttk.Button(top, text="生成提醒", style="Accent.TButton",
                   command=lambda: self._gen_reminder(text_area)).pack(side=tk.LEFT, padx=6, pady=10)
        ttk.Separator(win, orient=tk.HORIZONTAL).pack(fill=tk.X)

        cols = ("id", "customer", "content", "category", "urgency", "created")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=12)
        for c, t, w in [("id", "ID", 40), ("customer", "客户", 120), ("content", "待办内容", 280),
                        ("category", "分类", 80), ("urgency", "优先级", 70), ("created", "创建时间", 140)]:
            tree.heading(c, text=t)
            tree.column(c, width=w)
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        tk.Label(win, text="提醒文案", fg=Colors.TEXT_PRIMARY, bg=Colors.BG_WINDOW,
                 font=Fonts.HEADING).pack(anchor=tk.W, padx=12)
        text_area = tk.Text(win, height=8, wrap=tk.WORD, font=Fonts.MONO,
                            bg=Colors.BG_PANEL, fg=Colors.TEXT_PRIMARY, relief="flat",
                            padx=12, pady=10)
        text_area.pack(fill=tk.BOTH, expand=False, padx=12, pady=8)

        self._load_todos(tree)

    def _load_todos(self, tree):
        tree.delete(*tree.get_children())
        try:
            todo_mgr = self._get_todo_mgr()
            todos = todo_mgr.get_pending_todos()
            for t in todos:
                urgency = {"high": "高", "normal": "普通", "low": "低"}.get(t.urgency, t.urgency)
                cat = {"inquiry": "询价", "quotation": "报价", "order": "订单", "logistics": "物流"}.get(t.category, t.category)
                tree.insert("", tk.END, values=(t.id, t.talker_name or t.talker, t.content,
                    cat, urgency, t.created_at[:16].replace("T", " ") if t.created_at else ""))
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _mark_done(self, tree):
        sel = tree.selection()
        if not sel:
            return
        todo_id = int(tree.item(sel[0])["values"][0])
        try:
            self._get_todo_mgr().mark_done(todo_id)
            self._load_todos(tree)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _gen_reminder(self, text_area):
        try:
            text = self._get_todo_mgr().generate_reminder(
                granularity=self.config.get("reminder", {}).get("granularity", "daily"))
            text_area.delete("1.0", tk.END)
            text_area.insert("1.0", text)
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ─── MCP 信息 ───
    def _show_mcp_info(self):
        win = tk.Toplevel(self.root)
        win.title("MCP 协议")
        win.geometry("620x520")
        win.transient(self.root)
        apply_theme(win)

        top = tk.Frame(win, bg=Colors.BG_PANEL)
        top.pack(fill=tk.X)
        tk.Label(top, text="MCP 协议支持", fg=Colors.TEXT_PRIMARY, bg=Colors.BG_PANEL,
                 font=Fonts.TITLE, padx=20, pady=16).pack(side=tk.LEFT)
        ttk.Separator(win, orient=tk.HORIZONTAL).pack(fill=tk.X)

        container = tk.Frame(win, bg=Colors.BG_PANEL)
        container.pack(fill=tk.BOTH, expand=True, padx=20, pady=14)
        tk.Label(container, text="通过 MCP 协议，外部 AI 客户端（Claude Desktop / Trae）可用自然语言查询聊天记录",
            fg=Colors.TEXT_SECONDARY, bg=Colors.BG_PANEL, font=Fonts.BODY,
            wraplength=560, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 12))

        info = tk.Text(container, wrap=tk.WORD, font=Fonts.MONO, height=20,
                       bg=Colors.BG_SUBTLE, fg=Colors.TEXT_PRIMARY, relief="flat",
                       padx=12, pady=12)
        info.pack(fill=tk.BOTH, expand=True)
        info.insert(tk.END, "可用工具:\n")
        info.insert(tk.END, "  • search_chats - 关键词搜索聊天记录\n")
        info.insert(tk.END, "  • list_contacts - 列出联系人\n")
        info.insert(tk.END, "  • get_chat_history - 获取聊天历史\n")
        info.insert(tk.END, "  • transcribe_voice - 转写语音\n")
        info.insert(tk.END, "  • analyze_customer - 分析客户需求\n")
        info.insert(tk.END, "  • search_by_natural_language - 自然语言搜索\n\n")
        info.insert(tk.END, "启动 MCP server:\n")
        info.insert(tk.END, "  终端运行: 外贸助手 --mcp\n\n")
        info.insert(tk.END, "Claude Desktop 配置 (~/Library/Application Support/Claude/claude_desktop_config.json):\n")
        info.insert(tk.END, '{\n')
        info.insert(tk.END, '  "mcpServers": {\n')
        info.insert(tk.END, '    "trade-tools": {\n')
        info.insert(tk.END, '      "command": "/path/to/外贸助手.app/Contents/MacOS/TradeTools",\n')
        info.insert(tk.END, '      "args": ["--mcp"]\n')
        info.insert(tk.END, '    }\n')
        info.insert(tk.END, '  }\n')
        info.insert(tk.END, '}\n\n')
        info.insert(tk.END, "配置后在 Claude Desktop 中即可用自然语言查询，如:\n")
        info.insert(tk.END, '  "帮我找上周和张三聊的关于报价的记录"')
        info.config(state=tk.DISABLED)

    # ═══════ 队列轮询 ═══════
    def _poll_queue(self):
        try:
            while True:
                try:
                    msg_type, data = self.task_queue.get_nowait()
                except queue.Empty:
                    break

                if msg_type == "detect_done":
                    self._on_detect_done(data)
                elif msg_type == "detect_error":
                    self._set_status(f"检测失败: {data}")
                elif msg_type == "contacts_loaded":
                    self._on_contacts_loaded(data)
                elif msg_type == "contacts_error":
                    self._set_status(f"加载联系人失败: {data}")
                    messagebox.showerror(
                        "加载联系人失败",
                        f"{data}\n\n"
                        "可能原因：\n"
                        "1. 未加载微信密钥（请到「设置 → 微信密钥管理」加载）\n"
                        "2. 微信数据目录路径错误\n"
                        "3. 微信 4.1.x 内存扫描已失效，请加载 all_keys.json"
                    )
                elif msg_type == "keys_loaded":
                    self._on_keys_loaded(data)
                elif msg_type == "keys_error":
                    self._on_keys_error(data)
                elif msg_type == "chat_loaded":
                    self._on_chat_loaded(data)
                    # 自动批量转写语音
                    from src.wechat_parser.message_extractor import MSG_TYPE_VOICE
                    voice_msgs = [m for m in data if m.type == MSG_TYPE_VOICE]
                    if voice_msgs:
                        self._transcribe_all_voice(data)
                elif msg_type == "chat_error":
                    self.chat_progress.config(text=f"加载失败: {data}")
                elif msg_type == "transcribe_progress":
                    self.chat_progress.config(text=data)
                elif msg_type == "transcribe_done":
                    self.chat_progress.config(text=f"转写完成: {data['text'][:50]}...")
                    if self._current_talker:
                        self._load_chat_history(self._current_talker)
                elif msg_type == "transcribe_all_done":
                    self.chat_progress.config(text=f"所有语音转写完成 ({data} 条)")
                    if self._current_talker:
                        self._load_chat_history(self._current_talker)
                elif msg_type == "transcribe_error":
                    self.chat_progress.config(text=f"转写失败: {data}")
                elif msg_type == "analyze_progress":
                    self.analysis_text.config(state=tk.NORMAL)
                    self.analysis_text.delete("1.0", tk.END)
                    self.analysis_text.insert(tk.END, data + "\n")
                    self.analysis_text.config(state=tk.DISABLED)
                elif msg_type == "analyze_done":
                    self._on_analyze_done(data)
                elif msg_type == "analyze_error":
                    self.analysis_text.config(state=tk.NORMAL)
                    self.analysis_text.delete("1.0", tk.END)
                    self.analysis_text.insert(tk.END, f"分析失败:\n{data}")
                    self.analysis_text.config(state=tk.DISABLED)
        finally:
            self.root.after(100, self._poll_queue)


def main():
    # --mcp 模式：启动 MCP server
    if "--mcp" in sys.argv:
        from src.mcp_server import main as mcp_main
        mcp_main()
        return
    try:
        root = tk.Tk()
        if sys.platform == "darwin":
            try:
                root.tk.call("::tk::unsupported::MacWindowStyle", "style",
                             root._w, "document", "closeBox collapseBox")
            except Exception:
                pass
        app = TradeToolsApp(root)
        logger.info("外贸助手启动成功")
        root.mainloop()
    except Exception as e:
        logger.error("外贸助手启动失败", exc_info=True)
        crash_log = _write_crash_log(e)
        try:
            from tkinter import messagebox
            err_msg = f"外贸助手启动失败:\n{e}\n\n"
            if crash_log:
                err_msg += f"详细日志: {crash_log}\n运行日志: {get_app_dir() / 'app.log'}"
            messagebox.showerror("外贸助手 - 启动错误", err_msg)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()

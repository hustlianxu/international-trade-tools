"""清爽主题样式（仿微信配色）。

配色方案：浅灰底 + 微信绿点缀，简洁现代。
"""
import tkinter as tk
from tkinter import ttk


# ═══════ 配色 ═══════
class Colors:
    # 主色（微信绿）
    PRIMARY = "#07C160"
    PRIMARY_DARK = "#06AD56"
    PRIMARY_LIGHT = "#E8F8EE"

    # 背景色
    BG_WINDOW = "#F5F5F5"        # 主窗口背景
    BG_PANEL = "#FFFFFF"          # 面板背景
    BG_SIDEBAR = "#2E2E2E"        # 侧边栏深色
    BG_HOVER = "#EAEAEA"          # 悬停
    BG_SELECTED = "#D6EBFF"       # 选中

    # 文字
    TEXT_PRIMARY = "#1A1A1A"      # 主文字
    TEXT_SECONDARY = "#888888"    # 次要文字
    TEXT_MUTED = "#B0B0B0"        # 弱化文字
    TEXT_WHITE = "#FFFFFF"
    TEXT_ON_PRIMARY = "#FFFFFF"

    # 边框
    BORDER = "#E0E0E0"
    BORDER_LIGHT = "#EEEEEE"

    # 状态色
    SUCCESS = "#07C160"
    WARNING = "#FAAD14"
    DANGER = "#FF4D4F"
    INFO = "#1890FF"

    # 气泡
    BUBBLE_SELF = "#95EC69"       # 自己发的消息气泡（微信绿）
    BUBBLE_OTHER = "#FFFFFF"      # 对方消息气泡

    # 紧急
    URGENCY_HIGH = "#FF4D4F"
    URGENCY_NORMAL = "#1890FF"
    URGENCY_LOW = "#888888"


# ═══════ 字体 ═══════
class Fonts:
    TITLE = ("", 16, "bold")
    HEADING = ("", 13, "bold")
    BODY = ("", 11)
    BODY_BOLD = ("", 11, "bold")
    SMALL = ("", 9)
    SMALL_BOLD = ("", 9, "bold")
    MONO = ("Courier", 10)
    CONTACT = ("", 12)
    MESSAGE = ("", 11)
    TIMESTAMP = ("", 9)


def apply_theme(root: tk.Tk):
    """应用清爽主题到根窗口。"""
    root.configure(bg=Colors.BG_WINDOW)

    style = ttk.Style(root)

    # 优先使用 clam 主题（支持更多自定义）
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # ─── TFrame ───
    style.configure("TFrame", background=Colors.BG_PANEL)
    style.configure("Sidebar.TFrame", background=Colors.BG_SIDEBAR)
    style.configure("Panel.TFrame", background=Colors.BG_PANEL)
    style.configure("Window.TFrame", background=Colors.BG_WINDOW)

    # ─── TLabel ───
    style.configure("TLabel", background=Colors.BG_PANEL, foreground=Colors.TEXT_PRIMARY, font=Fonts.BODY)
    style.configure("Title.TLabel", font=Fonts.TITLE, foreground=Colors.TEXT_PRIMARY)
    style.configure("Heading.TLabel", font=Fonts.HEADING, foreground=Colors.TEXT_PRIMARY)
    style.configure("Sidebar.TLabel", background=Colors.BG_SIDEBAR, foreground=Colors.TEXT_WHITE, font=Fonts.BODY)
    style.configure("Muted.TLabel", foreground=Colors.TEXT_SECONDARY, font=Fonts.SMALL)
    style.configure("Timestamp.TLabel", foreground=Colors.TEXT_MUTED, font=Fonts.TIMESTAMP)
    style.configure("Success.TLabel", foreground=Colors.SUCCESS)
    style.configure("Danger.TLabel", foreground=Colors.DANGER)

    # ─── TButton ───
    style.configure("TButton", font=Fonts.BODY, padding=(12, 6))
    style.configure("Accent.TButton", font=Fonts.BODY_BOLD, foreground=Colors.TEXT_ON_PRIMARY)
    style.map("Accent.TButton",
        background=[("active", Colors.PRIMARY_DARK), ("!disabled", Colors.PRIMARY)],
        foreground=[("disabled", "#CCCCCC")])
    style.configure("Danger.TButton", foreground=Colors.DANGER)

    # ─── TEntry ───
    style.configure("TEntry", fieldbackground=Colors.BG_PANEL, borderwidth=1, relief="solid")
    style.map("TEntry",
        bordercolor=[("focus", Colors.PRIMARY), ("!focus", Colors.BORDER)])

    # ─── TCombobox ───
    style.configure("TCombobox", fieldbackground=Colors.BG_PANEL, padding=(8, 4))

    # ─── Treeview（联系人列表 / 消息列表）───
    style.configure("Treeview",
        background=Colors.BG_PANEL,
        foreground=Colors.TEXT_PRIMARY,
        fieldbackground=Colors.BG_PANEL,
        borderwidth=0,
        font=Fonts.CONTACT,
        rowheight=42)
    style.configure("Treeview.Heading",
        font=Fonts.SMALL_BOLD,
        foreground=Colors.TEXT_SECONDARY,
        background=Colors.BG_PANEL,
        relief="flat")
    style.map("Treeview",
        background=[("selected", Colors.BG_SELECTED)],
        foreground=[("selected", Colors.TEXT_PRIMARY)])

    # ─── TNotebook（标签页）───
    style.configure("TNotebook", background=Colors.BG_WINDOW, borderwidth=0)
    style.configure("TNotebook.Tab",
        font=Fonts.BODY,
        padding=(20, 10),
        background=Colors.BG_WINDOW,
        foreground=Colors.TEXT_SECONDARY)
    style.map("TNotebook.Tab",
        background=[("selected", Colors.BG_PANEL)],
        foreground=[("selected", Colors.PRIMARY)])

    # ─── TLabelframe ───
    style.configure("TLabelframe", background=Colors.BG_PANEL, bordercolor=Colors.BORDER)
    style.configure("TLabelframe.Label", font=Fonts.HEADING, foreground=Colors.TEXT_PRIMARY)

    # ─── TScrollbar ───
    style.configure("TScrollbar", background=Colors.BG_WINDOW, troughcolor=Colors.BG_WINDOW, borderwidth=0)
    style.map("TScrollbar", background=[("active", Colors.BORDER)])

    # ─── TSeparator ───
    style.configure("TSeparator", background=Colors.BORDER_LIGHT)

    # ─── TProgressbar ───
    style.configure("TProgressbar", troughcolor=Colors.BG_WINDOW, background=Colors.PRIMARY)


def create_bubble_text(parent, text: str, is_self: bool, timestamp: str = "") -> tk.Frame:
    """创建聊天气泡（仿微信）。

    Args:
        parent: 父容器
        text: 消息文本
        is_self: 是否为自己发的
        timestamp: 时间戳文本

    Returns:
        包含气泡的 Frame
    """
    bg = Colors.BUBBLE_SELF if is_self else Colors.BUBBLE_OTHER
    anchor = "e" if is_self else "w"

    container = tk.Frame(parent, bg=Colors.BG_PANEL)

    if timestamp:
        ts_label = tk.Label(container, text=timestamp, font=Fonts.TIMESTAMP,
                           fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL)
        ts_label.pack(anchor="center", pady=(8, 2))

    bubble = tk.Frame(container, bg=bg, padx=12, pady=8)
    bubble.pack(anchor=anchor, padx=20, pady=2)

    msg_label = tk.Label(bubble, text=text, font=Fonts.MESSAGE,
                         fg=Colors.TEXT_PRIMARY, bg=bg, justify="left", wraplength=400)
    msg_label.pack()

    return container

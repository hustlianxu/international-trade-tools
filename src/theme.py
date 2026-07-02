"""现代清爽主题（仿 Notion / Linear 风格）。

设计原则：
- 浅色 + 大量留白
- 字体层级清晰（标题/正文/辅助）
- 主色 #3B82F6（蓝），辅以微信绿点缀
- 圆角气泡、细腻分割线、柔和阴影感
"""
import platform
import tkinter as tk
from tkinter import ttk


# ════════════════════════════════════════
#  平台字体
# ════════════════════════════════════════
def _platform_fonts() -> dict:
    """根据平台选择中英文兼容字体。"""
    sys_name = platform.system()
    if sys_name == "Darwin":
        return {
            "sans": "PingFang SC",
            "mono": "Menlo",
            "sans_fallback": ".AppleSystemUIFont",
        }
    elif sys_name == "Windows":
        return {
            "sans": "Microsoft YaHei UI",
            "mono": "Consolas",
            "sans_fallback": "Segoe UI",
        }
    return {
        "sans": "Noto Sans CJK SC",
        "mono": "DejaVu Sans Mono",
        "sans_fallback": "DejaVu Sans",
    }


_FONTS = _platform_fonts()


# ════════════════════════════════════════
#  配色
# ════════════════════════════════════════
class Colors:
    # 主色（蓝，更现代）
    PRIMARY = "#3B82F6"
    PRIMARY_DARK = "#2563EB"
    PRIMARY_LIGHT = "#EFF6FF"
    PRIMARY_SOFT = "#DBEAFE"

    # 微信绿（仅用于自己发的气泡，保持微信感）
    WECHAT_GREEN = "#95EC69"

    # 背景层级
    BG_WINDOW = "#F7F8FA"        # 主窗口（最外层）
    BG_PANEL = "#FFFFFF"          # 面板
    BG_SUBTLE = "#F3F4F6"         # 次级背景（输入框、hover）
    BG_HOVER = "#F0F2F5"
    BG_SELECTED = "#E0EDFF"       # 选中

    # 侧栏（深色）
    BG_SIDEBAR = "#1F2937"
    BG_SIDEBAR_HOVER = "#374151"

    # 文字
    TEXT_PRIMARY = "#111827"      # 主文字
    TEXT_SECONDARY = "#4B5563"    # 次要
    TEXT_MUTED = "#9CA3AF"        # 弱化
    TEXT_WHITE = "#FFFFFF"
    TEXT_ON_PRIMARY = "#FFFFFF"
    TEXT_LINK = "#2563EB"

    # 边框
    BORDER = "#E5E7EB"
    BORDER_LIGHT = "#F3F4F6"
    BORDER_FOCUS = "#3B82F6"

    # 状态
    SUCCESS = "#10B981"
    WARNING = "#F59E0B"
    DANGER = "#EF4444"
    INFO = "#3B82F6"

    # 气泡
    BUBBLE_SELF = "#95EC69"
    BUBBLE_OTHER = "#FFFFFF"

    # 紧急
    URGENCY_HIGH = "#EF4444"
    URGENCY_NORMAL = "#3B82F6"
    URGENCY_LOW = "#9CA3AF"


# ════════════════════════════════════════
#  字体
# ════════════════════════════════════════
class Fonts:
    TITLE = (_FONTS["sans"], 17, "bold")
    HEADING = (_FONTS["sans"], 13, "bold")
    SUBHEADING = (_FONTS["sans"], 11, "bold")
    BODY = (_FONTS["sans"], 11)
    BODY_BOLD = (_FONTS["sans"], 11, "bold")
    SMALL = (_FONTS["sans"], 9)
    SMALL_BOLD = (_FONTS["sans"], 9, "bold")
    MONO = (_FONTS["mono"], 10)
    CONTACT = (_FONTS["sans"], 11)
    MESSAGE = (_FONTS["sans"], 11)
    TIMESTAMP = (_FONTS["sans"], 9)
    BUTTON = (_FONTS["sans"], 11)


# ════════════════════════════════════════
#  应用主题
# ════════════════════════════════════════
def apply_theme(root: tk.Tk):
    """应用现代清爽主题。"""
    root.configure(bg=Colors.BG_WINDOW)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # ─── TFrame ───
    style.configure("TFrame", background=Colors.BG_PANEL)
    style.configure("Window.TFrame", background=Colors.BG_WINDOW)
    style.configure("Panel.TFrame", background=Colors.BG_PANEL)
    style.configure("Subtle.TFrame", background=Colors.BG_SUBTLE)
    style.configure("Sidebar.TFrame", background=Colors.BG_SIDEBAR)

    # ─── TLabel ───
    style.configure("TLabel",
                    background=Colors.BG_PANEL,
                    foreground=Colors.TEXT_PRIMARY,
                    font=Fonts.BODY)
    style.configure("Title.TLabel",
                    font=Fonts.TITLE, foreground=Colors.TEXT_PRIMARY,
                    background=Colors.BG_PANEL)
    style.configure("Heading.TLabel",
                    font=Fonts.HEADING, foreground=Colors.TEXT_PRIMARY,
                    background=Colors.BG_PANEL)
    style.configure("Subheading.TLabel",
                    font=Fonts.SUBHEADING, foreground=Colors.TEXT_SECONDARY,
                    background=Colors.BG_PANEL)
    style.configure("Sidebar.TLabel",
                    background=Colors.BG_SIDEBAR, foreground=Colors.TEXT_WHITE,
                    font=Fonts.BODY)
    style.configure("Muted.TLabel",
                    foreground=Colors.TEXT_MUTED, font=Fonts.SMALL,
                    background=Colors.BG_PANEL)
    style.configure("Success.TLabel",
                    foreground=Colors.SUCCESS, font=Fonts.BODY_BOLD,
                    background=Colors.BG_PANEL)
    style.configure("Danger.TLabel",
                    foreground=Colors.DANGER, font=Fonts.BODY_BOLD,
                    background=Colors.BG_PANEL)

    # ─── TButton ───
    style.configure("TButton",
                    font=Fonts.BUTTON, padding=(14, 7),
                    background=Colors.BG_PANEL,
                    foreground=Colors.TEXT_PRIMARY,
                    borderwidth=0,
                    relief="flat")
    style.map("TButton",
              background=[("active", Colors.BG_HOVER), ("pressed", Colors.BG_SUBTLE)])

    # 主按钮（蓝）
    style.configure("Accent.TButton",
                    font=Fonts.BUTTON, padding=(14, 7),
                    foreground=Colors.TEXT_ON_PRIMARY,
                    background=Colors.PRIMARY,
                    borderwidth=0, relief="flat")
    style.map("Accent.TButton",
              background=[("active", Colors.PRIMARY_DARK),
                          ("pressed", Colors.PRIMARY_DARK),
                          ("!disabled", Colors.PRIMARY)],
              foreground=[("disabled", "#D1D5DB")])

    # 危险按钮（红）
    style.configure("Danger.TButton",
                    font=Fonts.BUTTON, padding=(14, 7),
                    foreground=Colors.TEXT_ON_PRIMARY,
                    background=Colors.DANGER,
                    borderwidth=0, relief="flat")
    style.map("Danger.TButton",
              background=[("active", "#DC2626"), ("!disabled", Colors.DANGER)])

    # 幽灵按钮（仅边框）
    style.configure("Ghost.TButton",
                    font=Fonts.BUTTON, padding=(14, 7),
                    foreground=Colors.TEXT_SECONDARY,
                    background=Colors.BG_PANEL,
                    borderwidth=1, relief="solid",
                    bordercolor=Colors.BORDER)
    style.map("Ghost.TButton",
              background=[("active", Colors.BG_HOVER)],
              foreground=[("active", Colors.TEXT_PRIMARY)])

    # ─── TEntry ───
    style.configure("TEntry",
                    fieldbackground=Colors.BG_PANEL,
                    foreground=Colors.TEXT_PRIMARY,
                    borderwidth=1,
                    relief="solid",
                    padding=(8, 6))
    style.map("TEntry",
              bordercolor=[("focus", Colors.BORDER_FOCUS), ("!focus", Colors.BORDER)])

    # ─── TCombobox ───
    style.configure("TCombobox",
                    fieldbackground=Colors.BG_PANEL,
                    foreground=Colors.TEXT_PRIMARY,
                    padding=(8, 6),
                    arrowcolor=Colors.TEXT_SECONDARY)
    style.map("TCombobox",
              fieldbackground=[("readonly", Colors.BG_PANEL)],
              bordercolor=[("focus", Colors.BORDER_FOCUS)])

    # ─── Treeview ───
    style.configure("Treeview",
                    background=Colors.BG_PANEL,
                    foreground=Colors.TEXT_PRIMARY,
                    fieldbackground=Colors.BG_PANEL,
                    borderwidth=0,
                    font=Fonts.CONTACT,
                    rowheight=44)
    style.configure("Treeview.Heading",
                    font=Fonts.SMALL_BOLD,
                    foreground=Colors.TEXT_SECONDARY,
                    background=Colors.BG_PANEL,
                    relief="flat",
                    padding=(8, 6))
    style.map("Treeview",
              background=[("selected", Colors.BG_SELECTED)],
              foreground=[("selected", Colors.TEXT_PRIMARY)])

    # ─── TNotebook ───
    style.configure("TNotebook", background=Colors.BG_WINDOW, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab",
                    font=Fonts.BODY,
                    padding=(20, 10),
                    background=Colors.BG_WINDOW,
                    foreground=Colors.TEXT_SECONDARY,
                    borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", Colors.BG_PANEL)],
              foreground=[("selected", Colors.PRIMARY)])

    # ─── TLabelframe ───
    style.configure("TLabelframe",
                    background=Colors.BG_PANEL,
                    bordercolor=Colors.BORDER,
                    relief="solid",
                    borderwidth=1,
                    padding=12)
    style.configure("TLabelframe.Label",
                    font=Fonts.SUBHEADING,
                    foreground=Colors.TEXT_SECONDARY,
                    background=Colors.BG_PANEL)

    # ─── TScrollbar ───
    style.configure("TScrollbar",
                    background=Colors.BG_WINDOW,
                    troughcolor=Colors.BG_WINDOW,
                    borderwidth=0,
                    arrowsize=14,
                    relief="flat")
    style.map("TScrollbar",
              background=[("active", Colors.BORDER)])

    # ─── TSeparator ───
    style.configure("TSeparator", background=Colors.BORDER_LIGHT)

    # ─── TProgressbar ───
    style.configure("TProgressbar",
                    troughcolor=Colors.BG_SUBTLE,
                    background=Colors.PRIMARY,
                    borderwidth=0,
                    lightcolor=Colors.PRIMARY,
                    darkcolor=Colors.PRIMARY)


# ════════════════════════════════════════
#  聊天气泡
# ════════════════════════════════════════
def create_bubble_text(parent, text: str, is_self: bool, timestamp: str = "") -> tk.Frame:
    """创建聊天气泡（仿微信，带圆角/留白）。

    Args:
        parent: 父容器
        text: 消息文本
        is_self: 是否为自己发的
        timestamp: 时间戳文本

    Returns:
        包含气泡的 Frame
    """
    bg = Colors.BUBBLE_SELF if is_self else Colors.BUBBLE_OTHER
    fg = Colors.TEXT_PRIMARY if not is_self else "#1A1A1A"
    anchor = "e" if is_self else "w"
    # 气泡左右留白对齐
    padx_bubble = (60, 12) if is_self else (12, 60)

    container = tk.Frame(parent, bg=Colors.BG_PANEL)

    if timestamp:
        ts_label = tk.Label(container, text=timestamp, font=Fonts.TIMESTAMP,
                            fg=Colors.TEXT_MUTED, bg=Colors.BG_PANEL)
        ts_label.pack(anchor="center", pady=(10, 3))

    bubble = tk.Frame(container, bg=bg, padx=14, pady=9)
    bubble.pack(anchor=anchor, padx=padx_bubble, pady=2)

    msg_label = tk.Label(bubble, text=text, font=Fonts.MESSAGE,
                         fg=fg, bg=bg, justify="left", wraplength=380)
    msg_label.pack()

    return container

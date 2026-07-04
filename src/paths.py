"""应用路径管理：跨平台统一配置/数据目录。

打包后（PyInstaller）程序目录可能只读，配置和数据放在用户目录：
  Windows: %APPDATA%/trade-tools/
  macOS:   ~/Library/Application Support/trade-tools/
  Linux:   ~/.config/trade-tools/
"""
import os
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """获取应用数据目录（可读写）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        app_dir = Path(base) / "trade-tools"
    elif sys.platform == "darwin":
        app_dir = Path.home() / "Library" / "Application Support" / "trade-tools"
    else:
        # Linux
        xdg_config = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        app_dir = Path(xdg_config) / "trade-tools"

    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_config_path() -> Path:
    """配置文件路径。"""
    return get_app_dir() / "config.yaml"


def get_db_path() -> Path:
    """数据库文件路径。"""
    data_dir = get_app_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "trade_tools.db"


def get_tmp_dir() -> Path:
    """临时文件目录。"""
    tmp_dir = get_app_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def get_resource_path(relative_path: str) -> Path:
    """获取打包内资源路径（PyInstaller 兼容）。

    打包后资源在 sys._MEIPASS 临时目录；开发模式在项目根目录。
    """
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 打包后
        return Path(sys._MEIPASS) / relative_path
    # 开发模式：项目根目录
    return Path(__file__).parent.parent / relative_path


def ensure_default_config():
    """如果用户目录无配置文件，从模板复制一份。"""
    config_path = get_config_path()
    if config_path.exists():
        return config_path

    # 查找模板
    template = get_resource_path("src/config/config.example.yaml")
    if not template.exists():
        # 打包后模板可能在内嵌资源
        template = get_resource_path("config.example.yaml")

    if template.exists():
        config_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        # 兜底：写一个最小配置
        config_path.write_text(
            'wechat:\n  db_storage_path: ""\n  process_name: "WeChat.exe"\n'
            'asr:\n  engine: "volcengine"\n  volcengine:\n    app_id: ""\n    access_token: ""\n'
            'llm:\n  enabled: ["deepseek"]\n  aggregator: ""\n'
            '  providers:\n    deepseek:\n      api_key: ""\n      base_url: "https://api.deepseek.com"\n'
            '      model: "deepseek-chat"\nstorage:\n  db_path: ""\n',
            encoding="utf-8",
        )
    return config_path

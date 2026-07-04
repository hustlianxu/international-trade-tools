"""外贸助手 Web 应用入口：启动 HTTP 服务 + 打开系统浏览器。

零额外依赖（stdlib http.server + webbrowser），跨平台，PyInstaller 友好。
替代旧的 tkinter gui_app.py，UI 由 HTML/CSS/JS 实现，可达现代 Web 水准。

启动：
    python src/web_app.py          # 开发模式
    打包后双击运行                    # 自动开浏览器
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import yaml

# 启动诊断（打包模式重定向日志）
def _setup_logging():
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        try:
            if sys.platform == "win32":
                base = __import__("os").environ.get("APPDATA", str(Path.home()))
                log_dir = Path(base) / "trade-tools"
            elif sys.platform == "darwin":
                log_dir = Path.home() / "Library" / "Application Support" / "trade-tools"
            else:
                xdg = __import__("os").environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
                log_dir = Path(xdg) / "trade-tools"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "app.log"
            f = open(log_file, "a", encoding="utf-8", buffering=1)
            f.write(f"\n{'='*60}\n外贸助手(Web)启动 @ {datetime.now().isoformat()}\n{'='*60}\n")
            sys.stdout = f
            sys.stderr = f
            logging.basicConfig(level=logging.INFO,
                format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                datefmt="%H:%M:%S", stream=f)
            return
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")


_setup_logging()
logger = logging.getLogger("web-app")


def _find_free_port() -> int:
    """找一个可用端口（避免固定端口被占用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_config() -> tuple[dict, Path]:
    from src.paths import ensure_default_config, get_config_path
    config_path = ensure_default_config()
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error("加载配置失败: %s", e)
        config = {}
    return config, config_path


def main():
    config, config_path = _load_config()
    logger.info("配置文件: %s", config_path)

    from src.web_backend import WebBackend, start_server

    backend = WebBackend(config, config_path)
    port = _find_free_port()
    srv, actual_port = start_server(backend, port=port)
    url = f"http://127.0.0.1:{actual_port}/"
    logger.info("外贸助手已启动: %s", url)

    # 后台线程跑 HTTP 服务
    srv_thread = threading.Thread(target=srv.serve_forever, daemon=True, name="http-server")
    srv_thread.start()

    # 延迟打开浏览器（等服务就绪）
    def _open_browser():
        time.sleep(0.6)
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning("打开浏览器失败: %s", e)
    threading.Thread(target=_open_browser, daemon=True).start()

    print(f"\n{'='*50}")
    print(f"  外贸助手已启动")
    print(f"  浏览器访问: {url}")
    print(f"  按 Ctrl+C 退出")
    print(f"{'='*50}\n")

    try:
        srv_thread.join()
    except KeyboardInterrupt:
        logger.info("收到退出信号，关闭服务")
        srv.shutdown()
        print("已退出")


if __name__ == "__main__":
    main()

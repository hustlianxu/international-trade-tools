"""Web 后端：基于 stdlib http.server 的 HTTP API + 静态前端服务。

零额外依赖（不用 Flask/pywebview），跨平台，PyInstaller 友好。
复用 MCPServer 的懒加载逻辑（key_store/extractor/asr/analyzer/store），
仅薄薄包一层 HTTP 路由。

启动：
    python src/web_app.py          # 开发模式
    打包后双击运行                    # 开箱即用（自动开浏览器）

API 路由：
    GET  /api/status            微信检测状态 + 版本 + 引擎信息
    GET  /api/detect            重新检测微信目录（后台）
    GET  /api/version           微信版本检测
    GET  /api/contacts          联系人列表
    GET  /api/chats/<talker>    聊天历史（?limit=&time_from=&time_to=）
    POST /api/transcribe        语音转写 {msg_svr_id}
    POST /api/analyze           AI 分析 {talker, limit}
    GET  /api/config            获取配置
    POST /api/config            保存配置
    POST /api/keys/scan         内存扫描（macOS osascript 提权）
    POST /api/keys/load_json    加载 all_keys.json {path}
    POST /api/keys/raw          设置手动 raw_key {raw_key}
    GET  /api/todos             待办列表
    POST /api/todos/<id>/done   标记待办完成
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

logger = logging.getLogger("web-backend")

# 前端静态文件目录
_FRONTEND_DIR = Path(__file__).parent / "web_frontend"


class WebBackend:
    """业务逻辑层：持有 MCPServer 实例，提供 HTTP 调用的方法。

    配置变更后调用 reset_components() 清空懒加载缓存，下次访问重建。
    """

    def __init__(self, config: dict, config_path: Path):
        from src.mcp_server import MCPServer
        self.config = config
        self.config_path = config_path
        self.mcp = MCPServer(config)
        self._lock = threading.Lock()
        self._server = None  # HTTP server 引用（用于 shutdown）

    def attach_server(self, server):
        self._server = server

    def shutdown(self):
        """关闭 HTTP 服务（从 Web UI 退出）。"""
        ok = False
        if self._server is not None:
            try:
                self._server.shutdown()
                ok = True
            except Exception as e:
                logger.error("shutdown 异常: %s", e)
        return {"ok": ok}

    # ─── 配置 ───
    def save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.config, f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)

    def reset_components(self):
        """配置变更后清空 MCPServer 的懒加载缓存。"""
        with self.mcp._init_lock:
            self.mcp._key_store = None
            self.mcp._extractor = None
            self.mcp._asr_engine = None
            self.mcp._analyzer = None
            self.mcp._wechat_error = None

    # ─── 状态 ───
    def get_status(self) -> dict:
        wechat_cfg = self.config.get("wechat", {})
        return {
            "db_storage_path": wechat_cfg.get("db_storage_path", ""),
            "process_name": wechat_cfg.get("process_name", ""),
            "all_keys_json_path": wechat_cfg.get("all_keys_json_path", ""),
            "raw_key_set": bool(wechat_cfg.get("raw_key", "")),
            "auto_scan": wechat_cfg.get("auto_scan", True),
            "wechat_ready": self.mcp._key_store is not None,
            "wechat_error": self.mcp._wechat_error,
        }

    def detect_wechat(self) -> dict:
        from src.wechat_parser.wechat_detector import detect_wechat
        detection = detect_wechat()
        wechat_cfg = self.config.setdefault("wechat", {})
        if detection.found:
            wechat_cfg["db_storage_path"] = detection.db_storage_path
            wechat_cfg["process_name"] = detection.process_name
            self.save_config()
            self.reset_components()
        return {
            "found": detection.found,
            "db_storage_path": detection.db_storage_path,
            "wxid": detection.wxid,
            "version": detection.version,
            "process_running": detection.process_running,
            "candidates": detection.candidates,
        }

    def detect_version(self) -> dict:
        from src.wechat_parser.wechat_version import detect_wechat_version
        info = detect_wechat_version()
        return {
            "found": info.found,
            "version": info.version,
            "supports_memory_scan": info.supports_memory_scan,
            "platform": info.platform,
        }

    # ─── 联系人/聊天/分析（委托 MCPServer） ───
    def list_contacts(self) -> dict:
        return self.mcp.tool_list_contacts()

    def get_chat_history(self, talker: str, limit: int = 50,
                         time_from: str = None, time_to: str = None) -> dict:
        return self.mcp.tool_get_chat_history(
            talker=talker, limit=limit, time_from=time_from, time_to=time_to)

    def transcribe(self, msg_svr_id) -> dict:
        return self.mcp.tool_transcribe_voice(msg_svr_id)

    def analyze(self, talker: str, limit: int = 100) -> dict:
        return self.mcp.tool_analyze_customer(talker=talker, limit=limit)

    def search_natural(self, query: str) -> dict:
        return self.mcp.tool_search_by_natural_language(query=query)

    # ─── 密钥管理 ───
    def scan_keys(self) -> dict:
        """内存扫描（macOS 弹 osascript 提权）。"""
        import sys
        wechat_cfg = self.config.get("wechat", {})
        db_path = wechat_cfg.get("db_storage_path", "")
        if not db_path:
            return {"ok": False, "error": "未设置微信数据目录"}
        if sys.platform != "darwin":
            # 非 Mac 直接调 scan_keys（Windows/Linux 需管理员/root）
            from src.wechat_parser.decryptor import scan_keys_macos, scan_keys_windows, scan_keys_linux
            try:
                if sys.platform == "win32":
                    store = scan_keys_windows(db_path)
                else:
                    store = scan_keys_linux(db_path)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        else:
            from src.wechat_parser.decryptor import scan_keys_macos_with_sudo_dialog
            try:
                store = scan_keys_macos_with_sudo_dialog(db_path)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        self.reset_components()
        return {"ok": True, **store.stats()}

    def load_keys_json(self, path: str) -> dict:
        wechat_cfg = self.config.get("wechat", {})
        db_path = wechat_cfg.get("db_storage_path", "")
        if not db_path:
            return {"ok": False, "error": "未设置微信数据目录"}
        try:
            from src.wechat_parser.decryptor import WeChatKeyStore
            store = WeChatKeyStore.load_all_keys_json(path, db_path)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        wechat_cfg["all_keys_json_path"] = path
        self.save_config()
        self.reset_components()
        return {"ok": True, **store.stats()}

    def set_raw_key(self, raw_key: str) -> dict:
        wechat_cfg = self.config.get("wechat", {})
        wechat_cfg["raw_key"] = raw_key.strip()
        self.save_config()
        self.reset_components()
        return {"ok": True}

    # ─── 待办 ───
    def list_todos(self) -> dict:
        store = self.mcp._get_store()
        from src.reminder.todo_manager import TodoManager
        mgr = TodoManager(store)
        todos = mgr.list_pending_todos()
        return {"total": len(todos), "todos": todos}

    def done_todo(self, todo_id: str) -> dict:
        store = self.mcp._get_store()
        from src.reminder.todo_manager import TodoManager
        mgr = TodoManager(store)
        mgr.mark_done(todo_id)
        return {"ok": True}


# ════════════════════════════════════════════════════════════════
#  HTTP 请求处理器
# ════════════════════════════════════════════════════════════════
def make_handler(backend: WebBackend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.info("HTTP %s - %s", self.address_string(), fmt % args)

        def _json(self, code: int, data):
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _ok(self, data):
            self._json(200, {"ok": True, "data": data})

        def _err(self, msg: str, code: int = 400):
            self._json(code, {"ok": False, "error": str(msg)})

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self):
            try:
                self._route_get()
            except Exception as e:
                logger.error("GET %s 异常: %s\n%s", self.path, e, traceback.format_exc())
                self._err(f"内部错误: {e}", 500)

        def do_POST(self):
            try:
                self._route_post()
            except Exception as e:
                logger.error("POST %s 异常: %s\n%s", self.path, e, traceback.format_exc())
                self._err(f"内部错误: {e}", 500)

        # ─── GET 路由 ───
        def _route_get(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if path == "/" or path == "/index.html":
                self._serve_file("index.html", "text/html; charset=utf-8")
            elif path == "/style.css":
                self._serve_file("style.css", "text/css; charset=utf-8")
            elif path == "/app.js":
                self._serve_file("app.js", "application/javascript; charset=utf-8")
            elif path == "/api/status":
                self._ok(backend.get_status())
            elif path == "/api/detect":
                self._ok(backend.detect_wechat())
            elif path == "/api/version":
                self._ok(backend.detect_version())
            elif path == "/api/contacts":
                self._ok(backend.list_contacts())
            elif path == "/api/config":
                self._ok(backend.config)
            elif path == "/api/todos":
                self._ok(backend.list_todos())
            elif path.startswith("/api/chats/"):
                talker = path[len("/api/chats/"):]
                self._ok(backend.get_chat_history(
                    talker=talker,
                    limit=int(qs.get("limit", 50)),
                    time_from=qs.get("time_from"),
                    time_to=qs.get("time_to"),
                ))
            else:
                self._err("未找到", 404)

        # ─── POST 路由 ───
        def _route_post(self):
            parsed = urlparse(self.path)
            path = parsed.path
            body = self._read_body()

            if path == "/api/config":
                backend.config = body
                backend.save_config()
                backend.reset_components()
                self._ok({"saved": True})
            elif path == "/api/transcribe":
                self._ok(backend.transcribe(body.get("msg_svr_id")))
            elif path == "/api/analyze":
                self._ok(backend.analyze(
                    talker=body.get("talker", ""),
                    limit=int(body.get("limit", 100)),
                ))
            elif path == "/api/keys/scan":
                self._ok(backend.scan_keys())
            elif path == "/api/keys/load_json":
                self._ok(backend.load_keys_json(body.get("path", "")))
            elif path == "/api/keys/raw":
                self._ok(backend.set_raw_key(body.get("raw_key", "")))
            elif path.startswith("/api/todos/") and path.endswith("/done"):
                todo_id = path[len("/api/todos/"):-len("/done")]
                self._ok(backend.done_todo(todo_id))
            elif path == "/api/shutdown":
                self._ok(backend.shutdown())
            else:
                self._err("未找到", 404)

        def _serve_file(self, name: str, mime: str):
            fp = _FRONTEND_DIR / name
            if not fp.exists():
                self._err(f"文件不存在: {name}", 404)
                return
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def start_server(backend: WebBackend, host: str = "127.0.0.1",
                 port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    """启动 HTTP 服务，返回 (server, actual_port)。port=0 自动选端口。"""
    handler = make_handler(backend)
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    backend.attach_server(srv)
    actual_port = srv.server_address[1]
    logger.info("Web 后端启动: http://%s:%d", host, actual_port)
    return srv, actual_port

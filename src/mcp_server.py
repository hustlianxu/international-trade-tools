"""MCP (Model Context Protocol) Server - 让外部 AI 客户端查询微信聊天记录。

通过 stdio 模式实现 JSON-RPC 2.0 协议，支持 Claude Desktop、Trae 等 MCP 客户端。
不依赖第三方 mcp 库（打包后无额外依赖），自己实现协议核心：
    initialize / tools/list / tools/call

启动方式：
    python src/mcp_server.py
    或：外贸助手.app/Contents/MacOS/TradeTools --mcp
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("mcp-server")

# MCP 协议版本（与 2024-11-05 规范兼容）
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "trade-tools-mcp"
SERVER_VERSION = "1.0.0"

# JSON-RPC 2.0 标准错误码
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


# ══════════════════════════════════════════════════════════════════════
# 工具定义（供 tools/list 返回给客户端）
# ══════════════════════════════════════════════════════════════════════
TOOLS = [
    {
        "name": "search_chats",
        "description": (
            "按关键词搜索微信聊天记录。可跨所有会话搜索，也可限定到指定会话。"
            "支持时间范围过滤。返回匹配的消息列表。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词（必填），如：报价、订单、发货、样品等",
                },
                "talker": {
                    "type": "string",
                    "description": "限定会话的 talker ID（可选，不填则搜索全部会话）",
                },
                "time_from": {
                    "type": "string",
                    "description": "起始时间，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS（可选）",
                },
                "time_to": {
                    "type": "string",
                    "description": "结束时间，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS（可选）",
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回条数，默认 20",
                    "default": 20,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "list_contacts",
        "description": (
            "列出所有微信联系人/会话。返回每个会话的 talker ID、昵称、最后消息时间。"
            "调用其他工具前可先用本工具获取 talker ID。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_chat_history",
        "description": (
            "获取指定联系人的聊天历史记录（按时间升序）。"
            "支持时间范围过滤与返回条数限制。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "talker": {
                    "type": "string",
                    "description": "会话的 talker ID（必填，可先调用 list_contacts 获取）",
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回条数，默认 50",
                    "default": 50,
                },
                "time_from": {
                    "type": "string",
                    "description": "起始时间，ISO 格式（可选）",
                },
                "time_to": {
                    "type": "string",
                    "description": "结束时间，ISO 格式（可选）",
                },
            },
            "required": ["talker"],
        },
    },
    {
        "name": "transcribe_voice",
        "description": (
            "转写指定的微信语音消息为文字。需提供 msg_svr_id。"
            "若该消息已转写过则直接返回缓存结果，否则实时调用 ASR 引擎转写。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "msg_svr_id": {
                    "type": "integer",
                    "description": "语音消息的服务器全局唯一 ID（必填，可从 search_chats/get_chat_history 结果获取）",
                },
            },
            "required": ["msg_svr_id"],
        },
    },
    {
        "name": "analyze_customer",
        "description": (
            "用 DeepSeek 大模型分析指定客户的聊天记录，提取需求要点、待办事项、已办事项、"
            "客户情绪等结构化信息。语音消息会先转写为文字再分析。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "talker": {
                    "type": "string",
                    "description": "客户会话的 talker ID（必填）",
                },
                "limit": {
                    "type": "integer",
                    "description": "分析最近多少条消息，默认 100",
                    "default": 100,
                },
            },
            "required": ["talker"],
        },
    },
    {
        "name": "search_by_natural_language",
        "description": (
            "自然语言搜索聊天记录。如「上周和张三聊的关于报价的记录」。"
            "内部先用 DeepSeek 把自然语言转为结构化搜索条件（关键词/客户/时间范围），再执行搜索。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然语言查询（必填），如：上周和张三聊的关于报价的记录、今天客户询问产品的消息",
                },
            },
            "required": ["query"],
        },
    },
]


# ══════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════
def _parse_time(s: Any) -> int:
    """把 ISO 字符串/数字字符串/时间戳转为 Unix 秒级时间戳。空值返回 0。"""
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip()
    if not s:
        return 0
    # 纯数字按时间戳处理
    if s.isdigit():
        return int(s)
    try:
        # 兼容 "YYYY-MM-DD HH:MM:SS" → "YYYY-MM-DDTHH:MM:SS"
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        # 仅日期补全为 00:00:00
        if len(s) == 10:
            s = s + "T00:00:00"
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp())
    except Exception:
        return 0


def _ts_to_str(ts: int) -> str:
    """Unix 时间戳转可读字符串 'YYYY-MM-DD HH:MM:SS'。"""
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)


def _format_message(m) -> dict:
    """格式化 WeChatMessage 为可 JSON 序列化的 dict。"""
    # 消息类型 → 可读字符串
    type_map = {1: "text", 3: "image", 34: "voice", 43: "video", 49: "complex"}
    return {
        "msg_svr_id": m.msg_svr_id,
        "talker": m.talker,
        "talker_name": m.talker_name or m.talker,
        "type": type_map.get(m.type, str(m.type)),
        "is_sender": bool(m.is_sender),
        "sender": "me" if m.is_sender else "customer",
        "content": m.content_text,
        "create_time": m.create_time,
        "time_str": _ts_to_str(m.create_time),
        "has_blob": bool(m.blob_data),
    }


def _format_analysis_result(result) -> dict:
    """格式化 AnalysisResult 为可 JSON 序列化的 dict。"""
    return {
        "talker": result.talker,
        "talker_name": result.talker_name,
        "analyzed_at": result.analyzed_at,
        "language": result.language,
        "summary": result.summary,
        "needs": [
            {
                "category": n.category,
                "summary": n.summary,
                "product": n.product,
                "quantity": n.quantity,
                "deadline": n.deadline,
                "urgency": n.urgency,
            }
            for n in result.needs
        ],
        "done_items": result.done_items,
        "todo_items": result.todo_items,
        "customer_mood": result.customer_mood,
    }


def _find_message_by_svr_id(extractor, msg_svr_id: int):
    """通过 msg_svr_id 在所有会话中查找消息（遍历 talker 表）。"""
    try:
        talkers = extractor.list_all_talkers()
    except Exception:
        return None
    for talker in talkers:
        try:
            conn = extractor._get_msg_db_connection(talker)
            table = extractor._get_table_name(conn, talker)
            if not table:
                continue
            cur = conn.execute(
                f"SELECT local_id, msg_svr_id, type, is_sender, create_time, "
                f"message_content, message_blob FROM {table} WHERE msg_svr_id=? LIMIT 1",
                (msg_svr_id,),
            )
            msgs = extractor._parse_messages(cur, talker)
            if msgs:
                return msgs[0]
        except Exception:
            continue
    return None


def _resolve_talker_by_name(extractor, name: str) -> str:
    """根据昵称模糊匹配 talker ID，找不到返回空字符串。"""
    if not name:
        return ""
    try:
        contacts = extractor.list_contacts()
    except Exception:
        return ""
    # 精确匹配优先，其次包含匹配
    for c in contacts:
        if c.get("name") == name:
            return c["talker"]
    for c in contacts:
        cname = c.get("name", "")
        if name in cname or cname in name:
            return c["talker"]
    return ""


# ══════════════════════════════════════════════════════════════════════
# MCP Server 主体
# ══════════════════════════════════════════════════════════════════════
class MCPServer:
    """MCP Server 主类：处理 JSON-RPC 协议与工具调用。"""

    def __init__(self, config: dict):
        self.config = config or {}
        # 运行时组件（懒加载，避免微信未配置时启动崩溃）
        self._key_store = None       # WeChatKeyStore（多密钥）
        self._extractor = None
        self._asr_engine = None
        self._analyzer = None
        self._store = None
        self._init_lock = threading.Lock()
        self._wechat_error = None  # 微信初始化失败的错误信息（缓存避免重复扫描）

    # ──────── 组件懒加载（带友好异常） ────────
    def _get_store(self):
        """获取 Store 实例。"""
        if self._store is None:
            with self._init_lock:
                if self._store is None:
                    from src.paths import get_db_path
                    from src.storage.store import Store
                    db_path = (
                        self.config.get("storage", {}).get("db_path")
                        or str(get_db_path())
                    )
                    self._store = Store(db_path)
        return self._store

    def _init_wechat(self) -> bool:
        """初始化微信密钥存储与消息提取器。成功返回 True，失败记录原因返回 False。

        密钥来源（按优先级）：
        1. all_keys.json（wechat.all_keys_json_path 配置）
        2. 手动 raw_key（wechat.raw_key，96 hex）
        3. 自动扫描（MCP 模式不弹窗提权，需已 sudo 运行）
        """
        if self._key_store is not None:
            return True
        if self._wechat_error:
            return False
        with self._init_lock:
            if self._key_store is not None:
                return True
            if self._wechat_error:
                return False
            try:
                from src.wechat_parser.decryptor import get_key_store
                from src.wechat_parser.message_extractor import MessageExtractor

                wechat_cfg = self.config.get("wechat", {})
                db_path = wechat_cfg.get("db_storage_path", "")
                if not db_path:
                    self._wechat_error = (
                        "微信 db_storage_path 未配置。请在「配置」页填写微信数据目录路径。"
                    )
                    return False
                if not Path(db_path).exists():
                    self._wechat_error = (
                        f"微信 db_storage 路径不存在: {db_path}\n"
                        "请确认微信已登录并填写正确路径。"
                    )
                    return False

                # MCP 模式默认不弹窗提权（避免阻塞 stdio 通道）
                # 用户应预先加载 all_keys.json 或在 sudo 下运行
                self._key_store = get_key_store(
                    db_storage_path=db_path,
                    manual_raw_key=wechat_cfg.get("raw_key", ""),
                    all_keys_json_path=wechat_cfg.get("all_keys_json_path", ""),
                    auto_scan=wechat_cfg.get("auto_scan", True),
                    use_sudo_dialog=False,
                )
                self._extractor = MessageExtractor.from_key_store(self._key_store, db_path)
                logger.info("[MCP] 微信解析组件初始化成功：%s", self._key_store.stats())
                return True
            except Exception as e:
                self._wechat_error = (
                    f"初始化微信解析失败: {e}\n"
                    "可在「设置」中加载 all_keys.json 或手动填入密钥。"
                )
                logger.error("[MCP] 微信初始化失败", exc_info=True)
                return False

    def _get_extractor(self):
        """获取 MessageExtractor，失败抛 RuntimeError。"""
        if not self._init_wechat():
            raise RuntimeError(self._wechat_error or "微信未初始化")
        return self._extractor

    def _get_asr(self):
        """获取 ASR 引擎实例。"""
        if self._asr_engine is None:
            with self._init_lock:
                if self._asr_engine is None:
                    from src.asr.base import create_asr
                    asr_cfg = self.config.get("asr", {})
                    if not asr_cfg:
                        raise RuntimeError("ASR 配置缺失，无法进行语音转写")
                    self._asr_engine = create_asr(asr_cfg)
        return self._asr_engine

    def _get_analyzer(self):
        """获取 LLM 分析器实例（支持多厂商）。"""
        if self._analyzer is None:
            with self._init_lock:
                if self._analyzer is None:
                    from src.llm import create_analyzer
                    llm_cfg = self.config.get("llm", {})
                    if not llm_cfg:
                        raise RuntimeError("LLM 配置缺失，无法执行 AI 分析。")
                    try:
                        self._analyzer = create_analyzer(llm_cfg)
                    except ValueError as e:
                        raise RuntimeError(
                            f"至少一个启用厂商需配置 api_key。{e} "
                            "请在「配置」页填写对应厂商的 API Key。"
                        ) from e
        return self._analyzer

    # ──────── 工具实现 ────────
    def tool_search_chats(
        self,
        keyword: str,
        talker: str = None,
        time_from: str = None,
        time_to: str = None,
        limit: int = 20,
    ) -> Any:
        """按关键词搜索聊天记录。"""
        if not keyword:
            raise RuntimeError("keyword 参数不能为空")
        limit = _safe_int(limit, default=20, minimum=1, maximum=500)
        extractor = self._get_extractor()
        tf = _parse_time(time_from)
        tt = _parse_time(time_to)
        messages = extractor.search_messages(
            keyword=keyword,
            talker_id=talker or None,
            time_from=tf,
            time_to=tt,
            limit=limit,
        )
        return {
            "keyword": keyword,
            "talker": talker or None,
            "total": len(messages),
            "messages": [_format_message(m) for m in messages],
        }

    def tool_list_contacts(self) -> Any:
        """列出所有联系人/会话。"""
        extractor = self._get_extractor()
        contacts = extractor.list_contacts()
        result = [
            {
                "talker": c["talker"],
                "name": c["name"],
                "type": c.get("type", "user"),
                "last_time": c.get("last_time", 0),
                "last_time_str": _ts_to_str(c.get("last_time", 0)),
            }
            for c in contacts
        ]
        return {"total": len(result), "contacts": result}

    def tool_get_chat_history(
        self,
        talker: str,
        limit: int = 50,
        time_from: str = None,
        time_to: str = None,
    ) -> Any:
        """获取指定联系人的聊天历史。"""
        if not talker:
            raise RuntimeError("talker 参数不能为空")
        limit = _safe_int(limit, default=50, minimum=1, maximum=1000)
        extractor = self._get_extractor()
        tf = _parse_time(time_from)
        tt = _parse_time(time_to)
        messages = extractor.extract_messages_by_time(
            talker_id=talker,
            time_from=tf,
            time_to=tt,
            limit=limit,
        )
        # 补充 talker_name
        talker_name = talker
        try:
            contacts = extractor.list_contacts()
            for c in contacts:
                if c["talker"] == talker:
                    talker_name = c.get("name") or talker
                    break
        except Exception:
            pass
        return {
            "talker": talker,
            "talker_name": talker_name,
            "total": len(messages),
            "messages": [_format_message(m) for m in messages],
        }

    def tool_transcribe_voice(self, msg_svr_id) -> Any:
        """转写指定语音消息。"""
        try:
            msg_svr_id = int(msg_svr_id)
        except (TypeError, ValueError):
            raise RuntimeError(f"msg_svr_id 必须为整数，收到: {msg_svr_id!r}")

        # 1. 先查本地缓存
        store = self._get_store()
        cached = store.get_transcription(msg_svr_id)
        if cached:
            return {
                "msg_svr_id": msg_svr_id,
                "text": cached,
                "cached": True,
            }

        # 2. 查找对应的语音消息
        extractor = self._get_extractor()
        from src.wechat_parser.message_extractor import MSG_TYPE_VOICE

        msg = _find_message_by_svr_id(extractor, msg_svr_id)
        if msg is None:
            raise RuntimeError(
                f"未找到 msg_svr_id={msg_svr_id} 的消息。可能该消息不在已解密的会话库中。"
            )
        if msg.type != MSG_TYPE_VOICE:
            type_map = {1: "文本", 3: "图片", 43: "视频", 49: "复合"}
            raise RuntimeError(
                f"msg_svr_id={msg_svr_id} 不是语音消息（类型: "
                f"{type_map.get(msg.type, msg.type)}），无法转写。"
            )
        if not msg.blob_data:
            raise RuntimeError(f"msg_svr_id={msg_svr_id} 的语音数据为空，无法转写。")

        # 3. 调用 ASR 转写
        asr = self._get_asr()
        from src.processor import process_voice_message

        text = process_voice_message(msg, asr, store)
        return {
            "msg_svr_id": msg_svr_id,
            "talker": msg.talker,
            "create_time": msg.create_time,
            "time_str": _ts_to_str(msg.create_time),
            "text": text or "",
            "cached": False,
        }

    def tool_analyze_customer(self, talker: str, limit: int = 100) -> Any:
        """用 DeepSeek 分析客户聊天记录。"""
        if not talker:
            raise RuntimeError("talker 参数不能为空")
        limit = _safe_int(limit, default=100, minimum=1, maximum=1000)

        extractor = self._get_extractor()
        analyzer = self._get_analyzer()
        messages = extractor.extract_messages_by_time(
            talker_id=talker, limit=limit
        )
        if not messages:
            return {
                "talker": talker,
                "summary": "无聊天记录",
                "needs": [],
                "done_items": [],
                "todo_items": [],
                "customer_mood": "",
            }

        # 解析 talker_name
        talker_name = talker
        try:
            contacts = extractor.list_contacts()
            for c in contacts:
                if c["talker"] == talker:
                    talker_name = c.get("name") or talker
                    break
        except Exception:
            pass

        # 构建 dialog_messages；语音消息先转写
        from src.wechat_parser.message_extractor import (
            MSG_TYPE_TEXT,
            MSG_TYPE_VOICE,
        )

        store = self._get_store()
        dialog_messages = []
        for m in messages:
            if m.type == MSG_TYPE_TEXT:
                if m.content_text.strip():
                    dialog_messages.append(
                        {
                            "is_sender": m.is_sender,
                            "text": m.content_text,
                            "time": _ts_to_str(m.create_time),
                        }
                    )
            elif m.type == MSG_TYPE_VOICE:
                text = store.get_transcription(m.msg_svr_id)
                if text is None:
                    try:
                        asr = self._get_asr()
                        from src.processor import process_voice_message

                        text = process_voice_message(m, asr, store)
                    except Exception as e:
                        logger.warning(
                            "[MCP] 语音转写失败 msg_svr_id=%s: %s", m.msg_svr_id, e
                        )
                        text = ""
                if text:
                    dialog_messages.append(
                        {
                            "is_sender": m.is_sender,
                            "text": f"[语音] {text}",
                            "time": _ts_to_str(m.create_time),
                        }
                    )

        if not dialog_messages:
            return {
                "talker": talker,
                "talker_name": talker_name,
                "summary": "聊天记录中无可分析的文字内容（可能全是图片/视频）",
                "needs": [],
                "done_items": [],
                "todo_items": [],
                "customer_mood": "",
            }

        result = analyzer.analyze_dialog(talker, talker_name, dialog_messages)
        # 保存分析结果到本地 store（与 GUI/CLI 一致）
        try:
            store.save_analysis(result)
        except Exception as e:
            logger.warning("[MCP] 保存分析结果失败: %s", e)
        return _format_analysis_result(result)

    def tool_search_by_natural_language(self, query: str) -> Any:
        """自然语言搜索：先 DeepSeek 解析条件，再执行搜索。"""
        if not query or not query.strip():
            raise RuntimeError("query 参数不能为空")
        analyzer = self._get_analyzer()

        # 第一步：用 DeepSeek 把自然语言转为结构化搜索条件
        system_prompt = (
            "你是一个搜索条件解析器。只输出 JSON，不要任何解释或 markdown 代码块。"
        )
        user_prompt = f"""请把用户的自然语言搜索请求转换为结构化搜索条件。

当前时间：{datetime.now().isoformat()}

用户查询：{query}

输出 JSON 格式（严格 JSON，无 markdown）：
{{
  "keyword": "搜索关键词（必填，从查询中提取核心业务词，如'报价'、'订单'、'产品'、'发货'等；若无明确业务词则用查询核心动词/名词）",
  "talker_name": "客户名称（如查询中明确提到如'张三'则填入，否则为空字符串）",
  "time_from": "起始时间 ISO 格式 YYYY-MM-DDTHH:MM:SS（如查询提到'上周'/'今天'/'昨天'/'最近三天'等则计算；否则为空字符串）",
  "time_to": "结束时间 ISO 格式 YYYY-MM-DDTHH:MM:SS（同上，无截止则为空字符串）"
}}

时间词解析规则：
- '上周'：上周一 00:00:00 到上周日 23:59:59
- '本周'：本周一 00:00:00 到当前时间
- '今天'：今天 00:00:00 到当前时间
- '昨天'：昨天 00:00:00 到 23:59:59
- '最近N天'：N天前 00:00:00 到当前时间
- '上月'：上月一号 00:00:00 到上月最后一天 23:59:59
- 若查询未明确时间范围，time_from 和 time_to 都返回空字符串
"""
        try:
            raw = analyzer.chat(system_prompt, user_prompt, json_mode=True)
            params = json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("[MCP] 自然语言解析失败，回退为直接关键词搜索: %s", e)
            params = {
                "keyword": query,
                "talker_name": "",
                "time_from": "",
                "time_to": "",
            }

        keyword = (params.get("keyword") or "").strip() or query
        talker_name = (params.get("talker_name") or "").strip()
        time_from = (params.get("time_from") or "").strip()
        time_to = (params.get("time_to") or "").strip()

        # 第二步：根据 talker_name 解析 talker ID
        talker_id = ""
        extractor = self._get_extractor()
        if talker_name:
            talker_id = _resolve_talker_by_name(extractor, talker_name)

        # 第三步：执行搜索
        tf = _parse_time(time_from)
        tt = _parse_time(time_to)
        messages = extractor.search_messages(
            keyword=keyword,
            talker_id=talker_id or None,
            time_from=tf,
            time_to=tt,
            limit=50,
        )
        return {
            "original_query": query,
            "parsed_params": {
                "keyword": keyword,
                "talker_name": talker_name,
                "talker_id": talker_id or None,
                "time_from": time_from,
                "time_to": time_to,
            },
            "total": len(messages),
            "messages": [_format_message(m) for m in messages],
        }

    # ──────── JSON-RPC 协议处理 ────────
    def serve(self):
        """主循环：从 stdin 读取 JSON-RPC 请求，写响应到 stdout。

        协议格式：每行一个 JSON 对象（newline-delimited JSON）。
        收到 EOF 时退出。
        """
        logger.info("[MCP] Server 启动，等待客户端请求...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                self._send_error(None, JSONRPC_PARSE_ERROR, f"JSON 解析失败: {e}")
                continue
            # 支持批量请求（JSON-RPC 2.0）
            if isinstance(request, list):
                for req in request:
                    self._handle_request(req)
            else:
                self._handle_request(request)
        logger.info("[MCP] stdin 已关闭，Server 退出")

    def _handle_request(self, request: dict):
        """处理单个 JSON-RPC 请求。"""
        if not isinstance(request, dict):
            self._send_error(None, JSONRPC_INVALID_REQUEST, "请求必须是 JSON 对象")
            return

        # 通知：无 "id" 字段，不响应
        has_id = "id" in request
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {}) or {}

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method in ("notifications/initialized", "initialized"):
                # 客户端初始化完成通知，无需响应
                return
            elif method == "tools/list":
                result = self._handle_tools_list(params)
            elif method == "tools/call":
                result = self._handle_tools_call(params)
            elif method == "ping":
                result = {}
            elif method == "shutdown":
                result = {}
            else:
                if not has_id:
                    # 未知通知，静默忽略
                    return
                self._send_error(
                    req_id, JSONRPC_METHOD_NOT_FOUND, f"未知方法: {method}"
                )
                return

            if has_id:
                self._send_result(req_id, result)
        except Exception as e:
            logger.error("[MCP] 处理请求 %s 失败: %s", method, e, exc_info=True)
            if has_id:
                self._send_error(req_id, JSONRPC_INTERNAL_ERROR, str(e))

    def _handle_initialize(self, params: dict) -> dict:
        """处理 initialize 请求，返回协议版本与能力声明。"""
        client_info = params.get("clientInfo", {})
        logger.info(
            "[MCP] 客户端初始化: %s v%s",
            client_info.get("name", "unknown"),
            client_info.get("version", "unknown"),
        )
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        }

    def _handle_tools_list(self, params: dict) -> dict:
        """处理 tools/list 请求，返回工具列表。"""
        return {"tools": TOOLS}

    def _handle_tools_call(self, params: dict) -> dict:
        """处理 tools/call 请求，调用指定工具。"""
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        if not isinstance(args, dict):
            args = {}

        # 工具调度表
        handlers = {
            "search_chats": lambda: self.tool_search_chats(**args),
            "list_contacts": lambda: self.tool_list_contacts(**args),
            "get_chat_history": lambda: self.tool_get_chat_history(**args),
            "transcribe_voice": lambda: self.tool_transcribe_voice(**args),
            "analyze_customer": lambda: self.tool_analyze_customer(**args),
            "search_by_natural_language": lambda: self.tool_search_by_natural_language(**args),
        }

        handler = handlers.get(name)
        if handler is None:
            return self._tool_error(f"未知工具: {name}。可用工具: {list(handlers.keys())}")

        try:
            result = handler()
            return self._tool_ok(result)
        except RuntimeError as e:
            # 业务级错误（如微信未配置、密钥错误、消息不存在等）
            logger.warning("[MCP] 工具 %s 业务错误: %s", name, e)
            return self._tool_error(str(e))
        except TypeError as e:
            # 参数不匹配
            logger.warning("[MCP] 工具 %s 参数错误: %s", name, e)
            return self._tool_error(f"参数错误: {e}")
        except Exception as e:
            logger.error("[MCP] 工具 %s 执行异常: %s", name, e, exc_info=True)
            return self._tool_error(f"工具执行异常: {e}")

    # ──────── 响应构造 ────────
    def _tool_ok(self, result: Any) -> dict:
        """构造工具调用成功响应（CallToolResult）。"""
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    def _tool_error(self, msg: str) -> dict:
        """构造工具调用错误响应（isError=true）。"""
        return {
            "content": [{"type": "text", "text": f"[错误] {msg}"}],
            "isError": True,
        }

    def _send_result(self, req_id, result: Any):
        """发送 JSON-RPC 成功响应到 stdout。"""
        response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _send_error(self, req_id, code: int, message: str):
        """发送 JSON-RPC 错误响应到 stdout。"""
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════════
# 辅助：参数安全转换
# ══════════════════════════════════════════════════════════════════════
def _safe_int(value, default: int, minimum: int = None, maximum: int = None) -> int:
    """把 value 安全转为 int，转换失败或越界则用 default。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


# ══════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════
def load_config() -> dict:
    """加载配置文件。

    优先级：环境变量 TRADE_TOOLS_CONFIG > 用户目录 config.yaml > 项目内默认。
    """
    from src.paths import ensure_default_config, get_config_path, get_db_path

    env_path = os.environ.get("TRADE_TOOLS_CONFIG", "").strip()
    if env_path:
        config_path = Path(env_path)
    else:
        config_path = get_config_path()
        if not config_path.exists():
            ensure_default_config()

    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error("[MCP] 加载配置失败 %s: %s", config_path, e)
        cfg = {}

    # 确保 db_path
    if not cfg.get("storage", {}).get("db_path"):
        cfg.setdefault("storage", {})["db_path"] = str(get_db_path())
    return cfg


def main():
    """MCP Server 入口：配置日志（输出到 stderr 不污染 stdout 协议通道）后启动。"""
    # 日志必须输出到 stderr，stdout 是 JSON-RPC 协议通道
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    config = load_config()
    server = MCPServer(config)
    try:
        server.serve()
    except KeyboardInterrupt:
        logger.info("[MCP] 收到中断信号，退出")


if __name__ == "__main__":
    main()

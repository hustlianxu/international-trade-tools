"""核心逻辑单元测试。

在沙箱/CI 环境验证纯逻辑函数（不依赖 pilk/mlx_whisper/openai 等外部库）。
运行: python -m pytest tests/ -v
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ═══════ 1. SILK 解码逻辑 ═══════
class TestSilkDecoder:
    def test_is_wechat_silk_detects_02_header(self):
        from src.wechat_parser.silk_decoder import is_wechat_silk
        assert is_wechat_silk(b"\x02\x53\x49\x4c\x4b") is True
        assert is_wechat_silk(b"#!SILK_V3") is False
        assert is_wechat_silk(b"") is False

    def test_normalize_wechat_silk_adds_header_and_footer(self):
        from src.wechat_parser.silk_decoder import normalize_wechat_silk, SILK_V3_HEADER
        # 微信格式: 0x02 + 数据
        wechat_data = b"\x02" + b"\x53\x49\x4c\x4b\x5f\x64\x61\x74\x61"
        result = normalize_wechat_silk(wechat_data)
        # 应加标准头和尾
        assert result.startswith(SILK_V3_HEADER)
        assert result.endswith(b"\xFF\xFF")
        # 0x02 应被去掉
        assert b"\x02" not in result[:len(SILK_V3_HEADER)]

    def test_normalize_standard_silk_unchanged(self):
        from src.wechat_parser.silk_decoder import normalize_wechat_silk
        standard = b"#!SILK_V3\x00\x01\x02"
        result = normalize_wechat_silk(standard)
        assert result == standard


# ═══════ 2. 路径管理 ═══════
class TestPaths:
    def test_get_app_dir_creates_directory(self):
        from src.paths import get_app_dir
        app_dir = get_app_dir()
        assert app_dir.exists()
        assert "trade-tools" in str(app_dir).lower() or "trade_tools" in str(app_dir).lower()

    def test_get_config_path_under_app_dir(self):
        from src.paths import get_app_dir, get_config_path
        config_path = get_config_path()
        assert config_path.parent == get_app_dir()
        assert config_path.name == "config.yaml"

    def test_get_db_path_creates_data_dir(self):
        from src.paths import get_db_path
        db_path = get_db_path()
        assert db_path.parent.exists()
        assert db_path.name == "trade_tools.db"

    def test_ensure_default_config_creates_file(self):
        from src.paths import ensure_default_config, get_config_path
        # 删除现有配置（如果有的话）
        config_path = get_config_path()
        if config_path.exists():
            config_path.unlink()
        result = ensure_default_config()
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "wechat" in content or "asr" in content


# ═══════ 3. 存储层 ═══════
class TestStore:
    def _get_test_store(self):
        from src.storage.store import Store
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return Store(tmp.name)

    def test_save_and_get_cursor(self):
        store = self._get_test_store()
        store.save_cursor("wxid_test123", 1000, 1700000000)
        cursor = store.get_cursor("wxid_test123")
        assert cursor["last_msg_svr_id"] == 1000
        assert cursor["last_create_time"] == 1700000000

    def test_cursor_upsert(self):
        store = self._get_test_store()
        store.save_cursor("wxid_test", 100, 1000)
        store.save_cursor("wxid_test", 200, 2000)
        cursor = store.get_cursor("wxid_test")
        assert cursor["last_msg_svr_id"] == 200  # 被更新

    def test_save_and_get_transcription(self):
        store = self._get_test_store()
        store.save_transcription(5001, "wxid_test", "/tmp/test.silk", "Hola cliente", "es", 5.5)
        text = store.get_transcription(5001)
        assert text == "Hola cliente"

    def test_todo_crud(self):
        store = self._get_test_store()
        from src.reminder.todo_manager import TodoItem
        item = TodoItem(
            talker="wxid_carlos",
            talker_name="Carlos Mexico",
            content="回复关于交期的疑问",
            category="logistics",
            urgency="high",
            created_at=datetime.now().isoformat(),
            status="pending",
        )
        todo_id = store.save_todo(item)
        assert todo_id > 0

        todos = store.get_todos(status="pending")
        assert len(todos) == 1
        assert todos[0].content == "回复关于交期的疑问"
        assert todos[0].talker_name == "Carlos Mexico"

        store.update_todo_status(todo_id, "done", datetime.now().isoformat())
        pending = store.get_todos(status="pending")
        assert len(pending) == 0


# ═══════ 4. TODO 管理器 ═══════
class TestTodoManager:
    def _get_mock_store(self):
        store = MagicMock()
        store.get_todos.return_value = []
        return store

    def test_generate_reminder_empty(self):
        from src.reminder.todo_manager import TodoManager
        store = self._get_mock_store()
        mgr = TodoManager(store)
        text = mgr.generate_reminder()
        assert "无待办" in text

    def test_generate_reminder_with_items(self):
        from src.reminder.todo_manager import TodoManager, TodoItem
        store = MagicMock()
        store.get_todos.return_value = [
            TodoItem(id=1, talker="wxid_1", talker_name="Carlos", content="发报价单",
                     urgency="high", created_at=datetime.now().isoformat(), status="pending"),
            TodoItem(id=2, talker="wxid_2", talker_name="María", content="确认样品",
                     urgency="normal", created_at=datetime.now().isoformat(), status="pending"),
        ]
        mgr = TodoManager(store)
        text = mgr.generate_reminder()
        assert "高优先级" in text
        assert "Carlos" in text
        assert "发报价单" in text
        assert "待办" in text
        assert "María" in text

    def test_add_from_analysis(self):
        from src.llm.deepseek_analyzer import AnalysisResult, CustomerNeed
        from src.reminder.todo_manager import TodoManager
        store = MagicMock()
        store.save_todo.return_value = 1
        mgr = TodoManager(store)

        result = AnalysisResult(
            talker="wxid_carlos",
            talker_name="Carlos",
            analyzed_at=datetime.now().isoformat(),
            summary="客户询问5000件A-100报价",
            needs=[CustomerNeed(category="inquiry", summary="询价5000件", urgency="high")],
            todo_items=["回复报价", "确认交期"],
            done_items=["已发送目录"],
        )
        mgr.add_from_analysis(result)
        # 应保存 2 个待办 + 1 个已办
        assert store.save_todo.call_count == 3


# ═══════ 5. DeepSeek 分析器（mock API）═══════
class TestDeepSeekAnalyzer:
    def test_analyze_dialog_parses_json(self):
        from src.llm.deepseek_analyzer import DeepSeekAnalyzer

        analyzer = DeepSeekAnalyzer({
            "api_key": "fake-key",
            "base_url": "https://fake",
            "model": "deepseek-chat",
        })

        # mock API 返回
        mock_response = json.dumps({
            "language": "es",
            "summary": "客户询价5000件A-100",
            "needs": [{
                "category": "inquiry",
                "summary": "询价",
                "product": "A-100",
                "quantity": "5000",
                "deadline": "",
                "urgency": "high",
            }],
            "done_items": [],
            "todo_items": ["回复报价", "确认交期"],
            "customer_mood": "急切",
        })

        with patch.object(analyzer, "_call_deepseek", return_value=mock_response):
            result = analyzer.analyze_dialog(
                talker="wxid_carlos",
                talker_name="Carlos",
                messages=[
                    {"is_sender": 0, "text": "Hola, necesito cotización para 5000 unidades del A-100", "time": "2026-07-02 10:00"},
                ],
            )

        assert result.language == "es"
        assert result.summary == "客户询价5000件A-100"
        assert len(result.needs) == 1
        assert result.needs[0].category == "inquiry"
        assert result.needs[0].urgency == "high"
        assert len(result.todo_items) == 2
        assert "回复报价" in result.todo_items
        assert result.customer_mood == "急切"

    def test_analyze_empty_dialog(self):
        from src.llm.deepseek_analyzer import DeepSeekAnalyzer

        analyzer = DeepSeekAnalyzer({"api_key": "fake", "base_url": "fake", "model": "fake"})
        result = analyzer.analyze_dialog("wxid_test", "Test", [])
        assert result.summary == ""  # 空对话不调 API

    def test_analyze_invalid_json_fallback(self):
        from src.llm.deepseek_analyzer import DeepSeekAnalyzer

        analyzer = DeepSeekAnalyzer({"api_key": "fake", "base_url": "fake", "model": "fake"})
        with patch.object(analyzer, "_call_deepseek", return_value="not valid json {{{"):
            result = analyzer.analyze_dialog("wxid_test", "Test", [
                {"is_sender": 0, "text": "Hola", "time": "2026-07-02 10:00"},
            ])
        assert "失败" in result.summary or "异常" in result.summary
        assert len(result.needs) == 0


# ═══════ 6. 配置加载 ═══════
class TestConfigLoad:
    def test_load_config_from_file(self):
        import yaml
        from src.paths import get_config_path

        # 写入测试配置
        config_path = get_config_path()
        test_config = {
            "wechat": {"db_storage_path": "/test/path", "process_name": "WeChat.exe"},
            "asr": {"engine": "volcengine"},
            "llm": {"deepseek": {"api_key": "sk-test"}},
        }
        with open(config_path, "w") as f:
            yaml.safe_dump(test_config, f)

        # 重新加载
        with open(config_path) as f:
            loaded = yaml.safe_load(f)
        assert loaded["asr"]["engine"] == "volcengine"
        assert loaded["llm"]["deepseek"]["api_key"] == "sk-test"


# ═══════ 7. GUI 模块导入（无头环境）═══════
class TestGuiImport:
    def test_gui_module_compiles(self):
        """验证 GUI 模块可被编译（不实际创建窗口）。"""
        import py_compile
        gui_path = PROJECT_ROOT / "src" / "gui_app.py"
        assert py_compile.compile(str(gui_path), doraise=True) is not None


if __name__ == "__main__":
    # 简单的手动运行（无 pytest 时）
    import traceback
    test_classes = [
        TestSilkDecoder, TestPaths, TestStore, TestTodoManager,
        TestDeepSeekAnalyzer, TestConfigLoad, TestGuiImport,
    ]
    passed = 0
    failed = 0
    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    print(f"  ✓ {cls.__name__}.{method_name}")
                    passed += 1
                except Exception as e:
                    print(f"  ✗ {cls.__name__}.{method_name}: {e}")
                    traceback.print_exc()
                    failed += 1
    print(f"\n=== 测试结果: {passed} 通过, {failed} 失败 ===")
    sys.exit(1 if failed else 0)

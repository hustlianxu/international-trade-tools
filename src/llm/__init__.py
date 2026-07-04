"""LLM 多厂商分析模块。

公开 API：
    create_analyzer(llm_config) -> LLMEngine   工厂函数（推荐入口）
    MultiAnalyzer                              多厂商聚合分析器
    LLMEngine                                  抽象基类
    AnalysisResult / CustomerNeed              数据类
    DeepSeekAnalyzer / OpenAIAnalyzer / ...    各厂商实现
"""
from src.llm.base import AnalysisResult, CustomerNeed, LLMEngine
from src.llm.claude_analyzer import ClaudeAnalyzer
from src.llm.deepseek_analyzer import DeepSeekAnalyzer
from src.llm.gemini_analyzer import GeminiAnalyzer
from src.llm.openai_analyzer import OpenAIAnalyzer
from src.llm.qwen_analyzer import QwenAnalyzer
from src.llm.registry import MultiAnalyzer, create_analyzer

__all__ = [
    "create_analyzer",
    "MultiAnalyzer",
    "LLMEngine",
    "AnalysisResult",
    "CustomerNeed",
    "DeepSeekAnalyzer",
    "OpenAIAnalyzer",
    "ClaudeAnalyzer",
    "GeminiAnalyzer",
    "QwenAnalyzer",
]

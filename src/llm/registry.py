"""LLM 工厂与多厂商聚合。

create_analyzer(llm_config) 根据配置返回单厂商 LLMEngine 或 MultiAnalyzer。
MultiAnalyzer 并发调用各厂商，可选 aggregator 厂商做综合分析。
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from src.llm.base import AnalysisResult, CustomerNeed, LLMEngine
from src.llm.prompt import AGGREGATE_PROMPT

logger = logging.getLogger(__name__)


# 厂商 id → Analyzer 类的注册表（延迟 import 以避免 SDK 缺失时崩溃）
_PROVIDER_CLASSES = {
    "deepseek": ("src.llm.deepseek_analyzer", "DeepSeekAnalyzer"),
    "openai": ("src.llm.openai_analyzer", "OpenAIAnalyzer"),
    "claude": ("src.llm.claude_analyzer", "ClaudeAnalyzer"),
    "gemini": ("src.llm.gemini_analyzer", "GeminiAnalyzer"),
    "qwen": ("src.llm.qwen_analyzer", "QwenAnalyzer"),
}


def _load_provider_class(provider_id: str):
    """按 id 加载 Analyzer 类。返回 (类, ImportError 友好提示)。"""
    spec = _PROVIDER_CLASSES.get(provider_id)
    if not spec:
        raise ValueError(f"不支持的 LLM 厂商: {provider_id}")
    module_path, class_name = spec
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _migrate_legacy_config(llm_config: dict) -> tuple[list[str], dict, str]:
    """把旧式配置迁移为新 schema。

    旧式：llm_config = {"provider": "deepseek", "deepseek": {...}}
    新式：llm_config = {"enabled": [...], "providers": {...}, "aggregator": ""}

    返回 (enabled_list, providers_dict, aggregator_id)。
    """
    if "providers" in llm_config and "enabled" in llm_config:
        return (
            list(llm_config.get("enabled") or []),
            dict(llm_config.get("providers") or {}),
            llm_config.get("aggregator", "") or "",
        )

    # 旧式：每个顶层 key（除 provider/aggregator/enabled/providers 外）视为一个厂商配置
    skip = {"provider", "aggregator", "enabled", "providers"}
    providers = {k: v for k, v in llm_config.items() if k not in skip and isinstance(v, dict)}
    if not providers:
        # 极端兜底：完全空配置
        return [], {}, ""
    enabled = [llm_config.get("provider", "") or next(iter(providers))]
    if enabled[0] not in providers:
        enabled = [next(iter(providers))]
    return enabled, providers, llm_config.get("aggregator", "") or ""


def create_analyzer(llm_config: dict) -> LLMEngine:
    """根据 llm 配置创建 LLMEngine 实例。

    新 schema：
        llm:
          enabled: ["deepseek", "openai"]
          aggregator: ""            # 可选，多厂商时指定聚合厂商 id
          providers:
            deepseek: {...}
            openai: {...}

    旧 schema（向后兼容）：
        llm:
          provider: "deepseek"
          deepseek: {...}

    Args:
        llm_config: config["llm"] 字典

    Returns:
        LLMEngine 实例（单厂商）或 MultiAnalyzer（多厂商）

    Raises:
        ValueError: 没有任何可用厂商（无 api_key 或 enabled 为空）
    """
    if not llm_config:
        raise ValueError("LLM 配置缺失，无法创建分析器")

    enabled, providers, aggregator_id = _migrate_legacy_config(llm_config)

    if not enabled:
        raise ValueError(
            "未启用任何 LLM 厂商（llm.enabled 为空）。"
            "请在配置中至少启用一个厂商，如 enabled: [\"deepseek\"]"
        )

    # 过滤出 providers 里实际有配置且 api_key 非空的厂商
    usable = []
    for pid in enabled:
        cfg = providers.get(pid)
        if not cfg:
            logger.warning("[LLM] 厂商 %s 在 providers 中无配置，跳过", pid)
            continue
        if not cfg.get("api_key"):
            logger.warning("[LLM] 厂商 %s 未配置 api_key，跳过", pid)
            continue
        usable.append(pid)

    if not usable:
        raise ValueError(
            "至少一个启用厂商需配置 api_key，请在「配置」页填写对应厂商的 API Key。"
        )

    # 实例化各厂商 analyzer
    engines: list[LLMEngine] = []
    for pid in usable:
        try:
            cls = _load_provider_class(pid)
            eng = cls(providers[pid])
            engines.append(eng)
        except ImportError as e:
            logger.error("[LLM] 厂商 %s SDK 缺失，跳过: %s", pid, e)
        except Exception as e:
            logger.error("[LLM] 厂商 %s 实例化失败，跳过: %s", pid, e)

    if not engines:
        raise ValueError(
            "所有启用的 LLM 厂商均无法初始化（SDK 缺失或配置错误）。"
            "请检查依赖与配置后重试。"
        )

    # 单厂商直接返回
    if len(engines) == 1:
        return engines[0]

    # 多厂商：尝试解析 aggregator
    aggregator: Optional[LLMEngine] = None
    if aggregator_id:
        if aggregator_id in providers and providers[aggregator_id].get("api_key"):
            try:
                cls = _load_provider_class(aggregator_id)
                aggregator = cls(providers[aggregator_id])
            except Exception as e:
                logger.error("[LLM] 聚合厂商 %s 初始化失败，将退化为不聚合: %s", aggregator_id, e)
        else:
            logger.warning(
                "[LLM] 聚合厂商 %s 未配置或无 api_key，将退化为不聚合",
                aggregator_id,
            )

    return MultiAnalyzer(engines, aggregator=aggregator)


class MultiAnalyzer(LLMEngine):
    """多厂商分析器：并发调用各厂商，可选 aggregator 做综合分析。

    - 单厂商失败不阻塞其他厂商（记录 error）。
    - 若 aggregator 非空：把各厂商成功的 AnalysisResult 序列化为 JSON，喂给
      AGGREGATE_PROMPT，由 aggregator 跑 chat() 输出合并的 AnalysisResult。
    - 若 aggregator 为空：返回第一个成功的结果，并在 sub_results 字段保留各厂商结果。
    """

    def __init__(self, engines: list[LLMEngine], aggregator: Optional[LLMEngine] = None):
        if not engines:
            raise ValueError("MultiAnalyzer 至少需要一个 LLMEngine")
        self._engines = list(engines)
        self._aggregator = aggregator

    def name(self) -> str:
        names = "+".join(e.name() for e in self._engines)
        if self._aggregator:
            return f"MultiAnalyzer({len(self._engines)} 厂商, 聚合={self._aggregator.name()})"
        return f"MultiAnalyzer({len(self._engines)} 厂商: {names})"

    def _provider_id(self) -> str:
        return "multi"

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        **kwargs,
    ) -> str:
        """通用对话接口：MultiAnalyzer 委托给第一个引擎。

        主要供 mcp_server 自然语言搜索等场景使用（避免外部调用者拿到 MultiAnalyzer
        时找不到 chat 方法）。
        """
        return self._engines[0].chat(system, user, json_mode=json_mode, **kwargs)

    def analyze_dialog(
        self,
        talker: str,
        talker_name: str,
        messages: list[dict],
    ) -> AnalysisResult:
        """并发分析，按 aggregator 配置决定合并策略。"""
        # 并发调用各厂商
        sub_results: list[AnalysisResult] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=len(self._engines)) as pool:
            future_to_engine = {
                pool.submit(e.analyze_dialog, talker, talker_name, messages): e
                for e in self._engines
            }
            for fut in as_completed(future_to_engine):
                eng = future_to_engine[fut]
                try:
                    res = fut.result()
                    if res is not None:
                        sub_results.append(res)
                except Exception as e:
                    errors.append(f"{eng.name()}: {e}")
                    logger.error("[MultiAnalyzer] 厂商 %s 分析失败: %s", eng.name(), e)

        if not sub_results:
            # 全部失败
            return AnalysisResult(
                talker=talker,
                talker_name=talker_name,
                analyzed_at=datetime.now().isoformat(),
                summary=f"所有厂商分析失败: {errors}",
                provider="multi",
            )

        # 无 aggregator：返回第一个成功结果，sub_results 保留所有子结果
        if self._aggregator is None:
            primary = sub_results[0]
            primary.sub_results = sub_results[1:]
            primary.provider = "multi"
            if errors:
                logger.warning(
                    "[MultiAnalyzer] 部分厂商失败（无聚合器，返回首个成功结果）: %s",
                    errors,
                )
            return primary

        # 有 aggregator：合并
        return self._aggregate(talker, talker_name, sub_results, errors)

    def _aggregate(
        self,
        talker: str,
        talker_name: str,
        sub_results: list[AnalysisResult],
        errors: list[str],
    ) -> AnalysisResult:
        """用 aggregator 厂商合并各子结果。"""
        sub_json = json.dumps(
            [self._result_to_dict(r) for r in sub_results],
            ensure_ascii=False,
        )
        prompt = AGGREGATE_PROMPT.format(n=len(sub_results), sub_results_json=sub_json)

        raw = ""
        try:
            raw = self._aggregator.chat(
                system="你是一位严谨的外贸业务分析助手，只输出 JSON，不要任何解释。",
                user=prompt,
                json_mode=True,
            )
            data = self._parse_json_response(raw)
        except Exception as e:
            logger.error(
                "[MultiAnalyzer] 聚合厂商 %s 合并失败: %s，回退为首个子结果",
                self._aggregator.name(), e,
            )
            primary = sub_results[0]
            primary.sub_results = sub_results[1:]
            primary.provider = "multi"
            return primary

        needs = []
        for n in data.get("needs", []):
            needs.append(CustomerNeed(
                category=n.get("category", "other"),
                summary=n.get("summary", ""),
                product=n.get("product", ""),
                quantity=n.get("quantity", ""),
                deadline=n.get("deadline", ""),
                urgency=n.get("urgency", "normal"),
            ))

        result = AnalysisResult(
            talker=talker,
            talker_name=talker_name,
            analyzed_at=datetime.now().isoformat(),
            language=data.get("language", ""),
            summary=data.get("summary", ""),
            needs=needs,
            done_items=data.get("done_items", []),
            todo_items=data.get("todo_items", []),
            customer_mood=data.get("customer_mood", ""),
            raw_text=sub_results[0].raw_text if sub_results else "",
            provider="multi",
            sub_results=sub_results,
        )
        if errors:
            logger.warning(
                "[MultiAnalyzer] 部分子厂商失败（已用聚合器合并成功结果）: %s", errors
            )
        return result

    @staticmethod
    def _result_to_dict(r: AnalysisResult) -> dict:
        """把 AnalysisResult 序列化为可喂给聚合 prompt 的 dict。"""
        return {
            "provider": r.provider or "",
            "language": r.language,
            "summary": r.summary,
            "needs": [
                {
                    "category": n.category,
                    "summary": n.summary,
                    "product": n.product,
                    "quantity": n.quantity,
                    "deadline": n.deadline,
                    "urgency": n.urgency,
                }
                for n in r.needs
            ],
            "done_items": r.done_items,
            "todo_items": r.todo_items,
            "customer_mood": r.customer_mood,
        }

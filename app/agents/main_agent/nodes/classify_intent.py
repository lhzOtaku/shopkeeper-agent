"""Strict three-way intent classifier for mainAgent."""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.llm import llm
from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.schemas import parse_intent_result
from app.agents.main_agent.state import Intent, MainAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.memory.session_memory import SessionMemory
from app.prompt.prompt_loader import load_prompt

UNSUPPORTED_CAPABILITY_MARKERS = (
    "报告",
    "日报",
    "周报",
    "月报",
    "为什么",
    "原因",
    "归因",
    "洞察",
    "建议",
)
QUERY_MARKERS = (
    "统计",
    "查询",
    "查一下",
    "多少",
    "排名",
    "top",
    "GMV",
    "销售额",
    "销量",
    "订单",
    "客单价",
    "退款率",
    "渠道",
    "地区",
    "品类",
    "会员",
)
DIRECT_QUERY_MARKERS = ("统计", "查询", "查一下", "帮我查", "请查")
FOLLOWUP_MARKERS = (
    "那",
    "这个",
    "它",
    "呢",
    "换成",
    "改成",
    "只看",
    "再看",
    "加上",
    "去掉",
    "不要",
    "继续",
    "按",
)
STRONG_FOLLOWUP_MARKERS = (
    "那",
    "这个",
    "它",
    "呢",
    "换成",
    "改成",
    "只看",
    "再看",
    "加上",
    "去掉",
    "不要",
    "继续",
)


def classify_text(message: str, memory: SessionMemory | None = None) -> Intent:
    """Deterministic fallback used only when LLM classification fails."""

    stripped = message.strip()
    lowered = stripped.lower()
    has_query_marker = any(marker.lower() in lowered for marker in QUERY_MARKERS)
    has_direct_marker = any(marker in stripped for marker in DIRECT_QUERY_MARKERS)
    if any(marker in stripped for marker in UNSUPPORTED_CAPABILITY_MARKERS):
        if not has_direct_marker:
            return "chat"
    if any(marker in stripped for marker in STRONG_FOLLOWUP_MARKERS):
        return "followup_query"
    if has_direct_marker:
        return "direct_query"
    if memory and memory.last_successful_query_context:
        if any(marker in stripped for marker in FOLLOWUP_MARKERS):
            return "followup_query"
        if len(stripped) <= 18 and has_query_marker:
            return "followup_query"
    if has_query_marker:
        return "direct_query"
    return "chat"


def classification_context(memory: SessionMemory) -> dict[str, str | bool]:
    """Return the minimal history required for direct-vs-follow-up routing."""

    context = memory.last_successful_query_context or {}
    return {
        "has_last_successful_query": bool(context),
        "last_resolved_query": context.get("resolved_query") or "无",
        "has_pending_query": bool(memory.pending_query),
    }


async def classify_intent(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    """Classify the current turn without rewriting or executing it."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "classify_intent", "status": "running"})
    message = state["message"]
    memory = runtime.context["memory"]

    try:
        prompt = PromptTemplate(
            template=load_prompt("main_agent/classify_intent"),
            input_variables=[
                "message",
                "has_last_successful_query",
                "last_resolved_query",
                "has_pending_query",
            ],
        )
        chain = prompt | llm | StrOutputParser()

        async def invoke_and_validate():
            raw = await chain.ainvoke(
                {"message": message, **classification_context(memory)}
            )
            return parse_intent_result(raw)

        result = await async_retry(
            "classify_intent.llm",
            invoke_and_validate,
            attempts=2,
            timeout_seconds=45,
        )
        intent = result.intent
        reason = result.reason
        source = "llm"
    except Exception as exc:
        intent = classify_text(message, memory)
        reason = "LLM 意图识别失败，已使用确定性规则兜底。"
        source = "fallback"
        logger.warning(
            "mainAgent intent classification failed; using fallback: "
            f"{exc}"
        )

    writer(
        {
            "type": "intent",
            "intent": intent,
            "reason": reason,
            "source": source,
        }
    )
    writer({"type": "progress", "step": "classify_intent", "status": "success"})
    return {"intent": intent, "intent_reason": reason}

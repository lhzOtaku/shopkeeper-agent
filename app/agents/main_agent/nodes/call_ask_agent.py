"""Node that executes one complete natural-language query through askAgent."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from app.agents.ask_agent.adapter import AskAgentAdapter
from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.state import MainAgentState
from app.core.log import logger

RETRYABLE_ERROR_MARKERS = (
    "Server disconnected",
    "All connection attempts failed",
    "Connection error",
    "ConnectError",
    "RemoteProtocolError",
    "TimeoutError",
    "timed out",
    "connection",
    "temporarily unavailable",
)
VALIDATION_ERROR_MARKERS = (
    "SQL 已校正",
    "SQL校正失败",
    "validation",
    "safety",
)


def effective_query_for_state(state: MainAgentState) -> tuple[str, str]:
    """Return the exact askAgent query and its source."""

    if state.get("effective_query"):
        return state["effective_query"], state.get("query_source") or "rewrite"
    if state.get("intent") == "direct_query":
        return state["message"], "raw"
    raise ValueError("No complete query is available for askAgent")


def classify_ask_failure(exc: Exception) -> tuple[str, bool, str]:
    """Map low-level failures to a stable type, retry flag, and safe message."""

    detail = str(exc)
    if any(marker.lower() in detail.lower() for marker in RETRYABLE_ERROR_MARKERS):
        return (
            "dependency",
            True,
            "askAgent 依赖的外部服务暂时不可用，可以稍后重试。",
        )
    if any(marker.lower() in detail.lower() for marker in VALIDATION_ERROR_MARKERS):
        return (
            "sql_validation",
            False,
            "SQL 未通过校验，请修改查询条件后重新提交。",
        )
    return "unknown", False, "本次查询执行失败，请修改问题后重新提交。"


def summarize_rows(rows: list[dict[str, Any]]) -> str:
    if rows:
        return f"查询完成，共 {len(rows)} 行结果。"
    return "查询完成，结果为空。"


async def call_ask_agent(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    """Run askAgent, normalize failures, and always continue to memory commit."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "call_ask_agent", "status": "running"})
    query, query_source = effective_query_for_state(state)
    writer({"type": "tool_start", "tool": "askAgent", "query": query})

    adapter = AskAgentAdapter(
        meta_mysql_repository=runtime.context["meta_mysql_repository"],
        embedding_client=runtime.context["embedding_client"],
        dw_mysql_repository=runtime.context["dw_mysql_repository"],
        column_qdrant_repository=runtime.context["column_qdrant_repository"],
        metric_qdrant_repository=runtime.context["metric_qdrant_repository"],
        value_es_repository=runtime.context["value_es_repository"],
    )

    ask_result: dict[str, Any] | None = None
    try:
        async for event in adapter.stream(query):
            writer(event)
            if event.get("type") == "tool_result":
                ask_result = event["data"]
        if not ask_result:
            raise RuntimeError("askAgent completed without a tool_result")

        if ask_result.get("success") and ask_result.get("query_spec"):
            content = summarize_rows(ask_result.get("rows") or [])
            progress_status = "success"
        else:
            content = ask_result.get("error_message") or "本次查询执行失败。"
            progress_status = "error"
    except Exception as exc:
        logger.warning(f"askAgent execution failed: {exc}")
        error_type, retryable, content = classify_ask_failure(exc)
        ask_result = {
            "success": False,
            "query": query,
            "sql": None,
            "rows": [],
            "query_spec": None,
            "error_type": error_type,
            "error_message": content,
            "retryable": retryable,
        }
        progress_status = "error"
        writer(
            {
                "type": "ask_failure",
                "error_type": error_type,
                "message": content,
                "retryable": retryable,
            }
        )

    writer(
        {"type": "progress", "step": "call_ask_agent", "status": progress_status}
    )
    writer({"type": "final", "content": content})
    return {
        "effective_query": query,
        "query_source": query_source,
        "ask_result": ask_result,
        "final_response": content,
    }

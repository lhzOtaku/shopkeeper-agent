"""Transactional session-memory commit after one mainAgent turn."""

from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.followup import ready_pending_from_failure
from app.agents.main_agent.state import MainAgentState
from app.core.log import logger
from app.memory.session_memory import SessionMemory


def apply_memory_update(state: MainAgentState, memory: SessionMemory) -> bool:
    """Apply one state transition and return whether success context changed."""

    message = state["message"]
    final_response = state.get("final_response") or ""
    memory.add_message("user", message)
    if final_response:
        memory.add_message("assistant", final_response)

    ask_result = state.get("ask_result")
    if ask_result is not None:
        query_spec = ask_result.get("query_spec")
        succeeded = bool(ask_result.get("success") and query_spec)
        if succeeded:
            resolved_query = ask_result.get("query") or state.get("effective_query")
            assert resolved_query
            raw_user_message = message
            if state.get("query_source") == "retry" and memory.pending_query:
                raw_user_message = (
                    memory.pending_query.get("original_user_message") or message
                )
            memory.remember_successful_query(
                raw_user_message=raw_user_message,
                resolved_query=resolved_query,
                query_spec=query_spec,
            )
            memory.clear_pending_query()
            return True

        rewrite_status = (state.get("rewrite_result") or {}).get("status")
        existing = None if rewrite_status == "independent" else memory.pending_query
        resolved_query = ask_result.get("query") or state.get("effective_query")
        if resolved_query:
            pending = ready_pending_from_failure(
                existing_pending=existing,
                original_user_message=message,
                resolved_query=resolved_query,
                origin_intent=state.get("intent") or "direct_query",
                query_source=state.get("query_source") or "raw",
                retryable=bool(ask_result.get("retryable")),
                error_message=ask_result.get("error_message"),
            )
            memory.set_pending_query(pending)
        return False

    action = state.get("pending_action") or {"action": "none"}
    action_name = action.get("action", "none")
    if action_name in {"set_clarifying", "set_ready"} and action.get("value"):
        memory.set_pending_query(action["value"])
    elif action_name == "clear":
        memory.clear_pending_query()
    return False


async def update_memory(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    """Commit messages, pending state, and successful query context exactly once."""

    memory = runtime.context["memory"]
    query_context_updated = apply_memory_update(state, memory)
    logger.info(
        "mainAgent memory committed: "
        f"messages={len(memory.messages)}, "
        f"query_context_updated={query_context_updated}, "
        f"pending_phase={(memory.pending_query or {}).get('phase')}"
    )
    runtime.stream_writer(
        {
            "type": "memory_update",
            "session_id": memory.session_id,
            "message_count": len(memory.messages),
            "query_context_updated": query_context_updated,
            "pending_phase": (memory.pending_query or {}).get("phase"),
        }
    )
    return {}

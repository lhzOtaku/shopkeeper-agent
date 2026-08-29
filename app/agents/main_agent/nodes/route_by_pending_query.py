"""Deterministic mainAgent entry routing for active query transactions."""

from langgraph.runtime import Runtime

from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.followup import decide_pending_entry
from app.agents.main_agent.state import MainAgentState


async def route_by_pending_query(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    """Prepare the graph entry route without calling an LLM."""

    pending = runtime.context["memory"].pending_query
    entry_route = decide_pending_entry(pending, state["message"])
    updates: dict[str, object] = {"entry_route": entry_route}
    if entry_route == "call_ask_agent" and pending:
        updates.update(
            {
                "intent": pending.get("origin_intent", "followup_query"),
                "effective_query": pending.get("resolved_query"),
                "query_source": "retry",
                "pending_action": {"action": "preserve"},
            }
        )
    return updates

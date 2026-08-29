"""mainAgent graph for chat, direct queries, and multi-turn follow-ups."""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.nodes.call_ask_agent import call_ask_agent
from app.agents.main_agent.nodes.classify_intent import classify_intent
from app.agents.main_agent.nodes.normal_chat import normal_chat
from app.agents.main_agent.nodes.rewrite_followup import rewrite_followup
from app.agents.main_agent.nodes.rewrite_responses import (
    respond_cancelled,
    respond_clarification,
    respond_clarification_exhausted,
    respond_retry_exhausted,
    respond_retry_unavailable,
    respond_rewrite_failed,
)
from app.agents.main_agent.nodes.route_by_pending_query import (
    route_by_pending_query,
)
from app.agents.main_agent.nodes.update_memory import update_memory
from app.agents.main_agent.state import MainAgentState


def route_after_entry(state: MainAgentState) -> str:
    return state.get("entry_route", "classify_intent")


def route_by_intent(state: MainAgentState) -> str:
    intent = state.get("intent", "chat")
    if intent == "direct_query":
        return "call_ask_agent"
    if intent == "followup_query":
        return "rewrite_followup"
    return "normal_chat"


def route_after_rewrite(state: MainAgentState) -> str:
    if state.get("rewrite_error"):
        return "respond_rewrite_failed"
    result = state.get("rewrite_result") or {}
    if result.get("clarification_exhausted"):
        return "respond_clarification_exhausted"
    status = result.get("status")
    if status in {"resolved", "independent"}:
        return "call_ask_agent"
    if status == "clarify":
        return "respond_clarification"
    if status == "cancelled":
        return "respond_cancelled"
    if status == "chat":
        return "normal_chat"
    return "respond_rewrite_failed"


graph_builder = StateGraph(
    state_schema=MainAgentState,
    context_schema=MainAgentContext,
)

graph_builder.add_node("route_by_pending_query", route_by_pending_query)
graph_builder.add_node("classify_intent", classify_intent)
graph_builder.add_node("rewrite_followup", rewrite_followup)
graph_builder.add_node("normal_chat", normal_chat)
graph_builder.add_node("call_ask_agent", call_ask_agent)
graph_builder.add_node("respond_clarification", respond_clarification)
graph_builder.add_node(
    "respond_clarification_exhausted", respond_clarification_exhausted
)
graph_builder.add_node("respond_cancelled", respond_cancelled)
graph_builder.add_node("respond_rewrite_failed", respond_rewrite_failed)
graph_builder.add_node("respond_retry_unavailable", respond_retry_unavailable)
graph_builder.add_node("respond_retry_exhausted", respond_retry_exhausted)
graph_builder.add_node("update_memory", update_memory)

graph_builder.add_edge(START, "route_by_pending_query")
graph_builder.add_conditional_edges(
    source="route_by_pending_query",
    path=route_after_entry,
    path_map={
        "classify_intent": "classify_intent",
        "rewrite_followup": "rewrite_followup",
        "call_ask_agent": "call_ask_agent",
        "respond_cancelled": "respond_cancelled",
        "respond_retry_unavailable": "respond_retry_unavailable",
        "respond_retry_exhausted": "respond_retry_exhausted",
    },
)
graph_builder.add_conditional_edges(
    source="classify_intent",
    path=route_by_intent,
    path_map={
        "normal_chat": "normal_chat",
        "call_ask_agent": "call_ask_agent",
        "rewrite_followup": "rewrite_followup",
    },
)
graph_builder.add_conditional_edges(
    source="rewrite_followup",
    path=route_after_rewrite,
    path_map={
        "call_ask_agent": "call_ask_agent",
        "respond_clarification": "respond_clarification",
        "respond_clarification_exhausted": "respond_clarification_exhausted",
        "respond_cancelled": "respond_cancelled",
        "respond_rewrite_failed": "respond_rewrite_failed",
        "normal_chat": "normal_chat",
    },
)

for terminal_node in (
    "normal_chat",
    "call_ask_agent",
    "respond_clarification",
    "respond_clarification_exhausted",
    "respond_cancelled",
    "respond_rewrite_failed",
    "respond_retry_unavailable",
    "respond_retry_exhausted",
):
    graph_builder.add_edge(terminal_node, "update_memory")

graph_builder.add_edge("update_memory", END)

graph = graph_builder.compile()

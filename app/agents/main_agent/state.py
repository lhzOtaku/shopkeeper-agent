"""State for the chat and multi-turn data-query mainAgent."""

from typing import Any, Literal, TypedDict

Intent = Literal["chat", "direct_query", "followup_query"]
QuerySource = Literal["raw", "rewrite", "retry"]
EntryRoute = Literal[
    "classify_intent",
    "rewrite_followup",
    "call_ask_agent",
    "respond_cancelled",
    "respond_retry_unavailable",
    "respond_retry_exhausted",
]
PendingActionName = Literal[
    "none",
    "preserve",
    "set_clarifying",
    "set_ready",
    "clear",
]


class PendingAction(TypedDict, total=False):
    """A deferred pending-query mutation committed by update_memory."""

    action: PendingActionName
    value: dict[str, Any] | None


class MainAgentState(TypedDict, total=False):
    """One chat turn handled by mainAgent."""

    session_id: str
    message: str

    entry_route: EntryRoute
    intent: Intent
    intent_reason: str

    rewrite_result: dict[str, Any] | None
    rewrite_error: str | None

    effective_query: str | None
    query_source: QuerySource | None

    pending_action: PendingAction | None
    ask_result: dict[str, Any] | None
    final_response: str

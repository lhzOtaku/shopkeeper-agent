"""Process-local session memory for chat and multi-turn data queries."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ChatRole = Literal["user", "assistant"]
PendingPhase = Literal["clarifying", "ready_to_execute"]


@dataclass
class MemoryMessage:
    """A compact chat message stored for a single browser session."""

    role: ChatRole
    content: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


def compact_successful_query_context(
    *,
    raw_user_message: str,
    resolved_query: str,
    query_spec: dict[str, Any],
) -> dict[str, Any]:
    """Build the compact successful-query evidence exposed to the rewriter."""

    return {
        "raw_user_message": raw_user_message,
        "resolved_query": resolved_query,
        "metrics": [
            {
                "name": item.get("name"),
                "description": item.get("description"),
            }
            for item in query_spec.get("metrics") or []
            if item.get("name")
        ],
        "tables": [
            {
                "name": item.get("name"),
                "description": item.get("description"),
            }
            for item in query_spec.get("tables") or []
            if item.get("name")
        ],
        "filters": [
            {
                "field": item.get("field") or item.get("column"),
                "operator": item.get("operator"),
                "value": item.get("value"),
            }
            for item in query_spec.get("filters") or []
            if item.get("value") is not None
        ],
        "dimensions": list(query_spec.get("dimensions") or []),
        "time_range": deepcopy(query_spec.get("time_range")),
        "sql": query_spec.get("sql"),
    }


@dataclass
class SessionMemory:
    """Short-lived memory shared by all turns in one chat session."""

    session_id: str
    messages: list[MemoryMessage] = field(default_factory=list)
    last_successful_query_context: dict[str, Any] | None = None
    pending_query: dict[str, Any] | None = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_message(self, role: ChatRole, content: str) -> None:
        """Append one chat message and refresh the memory timestamp."""

        self.messages.append(MemoryMessage(role=role, content=content))
        self.updated_at = datetime.now().isoformat()

    def remember_successful_query(
        self,
        *,
        raw_user_message: str,
        resolved_query: str,
        query_spec: dict[str, Any],
    ) -> None:
        """Atomically replace the latest successfully executed query context."""

        self.last_successful_query_context = compact_successful_query_context(
            raw_user_message=raw_user_message,
            resolved_query=resolved_query,
            query_spec=query_spec,
        )
        self.updated_at = datetime.now().isoformat()

    def remember_query(
        self,
        query: str,
        sql: str | None,
        rows: list[dict[str, Any]] | None,
        query_spec: dict[str, Any] | None = None,
    ) -> None:
        """Compatibility wrapper for older scripts; rows are intentionally ignored."""

        del rows
        if not query_spec:
            return
        normalized_spec = deepcopy(query_spec)
        if sql and not normalized_spec.get("sql"):
            normalized_spec["sql"] = sql
        self.remember_successful_query(
            raw_user_message=query,
            resolved_query=query,
            query_spec=normalized_spec,
        )

    def set_pending_query(self, value: dict[str, Any]) -> None:
        """Replace the active pending query transaction."""

        self.pending_query = deepcopy(value)
        self.updated_at = datetime.now().isoformat()

    def clear_pending_query(self) -> None:
        """Clear the active pending query transaction."""

        self.pending_query = None
        self.updated_at = datetime.now().isoformat()

    @property
    def last_query(self) -> str | None:
        """Compatibility view of the latest resolved query."""

        if not self.last_successful_query_context:
            return None
        return self.last_successful_query_context.get("resolved_query")

    @property
    def last_sql(self) -> str | None:
        """Compatibility view of the latest successful SQL."""

        if not self.last_successful_query_context:
            return None
        return self.last_successful_query_context.get("sql")

    @property
    def last_query_spec(self) -> dict[str, Any] | None:
        """Compatibility view used by older evaluation code."""

        return self.last_successful_query_context

    @property
    def last_metrics(self) -> list[str]:
        context = self.last_successful_query_context or {}
        return [
            item.get("name")
            for item in context.get("metrics") or []
            if item.get("name")
        ]

    @property
    def last_dimensions(self) -> list[str]:
        context = self.last_successful_query_context or {}
        return list(context.get("dimensions") or [])

    @property
    def last_filters(self) -> dict[str, Any]:
        context = self.last_successful_query_context or {}
        return {
            item.get("field"): item.get("value")
            for item in context.get("filters") or []
            if item.get("field") and item.get("value") is not None
        }

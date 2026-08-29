"""Deterministic lifecycle helpers for multi-turn query rewriting."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.agents.main_agent.schemas import RewriteResult

MAX_CLARIFICATION_COUNT = 2
MAX_EXECUTION_RETRY_COUNT = 2

RETRY_COMMANDS = {"重试", "再试一次", "重新执行", "再执行一次"}
CANCEL_COMMANDS = {"取消", "算了", "不查了", "停止查询"}


def normalized_command(message: str) -> str:
    """Normalize a short lifecycle command without changing business language."""

    return message.strip().rstrip("。！!？?").strip()


def is_retry_command(message: str) -> bool:
    return normalized_command(message) in RETRY_COMMANDS


def is_cancel_command(message: str) -> bool:
    return normalized_command(message) in CANCEL_COMMANDS


def decide_pending_entry(
    pending_query: dict[str, Any] | None,
    message: str,
) -> str:
    """Choose the entry route before ordinary intent classification."""

    if not pending_query:
        return "classify_intent"
    if pending_query.get("phase") == "clarifying":
        return "rewrite_followup"
    if pending_query.get("phase") != "ready_to_execute":
        return "rewrite_followup"
    if is_cancel_command(message):
        return "respond_cancelled"
    if is_retry_command(message):
        if not pending_query.get("retryable", False):
            return "respond_retry_unavailable"
        if (
            int(pending_query.get("execution_retry_count", 0))
            >= MAX_EXECUTION_RETRY_COUNT
        ):
            return "respond_retry_exhausted"
        return "call_ask_agent"
    return "rewrite_followup"


def pending_for_rewrite_prompt(
    pending_query: dict[str, Any] | None,
    current_message: str,
) -> dict[str, Any] | None:
    """Return a working pending copy with the latest clarification answer filled."""

    if not pending_query:
        return None
    working = deepcopy(pending_query)
    if working.get("phase") != "clarifying":
        return working

    history = working.get("clarification_history") or []
    for item in reversed(history):
        if item.get("answer") is None:
            item["answer"] = current_message
            break
    return working


def build_clarifying_pending(
    *,
    result: RewriteResult,
    current_message: str,
    existing_pending: dict[str, Any] | None,
    working_pending: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build the next pending clarification or return None when exhausted."""

    if result.status != "clarify" or not result.clarification_question:
        raise ValueError("A clarify result with a question is required")

    if existing_pending and working_pending:
        pending = deepcopy(working_pending)
        next_count = int(pending.get("clarification_count", 0)) + 1
    else:
        pending = {
            "phase": "clarifying",
            "origin_intent": "followup_query",
            "original_user_message": current_message,
            "clarification_count": 0,
            "clarification_history": [],
            "candidate_resolved_query": None,
            "resolved_query": None,
            "execution_retry_count": 0,
            "retryable": False,
            "last_error": None,
            "created_at": datetime.now().isoformat(),
        }
        next_count = 1

    if next_count > MAX_CLARIFICATION_COUNT:
        return None

    pending["phase"] = "clarifying"
    pending["clarification_count"] = next_count
    pending.setdefault("clarification_history", []).append(
        {
            "round": next_count,
            "question": result.clarification_question,
            "answer": None,
        }
    )
    pending["candidate_resolved_query"] = result.candidate_resolved_query
    pending["resolved_query"] = None
    pending["retryable"] = False
    pending["last_error"] = None
    return pending


def ready_pending_from_failure(
    *,
    existing_pending: dict[str, Any] | None,
    original_user_message: str,
    resolved_query: str,
    origin_intent: str,
    query_source: str,
    retryable: bool,
    error_message: str | None,
) -> dict[str, Any]:
    """Create a retryable transaction from a failed askAgent execution."""

    if existing_pending:
        pending = deepcopy(existing_pending)
    else:
        pending = {
            "clarification_count": 0,
            "clarification_history": [],
            "candidate_resolved_query": None,
            "created_at": datetime.now().isoformat(),
        }

    retry_count = int(pending.get("execution_retry_count", 0))
    if query_source == "retry":
        retry_count += 1
    elif pending.get("resolved_query") != resolved_query:
        retry_count = 0

    pending.update(
        {
            "phase": "ready_to_execute",
            "origin_intent": origin_intent,
            "original_user_message": pending.get("original_user_message")
            or original_user_message,
            "resolved_query": resolved_query,
            "execution_retry_count": retry_count,
            "retryable": retryable,
            "last_error": error_message,
        }
    )
    return pending

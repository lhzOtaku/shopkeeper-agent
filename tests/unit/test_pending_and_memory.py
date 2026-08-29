from copy import deepcopy

import pytest

from app.agents.main_agent.followup import (
    MAX_CLARIFICATION_COUNT,
    MAX_EXECUTION_RETRY_COUNT,
    build_clarifying_pending,
    decide_pending_entry,
    pending_for_rewrite_prompt,
    ready_pending_from_failure,
)
from app.agents.main_agent.nodes.update_memory import apply_memory_update
from app.agents.main_agent.schemas import RewriteResult
from app.memory.memory_store import MemoryStore
from app.memory.session_memory import SessionMemory

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("pending_kind", "message", "expected"),
    [
        ("none", "统计订单数", "classify_intent"),
        ("clarifying", "订单数", "rewrite_followup"),
        ("ready", "重试", "call_ask_agent"),
        ("ready", "取消", "respond_cancelled"),
        ("ready", "改成销量", "rewrite_followup"),
    ],
)
def test_pending_query_has_priority_over_ordinary_intent_routing(
    pending_kind, message, expected, clarifying_pending, ready_pending
):
    pending = {
        "none": None,
        "clarifying": clarifying_pending,
        "ready": ready_pending,
    }[pending_kind]
    assert decide_pending_entry(pending, message) == expected


def test_retry_is_rejected_when_error_is_not_retryable(ready_pending):
    ready_pending["retryable"] = False
    assert decide_pending_entry(ready_pending, "重试") == "respond_retry_unavailable"


def test_retry_limit_is_exactly_two(ready_pending):
    ready_pending["execution_retry_count"] = MAX_EXECUTION_RETRY_COUNT
    assert decide_pending_entry(ready_pending, "再试一次") == "respond_retry_exhausted"


def test_pending_prompt_uses_a_copy_and_fills_latest_answer(clarifying_pending):
    working = pending_for_rewrite_prompt(clarifying_pending, "追加订单数")
    assert clarifying_pending["clarification_history"][-1]["answer"] is None
    assert working["clarification_history"][-1]["answer"] == "追加订单数"


def test_clarification_is_created_then_exhausted_after_two_rounds(clarifying_pending):
    first = build_clarifying_pending(
        result=RewriteResult(
            status="clarify",
            clarification_question="哪个指标？",
            reason="ambiguous",
        ),
        current_message="那这个呢",
        existing_pending=None,
        working_pending=None,
    )
    assert first["clarification_count"] == 1
    working = pending_for_rewrite_prompt(first, "订单数")
    second = build_clarifying_pending(
        result=RewriteResult(
            status="clarify",
            clarification_question="追加还是替换？",
            reason="still ambiguous",
        ),
        current_message="订单数",
        existing_pending=first,
        working_pending=working,
    )
    assert second["clarification_count"] == MAX_CLARIFICATION_COUNT
    exhausted = build_clarifying_pending(
        result=RewriteResult(
            status="clarify",
            clarification_question="仍需确认？",
            reason="still ambiguous",
        ),
        current_message="不确定",
        existing_pending=second,
        working_pending=pending_for_rewrite_prompt(second, "不确定"),
    )
    assert exhausted is None


def test_retry_failure_increments_only_when_query_source_is_retry(ready_pending):
    retried = ready_pending_from_failure(
        existing_pending=ready_pending,
        original_user_message="重试",
        resolved_query=ready_pending["resolved_query"],
        origin_intent="followup_query",
        query_source="retry",
        retryable=True,
        error_message="still unavailable",
    )
    assert retried["execution_retry_count"] == 1
    first_failure = ready_pending_from_failure(
        existing_pending=None,
        original_user_message="统计订单数",
        resolved_query="统计订单数",
        origin_intent="direct_query",
        query_source="raw",
        retryable=True,
        error_message="unavailable",
    )
    assert first_failure["execution_retry_count"] == 0


def test_success_memory_is_compact_and_does_not_store_result_rows(successful_memory):
    context = successful_memory.last_successful_query_context
    assert context["metrics"] == [
        {"name": "GMV", "description": "有效订单成交金额"}
    ]
    assert "formula" not in context["metrics"][0]
    assert "result_summary" not in context
    assert "rows" not in context


def test_success_clears_pending_and_empty_rows_are_still_success(
    successful_memory, ready_pending, base_query_spec
):
    successful_memory.set_pending_query(ready_pending)
    changed = apply_memory_update(
        {
            "message": "重试",
            "intent": "followup_query",
            "effective_query": ready_pending["resolved_query"],
            "query_source": "retry",
            "ask_result": {
                "success": True,
                "query": ready_pending["resolved_query"],
                "rows": [],
                "query_spec": base_query_spec,
            },
            "final_response": "查询完成，结果为空。",
        },
        successful_memory,
    )
    assert changed is True
    assert successful_memory.pending_query is None
    assert successful_memory.last_successful_query_context["raw_user_message"] == "那直播渠道呢"


def test_failure_preserves_last_success_and_creates_retry_transaction(
    successful_memory,
):
    before = deepcopy(successful_memory.last_successful_query_context)
    changed = apply_memory_update(
        {
            "message": "那直播渠道呢",
            "intent": "followup_query",
            "effective_query": "统计直播渠道女装GMV",
            "query_source": "rewrite",
            "ask_result": {
                "success": False,
                "query": "统计直播渠道女装GMV",
                "query_spec": None,
                "retryable": True,
                "error_message": "connection failed",
            },
            "final_response": "可重试",
        },
        successful_memory,
    )
    assert changed is False
    assert successful_memory.last_successful_query_context == before
    assert successful_memory.pending_query["phase"] == "ready_to_execute"


@pytest.mark.parametrize("action", ["set_clarifying", "set_ready"])
def test_pending_actions_are_committed_atomically(action, clarifying_pending):
    memory = SessionMemory("s")
    apply_memory_update(
        {
            "message": "追问",
            "pending_action": {"action": action, "value": clarifying_pending},
        },
        memory,
    )
    assert memory.pending_query == clarifying_pending


def test_failure_does_not_share_mutable_state_between_sessions(base_query_spec):
    store = MemoryStore()
    first = store.get_or_create("first")
    second = store.get_or_create("second")
    first.remember_successful_query(
        raw_user_message="q", resolved_query="q", query_spec=base_query_spec
    )
    assert first is not second
    assert second.last_successful_query_context is None
    first.set_pending_query({"phase": "clarifying"})
    assert second.pending_query is None

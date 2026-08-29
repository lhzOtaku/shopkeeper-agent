from copy import deepcopy

import pytest

from app.agents.main_agent.followup import decide_pending_entry
from app.agents.main_agent.nodes.classify_intent import classify_text
from app.agents.main_agent.nodes.update_memory import apply_memory_update
from app.memory.session_memory import SessionMemory

pytestmark = [pytest.mark.unit, pytest.mark.multiturn]


def success_state(message, query, spec, *, source="rewrite", rows=None):
    return {
        "message": message,
        "intent": "followup_query",
        "effective_query": query,
        "query_source": source,
        "ask_result": {
            "success": True,
            "query": query,
            "rows": [] if rows is None else rows,
            "query_spec": spec,
        },
        "final_response": "查询完成。",
    }


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("统计2025年直播渠道订单数", "direct_query"),
        ("那直播渠道呢", "followup_query"),
        ("改成直播渠道", "followup_query"),
        ("加上订单数", "followup_query"),
        ("去掉地区条件", "followup_query"),
        ("这是独立问题：统计2025年销量", "direct_query"),
        ("生成月报", "chat"),
        ("为什么GMV下降", "chat"),
    ],
)
def test_new_query_followups_and_non_query_activities_are_separated(
    message, expected, successful_memory
):
    assert classify_text(message, successful_memory) == expected


def test_replace_filter_commits_only_after_success(successful_memory, base_query_spec):
    spec = deepcopy(base_query_spec)
    spec["filters"] = [
        {"field": "dim_channel.channel_name", "operator": "=", "value": "直播"}
    ]
    apply_memory_update(
        success_state("改成直播渠道", "统计2025年直播渠道GMV", spec),
        successful_memory,
    )
    assert successful_memory.last_filters == {"dim_channel.channel_name": "直播"}


def test_add_metric_updates_compact_success_context(successful_memory, base_query_spec):
    spec = deepcopy(base_query_spec)
    spec["metrics"].append({"name": "订单数", "description": "有效订单数"})
    apply_memory_update(
        success_state("加上订单数", "统计2025年女装GMV和订单数", spec),
        successful_memory,
    )
    assert successful_memory.last_metrics == ["GMV", "订单数"]


def test_remove_filter_updates_context_only_on_success(successful_memory, base_query_spec):
    spec = deepcopy(base_query_spec)
    spec["filters"] = []
    apply_memory_update(
        success_state("去掉品类条件", "统计2025年GMV", spec), successful_memory
    )
    assert successful_memory.last_filters == {}


def test_independent_failure_replaces_old_pending_transaction(
    successful_memory, clarifying_pending
):
    successful_memory.set_pending_query(clarifying_pending)
    apply_memory_update(
        {
            "message": "统计2025年销量，这是独立问题",
            "intent": "followup_query",
            "rewrite_result": {"status": "independent"},
            "effective_query": "统计2025年销量",
            "query_source": "rewrite",
            "ask_result": {
                "success": False,
                "query": "统计2025年销量",
                "query_spec": None,
                "retryable": True,
                "error_message": "unavailable",
            },
        },
        successful_memory,
    )
    assert successful_memory.pending_query["original_user_message"] == "统计2025年销量，这是独立问题"
    assert successful_memory.pending_query["clarification_history"] == []


def test_cancel_during_clarification_clears_pending(
    successful_memory, clarifying_pending
):
    successful_memory.set_pending_query(clarifying_pending)
    apply_memory_update(
        {
            "message": "取消",
            "final_response": "已取消",
            "pending_action": {"action": "clear"},
        },
        successful_memory,
    )
    assert successful_memory.pending_query is None


def test_chat_during_clarification_preserves_pending(
    successful_memory, clarifying_pending
):
    successful_memory.set_pending_query(clarifying_pending)
    apply_memory_update(
        {
            "message": "你好",
            "final_response": "你好",
            "pending_action": {"action": "preserve"},
        },
        successful_memory,
    )
    assert successful_memory.pending_query == clarifying_pending


def test_exhaustion_clear_requires_new_complete_question(
    successful_memory, clarifying_pending
):
    successful_memory.set_pending_query(clarifying_pending)
    apply_memory_update(
        {
            "message": "还是那个",
            "pending_action": {"action": "clear"},
            "final_response": "请重新描述完整问数",
        },
        successful_memory,
    )
    assert successful_memory.pending_query is None
    assert decide_pending_entry(None, "统计2025年订单数") == "classify_intent"


def test_retry_reexecutes_complete_query_not_previous_sql(ready_pending):
    assert decide_pending_entry(ready_pending, "重试") == "call_ask_agent"
    assert ready_pending["resolved_query"] == "统计2025年直播渠道女装GMV"
    assert "sql" not in ready_pending


def test_retry_success_uses_original_business_message(
    successful_memory, ready_pending, base_query_spec
):
    successful_memory.set_pending_query(ready_pending)
    apply_memory_update(
        success_state(
            "重试",
            ready_pending["resolved_query"],
            base_query_spec,
            source="retry",
        ),
        successful_memory,
    )
    assert successful_memory.last_successful_query_context["raw_user_message"] == "那直播渠道呢"


def test_unknown_auxiliary_event_cannot_mark_request_failed(
    successful_memory, base_query_spec
):
    state = success_state("统计订单数", "统计订单数", base_query_spec, source="raw")
    state["auxiliary_events"] = [{"type": "unknown", "error": "ignored"}]
    assert apply_memory_update(state, successful_memory) is True


def test_null_aggregate_is_not_empty_and_not_execution_failure(
    successful_memory, base_query_spec
):
    rows = [{"gmv": None}]
    assert apply_memory_update(
        success_state("未来GMV", "未来GMV", base_query_spec, rows=rows),
        successful_memory,
    ) is True


def test_different_sessions_remain_isolated_after_multiple_turns(base_query_spec):
    first = SessionMemory("first")
    second = SessionMemory("second")
    apply_memory_update(
        success_state("统计GMV", "统计GMV", base_query_spec, source="raw"), first
    )
    assert first.last_successful_query_context is not None
    assert second.last_successful_query_context is None
    assert second.messages == []

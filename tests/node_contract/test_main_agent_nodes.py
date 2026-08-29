from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.agents.main_agent.nodes.call_ask_agent import (
    call_ask_agent,
    classify_ask_failure,
    effective_query_for_state,
    summarize_rows,
)
from app.agents.main_agent.nodes.classify_intent import classify_intent
from app.agents.main_agent.nodes.rewrite_followup import rewrite_followup
from app.agents.main_agent.nodes.rewrite_responses import (
    respond_cancelled,
    respond_clarification,
    respond_clarification_exhausted,
    respond_retry_exhausted,
)
from app.agents.main_agent.nodes.route_by_pending_query import route_by_pending_query
from app.agents.main_agent.schemas import IntentResult, RewriteResult
from app.memory.session_memory import SessionMemory

pytestmark = [pytest.mark.unit, pytest.mark.node]


async def test_route_by_pending_query_is_repository_and_llm_free(
    event_runtime, ready_pending
):
    memory = SessionMemory("s")
    memory.set_pending_query(ready_pending)
    runtime, _ = event_runtime(memory=memory)
    result = await route_by_pending_query({"message": "重试"}, runtime)
    assert result == {
        "entry_route": "call_ask_agent",
        "intent": "followup_query",
        "effective_query": ready_pending["resolved_query"],
        "query_source": "retry",
        "pending_action": {"action": "preserve"},
    }


async def test_classify_intent_uses_validated_llm_result(monkeypatch, event_runtime):
    memory = SessionMemory("s")
    runtime, events = event_runtime(memory=memory)
    retry = AsyncMock(
        return_value=IntentResult(intent="direct_query", reason="完整问数")
    )
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.classify_intent.async_retry", retry
    )
    result = await classify_intent({"message": "统计订单数"}, runtime)
    assert result == {"intent": "direct_query", "intent_reason": "完整问数"}
    assert any(event.get("source") == "llm" for event in events)


async def test_classify_intent_falls_back_when_llm_times_out(
    monkeypatch, event_runtime
):
    runtime, events = event_runtime(memory=SessionMemory("s"))
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.classify_intent.async_retry",
        AsyncMock(side_effect=TimeoutError("LLM timeout")),
    )
    result = await classify_intent({"message": "统计2025年订单数"}, runtime)
    assert result["intent"] == "direct_query"
    assert any(event.get("source") == "fallback" for event in events)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            {"message": "统计订单数", "intent": "direct_query"},
            ("统计订单数", "raw"),
        ),
        (
            {
                "message": "那直播呢",
                "intent": "followup_query",
                "effective_query": "统计直播渠道订单数",
                "query_source": "rewrite",
            },
            ("统计直播渠道订单数", "rewrite"),
        ),
    ],
)
def test_effective_query_contract(state, expected):
    assert effective_query_for_state(state) == expected


def test_incomplete_followup_never_executes_ask_agent():
    with pytest.raises(ValueError, match="No complete query"):
        effective_query_for_state({"message": "那这个呢", "intent": "followup_query"})


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (TimeoutError("timed out"), "dependency", True),
        (RuntimeError("SQL 已校正 3 次"), "sql_validation", False),
        (RuntimeError("unexpected"), "unknown", False),
    ],
)
def test_ask_failure_retryability_is_explicit(error, kind, retryable):
    actual_kind, actual_retryable, safe_message = classify_ask_failure(error)
    assert (actual_kind, actual_retryable) == (kind, retryable)
    assert str(error) not in safe_message


def test_row_summary_distinguishes_empty_success():
    assert summarize_rows([]) == "查询完成，结果为空。"
    assert summarize_rows([{"value": 1}]) == "查询完成，共 1 行结果。"


async def test_call_ask_agent_success_uses_adapter_and_returns_partial_state(
    monkeypatch, event_runtime, base_query_spec
):
    async def fake_stream(_self, query):
        yield {"type": "tool_progress", "step": "run_sql", "status": "success"}
        yield {
            "type": "tool_result",
            "data": {
                "success": True,
                "query": query,
                "sql": "SELECT 1",
                "rows": [],
                "query_spec": base_query_spec,
                "retryable": False,
            },
        }

    monkeypatch.setattr(
        "app.agents.main_agent.nodes.call_ask_agent.AskAgentAdapter.stream",
        fake_stream,
    )
    runtime, events = event_runtime(
        meta_mysql_repository=None,
        embedding_client=None,
        dw_mysql_repository=None,
        column_qdrant_repository=None,
        metric_qdrant_repository=None,
        value_es_repository=None,
    )
    result = await call_ask_agent(
        {"message": "统计订单数", "intent": "direct_query"}, runtime
    )
    assert result["ask_result"]["success"] is True
    assert result["query_source"] == "raw"
    assert result["final_response"] == "查询完成，结果为空。"
    assert any(event.get("type") == "final" for event in events)


async def test_call_ask_agent_normalizes_dependency_exception(
    monkeypatch, event_runtime
):
    async def failing_stream(_self, _query):
        raise TimeoutError("timed out")
        yield

    monkeypatch.setattr(
        "app.agents.main_agent.nodes.call_ask_agent.AskAgentAdapter.stream",
        failing_stream,
    )
    runtime, events = event_runtime(
        meta_mysql_repository=None,
        embedding_client=None,
        dw_mysql_repository=None,
        column_qdrant_repository=None,
        metric_qdrant_repository=None,
        value_es_repository=None,
    )
    result = await call_ask_agent(
        {"message": "统计订单数", "intent": "direct_query"}, runtime
    )
    assert result["ask_result"]["success"] is False
    assert result["ask_result"]["retryable"] is True
    assert any(event.get("type") == "ask_failure" for event in events)


@pytest.mark.parametrize(
    ("status", "expected_action"),
    [
        ("resolved", "preserve"),
        ("independent", "clear"),
        ("cancelled", "clear"),
        ("chat", "preserve"),
    ],
)
async def test_rewrite_followup_maps_status_to_state_update(
    status, expected_action, monkeypatch, event_runtime, successful_memory
):
    kwargs = {"status": status, "reason": "mapped"}
    if status in {"resolved", "independent"}:
        kwargs["resolved_query"] = "统计2025年直播渠道订单数"
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.rewrite_followup._invoke_rewriter",
        AsyncMock(return_value="unused"),
    )
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.rewrite_followup.parse_rewrite_result",
        lambda _raw: RewriteResult(**kwargs),
    )
    runtime, _ = event_runtime(memory=successful_memory)
    result = await rewrite_followup({"message": "那直播呢"}, runtime)
    assert result["rewrite_result"]["status"] == status
    assert result["pending_action"]["action"] == expected_action


async def test_invalid_rewrite_is_repaired_once(
    monkeypatch, event_runtime, successful_memory
):
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.rewrite_followup._invoke_rewriter",
        AsyncMock(return_value="not json"),
    )
    repair = AsyncMock(
        return_value=RewriteResult(
            status="resolved",
            resolved_query="统计2025年直播渠道GMV",
            reason="repaired",
        )
    )
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.rewrite_followup._repair_rewrite_output",
        repair,
    )
    runtime, _ = event_runtime(memory=successful_memory)
    result = await rewrite_followup({"message": "那直播呢"}, runtime)
    repair.assert_awaited_once()
    assert result["rewrite_error"] is None


async def test_repair_failure_preserves_pending(
    monkeypatch, event_runtime, successful_memory, clarifying_pending
):
    successful_memory.set_pending_query(clarifying_pending)
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.rewrite_followup._invoke_rewriter",
        AsyncMock(return_value="bad"),
    )
    monkeypatch.setattr(
        "app.agents.main_agent.nodes.rewrite_followup._repair_rewrite_output",
        AsyncMock(side_effect=ValueError("still bad")),
    )
    runtime, events = event_runtime(memory=successful_memory)
    result = await rewrite_followup({"message": "订单数"}, runtime)
    assert result["pending_action"] == {"action": "preserve"}
    assert result["rewrite_error"]
    assert any(event.get("status") == "error" for event in events)


@pytest.mark.parametrize(
    ("node", "expected_action"),
    [
        (respond_cancelled, "clear"),
        (respond_clarification_exhausted, "clear"),
        (respond_retry_exhausted, "clear"),
    ],
)
async def test_terminal_lifecycle_responses_clear_pending(
    node, expected_action, event_runtime
):
    runtime, events = event_runtime()
    result = await node({}, runtime)
    assert result["pending_action"]["action"] == expected_action
    assert any(event.get("type") == "final" for event in events)


async def test_clarification_response_preserves_pending_payload(
    event_runtime, clarifying_pending
):
    runtime, events = event_runtime()
    state = {
        "rewrite_result": {"clarification_question": "追加还是替换？"},
        "pending_action": {"action": "set_clarifying", "value": deepcopy(clarifying_pending)},
    }
    result = await respond_clarification(state, runtime)
    assert result["final_response"] == "追加还是替换？"
    assert any(event.get("type") == "clarification" for event in events)

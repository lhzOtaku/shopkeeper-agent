from unittest.mock import AsyncMock

import pytest

from app.agents.ask_agent.adapter import AskAgentAdapter
from app.agents.ask_agent.nodes.correct_sql import correct_sql
from app.agents.ask_agent.nodes.fail_sql_correction import fail_sql_correction
from app.agents.ask_agent.nodes.generate_sql import generate_sql

pytestmark = [pytest.mark.unit, pytest.mark.node]


async def test_generate_sql_initializes_correction_and_safety_state(
    monkeypatch, event_runtime
):
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.generate_sql.async_retry",
        AsyncMock(return_value="SELECT COUNT(*) FROM fact_order"),
    )
    runtime, events = event_runtime()
    result = await generate_sql(
        {
            "query": "统计订单数",
            "table_infos": [],
            "generation_context": {
                "date_info": {"date": "2026-07-22"},
                "db_info": {"dialect": "mysql", "version": "8.0"},
            },
        },
        runtime,
    )
    assert result["sql"] == result["original_sql"]
    assert result["correction_count"] == 0
    assert result["sql_validation_records"] == []
    assert result["sql_safety_blocked"] is False
    assert any(event.get("type") == "sql" for event in events)


async def test_generate_sql_llm_timeout_propagates_and_emits_error_progress(
    monkeypatch, event_runtime
):
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.generate_sql.async_retry",
        AsyncMock(side_effect=TimeoutError("LLM timeout")),
    )
    runtime, events = event_runtime()
    with pytest.raises(TimeoutError):
        await generate_sql(
            {
                "query": "q",
                "table_infos": [],
                "generation_context": {},
            },
            runtime,
        )
    assert events[-1]["status"] == "error"


async def test_correct_sql_increments_count_and_keeps_audit_record(
    monkeypatch, event_runtime
):
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.correct_sql.async_retry",
        AsyncMock(return_value="SELECT order_id FROM fact_order"),
    )
    runtime, _ = event_runtime()
    result = await correct_sql(
        {
            "query": "订单",
            "table_infos": [],
            "generation_context": {},
            "sql": "SELECT missing FROM fact_order",
            "original_sql": "SELECT missing FROM fact_order",
            "error": "Unknown column",
            "correction_count": 1,
        },
        runtime,
    )
    assert result["correction_count"] == 2
    assert result["sql_correction_records"][-1]["before_sql"].startswith("SELECT missing")
    assert result["sql_correction_records"][-1]["corrected_sql"].startswith("SELECT order_id")


async def test_correction_exhaustion_raises_locatable_failure(event_runtime):
    runtime, events = event_runtime()
    with pytest.raises(RuntimeError, match="SQL 已校正 3 次"):
        await fail_sql_correction(
            {"correction_count": 3, "error": "Unknown column"}, runtime
        )
    assert events[-1]["status"] == "error"


async def test_adapter_normalizes_graph_events_and_ignores_unknown_auxiliary_event(
    monkeypatch
):
    async def fake_astream(**_kwargs):
        yield {"type": "progress", "step": "recall", "status": "success"}
        yield {"type": "unknown_auxiliary", "data": "ignored"}
        yield {"type": "sql", "sql": "SELECT 1"}
        yield {"type": "result", "data": []}
        yield {"type": "query_spec", "data": {"sql": "SELECT 1"}}

    monkeypatch.setattr(
        "app.agents.ask_agent.adapter.data_query_graph.astream", fake_astream
    )
    adapter = AskAgentAdapter(None, None, None, None, None, None)
    events = [event async for event in adapter.stream("q")]
    assert [event["type"] for event in events] == [
        "tool_progress",
        "tool_sql",
        "tool_result",
    ]
    assert events[-1]["data"]["success"] is True
    assert events[-1]["data"]["rows"] == []


@pytest.mark.parametrize(
    "dependency_key",
    [
        "column_qdrant_repository",
        "metric_qdrant_repository",
        "value_es_repository",
        "meta_mysql_repository",
        "dw_mysql_repository",
    ],
)
def test_runtime_dependencies_are_repositories_not_llm_tools(dependency_key):
    assert dependency_key.endswith("_repository")
    assert "tool" not in dependency_key

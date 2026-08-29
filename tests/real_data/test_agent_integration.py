import json
import os
import uuid

import pytest

from app.agents.ask_agent.adapter import AskAgentAdapter
from app.memory.memory_store import memory_store
from app.services.chat_service import ChatService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.real_data,
    pytest.mark.slow,
]


def require_llm_key():
    if not os.getenv("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY is not configured for real LLM integration")


async def test_real_ask_agent_one_turn_returns_executable_semantic_context(
    real_repositories,
):
    require_llm_key()
    adapter = AskAgentAdapter(
        real_repositories.meta,
        real_repositories.embedding,
        real_repositories.dw,
        real_repositories.columns,
        real_repositories.metrics,
        real_repositories.values,
    )
    events = [
        event
        async for event in adapter.stream("统计2025年女装品类的GMV")
    ]
    result = next(event["data"] for event in events if event["type"] == "tool_result")
    assert result["success"] is True
    assert result["sql"]
    assert result["query_spec"]["metrics"][0]["name"] == "GMV"
    assert result["query_spec"]["metrics"][0]["base_table"] == "fact_order_item"
    assert result["rows"]


async def collect_chat(service, session_id, message):
    events = []
    async for data in service.chat(session_id, message):
        payload = data.removeprefix("data: ").strip()
        events.append(json.loads(payload))
    return events


async def test_real_main_agent_preserves_session_and_executes_followup(
    real_repositories,
):
    require_llm_key()
    session_id = f"real-test-{uuid.uuid4()}"
    memory_store.clear(session_id)
    service = ChatService(
        real_repositories.meta,
        real_repositories.embedding,
        real_repositories.dw,
        real_repositories.columns,
        real_repositories.metrics,
        real_repositories.values,
    )
    first = await collect_chat(service, session_id, "统计2025年女装品类的GMV")
    first_result = next(
        event["data"]
        for event in first
        if event.get("type") == "tool_result" and event.get("tool") == "askAgent"
    )
    assert first_result["success"] is True

    second = await collect_chat(service, session_id, "改成直播渠道")
    second_result = next(
        event["data"]
        for event in second
        if event.get("type") == "tool_result" and event.get("tool") == "askAgent"
    )
    assert second_result["success"] is True
    memory = memory_store.get_or_create(session_id)
    assert memory.last_successful_query_context is not None
    assert "直播" in memory.last_successful_query_context["resolved_query"]
    memory_store.clear(session_id)

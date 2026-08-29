import json

import pytest
from pydantic import ValidationError

from app.agents.main_agent.nodes.classify_intent import (
    classification_context,
    classify_text,
)
from app.agents.main_agent.schemas import parse_intent_result, parse_rewrite_result
from app.memory.session_memory import SessionMemory

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("intent", ["chat", "direct_query", "followup_query"])
def test_intent_schema_accepts_only_the_three_real_intents(intent):
    result = parse_intent_result({"intent": intent, "reason": "  valid  "})
    assert result.intent == intent
    assert result.reason == "valid"


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "report", "reason": "unsupported"},
        {"intent": "chat", "reason": ""},
        {"intent": "chat", "reason": "ok", "sql": "SELECT 1"},
        ["chat"],
    ],
)
def test_intent_schema_rejects_unknown_blank_extra_and_non_object(payload):
    with pytest.raises((ValidationError, ValueError, TypeError)):
        parse_intent_result(payload)


@pytest.mark.parametrize(
    ("status", "fields"),
    [
        ("resolved", {"resolved_query": "统计2025年订单数"}),
        ("independent", {"resolved_query": "统计2025年销量"}),
        (
            "clarify",
            {
                "candidate_resolved_query": "统计GMV和订单数",
                "clarification_question": "追加还是替换？",
            },
        ),
        ("cancelled", {}),
        ("chat", {}),
    ],
)
def test_rewrite_schema_accepts_all_five_status_contracts(status, fields):
    result = parse_rewrite_result(
        {
            "status": status,
            "resolved_query": None,
            "candidate_resolved_query": None,
            "clarification_question": None,
            "reason": "contract",
            **fields,
        }
    )
    assert result.status == status


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "resolved", "resolved_query": None},
        {"status": "clarify", "clarification_question": None},
        {
            "status": "cancelled",
            "clarification_question": "should not exist",
        },
        {"status": "unknown"},
    ],
)
def test_rewrite_schema_rejects_status_field_mismatches(payload):
    complete = {
        "resolved_query": None,
        "candidate_resolved_query": None,
        "clarification_question": None,
        "reason": "invalid combination",
        **payload,
    }
    with pytest.raises(ValidationError):
        parse_rewrite_result(complete)


def test_fenced_json_is_the_only_format_recovery_performed_locally():
    raw = """```json
    {"status":"chat","resolved_query":null,"candidate_resolved_query":null,
    "clarification_question":null,"reason":"闲聊"}
    ```"""
    assert parse_rewrite_result(raw).status == "chat"
    with pytest.raises(json.JSONDecodeError):
        parse_rewrite_result("prefix {not valid json}")


@pytest.mark.parametrize(
    ("message", "has_history", "expected"),
    [
        ("你好", False, "chat"),
        ("生成一份月报", False, "chat"),
        ("为什么GMV下降", False, "chat"),
        ("统计2025年华东订单数", True, "direct_query"),
        ("查询GMV并分析原因", False, "direct_query"),
        ("那直播渠道呢", True, "followup_query"),
        ("订单数", True, "followup_query"),
        ("那直播渠道呢", False, "followup_query"),
    ],
)
def test_deterministic_intent_fallback_maps_real_activity_types(
    message, has_history, expected, successful_memory
):
    memory = successful_memory if has_history else SessionMemory("empty")
    assert classify_text(message, memory) == expected


def test_classification_context_is_compact_and_never_contains_sql(successful_memory):
    context = classification_context(successful_memory)
    assert context["has_last_successful_query"] is True
    assert context["last_resolved_query"] == "统计2025年女装GMV"
    assert "sql" not in context

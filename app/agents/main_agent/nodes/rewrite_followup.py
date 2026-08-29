"""LLM follow-up rewrite node with strict output validation and one repair call."""

from __future__ import annotations

import json

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.llm import llm
from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.followup import (
    build_clarifying_pending,
    pending_for_rewrite_prompt,
)
from app.agents.main_agent.schemas import RewriteResult, parse_rewrite_result
from app.agents.main_agent.state import MainAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.prompt.prompt_loader import load_prompt


async def _invoke_rewriter(payload: dict[str, str]) -> str:
    prompt = PromptTemplate(
        template=load_prompt("main_agent/rewrite_followup"),
        input_variables=[
            "message",
            "last_successful_query_context",
            "pending_query",
        ],
    )
    chain = prompt | llm | StrOutputParser()
    return await async_retry(
        "rewrite_followup.llm",
        lambda: chain.ainvoke(payload),
        attempts=2,
        timeout_seconds=60,
    )


async def _repair_rewrite_output(raw: str, validation_error: str) -> RewriteResult:
    prompt = PromptTemplate(
        template=load_prompt("main_agent/repair_rewrite_output"),
        input_variables=["raw_output", "validation_error"],
    )
    chain = prompt | llm | StrOutputParser()
    repaired = await async_retry(
        "repair_rewrite_output.llm",
        lambda: chain.ainvoke(
            {"raw_output": raw, "validation_error": validation_error}
        ),
        attempts=2,
        timeout_seconds=45,
    )
    return parse_rewrite_result(repaired)


async def rewrite_followup(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    """Resolve, clarify, cancel, or hand off one context-dependent turn."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "rewrite_followup", "status": "running"})
    memory = runtime.context["memory"]
    existing_pending = memory.pending_query
    working_pending = pending_for_rewrite_prompt(existing_pending, state["message"])
    payload = {
        "message": state["message"],
        "last_successful_query_context": json.dumps(
            memory.last_successful_query_context,
            ensure_ascii=False,
            default=str,
        ),
        "pending_query": json.dumps(
            working_pending, ensure_ascii=False, default=str
        ),
    }

    raw = ""
    try:
        raw = await _invoke_rewriter(payload)
        logger.info(f"Rewrite_followup raw LLM output: {raw}")

        result = parse_rewrite_result(raw)
    except Exception as first_error:
        try:
            result = await _repair_rewrite_output(raw, str(first_error))
        except Exception as repair_error:
            error_message = f"{first_error}; repair failed: {repair_error}"
            logger.warning(f"Follow-up rewrite output is invalid: {error_message}")
            writer(
                {
                    "type": "progress",
                    "step": "rewrite_followup",
                    "status": "error",
                }
            )
            return {
                "rewrite_result": None,
                "rewrite_error": error_message,
                "pending_action": {"action": "preserve"},
            }

    result_data = result.model_dump()
    updates: dict[str, object] = {
        "rewrite_result": result_data,
        "rewrite_error": None,
    }

    if result.status in {"resolved", "independent"}:
        updates["effective_query"] = result.resolved_query
        updates["query_source"] = "rewrite"
        updates["pending_action"] = {
            "action": "clear" if result.status == "independent" else "preserve"
        }
    elif result.status == "clarify":
        pending = build_clarifying_pending(
            result=result,
            current_message=state["message"],
            existing_pending=existing_pending,
            working_pending=working_pending,
        )
        if pending is None:
            result_data["clarification_exhausted"] = True
            updates["rewrite_result"] = result_data
            updates["pending_action"] = {"action": "clear"}
        else:
            updates["pending_action"] = {
                "action": "set_clarifying",
                "value": pending,
            }
    elif result.status == "cancelled":
        updates["pending_action"] = {"action": "clear"}
    else:
        updates["pending_action"] = {"action": "preserve"}

    writer(
        {
            "type": "followup_rewrite",
            "status": result.status,
            "original_message": state["message"],
            "resolved_query": result.resolved_query,
            "clarification_question": result.clarification_question,
            "reason": result.reason,
        }
    )
    writer({"type": "progress", "step": "rewrite_followup", "status": "success"})
    return updates

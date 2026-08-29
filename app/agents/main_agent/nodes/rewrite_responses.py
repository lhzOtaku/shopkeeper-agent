"""User-facing terminal responses for follow-up lifecycle branches."""

from langgraph.runtime import Runtime

from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.followup import MAX_EXECUTION_RETRY_COUNT
from app.agents.main_agent.state import MainAgentState


def _finish(runtime: Runtime[MainAgentContext], content: str) -> None:
    runtime.stream_writer({"type": "final", "content": content})


async def respond_clarification(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    result = state.get("rewrite_result") or {}
    content = result.get("clarification_question") or "请补充一个完整问数。"
    pending = (state.get("pending_action") or {}).get("value") or {}
    runtime.stream_writer(
        {
            "type": "clarification",
            "question": content,
            "round": pending.get("clarification_count", 1),
            "max_rounds": 2,
        }
    )
    _finish(runtime, content)
    return {"final_response": content}


async def respond_clarification_exhausted(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    del state
    content = (
        "当前信息仍不足以确定查询条件，请重新描述一个完整问数，"
        "例如“查询上个月快手渠道的订单数”。"
    )
    _finish(runtime, content)
    return {
        "final_response": content,
        "pending_action": {"action": "clear"},
    }


async def respond_cancelled(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    del state
    content = "好的，已取消当前查询。"
    runtime.stream_writer({"type": "query_cancelled", "content": content})
    _finish(runtime, content)
    return {
        "final_response": content,
        "pending_action": {"action": "clear"},
    }


async def respond_rewrite_failed(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    del state
    content = "本次追问解析出现系统异常，尚未执行查询，请直接重试。"
    runtime.stream_writer(
        {"type": "rewrite_failed", "content": content, "retryable": True}
    )
    _finish(runtime, content)
    return {
        "final_response": content,
        "pending_action": {"action": "preserve"},
    }


async def respond_retry_unavailable(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    del state
    content = "上次失败不适合直接重试，请修改查询条件后重新提交。"
    _finish(runtime, content)
    return {
        "final_response": content,
        "pending_action": {"action": "preserve"},
    }


async def respond_retry_exhausted(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    del state
    content = (
        f"已连续重试 {MAX_EXECUTION_RETRY_COUNT} 次仍未成功，"
        "请稍后再试或修改查询条件。"
    )
    _finish(runtime, content)
    return {
        "final_response": content,
        "pending_action": {"action": "clear"},
    }

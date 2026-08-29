"""SQL correction failure node."""

from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.sql_correction_test import log_sql_correction_summary
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger


async def fail_sql_correction(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    """Stop the graph when SQL still fails after the correction limit."""

    writer = runtime.stream_writer
    step = "SQL校正失败"
    correction_count = state.get("correction_count", 0)
    error = state.get("error") or "未知 SQL 校验错误"
    message = (
        f"SQL 已校正 {correction_count} 次，仍未通过校验，已停止执行。"
        f"最后一次错误：{error}"
    )

    logger.error(message)
    log_sql_correction_summary(
        {**state, "error": error},
        "SQL校正失败测试：超过最大校正次数仍未通过",
    )
    writer({"type": "progress", "step": step, "status": "error"})
    raise RuntimeError(message)

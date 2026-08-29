"""Node that calls reportAgent from mainAgent."""

from langgraph.runtime import Runtime

from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.state import MainAgentState
from app.agents.report_agent.graph import graph as report_graph
from app.agents.report_agent.state import ReportAgentState
from app.core.log import logger


async def call_report_agent(
    state: MainAgentState, runtime: Runtime[MainAgentContext]
):
    """Run the placeholder reportAgent graph and stream its result."""

    writer = runtime.stream_writer
    logger.info("mainAgent 节点开始处理：call_report_agent")
    writer({"type": "progress", "step": "call_report_agent", "status": "running"})
    topic = state["message"]
    writer({"type": "tool_start", "tool": "reportAgent", "query": topic})

    report: dict = {}
    async for event in report_graph.astream(
        input=ReportAgentState(topic=topic), context={}, stream_mode="custom"
    ):
        writer(event)

    result = await report_graph.ainvoke(
        input=ReportAgentState(topic=topic), context={}
    )
    report = result.get("report", {})

    writer({"type": "tool_result", "tool": "reportAgent", "data": report})
    content = report.get("message", "报告生成功能已规划，当前返回报告大纲。")
    logger.info("mainAgent 节点处理完成：call_report_agent")
    writer({"type": "progress", "step": "call_report_agent", "status": "success"})
    writer({"type": "final", "content": content})
    return {"report_result": report, "final_response": content}

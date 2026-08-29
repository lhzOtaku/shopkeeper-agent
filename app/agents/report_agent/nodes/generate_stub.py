"""Placeholder report-generation node."""

from langgraph.runtime import Runtime

from app.agents.report_agent.state import ReportAgentState


async def generate_stub(state: ReportAgentState, runtime: Runtime[dict]):
    """Return a report scaffold before full multi-step report generation exists."""

    writer = runtime.stream_writer
    writer(
        {
            "type": "tool_progress",
            "tool": "reportAgent",
            "step": "生成报告大纲",
            "status": "running",
        }
    )

    topic = state["topic"]
    report = {
        "title": topic,
        "status": "占位",
        "outline": [
            "经营概览",
            "核心指标表现",
            "区域/品类拆解",
            "异常与机会点",
            "后续分析建议",
        ],
        "message": "reportAgent 架子已就绪，后续会接入多次 askAgent 查数和分析正文生成。",
    }

    writer(
        {
            "type": "tool_progress",
            "tool": "reportAgent",
            "step": "生成报告大纲",
            "status": "success",
        }
    )
    return {"report": report}

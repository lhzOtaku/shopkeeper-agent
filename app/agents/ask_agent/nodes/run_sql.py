"""Execute the final SQL and emit rows plus structured query memory."""

from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.memory.query_spec import build_query_spec


async def run_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Run SQL and return final data-query artifacts."""

    writer = runtime.stream_writer
    step = "run_sql"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        sql = state["sql"]
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        rows = await dw_mysql_repository.run(sql)
        query_spec = build_query_spec(
            query=state["query"],
            sql=sql,
            state=state,
            rows=rows,
        )
        logger.info(f"SQL execution result: {rows}")
        writer({"type": "progress", "step": step, "status": "success"})
        writer({"type": "result", "data": rows})
        writer({"type": "query_spec", "data": query_spec})
        return {"rows": rows, "query_spec": query_spec}

    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

"""SQL correction node."""

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.sql_correction_test import is_sql_correction_test_mode
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.prompt.prompt_loader import load_prompt


async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Correct SQL according to validation errors and structured context."""

    writer = runtime.stream_writer
    step = "correct_sql"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        table_infos = state["table_infos"]
        generation_context = state.get("generation_context", {})
        query = state["query"]
        sql = state["sql"]
        error = state["error"]
        original_sql = state.get("original_sql") or sql
        correction_count = state.get("correction_count", 0) + 1
        correction_records = list(state.get("sql_correction_records") or [])

        prompt = PromptTemplate(
            template=load_prompt("ask_agent/correct_sql"),
            input_variables=[
                "table_infos",
                "generation_context",
                "query",
                "original_sql",
                "sql",
                "error",
            ],
        )
        chain = prompt | llm | StrOutputParser()
        payload = {
            "table_infos": yaml.dump(
                table_infos, allow_unicode=True, sort_keys=False
            ),
            "generation_context": yaml.dump(
                generation_context, allow_unicode=True, sort_keys=False
            ),
            "query": query,
            "original_sql": original_sql,
            "sql": sql,
            "error": error,
        }

        result = await async_retry(
            "correct_sql.llm",
            lambda: chain.ainvoke(payload),
            attempts=3,
            timeout_seconds=90,
        )

        logger.info(f"Corrected SQL round {correction_count}: {result}")
        correction_records.append(
            {
                "correction_count": correction_count,
                "before_sql": sql,
                "error": error,
                "corrected_sql": result,
            }
        )
        if not is_sql_correction_test_mode():
            writer({"type": "sql", "sql": result})
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "sql": result,
            "correction_count": correction_count,
            "sql_correction_records": correction_records,
            "sql_safety_blocked": False,
        }
    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

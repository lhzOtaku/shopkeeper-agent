"""SQL generation node."""

import yaml
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.prompt.prompt_loader import load_prompt


def log_raw_llm_output(output: BaseMessage) -> BaseMessage:
    """Log the raw LLM message before StrOutputParser."""

    logger.info(f"SQL generation raw LLM type: {type(output).__name__}")
    logger.info(f"SQL generation raw LLM content: {output.content}")
    return output


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Generate candidate SQL from recalled structured context."""

    writer = runtime.stream_writer
    step = "generate_sql"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        table_infos = state["table_infos"]
        generation_context = state.get("generation_context", {})
        query = state["query"]

        prompt = PromptTemplate(
            template=load_prompt("ask_agent/generate_sql"),
            input_variables=[
                "table_infos",
                "generation_context",
                "query",
            ],
        )
        chain = (
            prompt
            | llm
            | RunnableLambda(log_raw_llm_output)
            | StrOutputParser()
        )
        payload = {
            "table_infos": yaml.dump(
                table_infos, allow_unicode=True, sort_keys=False
            ),
            "generation_context": yaml.dump(
                generation_context, allow_unicode=True, sort_keys=False
            ),
            "query": query,
        }

        result = await async_retry(
            "generate_sql.llm",
            lambda: chain.ainvoke(payload),
            attempts=3,
            timeout_seconds=90,
        )
        logger.info(f"Generated SQL: {result}")
        writer({"type": "sql", "sql": result})
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "sql": result,
            "original_sql": result,
            "correction_count": 0,
            "sql_validation_records": [],
            "sql_correction_records": [],
            "sql_safety_records": [],
            "sql_safety_blocked": False,
        }

    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

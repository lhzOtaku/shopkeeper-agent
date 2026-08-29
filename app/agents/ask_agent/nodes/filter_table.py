"""Filter table context for SQL generation."""

from copy import deepcopy

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.state import DataAgentState, TableInfoState
from app.core.log import logger
from app.core.retry import async_retry
from app.prompt.prompt_loader import load_prompt


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Keep only table/column candidates relevant to the user question."""

    writer = runtime.stream_writer
    step = "filter_table"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        table_infos: list[TableInfoState] = state["table_infos"]

        prompt = PromptTemplate(
            template=load_prompt("ask_agent/filter_table_info"),
            input_variables=["query", "table_infos"],
        )
        chain = prompt | llm | JsonOutputParser()
        payload = {
            "query": query,
            "table_infos": yaml.dump(
                table_infos, allow_unicode=True, sort_keys=False
            ),
        }
        result = await async_retry(
            "filter_table.llm",
            lambda: chain.ainvoke(payload),
            attempts=2,
            timeout_seconds=60,
        )

        filtered_table_infos: list[TableInfoState] = []
        for table_info in table_infos:
            table_name = table_info["name"]
            selected_columns = result.get(table_name)
            if not selected_columns:
                continue
            filtered_table = deepcopy(table_info)
            filtered_table["columns"] = [
                column_info
                for column_info in table_info.get("columns", [])
                if column_info.get("name") in selected_columns
            ]
            filtered_table_infos.append(filtered_table)

        logger.info(
            "Filtered tables: "
            f"{[table_info['name'] for table_info in filtered_table_infos]}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {"table_infos": filtered_table_infos}

    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

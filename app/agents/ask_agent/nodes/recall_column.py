"""Column recall node backed by Qdrant."""

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.embedding_retry import embed_query_with_retry
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.entities.column_info import ColumnInfo
from app.prompt.prompt_loader import load_prompt


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Recall fields that may match the user's business expression."""

    writer = runtime.stream_writer
    step = "recall_column"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        keywords = state["keywords"]
        query = state["query"]
        column_qdrant_repository = runtime.context["column_qdrant_repository"]
        embedding_client = runtime.context["embedding_client"]

        prompt = PromptTemplate(
            template=load_prompt("ask_agent/extend_keywords_for_column_recall"),
            input_variables=["query"],
        )
        chain = prompt | llm | JsonOutputParser()
        result = await async_retry(
            "recall_column.extend_keywords",
            lambda: chain.ainvoke({"query": query}),
            attempts=2,
            timeout_seconds=45,
        )
        keywords = list(dict.fromkeys([*keywords, *result]))

        column_info_map: dict[str, ColumnInfo] = {}
        failed_keywords: list[str] = []
        for keyword in keywords:
            try:
                embedding = await embed_query_with_retry(embedding_client, keyword)
                current_column_infos: list[
                    ColumnInfo
                ] = await column_qdrant_repository.search(embedding)
            except Exception as exc:
                failed_keywords.append(keyword)
                logger.warning(f"Column recall skipped keyword={keyword}: {exc}")
                continue
            for column_info in current_column_infos:
                if column_info.id not in column_info_map:
                    column_info_map[column_info.id] = column_info

        if failed_keywords:
            logger.warning(f"Column recall skipped {len(failed_keywords)} keywords")

        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_column_infos": list(column_info_map.values())}
    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

"""Field-value recall node backed by Elasticsearch."""

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.entities.value_info import ValueInfo
from app.prompt.prompt_loader import load_prompt


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Recall real field values that may be needed in SQL filters."""

    writer = runtime.stream_writer
    step = "recall_value"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        keywords = state["keywords"]
        value_es_repository = runtime.context["value_es_repository"]

        prompt = PromptTemplate(
            template=load_prompt("ask_agent/extend_keywords_for_value_recall"),
            input_variables=["query"],
        )
        chain = prompt | llm | JsonOutputParser()
        result = await async_retry(
            "recall_value.extend_keywords",
            lambda: chain.ainvoke({"query": query}),
            attempts=2,
            timeout_seconds=45,
        )
        keywords = list(dict.fromkeys([*keywords, *result]))

        value_infos_map: dict[str, ValueInfo] = {}
        failed_keywords: list[str] = []
        for keyword in keywords:
            try:
                current_value_infos: list[ValueInfo] = await value_es_repository.search(
                    keyword
                )
            except Exception as exc:
                failed_keywords.append(keyword)
                logger.warning(f"Value recall skipped keyword={keyword}: {exc}")
                continue
            for current_value_info in current_value_infos:
                if current_value_info.id not in value_infos_map:
                    value_infos_map[current_value_info.id] = current_value_info

        if failed_keywords:
            logger.warning(f"Value recall skipped {len(failed_keywords)} keywords")
        logger.info(f"Recalled values: {list(value_infos_map.keys())}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_value_infos": list(value_infos_map.values())}
    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

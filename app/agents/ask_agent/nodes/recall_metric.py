"""Metric recall node backed by Qdrant plus Meta MySQL fallback."""

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.embedding_retry import embed_query_with_retry
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import async_retry
from app.entities.metric_info import MetricInfo
from app.entities.metric_recall_candidate import MetricRecallCandidate
from app.prompt.prompt_loader import load_prompt


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Recall business metrics that may match the user question."""

    writer = runtime.stream_writer
    step = "recall_metric"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        keywords = state["keywords"]
        embedding_client = runtime.context["embedding_client"]
        metric_qdrant_repository = runtime.context["metric_qdrant_repository"]
        meta_mysql_repository = runtime.context["meta_mysql_repository"]

        prompt = PromptTemplate(
            template=load_prompt("ask_agent/extend_keywords_for_metric_recall"),
            input_variables=["query"],
        )
        chain = prompt | llm | JsonOutputParser()
        result = await async_retry(
            "recall_metric.extend_keywords",
            lambda: chain.ainvoke({"query": query}),
            attempts=2,
            timeout_seconds=45,
        )
        keywords = list(dict.fromkeys([*keywords, *result]))

        candidate_metric_ids: list[str] = []
        failed_keywords: list[str] = []
        for keyword in keywords:
            try:
                embedding = await embed_query_with_retry(embedding_client, keyword)
                current_metric_candidates: list[
                    MetricRecallCandidate
                ] = await metric_qdrant_repository.search(embedding)
            except Exception as exc:
                failed_keywords.append(keyword)
                logger.warning(f"Metric recall skipped keyword={keyword}: {exc}")
                continue
            candidate_metric_ids.extend(
                candidate.metric_id for candidate in current_metric_candidates
            )

        candidate_metric_ids = list(dict.fromkeys(candidate_metric_ids))
        metric_info_map: dict[str, MetricInfo] = {
            metric_info.id: metric_info
            for metric_info in await meta_mysql_repository.get_metric_infos_by_ids(
                candidate_metric_ids
            )
        }

        if failed_keywords or not metric_info_map:
            fallback_metric_infos = await meta_mysql_repository.search_metric_infos_by_texts(
                [query, *keywords],
                limit=20,
            )
            for metric_info in fallback_metric_infos:
                if metric_info.id not in metric_info_map:
                    metric_info_map[metric_info.id] = metric_info
            if fallback_metric_infos:
                logger.warning(
                    "Metric recall supplemented by Meta fallback: "
                    f"{[metric_info.name for metric_info in fallback_metric_infos]}"
                )

        logger.info(f"Recalled metrics: {list(metric_info_map.keys())}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_metric_infos": list(metric_info_map.values())}
    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

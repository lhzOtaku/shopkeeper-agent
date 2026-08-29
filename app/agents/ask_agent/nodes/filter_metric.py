"""Filter and resolve metric context for SQL generation."""

from dataclasses import asdict

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.llm import llm
from app.agents.ask_agent.metric_resolution import (
    REFUND_RATE_METRICS,
    has_product_context,
    resolve_refund_rate_metrics,
)
from app.agents.ask_agent.state import DataAgentState, MetricInfoState
from app.core.log import logger
from app.core.retry import async_retry
from app.entities.metric_info import MetricInfo
from app.prompt.prompt_loader import load_prompt


def _metric_info_state(metric_info: MetricInfo) -> MetricInfoState:
    return MetricInfoState(
        name=metric_info.name,
        description=metric_info.description,
        alias=metric_info.alias,
        default_variant_id=metric_info.default_variant_id,
        variants=[asdict(variant) for variant in metric_info.variants],
    )


async def filter_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Keep only metrics relevant to the user question."""

    writer = runtime.stream_writer
    step = "filter_metric"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        metric_infos: list[MetricInfoState] = state["metric_infos"]
        value_infos = state.get("retrieved_value_infos", [])

        metric_candidates = [
            {
                "name": metric_info.get("name"),
                "description": metric_info.get("description"),
                "alias": metric_info.get("alias") or [],
            }
            for metric_info in metric_infos
        ]
        prompt = PromptTemplate(
            template=load_prompt("ask_agent/filter_metric_info"),
            input_variables=["query", "metric_infos"],
        )
        chain = prompt | llm | JsonOutputParser()
        payload = {
            "query": query,
            "metric_infos": yaml.dump(
                metric_candidates, allow_unicode=True, sort_keys=False
            ),
        }
        result = await async_retry(
            "filter_metric.llm",
            lambda: chain.ainvoke(payload),
            attempts=2,
            timeout_seconds=60,
        )

        selected_names = set(result)
        metric_map = {
            metric_info.get("name"): metric_info for metric_info in metric_infos
        }
        resolution = resolve_refund_rate_metrics(
            query=query,
            llm_selected_names=selected_names,
            available_metric_names=set(metric_map),
            product_context=has_product_context(query, value_infos),
        )

        if resolution.missing_names:
            meta_mysql_repository = runtime.context["meta_mysql_repository"]
            missing_metric_infos = await meta_mysql_repository.get_metric_infos_by_ids(
                sorted(resolution.missing_names & REFUND_RATE_METRICS)
            )
            metric_map.update(
                {
                    metric_info.name: _metric_info_state(metric_info)
                    for metric_info in missing_metric_infos
                }
            )

        filtered_metric_infos = [
            metric_info
            for name, metric_info in metric_map.items()
            if name in resolution.selected_names
        ]

        logger.info(
            "Filtered metrics: "
            f"{[metric_info['name'] for metric_info in filtered_metric_infos]}; "
            f"refund_resolution={resolution.reason}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {"metric_infos": filtered_metric_infos}

    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

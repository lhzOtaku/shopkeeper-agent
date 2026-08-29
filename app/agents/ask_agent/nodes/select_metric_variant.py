"""
指标口径选择节点。

召回和过滤阶段只负责确定用户要看哪个业务指标；本节点负责在该指标的多个
variant 里选择一个具体计算口径，例如 GMV 在订单粒度使用 fact_order，在商品
或品类粒度使用 fact_order_item。
"""

from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.metric_resolution import has_product_context
from app.agents.ask_agent.state import (
    DataAgentState,
    MetricInfoState,
    MetricVariantState,
)
from app.core.log import logger

ORDER_GRAIN_HINTS = (
    "不要直接平均商品明细金额",
    "不要用商品明细",
    "不要用明细金额",
    "不要用 fact_order_item",
    "不要用fact_order_item",
)
ITEM_GRAIN_HINTS = (
    "不要用订单表",
    "不要用 order_amount",
    "不要用order_amount",
    "商品销售额",
    "商品明细金额",
    "销量",
)
ORDER_GRAINS = {"order", "channel", "region", "member", "date"}
ITEM_GRAINS = {"order_item", "product"}


def _table_names(state: DataAgentState) -> set[str]:
    return {table_info["name"] for table_info in state.get("table_infos", [])}


def _prefers_order_grain(query: str) -> bool:
    return any(hint in query for hint in ORDER_GRAIN_HINTS)


def _prefers_item_grain(query: str) -> bool:
    return any(hint in query for hint in ITEM_GRAIN_HINTS)


def _score_variant(
    variant: MetricVariantState,
    *,
    query: str,
    table_names: set[str],
    default_variant_id: str | None,
    product_context: bool,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    suitable_dimensions = set(variant.get("suitable_dimensions") or [])
    grain = variant.get("grain")
    base_table = variant.get("base_table")
    prefers_order_grain = _prefers_order_grain(query)
    prefers_item_grain = _prefers_item_grain(query)

    if variant.get("id") == default_variant_id:
        score += 1
        reasons.append("default variant")

    if prefers_order_grain:
        if grain in ORDER_GRAINS:
            score += 12
            reasons.append("query explicitly prefers order grain")
        if grain in ITEM_GRAINS:
            score -= 12
            reasons.append("query rejects item-grain calculation")

    if prefers_item_grain:
        if grain in ITEM_GRAINS:
            score += 12
            reasons.append("query explicitly prefers item grain")
        if grain in ORDER_GRAINS:
            score -= 12
            reasons.append("query rejects order-grain calculation")

    if base_table and base_table in table_names:
        score += 3
        reasons.append(f"base table {base_table} selected")

    matched_dimensions = suitable_dimensions & table_names
    if matched_dimensions:
        score += 2 * len(matched_dimensions)
        reasons.append(f"matched dimensions {sorted(matched_dimensions)}")

    if product_context:
        if "dim_product" in suitable_dimensions or grain in ITEM_GRAINS:
            score += 8
            reasons.append("product/category context prefers item grain")
        if grain in ORDER_GRAINS and "dim_product" not in suitable_dimensions:
            score -= 3
    elif grain in ORDER_GRAINS:
        score += 4
        reasons.append("non-product context prefers order grain")

    return score, reasons


def _select_variant(
    metric_info: MetricInfoState,
    query: str,
    table_names: set[str],
    product_context: bool,
) -> MetricVariantState:
    variants = metric_info.get("variants") or []
    if not variants:
        raise ValueError(
            f"Metric '{metric_info['name']}' has no variants; "
            "every metric must define at least one executable variant."
        )

    default_variant_id = metric_info.get("default_variant_id")
    if default_variant_id not in {variant.get("id") for variant in variants}:
        raise ValueError(
            f"Metric '{metric_info['name']}' references unknown default variant "
            f"'{default_variant_id}'"
        )
    if _prefers_order_grain(query):
        order_variants = [
            variant
            for variant in variants
            if variant.get("grain") in ORDER_GRAINS
        ]
        if order_variants:
            variants = order_variants
    elif _prefers_item_grain(query):
        item_variants = [
            variant
            for variant in variants
            if variant.get("grain") in ITEM_GRAINS
        ]
        if item_variants:
            variants = item_variants
    elif product_context:
        item_variants = [
            variant
            for variant in variants
            if variant.get("grain") in ITEM_GRAINS
        ]
        if item_variants:
            variants = item_variants

    scored_variants = []
    for variant in variants:
        score, reasons = _score_variant(
            variant,
            query=query,
            table_names=table_names,
            default_variant_id=default_variant_id,
            product_context=product_context,
        )
        scored_variants.append((score, variant, reasons))

    scored_variants.sort(key=lambda item: item[0], reverse=True)
    score, selected_variant, reasons = scored_variants[0]
    selected_variant = MetricVariantState(**selected_variant)
    selected_variant["selection_reason"] = f"score={score}; " + "; ".join(reasons)
    return selected_variant


async def select_metric_variant(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    """为过滤后的每个指标选择最终计算口径。"""

    writer = runtime.stream_writer
    step = "选择指标口径"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        table_names = _table_names(state)
        product_context = has_product_context(
            query, state.get("retrieved_value_infos", [])
        )
        metric_infos = state.get("metric_infos", [])
        selected_metric_infos: list[MetricInfoState] = []

        for metric_info in metric_infos:
            selected_variant = _select_variant(
                metric_info, query, table_names, product_context
            )
            metric_info["selected_variant"] = selected_variant
            selected_metric_infos.append(metric_info)

        logger.info(
            "选择后的指标口径："
            f"{[(m['name'], m.get('selected_variant', {}).get('name')) for m in selected_metric_infos]}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {"metric_infos": selected_metric_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise

"""Deterministic metric-intent helpers shared by askAgent nodes."""

import re
from dataclasses import dataclass
from typing import Iterable

from app.entities.value_info import ValueInfo

ORDER_REFUND_RATE = "订单退款率"
QUANTITY_REFUND_RATE = "件数退款率"
REFUND_RATE_METRICS = {ORDER_REFUND_RATE, QUANTITY_REFUND_RATE}

PRODUCT_CONTEXT_HINTS = (
    "商品",
    "产品",
    "品类",
    "类目",
    "分类",
    "品牌",
    "SKU",
    "价格带",
)

REFUND_RATE_ALIASES = {
    ORDER_REFUND_RATE: ("订单退款率", "退款订单率", "订单售后率"),
    QUANTITY_REFUND_RATE: (
        "件数退款率",
        "商品退款率",
        "品类退款率",
        "退货件数占比",
    ),
}
GENERIC_REFUND_RATE_ALIASES = ("退款率", "售后率", "退货率")
NEGATION_PREFIX_PATTERN = re.compile(
    r"(?:不要|不看|不用|排除|别用|别看|不是|无需|无须)(?:再|只)?(?:按|用|看)?\s*$"
)
NEGATION_SUFFIX_PATTERN = re.compile(
    r"^\s*(?:不要|不看|不用|排除|别用|别看)"
)


@dataclass(frozen=True)
class RefundRateResolution:
    """Resolved metric names plus any definitions missing from recall."""

    selected_names: set[str]
    missing_names: set[str]
    reason: str


def _occurrences(text: str, phrase: str) -> Iterable[tuple[int, int]]:
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return
        end = index + len(phrase)
        yield index, end
        start = end


def _is_negated(query: str, start: int, end: int) -> bool:
    prefix = query[max(0, start - 10) : start]
    suffix = query[end : end + 6]
    return bool(
        NEGATION_PREFIX_PATTERN.search(prefix)
        or NEGATION_SUFFIX_PATTERN.search(suffix)
    )


def has_explicit_product_context(query: str) -> bool:
    """Return true for a non-negated product dimension mentioned in the query."""

    normalized_query = query.lower()
    for hint in PRODUCT_CONTEXT_HINTS:
        normalized_hint = hint.lower()
        for start, end in _occurrences(normalized_query, normalized_hint):
            if not _is_negated(normalized_query, start, end):
                return True
    return False


def has_bound_product_value(query: str, value_infos: Iterable[ValueInfo]) -> bool:
    """Use a strong ES value binding instead of enumerating product values."""

    normalized_query = query.lower()
    for value_info in value_infos:
        value = str(value_info.value or "").strip().lower()
        if (
            value
            and value_info.table_id == "dim_product"
            and value in normalized_query
        ):
            return True
    return False


def has_product_context(query: str, value_infos: Iterable[ValueInfo]) -> bool:
    """Detect product analysis from dimension language or an ES field binding."""

    return has_explicit_product_context(query) or has_bound_product_value(
        query, value_infos
    )


def _specific_refund_mentions(
    query: str,
) -> tuple[set[str], set[str], list[tuple[int, int]]]:
    included: set[str] = set()
    excluded: set[str] = set()
    specific_spans: list[tuple[int, int]] = []

    for metric_name, aliases in REFUND_RATE_ALIASES.items():
        for alias in aliases:
            for start, end in _occurrences(query, alias):
                specific_spans.append((start, end))
                if _is_negated(query, start, end):
                    excluded.add(metric_name)
                else:
                    included.add(metric_name)

    included.difference_update(excluded)
    return included, excluded, specific_spans


def _inside_specific_span(
    start: int, end: int, specific_spans: Iterable[tuple[int, int]]
) -> bool:
    return any(span_start <= start and end <= span_end for span_start, span_end in specific_spans)


def _generic_refund_mentions(
    query: str, specific_spans: list[tuple[int, int]]
) -> tuple[bool, bool]:
    included = False
    excluded = False
    for alias in GENERIC_REFUND_RATE_ALIASES:
        for start, end in _occurrences(query, alias):
            if _inside_specific_span(start, end, specific_spans):
                continue
            if _is_negated(query, start, end):
                excluded = True
            else:
                included = True
    return included, excluded


def resolve_refund_rate_metrics(
    *,
    query: str,
    llm_selected_names: set[str],
    available_metric_names: set[str],
    product_context: bool,
) -> RefundRateResolution:
    """Resolve the two first-class refund-rate metrics without blind overrides."""

    selected_non_refund = llm_selected_names - REFUND_RATE_METRICS
    llm_refund_names = llm_selected_names & REFUND_RATE_METRICS
    explicit_includes, explicit_excludes, spans = _specific_refund_mentions(query)
    generic_included, generic_excluded = _generic_refund_mentions(query, spans)

    if generic_excluded and not generic_included and not explicit_includes:
        resolved_refund_names: set[str] = set()
        reason = "generic refund rate explicitly excluded"
    elif explicit_includes:
        resolved_refund_names = explicit_includes - explicit_excludes
        reason = "explicit refund-rate metric request"
    elif generic_included:
        allowed = REFUND_RATE_METRICS - explicit_excludes
        if len(allowed) == 1:
            resolved_refund_names = set(allowed)
            reason = "generic refund rate resolved after explicit exclusion"
        elif product_context and QUANTITY_REFUND_RATE in allowed:
            resolved_refund_names = {QUANTITY_REFUND_RATE}
            reason = "generic refund rate resolved from product context"
        elif ORDER_REFUND_RATE in allowed:
            resolved_refund_names = {ORDER_REFUND_RATE}
            reason = "generic refund rate resolved to order-level default"
        else:
            resolved_refund_names = llm_refund_names & allowed
            reason = "ambiguous refund rate preserved from LLM selection"
    elif explicit_excludes:
        resolved_refund_names = llm_refund_names - explicit_excludes
        reason = "explicit refund-rate exclusion"
    else:
        resolved_refund_names = llm_refund_names
        reason = "no deterministic refund-rate override"

    selected_names = selected_non_refund | resolved_refund_names
    missing_names = selected_names - available_metric_names
    return RefundRateResolution(
        selected_names=selected_names,
        missing_names=missing_names,
        reason=reason,
    )

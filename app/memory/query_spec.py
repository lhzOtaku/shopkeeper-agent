"""Structured query memory used for multi-turn data questions."""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

DATE_RANGE_PATTERN = re.compile(
    r"(?P<field>[A-Za-z_][\w.]*)\s*(?:>=|>|=)\s*['\"]?(?P<start>\d{4}-?\d{2}-?\d{2})"
    r"['\"]?\s+AND\s+(?P=field)\s*(?:<=|<)\s*['\"]?(?P<end>\d{4}-?\d{2}-?\d{2})",
    re.IGNORECASE,
)


def _metric_specs(generation_context: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for metric in generation_context.get("metrics") or []:
        selected_variant = metric.get("selected_variant") or {}
        metrics.append(
            {
                "name": metric.get("name"),
                "description": metric.get("description"),
                "selected_variant": selected_variant.get("name"),
                "grain": selected_variant.get("grain"),
                "formula": selected_variant.get("formula"),
                "base_table": selected_variant.get("base_table"),
                "required_joins": selected_variant.get("required_joins") or [],
            }
        )
    return [metric for metric in metrics if metric.get("name")]


def _filters(generation_context: dict[str, Any]) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for binding in generation_context.get("value_bindings") or []:
        filters.append(
            {
                "field": binding.get("column_id"),
                "table": binding.get("table_id"),
                "column": binding.get("column_name"),
                # Value recall proves the binding, not the final SQL operator.
                "operator": None,
                "value": binding.get("value"),
                "field_role": binding.get("field_role"),
            }
        )
    return [item for item in filters if item.get("value")]


def _tables(generation_context: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in generation_context.get("tables") or []:
        tables.append(
            {
                "name": table.get("name"),
                "role": table.get("role"),
                "description": table.get("description"),
                "grain": table.get("grain"),
            }
        )
    return [table for table in tables if table.get("name")]


def _join_relations(generation_context: dict[str, Any]) -> list[str]:
    return [
        relation.get("join_condition")
        for relation in generation_context.get("join_relations") or []
        if relation.get("join_condition")
    ]


def _group_by_columns(sql: str) -> list[str]:
    try:
        expression = sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return []

    group = expression.args.get("group")
    if not group:
        return []

    columns: list[str] = []
    for item in group.expressions:
        if isinstance(item, exp.Column):
            columns.append(item.sql(dialect="mysql"))
        else:
            columns.append(item.sql(dialect="mysql"))
    return columns


def _time_range(sql: str) -> dict[str, Any] | None:
    match = DATE_RANGE_PATTERN.search(sql)
    if not match:
        return None
    return {
        "field": match.group("field"),
        "start": match.group("start"),
        "end": match.group("end"),
    }


def build_query_spec(
    query: str,
    sql: str,
    state: dict[str, Any],
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a compact, reusable representation of the executed query."""

    generation_context = state.get("generation_context") or {}
    result_columns = list((rows or [{}])[0].keys()) if rows else []
    return {
        "original_query": query,
        "resolved_query": query,
        "metrics": _metric_specs(generation_context),
        "filters": _filters(generation_context),
        "dimensions": _group_by_columns(sql),
        "time_range": _time_range(sql),
        "tables": _tables(generation_context),
        "join_relations": _join_relations(generation_context),
        "sql": sql,
        "result_summary": {
            "row_count": len(rows or []),
            "columns": result_columns,
        },
    }

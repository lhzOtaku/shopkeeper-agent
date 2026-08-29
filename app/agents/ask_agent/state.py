"""State definitions for the ecommerce data-query agent."""

from typing import Any, TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo


class MetricVariantState(TypedDict, total=False):
    """Metric formula selected for a concrete analysis grain."""

    id: str
    metric_id: str
    name: str
    grain: str
    formula: str
    relevant_columns: list[str]
    default_filters: list[str]
    suitable_dimensions: list[str]
    base_table: str
    required_joins: list[str]
    selection_reason: str


class MetricInfoState(TypedDict, total=False):
    """Metric identity plus executable variants used before variant selection."""

    name: str
    description: str
    alias: list[str]
    default_variant_id: str
    variants: list[MetricVariantState]
    selected_variant: MetricVariantState


class ColumnInfoState(TypedDict, total=False):
    """Column context sent to SQL generation."""

    name: str
    type: str
    role: str
    examples: list[Any]
    description: str
    alias: list[str]


class TableInfoState(TypedDict, total=False):
    """Table context sent to SQL generation."""

    name: str
    role: str
    description: str
    grain: str
    columns: list[ColumnInfoState]


class ValueBindingState(TypedDict, total=False):
    """Real field value matched from Elasticsearch."""

    value: str
    column_id: str
    table_id: str
    column_name: str
    column_alias: list[str]
    field_role: str


class TableRelationState(TypedDict, total=False):
    """Stable join relation from Meta MySQL."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    relation_type: str
    description: str
    join_condition: str


class GenerationContextState(TypedDict, total=False):
    """Final structured context merged from Meta/Qdrant/ES."""

    metrics: list[MetricInfoState]
    tables: list[TableInfoState]
    value_bindings: list[ValueBindingState]
    join_relations: list[TableRelationState]


class DateInfoState(TypedDict, total=False):
    """Current date context for SQL generation."""

    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict, total=False):
    """Database dialect/version context for SQL generation."""

    dialect: str
    version: str


class DataAgentState(TypedDict, total=False):
    """Shared state for one data-query graph execution."""

    query: str
    keywords: list[str]

    retrieved_column_infos: list[ColumnInfo]
    retrieved_metric_infos: list[MetricInfo]
    retrieved_value_infos: list[ValueInfo]

    table_infos: list[TableInfoState]
    metric_infos: list[MetricInfoState]
    generation_context: GenerationContextState
    date_info: DateInfoState
    db_info: DBInfoState

    sql: str
    original_sql: str
    error: str | None
    correction_count: int
    sql_validation_records: list[dict[str, Any]]
    sql_correction_records: list[dict[str, Any]]
    sql_safety_records: list[dict[str, Any]]
    sql_safety_blocked: bool

    rows: list[dict[str, Any]]
    query_spec: dict[str, Any]

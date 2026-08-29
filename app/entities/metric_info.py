"""Metric metadata entities used by the Meta MySQL knowledge source."""

from dataclasses import dataclass


@dataclass
class MetricVariantInfo:
    """同一指标在一个具体粒度下的计算口径。"""

    id: str
    metric_id: str
    name: str
    grain: str
    formula: str
    relevant_columns: list[str]
    default_filters: list[str] | None = None
    suitable_dimensions: list[str] | None = None
    base_table: str | None = None
    required_joins: list[str] | None = None


@dataclass
class MetricInfo:
    """指标身份与其可执行口径的聚合，不重复保存口径字段。"""

    id: str
    name: str
    description: str
    alias: list[str]
    default_variant_id: str
    variants: list[MetricVariantInfo]

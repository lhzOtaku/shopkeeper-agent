"""Mappings for metric variant metadata and its field dependencies."""

from dataclasses import asdict

from app.entities.metric_info import MetricVariantInfo
from app.entities.metric_variant_column import MetricVariantColumn
from app.models.metric_variant import MetricVariantMySQL
from app.models.metric_variant_column import MetricVariantColumnMySQL


class MetricVariantMapper:
    """Convert metric-variant ORM records to business entities and back."""

    @staticmethod
    def to_entity(
        model: MetricVariantMySQL, relevant_columns: list[str]
    ) -> MetricVariantInfo:
        return MetricVariantInfo(
            id=model.id,
            metric_id=model.metric_id,
            name=model.name,
            grain=model.grain,
            formula=model.formula,
            relevant_columns=relevant_columns,
            default_filters=model.default_filters or [],
            suitable_dimensions=model.suitable_dimensions or [],
            base_table=model.base_table,
            required_joins=model.required_joins or [],
        )

    @staticmethod
    def to_model(entity: MetricVariantInfo) -> MetricVariantMySQL:
        data = asdict(entity)
        data.pop("relevant_columns")
        return MetricVariantMySQL(**data)


class MetricVariantColumnMapper:
    """Convert metric-variant field-dependency records."""

    @staticmethod
    def to_entity(model: MetricVariantColumnMySQL) -> MetricVariantColumn:
        return MetricVariantColumn(variant_id=model.variant_id, column_id=model.column_id)

    @staticmethod
    def to_model(entity: MetricVariantColumn) -> MetricVariantColumnMySQL:
        return MetricVariantColumnMySQL(**asdict(entity))

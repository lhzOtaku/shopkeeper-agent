"""Relationship between one metric variant and one required column."""

from dataclasses import dataclass


@dataclass
class MetricVariantColumn:
    """A metric formula depends on a concrete table.column identifier."""

    variant_id: str
    column_id: str

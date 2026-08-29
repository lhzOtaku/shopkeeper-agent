"""Lightweight metric candidate returned by Qdrant semantic retrieval."""

from dataclasses import dataclass


@dataclass
class MetricRecallCandidate:
    """Semantic recall data that must be hydrated from Meta MySQL before use."""

    metric_id: str
    name: str
    description: str
    alias: list[str]

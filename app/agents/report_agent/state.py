"""State for reportAgent."""

from typing import TypedDict


class ReportAgentState(TypedDict):
    """Minimal report request state."""

    topic: str
    report: dict

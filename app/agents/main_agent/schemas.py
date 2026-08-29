"""Strict LLM output schemas used by mainAgent."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.main_agent.state import Intent

RewriteStatus = Literal[
    "resolved",
    "independent",
    "clarify",
    "cancelled",
    "chat",
]


class IntentResult(BaseModel):
    """Validated intent-classification result."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class RewriteResult(BaseModel):
    """Validated follow-up rewrite result with status-dependent fields."""

    model_config = ConfigDict(extra="forbid")

    status: RewriteStatus
    resolved_query: str | None = Field(default=None, max_length=1200)
    candidate_resolved_query: str | None = Field(default=None, max_length=1200)
    clarification_question: str | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=300)

    @field_validator(
        "resolved_query",
        "candidate_resolved_query",
        "clarification_question",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_status_fields(self) -> RewriteResult:
        if self.status in {"resolved", "independent"}:
            if not self.resolved_query:
                raise ValueError(f"resolved_query is required for {self.status}")
            if self.clarification_question:
                raise ValueError(
                    f"clarification_question must be null for {self.status}"
                )
            if self.candidate_resolved_query:
                raise ValueError(
                    f"candidate_resolved_query must be null for {self.status}"
                )
        elif self.status == "clarify":
            if self.resolved_query:
                raise ValueError("resolved_query must be null for clarify")
            if not self.clarification_question:
                raise ValueError(
                    "clarification_question is required for clarify"
                )
        else:
            if self.resolved_query:
                raise ValueError(
                    f"resolved_query must be null for {self.status}"
                )
            if self.candidate_resolved_query:
                raise ValueError(
                    f"candidate_resolved_query must be null for {self.status}"
                )
            if self.clarification_question:
                raise ValueError(
                    f"clarification_question must be null for {self.status}"
                )
        return self


CODE_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL
)


def _load_json_object(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise TypeError("Model output must be a JSON string or object")

    text = value.strip()
    match = CODE_FENCE_PATTERN.match(text)
    if match:
        text = match.group("body").strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Model output must be a JSON object")
    return parsed


def parse_intent_result(value: str | dict[str, Any]) -> IntentResult:
    """Parse and strictly validate an intent result."""

    return IntentResult.model_validate(_load_json_object(value))


def parse_rewrite_result(value: str | dict[str, Any]) -> RewriteResult:
    """Parse and strictly validate a follow-up rewrite result."""

    return RewriteResult.model_validate(_load_json_object(value))

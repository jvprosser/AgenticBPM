"""Pydantic contracts for data-source typeahead broker responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


RESOLVE_DATA_SOURCE_INTENT = "RESOLVE_DATA_SOURCE_INTENT"


class SuggestSourcesRequest(BaseModel):
    user_raw_input: str = ""

    @field_validator("user_raw_input", mode="before")
    @classmethod
    def coerce_input(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class DataSourceMatch(BaseModel):
    source_name: str
    match_confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("source_name", "rationale", mode="before")
    @classmethod
    def coerce_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("match_confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


class ResolveDataSourceAgentRequest(BaseModel):
    instruction_intent: str = RESOLVE_DATA_SOURCE_INTENT
    user_raw_input: str
    infrastructure_catalog: list[dict[str, Any]] = Field(default_factory=list)

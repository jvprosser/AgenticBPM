"""Pydantic contracts for node task and group charter metadata."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _coerce_procedure_source(item: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy ``human_procedure`` payloads; persist ``user_procedure``."""
    if not item.get("user_procedure") and item.get("human_procedure"):
        item = {**item, "user_procedure": item["human_procedure"]}
    return item


class DataSourceProcedure(BaseModel):
    source_name: str = ""
    user_procedure: str = ""
    data_destinations: Optional[str] = ""
    is_intermediate: Optional[bool] = False
    qualified_name: Optional[str] = ""
    destination: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_procedure_source(data)
        return data

    @field_validator(
        "source_name",
        "user_procedure",
        "data_destinations",
        "qualified_name",
        "destination",
        mode="before",
    )
    @classmethod
    def coerce_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("is_intermediate", mode="before")
    @classmethod
    def coerce_intermediate(cls, value: Any) -> bool:
        if value is None:
            return False
        return bool(value)


class NodeTaskMetadata(BaseModel):
    input_parameter: Optional[str] = ""
    data_sources: list[DataSourceProcedure] = Field(default_factory=list)
    output_end_product: Optional[str] = ""
    final_activity: Optional[str] = ""
    user_validation_required: Optional[bool] = False

    @field_validator("input_parameter", "output_end_product", "final_activity", mode="before")
    @classmethod
    def coerce_text_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("user_validation_required", mode="before")
    @classmethod
    def coerce_validation_flag(cls, value: Any) -> bool:
        if value is None:
            return False
        return bool(value)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "NodeTaskMetadata":
        raw_sources = data.get("data_sources")
        sources: list[DataSourceProcedure] = []
        if isinstance(raw_sources, list):
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                normalized = _coerce_procedure_source(item)
                entry = DataSourceProcedure.model_validate(normalized)
                if (
                    entry.source_name
                    or entry.user_procedure
                    or entry.data_destinations
                    or entry.qualified_name
                    or entry.destination
                ):
                    sources.append(entry)
        return cls(
            input_parameter=data.get("input_parameter"),
            data_sources=sources,
            output_end_product=data.get("output_end_product"),
            final_activity=data.get("final_activity"),
            user_validation_required=data.get("user_validation_required"),
        )


class GroupMetadata(BaseModel):
    name: Optional[str] = None
    owner: Optional[str] = None
    description: Optional[str] = None


class AggregatedPipelineTask(BaseModel):
    id: str
    label: str


class AggregatedPipelineSource(BaseModel):
    source_name: str
    human_procedures: list[str] = Field(default_factory=list)


class AggregatedPipeline(BaseModel):
    scope_tasks: list[AggregatedPipelineTask] = Field(default_factory=list)
    data_sources: list[AggregatedPipelineSource] = Field(default_factory=list)
    output_products: list[str] = Field(default_factory=list)

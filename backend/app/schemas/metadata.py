"""Pydantic contracts for node task and group charter metadata."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _coerce_procedure_source(item: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy ``human_procedure`` payloads; persist ``user_procedure``."""
    if not item.get("user_procedure") and item.get("human_procedure"):
        item = {**item, "user_procedure": item["human_procedure"]}
    return item


class SubtaskItem(BaseModel):
    subtask_id: Optional[str] = None
    source_name: str = ""
    user_procedure: Optional[str] = ""
    human_procedure: Optional[str] = Field(default=None, exclude=True)
    data_destinations: Optional[str] = ""
    is_intermediate: Optional[bool] = False
    execution_mode: Optional[str] = "agent_automated"
    agent_endpoint_key: Optional[str] = ""
    input_parameter_mappings: Optional[dict[str, Any]] = Field(default_factory=dict)
    artifact_path_pattern: Optional[str] = ""
    qualified_name: Optional[str] = ""
    destination: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _coerce_procedure_source(data)
        return data

    @model_validator(mode="after")
    def sync_user_procedure(self) -> "SubtaskItem":
        if not self.user_procedure and self.human_procedure:
            self.user_procedure = self.human_procedure
        return self

    @field_validator("subtask_id", mode="before")
    @classmethod
    def coerce_subtask_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator(
        "source_name",
        "user_procedure",
        "data_destinations",
        "execution_mode",
        "agent_endpoint_key",
        "artifact_path_pattern",
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

    @field_validator("input_parameter_mappings", mode="before")
    @classmethod
    def coerce_mappings(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return {}


# Backward-compatible alias used by existing imports and API routes.
DataSourceProcedure = SubtaskItem


class NodeMetadataModel(BaseModel):
    input_parameter: Optional[str] = ""
    data_sources: list[SubtaskItem] = Field(default_factory=list)
    final_activity: Optional[str] = ""
    output_end_product: Optional[str] = ""
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
    def from_payload(cls, data: dict[str, Any]) -> "NodeMetadataModel":
        raw_sources = data.get("data_sources")
        sources: list[SubtaskItem] = []
        if isinstance(raw_sources, list):
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                normalized = _coerce_procedure_source(item)
                entry = SubtaskItem.model_validate(normalized)
                if _subtask_item_has_content(entry):
                    sources.append(entry)
        return cls(
            input_parameter=data.get("input_parameter"),
            data_sources=sources,
            output_end_product=data.get("output_end_product"),
            final_activity=data.get("final_activity"),
            user_validation_required=data.get("user_validation_required"),
        )


def _subtask_item_has_content(entry: SubtaskItem) -> bool:
    return bool(
        entry.subtask_id
        or entry.source_name
        or entry.user_procedure
        or entry.data_destinations
        or entry.qualified_name
        or entry.destination
        or entry.agent_endpoint_key
        or entry.artifact_path_pattern
        or entry.input_parameter_mappings
    )


# Backward-compatible alias used by existing imports and API routes.
NodeTaskMetadata = NodeMetadataModel


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

"""Pydantic contracts for node task and group charter metadata."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class DataSourceProcedure(BaseModel):
    source_name: str = ""
    human_procedure: str = ""

    @field_validator("source_name", "human_procedure", mode="before")
    @classmethod
    def coerce_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class NodeTaskMetadata(BaseModel):
    data_sources: list[DataSourceProcedure] = Field(default_factory=list)
    output_end_product: str = ""

    @field_validator("output_end_product", mode="before")
    @classmethod
    def coerce_output(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "NodeTaskMetadata":
        raw_sources = data.get("data_sources")
        sources: list[DataSourceProcedure] = []
        if isinstance(raw_sources, list):
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                entry = DataSourceProcedure.model_validate(item)
                if entry.source_name or entry.human_procedure:
                    sources.append(entry)
        return cls(
            data_sources=sources,
            output_end_product=data.get("output_end_product"),
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

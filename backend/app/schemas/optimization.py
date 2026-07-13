"""Unified platform payload for Cloudera Agent Studio optimization dispatch."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


SYSTEM_MANDATE = (
    "Analyze the human procedures and data inputs across the provided process map. "
    "Identify cross-lane data contiguities and group optimization targets into "
    "Assistant Zones. Strictly avoid nodes marked in the strategic_override list. "
    "Return a validated blueprint matching the AgentStudioWorkflow schema."
)

INSTRUCTION_INTENT = "OPTIMIZE_PROCESS_TOPOLOGY"


class OptimizationDataset(BaseModel):
    process_id: str
    graph_nodes: list[dict[str, Any]]
    graph_edges: list[dict[str, Any]]
    active_capabilities: dict[str, Any]
    strategic_overrides: list[str] = Field(default_factory=list)


class ExecuteAgentRequest(BaseModel):
    instruction_intent: str = INSTRUCTION_INTENT
    system_mandate: str = SYSTEM_MANDATE
    dataset: OptimizationDataset

"""Pydantic oracle for Step 5c — Agent Studio workflow proposals."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

WorkflowType = Literal["task", "conversational"]

BASELINE_TOOLS = frozenset({"code_execution", "vector_search", "pdf_text_extractor"})


class Agent(BaseModel):
    name: str
    role: str
    goal: str
    backstory: str
    tools: list[str] = Field(default_factory=list)


class Task(BaseModel):
    description: str
    agent: str


class AgentStudioWorkflow(BaseModel):
    workflow_name: str
    type: WorkflowType = "task"
    manager_agent: bool = False
    planning: bool = False
    agents: list[Agent] = Field(..., min_length=1)
    tasks: list[Task] = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str

    @model_validator(mode="after")
    def task_agents_must_exist(self) -> AgentStudioWorkflow:
        names = {a.name for a in self.agents}
        for task in self.tasks:
            if task.agent not in names:
                raise ValueError(
                    f"Task references unknown agent '{task.agent}'; "
                    f"known agents: {sorted(names)}"
                )
        return self

    def validate_tools_subset(
        self, *, discovery_active: bool, allowed_tools: set[str]
    ) -> None:
        """Tools on each agent must be drawn from the Step 5b capability matrix."""
        pool = allowed_tools if discovery_active else (BASELINE_TOOLS | allowed_tools)
        for agent in self.agents:
            bad = [t for t in agent.tools if t not in pool]
            if bad:
                raise ValueError(
                    f"Agent '{agent.name}' tools {bad} not in allowed set "
                    f"(discovery_active={discovery_active})."
                )


def validate_workflow_oracle(
    raw: dict[str, Any],
    *,
    discovery_active: bool,
    allowed_tools: set[str],
) -> AgentStudioWorkflow:
    """Schema validation + capability-matrix subset check before persistence."""
    workflow = AgentStudioWorkflow.model_validate(raw)
    workflow.validate_tools_subset(
        discovery_active=discovery_active, allowed_tools=allowed_tools
    )
    return workflow

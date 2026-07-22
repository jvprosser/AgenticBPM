"""Pydantic contracts for claim execution runtime records."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

ClaimInstanceStatus = Literal[
    "INITIATED",
    "PROCESSING",
    "AWAITING_USER_VALIDATION",
    "COMPLETED",
    "FAILED",
]

SubtaskExecutionStatus = Literal[
    "PENDING",
    "RUNNING",
    "AWAITING_USER_VALIDATION",
    "APPROVED",
    "FAILED",
]

CLAIM_INSTANCE_STATUSES: frozenset[str] = frozenset(
    {"INITIATED", "PROCESSING", "AWAITING_USER_VALIDATION", "COMPLETED", "FAILED"}
)

SUBTASK_EXECUTION_STATUSES: frozenset[str] = frozenset(
    {"PENDING", "RUNNING", "AWAITING_USER_VALIDATION", "APPROVED", "FAILED"}
)


class ClaimInstance(BaseModel):
    id: str
    claim_number: str
    process_id: str
    claim_parameters: dict[str, Any] = Field(default_factory=dict)
    status: ClaimInstanceStatus = "INITIATED"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SubtaskExecution(BaseModel):
    id: str
    claim_instance_id: str
    subtask_id: str
    subtask_name: Optional[str] = None
    status: SubtaskExecutionStatus = "PENDING"
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    artifact_path: Optional[str] = None
    output_payload: Optional[dict[str, Any]] = None
    validation_feedback: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ClaimInstanceCreate(BaseModel):
    process_id: str
    claim_number: str
    claim_parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("claim_number", mode="before")
    @classmethod
    def coerce_claim_number(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("claim_number is required.")
        return text


class SubtaskExecutionCreate(BaseModel):
    claim_instance_id: str
    subtask_id: str
    subtask_name: Optional[str] = None

    @field_validator("subtask_id", mode="before")
    @classmethod
    def coerce_subtask_id(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("subtask_id is required.")
        return text

"""CRUD helpers for claim execution instances and subtask execution logs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Optional

from .schemas.execution import (
    CLAIM_INSTANCE_STATUSES,
    SUBTASK_EXECUTION_STATUSES,
    ClaimInstance,
    SubtaskExecution,
)


def _parse_json_object(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_claim_instance(row: sqlite3.Row) -> ClaimInstance:
    return ClaimInstance(
        id=row["id"],
        claim_number=row["claim_number"],
        process_id=row["process_id"],
        claim_parameters=_parse_json_object(row["claim_parameters_json"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_subtask_execution(row: sqlite3.Row) -> SubtaskExecution:
    output_payload = _parse_json_object(row["output_payload_json"])
    return SubtaskExecution(
        id=row["id"],
        claim_instance_id=row["claim_instance_id"],
        subtask_id=row["subtask_id"],
        subtask_name=row["subtask_name"],
        status=row["status"],
        trace_id=row["trace_id"],
        session_id=row["session_id"],
        artifact_path=row["artifact_path"],
        output_payload=output_payload or None,
        validation_feedback=row["validation_feedback"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _assert_process_exists(conn: sqlite3.Connection, process_id: str) -> None:
    row = conn.execute("SELECT id FROM process WHERE id = ?", (process_id,)).fetchone()
    if row is None:
        raise ValueError(f"Process '{process_id}' not found.")


def _assert_claim_exists(conn: sqlite3.Connection, claim_id: str) -> None:
    row = conn.execute("SELECT id FROM claim_instance WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ValueError(f"Claim instance '{claim_id}' not found.")


def create_claim_instance(
    conn: sqlite3.Connection,
    process_id: str,
    claim_number: str,
    parameters_json: Optional[dict[str, Any]] = None,
) -> ClaimInstance:
    """Insert a new claim execution instance bound to a saved process."""
    _assert_process_exists(conn, process_id)
    claim_number = str(claim_number or "").strip()
    if not claim_number:
        raise ValueError("claim_number is required.")

    claim_id = uuid.uuid4().hex
    params_text = json.dumps(parameters_json or {}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO claim_instance "
        "(id, claim_number, process_id, claim_parameters_json, status) "
        "VALUES (?, ?, ?, ?, 'INITIATED')",
        (claim_id, claim_number, process_id, params_text),
    )
    row = conn.execute("SELECT * FROM claim_instance WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise RuntimeError("Failed to load claim instance after insert.")
    return _row_to_claim_instance(row)


def get_claim_instance(conn: sqlite3.Connection, claim_id: str) -> ClaimInstance:
    """Fetch a claim execution instance by primary key."""
    row = conn.execute("SELECT * FROM claim_instance WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ValueError(f"Claim instance '{claim_id}' not found.")
    return _row_to_claim_instance(row)


def update_claim_status(conn: sqlite3.Connection, claim_id: str, status: str) -> None:
    """Update claim lifecycle status and bump ``updated_at``."""
    normalized = str(status or "").strip().upper()
    if normalized not in CLAIM_INSTANCE_STATUSES:
        raise ValueError(f"Invalid claim status: {status}")
    _assert_claim_exists(conn, claim_id)
    conn.execute(
        "UPDATE claim_instance SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (normalized, claim_id),
    )


def create_subtask_execution(
    conn: sqlite3.Connection,
    claim_instance_id: str,
    subtask_id: str,
    subtask_name: Optional[str] = None,
) -> SubtaskExecution:
    """Create a pending subtask execution row for a claim instance."""
    _assert_claim_exists(conn, claim_instance_id)
    subtask_id = str(subtask_id or "").strip()
    if not subtask_id:
        raise ValueError("subtask_id is required.")

    execution_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO subtask_execution "
        "(id, claim_instance_id, subtask_id, subtask_name, status) "
        "VALUES (?, ?, ?, ?, 'PENDING')",
        (execution_id, claim_instance_id, subtask_id, subtask_name),
    )
    row = conn.execute(
        "SELECT * FROM subtask_execution WHERE id = ?", (execution_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to load subtask execution after insert.")
    return _row_to_subtask_execution(row)


def update_subtask_execution(
    conn: sqlite3.Connection,
    execution_id: str,
    status: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    artifact_path: Optional[str] = None,
    output_payload: Optional[dict[str, Any]] = None,
    validation_feedback: Optional[str] = None,
) -> None:
    """Persist workflow telemetry and output payload for a subtask execution."""
    normalized = str(status or "").strip().upper()
    if normalized not in SUBTASK_EXECUTION_STATUSES:
        raise ValueError(f"Invalid subtask execution status: {status}")

    row = conn.execute(
        "SELECT id FROM subtask_execution WHERE id = ?", (execution_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Subtask execution '{execution_id}' not found.")

    output_text = (
        json.dumps(output_payload, ensure_ascii=False) if output_payload is not None else None
    )
    conn.execute(
        "UPDATE subtask_execution SET "
        "status = ?, trace_id = ?, session_id = ?, artifact_path = ?, "
        "output_payload_json = ?, validation_feedback = ?, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (
            normalized,
            trace_id,
            session_id,
            artifact_path,
            output_text,
            validation_feedback,
            execution_id,
        ),
    )

"""Claim execution orchestration helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import config, crud_claims, metadata as metadata_svc
from .schemas.execution import ClaimInstance, SubtaskExecution


def _subtask_key(node_id: str, index: int, subtask_id: Optional[str]) -> str:
    if subtask_id and str(subtask_id).strip():
        return str(subtask_id).strip()
    return f"{node_id}:subtask:{index}"


def list_claim_instances(
    conn,
    process_id: str,
    *,
    target_node_id: Optional[str] = None,
) -> list[ClaimInstance]:
    rows = conn.execute(
        "SELECT * FROM claim_instance WHERE process_id = ? ORDER BY created_at DESC",
        (process_id,),
    ).fetchall()
    claims = [crud_claims._row_to_claim_instance(row) for row in rows]
    if not target_node_id:
        return claims
    filtered: list[ClaimInstance] = []
    for claim in claims:
        node_id = claim.claim_parameters.get("target_node_id")
        if node_id == target_node_id:
            filtered.append(claim)
    return filtered


def list_subtask_executions(conn, claim_instance_id: str) -> list[SubtaskExecution]:
    rows = conn.execute(
        "SELECT * FROM subtask_execution WHERE claim_instance_id = ? "
        "ORDER BY created_at ASC",
        (claim_instance_id,),
    ).fetchall()
    return [crud_claims._row_to_subtask_execution(row) for row in rows]


def get_claim_detail(conn, claim_id: str) -> dict[str, Any]:
    claim = crud_claims.get_claim_instance(conn, claim_id)
    executions = list_subtask_executions(conn, claim_id)
    return {
        "claim": claim.model_dump(),
        "subtask_executions": [item.model_dump() for item in executions],
    }


def run_claim_for_node(
    conn,
    process_id: str,
    target_node_id: str,
    claim_number: str,
    claim_parameters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a claim instance and pending subtask execution rows from node metadata."""
    meta = metadata_svc.get_node_task_metadata(conn, target_node_id)
    params = dict(claim_parameters or {})
    params["target_node_id"] = target_node_id
    if meta.get("input_parameter"):
        param_key = str(meta["input_parameter"]).strip()
        if param_key and param_key not in params:
            params[param_key] = claim_number

    claim = crud_claims.create_claim_instance(conn, process_id, claim_number, params)
    sources = meta.get("data_sources") or []
    executions: list[SubtaskExecution] = []

    if not sources:
        crud_claims.update_claim_status(conn, claim.id, "COMPLETED")
        claim = crud_claims.get_claim_instance(conn, claim.id)
        return get_claim_detail(conn, claim.id)

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            continue
        subtask_id = _subtask_key(target_node_id, index, source.get("subtask_id"))
        subtask_name = str(source.get("source_name") or subtask_id).strip() or subtask_id
        execution = crud_claims.create_subtask_execution(
            conn,
            claim.id,
            subtask_id,
            subtask_name,
        )
        executions.append(execution)

    if executions:
        first = executions[0]
        mode = str((sources[0] or {}).get("execution_mode") or "agent_automated")
        first_status = "RUNNING" if mode == "agent_automated" else "AWAITING_USER_VALIDATION"
        crud_claims.update_subtask_execution(conn, first.id, first_status)
        claim_status = "PROCESSING"
        if first_status == "AWAITING_USER_VALIDATION":
            claim_status = "AWAITING_USER_VALIDATION"
        crud_claims.update_claim_status(conn, claim.id, claim_status)

    return get_claim_detail(conn, claim.id)


def _sync_claim_status_from_executions(conn, claim_id: str) -> None:
    executions = list_subtask_executions(conn, claim_id)
    if not executions:
        crud_claims.update_claim_status(conn, claim_id, "COMPLETED")
        return

    statuses = [item.status for item in executions]
    if any(status == "FAILED" for status in statuses):
        crud_claims.update_claim_status(conn, claim_id, "FAILED")
        return
    if any(status == "AWAITING_USER_VALIDATION" for status in statuses):
        crud_claims.update_claim_status(conn, claim_id, "AWAITING_USER_VALIDATION")
        return
    if any(status == "RUNNING" for status in statuses):
        crud_claims.update_claim_status(conn, claim_id, "PROCESSING")
        return
    if all(status in {"APPROVED", "COMPLETED"} for status in statuses):
        crud_claims.update_claim_status(conn, claim_id, "COMPLETED")
        return
    crud_claims.update_claim_status(conn, claim_id, "PROCESSING")


def _advance_pipeline(conn, claim_id: str) -> None:
    executions = list_subtask_executions(conn, claim_id)
    for execution in executions:
        if execution.status == "PENDING":
            crud_claims.update_subtask_execution(conn, execution.id, "RUNNING")
            _sync_claim_status_from_executions(conn, claim_id)
            return
    _sync_claim_status_from_executions(conn, claim_id)


def approve_subtask_execution(
    conn,
    execution_id: str,
    validation_feedback: Optional[str] = None,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM subtask_execution WHERE id = ?", (execution_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Subtask execution '{execution_id}' not found.")

    execution = crud_claims._row_to_subtask_execution(row)
    crud_claims.update_subtask_execution(
        conn,
        execution_id,
        "APPROVED",
        validation_feedback=validation_feedback,
    )
    _advance_pipeline(conn, execution.claim_instance_id)
    return get_claim_detail(conn, execution.claim_instance_id)


def reject_subtask_execution(
    conn,
    execution_id: str,
    validation_feedback: Optional[str] = None,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM subtask_execution WHERE id = ?", (execution_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Subtask execution '{execution_id}' not found.")

    execution = crud_claims._row_to_subtask_execution(row)
    crud_claims.update_subtask_execution(
        conn,
        execution_id,
        "RUNNING",
        trace_id=None,
        session_id=None,
        artifact_path=None,
        output_payload=None,
        validation_feedback=validation_feedback,
    )
    crud_claims.update_claim_status(conn, execution.claim_instance_id, "PROCESSING")
    return get_claim_detail(conn, execution.claim_instance_id)


def read_execution_artifact(conn, execution_id: str) -> Any:
    row = conn.execute(
        "SELECT artifact_path, output_payload_json FROM subtask_execution WHERE id = ?",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Subtask execution '{execution_id}' not found.")

    artifact_path = row["artifact_path"]
    if artifact_path:
        path = Path(str(artifact_path))
        if not path.is_absolute():
            path = config.REPO_ROOT / path
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw

    payload = crud_claims._parse_json_object(row["output_payload_json"])
    if payload:
        return payload
    raise ValueError("No artifact is available for this subtask execution.")

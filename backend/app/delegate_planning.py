"""Proxy node task metadata to the Cloudera TaskPlanner workflow deployment."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, field_validator

from . import config, db, discovery, ingestion, metadata as metadata_svc

logger = logging.getLogger(__name__)

_CREATE_SESSION_PATH = "/api/workflow/createSession"
_KICKOFF_PATH = "/api/workflow/kickoff"
_EVENTS_PATH = "/api/workflow/events"


class SubtaskRow(BaseModel):
    source_name: str = ""
    human_procedure: str = ""
    data_destinations: Optional[str] = ""
    is_intermediate: Optional[bool] = False

    @field_validator("source_name", "human_procedure", "data_destinations", mode="before")
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


class DelegatePlanningRequest(BaseModel):
    process_instance_id: str
    target_node_id: str
    final_activity: str = ""
    finalized_artifact: str = ""
    subtasks: list[SubtaskRow] = Field(default_factory=list)

    @field_validator("final_activity", "finalized_artifact", mode="before")
    @classmethod
    def coerce_text_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


def _resolve_workflow_token(user_token: Optional[str]) -> tuple[Optional[str], str]:
    workflow_key = config.WORKFLOW_API_KEY.strip()
    if workflow_key:
        return workflow_key, "env:WORKFLOW_API_KEY"
    return discovery.resolve_platform_token(user_token)


def _workflow_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _metadata_context(body: DelegatePlanningRequest) -> str:
    metadata_object = {
        "process_instance_id": body.process_instance_id,
        "target_node_id": body.target_node_id,
        "final_activity": body.final_activity,
        "finalized_artifact": body.finalized_artifact,
        "data_sources": [row.model_dump() for row in body.subtasks],
    }
    return json.dumps(metadata_object, ensure_ascii=False)


def _build_kickoff_inputs(body: DelegatePlanningRequest) -> dict[str, str]:
    return {
        "user_input": config.WORKFLOW_DELEGATE_USER_INPUT,
        "context": _metadata_context(body),
    }


def _persist_node_metadata(body: DelegatePlanningRequest) -> dict[str, Any]:
    payload = {
        "data_sources": [row.model_dump() for row in body.subtasks],
        "output_end_product": body.finalized_artifact,
        "final_activity": body.final_activity,
    }
    with db.get_conn() as conn:
        if ingestion.get_graph(conn, body.process_instance_id) is None:
            raise ValueError("Process not found.")
        saved = metadata_svc.upsert_metadata(
            conn,
            body.process_instance_id,
            "node",
            body.target_node_id,
            payload,
        )
        db.touch_process_updated_at(conn, body.process_instance_id)
    return saved


async def _workflow_post(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = f"{config.WORKFLOW_BASE_URL.rstrip('/')}{path}"
    response = await client.post(
        url,
        headers=_workflow_headers(token),
        json=payload if payload is not None else {},
    )
    response.raise_for_status()
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise ValueError(f"Unexpected JSON shape from workflow POST {path}")
    return parsed


async def _create_workflow_session(
    client: httpx.AsyncClient,
    token: str,
) -> tuple[str, Optional[str]]:
    data = await _workflow_post(client, _CREATE_SESSION_PATH, token, {})
    session_id = data.get("session_id")
    if not session_id:
        raise ValueError("Workflow createSession response missing session_id.")
    return str(session_id), data.get("session_directory")


async def _kickoff_workflow(
    client: httpx.AsyncClient,
    token: str,
    body: DelegatePlanningRequest,
) -> str:
    payload = {"inputs": _build_kickoff_inputs(body)}
    data = await _workflow_post(client, _KICKOFF_PATH, token, payload)
    trace_id = data.get("trace_id")
    if not trace_id:
        raise ValueError("Workflow kickoff response missing trace_id.")
    return str(trace_id)


async def _preview_workflow_events(
    client: httpx.AsyncClient,
    token: str,
    trace_id: str,
) -> list[Any]:
    url = f"{config.WORKFLOW_BASE_URL.rstrip('/')}{_EVENTS_PATH}"
    preview: list[Any] = []
    async with client.stream(
        "GET",
        url,
        headers=_workflow_headers(token),
        params={"trace_id": trace_id},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            try:
                preview.append(json.loads(line))
            except json.JSONDecodeError:
                preview.append(line)
            if len(preview) >= config.WORKFLOW_EVENTS_PREVIEW_LIMIT:
                break
    return preview


async def submit_planning_request(
    body: DelegatePlanningRequest,
    user_token: Optional[str],
) -> dict[str, Any]:
    """Persist task metadata locally, then run the Cloudera workflow delegation flow."""
    token, token_source = _resolve_workflow_token(user_token)
    if not token:
        return {
            "ok": False,
            "toast_message": (
                "Cloudera authentication required. Set WORKFLOW_API_KEY or "
                "CLOUDERA_AI_TOKEN on the backend, or sign in to Cloudera AI."
            ),
            "detail": "no_auth_token",
        }

    try:
        saved_metadata = _persist_node_metadata(body)
    except ValueError as exc:
        return {
            "ok": False,
            "toast_message": str(exc),
            "detail": "process_not_found",
        }

    kickoff_inputs = _build_kickoff_inputs(body)
    logger.info(
        "TaskPlanner workflow dispatch process=%s node=%s subtasks=%d base_url=%s",
        body.process_instance_id,
        body.target_node_id,
        len(body.subtasks),
        config.WORKFLOW_BASE_URL,
    )
    logger.debug("TaskPlanner kickoff inputs=%s", kickoff_inputs)

    try:
        async with httpx.AsyncClient(
            timeout=config.WORKFLOW_TIMEOUT_S,
            verify=config.WORKFLOW_SSL_VERIFY,
        ) as client:
            session_id, session_directory = await _create_workflow_session(client, token)
            trace_id = await _kickoff_workflow(client, token, body)
            try:
                events_preview = await _preview_workflow_events(client, token, trace_id)
            except Exception as exc:
                logger.warning("Workflow events preview unavailable: %s", exc)
                events_preview = []

        return {
            "ok": True,
            "status": "dispatched",
            "toast_message": f"Workflow started. Trace ID: {trace_id}",
            "process_instance_id": body.process_instance_id,
            "target_node_id": body.target_node_id,
            "metadata": saved_metadata,
            "workflow_base_url": config.WORKFLOW_BASE_URL,
            "token_source": token_source,
            "session_id": session_id,
            "session_directory": session_directory,
            "trace_id": trace_id,
            "workflow_events": events_preview,
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            detail_body = exc.response.json()
            message = detail_body.get("reason") or detail_body.get("error") or str(detail_body)[:200]
        except Exception:
            message = exc.response.text[:200]
        logger.warning("TaskPlanner workflow HTTP %s: %s", status, message)
        return {
            "ok": False,
            "toast_message": (
                f"Cloudera workflow error (HTTP {status}). "
                "Confirm WORKFLOW_BASE_URL and WORKFLOW_API_KEY are configured."
            ),
            "detail": f"gateway_http_{status}",
            "gateway_message": message,
            "metadata": saved_metadata,
        }
    except httpx.TimeoutException:
        logger.warning("TaskPlanner workflow timeout after %.0fs", config.WORKFLOW_TIMEOUT_S)
        return {
            "ok": False,
            "toast_message": (
                f"Cloudera workflow timed out after {config.WORKFLOW_TIMEOUT_S:.0f}s. "
                "Try again or check workflow deployment load."
            ),
            "detail": "gateway_timeout",
            "metadata": saved_metadata,
        }
    except httpx.RequestError as exc:
        logger.warning("TaskPlanner workflow unreachable: %s", exc)
        return {
            "ok": False,
            "toast_message": (
                "Cloudera workflow is unreachable. Verify WORKFLOW_BASE_URL and "
                "network connectivity."
            ),
            "detail": "gateway_unreachable",
            "metadata": saved_metadata,
        }
    except ValueError as exc:
        logger.warning("TaskPlanner workflow response invalid: %s", exc)
        return {
            "ok": False,
            "toast_message": str(exc),
            "detail": "gateway_invalid_response",
            "metadata": saved_metadata,
        }
    except Exception as exc:
        logger.exception("TaskPlanner workflow dispatch failed")
        return {
            "ok": False,
            "toast_message": "Cloudera workflow encountered an unexpected error during dispatch.",
            "detail": f"gateway_error:{type(exc).__name__}",
            "metadata": saved_metadata,
        }

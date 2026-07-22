"""Proxy node task metadata to the Cloudera TaskPlanner workflow deployment."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, field_validator

from . import config, db, discovery, ingestion, metadata as metadata_svc

logger = logging.getLogger(__name__)

_CREATE_SESSION_PATH = "/api/workflow/createSession"
_KICKOFF_PATH = "/api/workflow/kickoff"
_EVENTS_PATH = "/api/workflow/events"
_FILE_UPLOAD_PATH = "/api/file/upload"

_COMPLETION_STATUSES = frozenset(
    {"complete", "completed", "done", "finished", "success", "succeeded"}
)
_ENRICHED_KEYS = (
    "enriched_json",
    "enrichedJson",
    "enriched_json_object",
    "enriched",
    "Enriched JSON Object",
)


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
    if config.WORKFLOW_API_KEY.strip():
        if os.environ.get("WORKFLOW_API_KEY", "").strip():
            return config.WORKFLOW_API_KEY.strip(), "env:WORKFLOW_API_KEY"
        return config.WORKFLOW_API_KEY.strip(), "env:CDSW_APIV2_KEY"
    return discovery.resolve_platform_token(user_token)


def _workflow_headers(token: str, *, json_body: bool = True) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


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


def _event_fingerprint(event: Any) -> str:
    if isinstance(event, dict):
        return json.dumps(event, sort_keys=True, ensure_ascii=False)
    return str(event)


def _nested_enriched_value(value: Any) -> Any | None:
    if isinstance(value, dict):
        for key in _ENRICHED_KEYS:
            if key in value and value[key] not in (None, "", {}):
                return value[key]
        for nest_key in ("output", "result", "data", "payload", "content"):
            nested = _nested_enriched_value(value.get(nest_key))
            if nested is not None:
                return nested
    return None


def _extract_enriched_json(events: list[Any]) -> Any | None:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        enriched = _nested_enriched_value(event)
        if enriched is not None:
            return enriched
    return None


def _event_signals_completion(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    for key in ("status", "state", "event", "type", "phase"):
        raw = event.get(key)
        if raw is None:
            continue
        if str(raw).lower() in _COMPLETION_STATUSES:
            return True
    if event.get("completed") is True or event.get("is_complete") is True:
        return True
    if _nested_enriched_value(event) is not None:
        return True
    return False


def _workflow_completed(events: list[Any]) -> bool:
    if not events:
        return False
    if _extract_enriched_json(events) is not None:
        return True
    return any(_event_signals_completion(event) for event in events)


def _parse_event_line(line: str) -> Any | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if stripped.startswith("data:"):
        stripped = stripped[5:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _parse_events_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "workflow_events", "data"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return nested
        return [payload]
    return [payload]


async def _fetch_workflow_events_batch(
    client: httpx.AsyncClient,
    token: str,
    trace_id: str,
) -> list[Any]:
    url = f"{config.WORKFLOW_BASE_URL.rstrip('/')}{_EVENTS_PATH}"
    headers = _workflow_headers(token, json_body=False)

    try:
        async with client.stream(
            "GET",
            url,
            headers=headers,
            params={"trace_id": trace_id},
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                events: list[Any] = []
                async for line in response.aiter_lines():
                    parsed = _parse_event_line(line)
                    if parsed is not None:
                        events.append(parsed)
                return events

            raw = (await response.aread()).decode("utf-8", errors="replace").strip()
            if not raw:
                return []
            try:
                return _parse_events_payload(json.loads(raw))
            except json.JSONDecodeError:
                return [
                    parsed
                    for line in raw.splitlines()
                    if (parsed := _parse_event_line(line)) is not None
                ]
    except httpx.HTTPStatusError:
        raise
    except httpx.RequestError:
        raise


async def _poll_workflow_until_complete(
    client: httpx.AsyncClient,
    token: str,
    trace_id: str,
) -> tuple[list[Any], Any | None, bool]:
    deadline = time.monotonic() + config.WORKFLOW_POLL_TIMEOUT_S
    seen: set[str] = set()
    all_events: list[Any] = []

    while time.monotonic() < deadline:
        batch = await _fetch_workflow_events_batch(client, token, trace_id)
        for event in batch:
            fingerprint = _event_fingerprint(event)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            all_events.append(event)

        enriched = _extract_enriched_json(all_events)
        if _workflow_completed(all_events):
            logger.info(
                "Workflow poll complete trace_id=%s events=%d enriched=%s",
                trace_id,
                len(all_events),
                enriched is not None,
            )
            return all_events, enriched, True

        await asyncio.sleep(config.WORKFLOW_POLL_INTERVAL_S)

    logger.warning(
        "Workflow poll timed out trace_id=%s after %.0fs events=%d",
        trace_id,
        config.WORKFLOW_POLL_TIMEOUT_S,
        len(all_events),
    )
    return all_events, _extract_enriched_json(all_events), False


def _save_local_artifact(
    body: DelegatePlanningRequest,
    trace_id: str,
    enriched_json: Any,
) -> str:
    config.ensure_dirs()
    artifact_dir = (
        config.ARTIFACT_DIR / body.process_instance_id / body.target_node_id
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{trace_id}_enriched.json"
    path = artifact_dir / filename
    path.write_text(
        json.dumps(enriched_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


async def _upload_enriched_artifact(
    client: httpx.AsyncClient,
    token: str,
    session_id: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    url = f"{config.WORKFLOW_BASE_URL.rstrip('/')}{_FILE_UPLOAD_PATH}"
    response = await client.post(
        url,
        params={"session_id": session_id},
        headers=_workflow_headers(token, json_body=False),
        files={"file": (filename, content, "application/json")},
    )
    response.raise_for_status()
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise ValueError("Unexpected JSON shape from workflow file upload.")
    return parsed


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
                "Cloudera authentication required. Set WORKFLOW_API_KEY / CDSW_APIV2_KEY "
                "or CLOUDERA_AI_TOKEN on the backend, or sign in to Cloudera AI."
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
            logger.info("Polling workflow events trace_id=%s", trace_id)
            workflow_events, enriched_json, poll_completed = await _poll_workflow_until_complete(
                client, token, trace_id
            )

            local_artifact_path: str | None = None
            artifact_upload: dict[str, Any] | None = None
            artifact_filename = f"{trace_id}_enriched.json"

            if enriched_json is not None:
                local_artifact_path = _save_local_artifact(body, trace_id, enriched_json)
                artifact_bytes = Path(local_artifact_path).read_bytes()
                try:
                    artifact_upload = await _upload_enriched_artifact(
                        client,
                        token,
                        session_id,
                        artifact_filename,
                        artifact_bytes,
                    )
                    logger.info(
                        "Uploaded enriched artifact session_id=%s file_path=%s",
                        session_id,
                        artifact_upload.get("file_path"),
                    )
                except Exception as exc:
                    logger.warning("Workflow artifact upload failed: %s", exc)
                    artifact_upload = {
                        "success": False,
                        "message": str(exc),
                    }

        if not poll_completed:
            return {
                "ok": False,
                "toast_message": (
                    f"Workflow polling timed out after {config.WORKFLOW_POLL_TIMEOUT_S:.0f}s "
                    f"for trace {trace_id}."
                ),
                "detail": "poll_timeout",
                "metadata": saved_metadata,
                "trace_id": trace_id,
                "session_id": session_id,
                "workflow_events": workflow_events,
                "enriched_json": enriched_json,
                "local_artifact_path": local_artifact_path,
                "artifact_upload": artifact_upload,
            }

        if enriched_json is None:
            return {
                "ok": False,
                "toast_message": (
                    "Workflow completed but no Enriched JSON Object was found in the event stream."
                ),
                "detail": "enriched_json_missing",
                "metadata": saved_metadata,
                "trace_id": trace_id,
                "session_id": session_id,
                "workflow_events": workflow_events,
                "local_artifact_path": local_artifact_path,
                "artifact_upload": artifact_upload,
            }

        upload_path = (
            artifact_upload.get("file_path") if isinstance(artifact_upload, dict) else None
        )
        toast = "Workflow completed."
        if upload_path:
            toast = f"Workflow completed. Artifact uploaded to {upload_path}."

        return {
            "ok": True,
            "status": "completed",
            "toast_message": toast,
            "process_instance_id": body.process_instance_id,
            "target_node_id": body.target_node_id,
            "metadata": saved_metadata,
            "workflow_base_url": config.WORKFLOW_BASE_URL,
            "token_source": token_source,
            "session_id": session_id,
            "session_directory": session_directory,
            "trace_id": trace_id,
            "workflow_events": workflow_events,
            "enriched_json": enriched_json,
            "local_artifact_path": local_artifact_path,
            "artifact_upload": artifact_upload,
            "poll_completed": poll_completed,
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

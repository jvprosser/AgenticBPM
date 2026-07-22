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
from pydantic import BaseModel, Field, field_validator, model_validator

from . import config, db, discovery, ingestion, metadata as metadata_svc

logger = logging.getLogger(__name__)

_CREATE_SESSION_PATH = "/api/workflow/createSession"
_KICKOFF_PATH = "/api/workflow/kickoff"
_EVENTS_PATH = "/api/workflow/events"
_FILE_UPLOAD_PATH = "/api/file/upload"

_COMPLETION_STATUSES = frozenset(
    {"complete", "completed", "done", "finished", "success", "succeeded"}
)
_CREW_KICKOFF_COMPLETED_TYPE = "crew_kickoff_completed"
_ENRICHED_KEYS = (
    "enriched_json",
    "enrichedJson",
    "enriched_json_object",
    "enriched",
    "Enriched JSON Object",
)

# In-memory workflow poll sessions keyed by trace_id (browser-driven polling).
_poll_sessions: dict[str, dict[str, Any]] = {}


class SubtaskRow(BaseModel):
    source_name: str = ""
    user_procedure: str = ""
    data_destinations: Optional[str] = ""
    is_intermediate: Optional[bool] = False
    qualified_name: Optional[str] = ""
    destination: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("user_procedure") and data.get("human_procedure"):
            data = {**data, "user_procedure": data["human_procedure"]}
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


class DelegatePlanningRequest(BaseModel):
    process_instance_id: str
    target_node_id: str
    input_parameter: str = ""
    final_activity: str = ""
    finalized_artifact: str = ""
    user_validation_required: bool = False
    subtasks: list[SubtaskRow] = Field(default_factory=list)

    @field_validator("input_parameter", "final_activity", "finalized_artifact", mode="before")
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
        "input_parameter": body.input_parameter,
        "final_activity": body.final_activity,
        "finalized_artifact": body.finalized_artifact,
        "user_validation_required": body.user_validation_required,
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
        "input_parameter": body.input_parameter,
        "data_sources": [row.model_dump() for row in body.subtasks],
        "output_end_product": body.finalized_artifact,
        "final_activity": body.final_activity,
        "user_validation_required": body.user_validation_required,
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


_BLOCKED_AGENT_OUTPUT_KEYS = frozenset({"inputs", "input", "payload", "metadata", "context"})


def _extract_json_from_markdown_text(text: str) -> Any | None:
    import re

    stripped = text.strip()
    if not stripped:
        return None
    fenced_blocks = re.findall(r"```(?:json)?\s*\n?([\s\S]*?)```", stripped, flags=re.IGNORECASE)
    for block in reversed(fenced_blocks):
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _is_structured_text_object(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(value.get("objective") or value.get("steps") or value.get("key_considerations"))


def _source_has_catalog_metadata(source: Any) -> bool:
    if not isinstance(source, dict):
        return False
    for key in (
        "qualified_name",
        "business_terms",
        "classifications",
        "asset_type",
        "owner",
        "description",
        "destination",
    ):
        if source.get(key) not in (None, "", {}, []):
            return True
    return False


def _looks_like_augmented_metadata(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if _is_structured_text_object(value.get("final_activity")):
        return True
    sources = value.get("data_sources") or value.get("subtasks")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            procedure = source.get("user_procedure") or source.get("human_procedure")
            if _is_structured_text_object(procedure):
                return True
            if _source_has_catalog_metadata(source):
                return True
    return False


def _extract_agent_output_from_value(value: Any, *, depth: int = 0) -> Any | None:
    if value is None or depth > 8:
        return None
    if isinstance(value, str):
        parsed = _extract_json_from_markdown_text(value)
        if _looks_like_augmented_metadata(parsed):
            return parsed
        return None
    if isinstance(value, dict):
        if _looks_like_augmented_metadata(value):
            return value
        preferred_keys = (
            "agent_output",
            "enriched_json",
            "enrichedJson",
            "output",
            "result",
            "content",
            "message",
            "data",
            "answer",
            "final_answer",
            "finalAnswer",
            "final_result",
        )
        for key in preferred_keys:
            if key not in value:
                continue
            found = _extract_agent_output_from_value(value.get(key), depth=depth + 1)
            if found is not None:
                return found
        for key, nested in value.items():
            if key in _BLOCKED_AGENT_OUTPUT_KEYS:
                continue
            found = _extract_agent_output_from_value(nested, depth=depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for item in reversed(value):
            found = _extract_agent_output_from_value(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def _extract_agent_output(events: list[Any]) -> Any | None:
    """Return the TaskPlanner agent's augmented metadata payload from workflow events."""
    completed = _find_crew_kickoff_completed(events)
    if completed:
        found = _extract_agent_output_from_value(completed)
        if found is not None:
            return found
    for event in reversed(events):
        found = _extract_agent_output_from_value(event)
        if found is not None:
            return found
    legacy = _extract_enriched_json(events)
    if _looks_like_augmented_metadata(legacy):
        return legacy
    return None


def _find_crew_kickoff_completed(events: list[Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        if isinstance(event, dict) and event.get("type") == _CREW_KICKOFF_COMPLETED_TYPE:
            return event
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
    return _find_crew_kickoff_completed(events) is not None


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

        enriched = _extract_agent_output(all_events)
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
    return all_events, _extract_agent_output(all_events), False


def _merge_event_batch(state: dict[str, Any], batch: list[Any]) -> None:
    seen = set(state["seen"])
    for event in batch:
        fingerprint = _event_fingerprint(event)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        state["seen"].append(fingerprint)
        state["events"].append(event)
    state["seen"] = list(seen)


def _build_completed_response(state: dict[str, Any], token_source: str) -> dict[str, Any]:
    body = DelegatePlanningRequest.model_validate(state["body"])
    enriched_json = state.get("enriched_json")
    artifact_upload = state.get("artifact_upload")
    upload_path = (
        artifact_upload.get("file_path") if isinstance(artifact_upload, dict) else None
    )
    toast = "Workflow completed."
    if upload_path:
        toast = f"Workflow completed. Artifact uploaded to {upload_path}."

    final_result = state.get("final_result")
    agent_output = state.get("agent_output") or enriched_json
    return {
        "ok": True,
        "status": "completed",
        "toast_message": toast,
        "process_instance_id": body.process_instance_id,
        "target_node_id": body.target_node_id,
        "metadata": state.get("metadata"),
        "workflow_base_url": config.WORKFLOW_BASE_URL,
        "token_source": token_source,
        "session_id": state["session_id"],
        "session_directory": state.get("session_directory"),
        "trace_id": state["trace_id"],
        "final_result": final_result,
        "agent_output": agent_output,
        "output": agent_output,
        "enriched_json": agent_output,
        "workflow_events": state.get("events"),
        "local_artifact_path": state.get("local_artifact_path"),
        "artifact_upload": artifact_upload,
        "poll_completed": True,
        "poll_count": state.get("poll_count", 0),
    }


async def _finalize_completed_session(
    client: httpx.AsyncClient,
    token: str,
    state: dict[str, Any],
) -> None:
    body = DelegatePlanningRequest.model_validate(state["body"])
    trace_id = state["trace_id"]
    state["final_result"] = _find_crew_kickoff_completed(state["events"])
    agent_output = _extract_agent_output(state["events"])
    state["agent_output"] = agent_output
    state["enriched_json"] = agent_output
    state["completed"] = True

    if agent_output is None:
        return

    local_artifact_path = _save_local_artifact(body, trace_id, agent_output)
    state["local_artifact_path"] = local_artifact_path
    artifact_filename = f"{trace_id}_enriched.json"
    try:
        state["artifact_upload"] = await _upload_enriched_artifact(
            client,
            token,
            state["session_id"],
            artifact_filename,
            Path(local_artifact_path).read_bytes(),
        )
    except Exception as exc:
        logger.warning("Workflow artifact upload failed: %s", exc)
        state["artifact_upload"] = {"success": False, "message": str(exc)}


async def start_planning_request(
    body: DelegatePlanningRequest,
    user_token: Optional[str],
) -> dict[str, Any]:
    """Persist metadata, create session, kickoff workflow; polling continues via GET."""
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
        "TaskPlanner workflow kickoff process=%s node=%s subtasks=%d base_url=%s",
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

        _poll_sessions[trace_id] = {
            "trace_id": trace_id,
            "session_id": session_id,
            "session_directory": session_directory,
            "body": body.model_dump(),
            "metadata": saved_metadata,
            "token_source": token_source,
            "events": [],
            "seen": [],
            "started_at": time.monotonic(),
            "completed": False,
            "poll_count": 0,
            "enriched_json": None,
            "local_artifact_path": None,
            "artifact_upload": None,
        }

        return {
            "ok": True,
            "status": "running",
            "toast_message": f"Workflow started. Trace ID: {trace_id}",
            "process_instance_id": body.process_instance_id,
            "target_node_id": body.target_node_id,
            "metadata": saved_metadata,
            "workflow_base_url": config.WORKFLOW_BASE_URL,
            "token_source": token_source,
            "session_id": session_id,
            "session_directory": session_directory,
            "trace_id": trace_id,
            "poll_completed": False,
        }
    except httpx.HTTPStatusError as exc:
        return _workflow_http_error(exc, saved_metadata)
    except httpx.TimeoutException:
        return _workflow_timeout_error(saved_metadata)
    except httpx.RequestError as exc:
        return _workflow_unreachable_error(exc, saved_metadata)
    except ValueError as exc:
        return _workflow_invalid_error(exc, saved_metadata)
    except Exception as exc:
        logger.exception("TaskPlanner workflow kickoff failed")
        return _workflow_unexpected_error(exc, saved_metadata)


async def poll_planning_status(
    trace_id: str,
    user_token: Optional[str],
) -> dict[str, Any]:
    """Fetch one workflow events batch; browser calls this repeatedly until complete."""
    state = _poll_sessions.get(trace_id)
    if state is None:
        return {
            "ok": False,
            "toast_message": f"Unknown or expired workflow trace_id: {trace_id}",
            "detail": "unknown_trace_id",
        }

    token_source = state.get("token_source", "unknown")
    if state.get("completed"):
        return _build_completed_response(state, token_source)

    elapsed = time.monotonic() - float(state["started_at"])
    if elapsed > config.WORKFLOW_POLL_TIMEOUT_S:
        return {
            "ok": False,
            "status": "timeout",
            "toast_message": (
                f"Workflow polling timed out after {config.WORKFLOW_POLL_TIMEOUT_S:.0f}s "
                f"for trace {trace_id}."
            ),
            "detail": "poll_timeout",
            "trace_id": trace_id,
            "session_id": state["session_id"],
            "workflow_events": state["events"],
            "enriched_json": state.get("enriched_json"),
            "poll_completed": False,
            "poll_count": state.get("poll_count", 0),
        }

    token, _ = _resolve_workflow_token(user_token)
    if not token:
        return {
            "ok": False,
            "toast_message": "Cloudera authentication required for workflow polling.",
            "detail": "no_auth_token",
            "trace_id": trace_id,
        }

    state["poll_count"] = int(state.get("poll_count", 0)) + 1
    logger.info(
        "Workflow poll #%s trace_id=%s -> GET %s",
        state["poll_count"],
        trace_id,
        _EVENTS_PATH,
    )

    try:
        async with httpx.AsyncClient(
            timeout=config.WORKFLOW_TIMEOUT_S,
            verify=config.WORKFLOW_SSL_VERIFY,
        ) as client:
            batch = await _fetch_workflow_events_batch(client, token, trace_id)
            _merge_event_batch(state, batch)

            if _workflow_completed(state["events"]):
                await _finalize_completed_session(client, token, state)
                final_result = state.get("final_result")
                agent_output = state.get("agent_output")
                if final_result is None and agent_output is None:
                    return {
                        "ok": False,
                        "status": "completed",
                        "toast_message": (
                            "Workflow completed but no crew_kickoff_completed event was found."
                        ),
                        "detail": "final_result_missing",
                        "trace_id": trace_id,
                        "session_id": state["session_id"],
                        "poll_completed": True,
                        "poll_count": state["poll_count"],
                    }
                return _build_completed_response(state, token_source)

            return {
                "ok": True,
                "status": "running",
                "toast_message": "Waiting for workflow to complete…",
                "trace_id": trace_id,
                "session_id": state["session_id"],
                "poll_completed": False,
                "poll_count": state["poll_count"],
            }
    except httpx.HTTPStatusError as exc:
        err = _workflow_http_error(exc, state.get("metadata"))
        err["trace_id"] = trace_id
        err["status"] = "error"
        return err
    except httpx.TimeoutException:
        err = _workflow_timeout_error(state.get("metadata"))
        err["trace_id"] = trace_id
        err["status"] = "error"
        return err
    except httpx.RequestError as exc:
        err = _workflow_unreachable_error(exc, state.get("metadata"))
        err["trace_id"] = trace_id
        err["status"] = "error"
        return err
    except ValueError as exc:
        err = _workflow_invalid_error(exc, state.get("metadata"))
        err["trace_id"] = trace_id
        err["status"] = "error"
        return err
    except Exception as exc:
        logger.exception("TaskPlanner workflow poll failed trace_id=%s", trace_id)
        err = _workflow_unexpected_error(exc, state.get("metadata"))
        err["trace_id"] = trace_id
        err["status"] = "error"
        return err


def _workflow_http_error(
    exc: httpx.HTTPStatusError, saved_metadata: Any
) -> dict[str, Any]:
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


def _workflow_timeout_error(saved_metadata: Any) -> dict[str, Any]:
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


def _workflow_unreachable_error(exc: httpx.RequestError, saved_metadata: Any) -> dict[str, Any]:
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


def _workflow_invalid_error(exc: ValueError, saved_metadata: Any) -> dict[str, Any]:
    logger.warning("TaskPlanner workflow response invalid: %s", exc)
    return {
        "ok": False,
        "toast_message": str(exc),
        "detail": "gateway_invalid_response",
        "metadata": saved_metadata,
    }


def _workflow_unexpected_error(exc: Exception, saved_metadata: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "toast_message": "Cloudera workflow encountered an unexpected error during dispatch.",
        "detail": f"gateway_error:{type(exc).__name__}",
        "metadata": saved_metadata,
    }


async def submit_planning_request(
    body: DelegatePlanningRequest,
    user_token: Optional[str],
) -> dict[str, Any]:
    """Blocking delegation for callers that want a single round-trip."""
    started = await start_planning_request(body, user_token)
    if not started.get("ok"):
        return started
    trace_id = started.get("trace_id")
    if not trace_id:
        return started

    deadline = time.monotonic() + config.WORKFLOW_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        polled = await poll_planning_status(trace_id, user_token)
        if polled.get("status") in {"completed", "timeout", "error"} or not polled.get("ok"):
            return polled
        await asyncio.sleep(config.WORKFLOW_POLL_INTERVAL_S)

    return await poll_planning_status(trace_id, user_token)


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

"""Proxy node task metadata to the Cloudera TaskPlanner Agent (executeAgent gateway)."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, field_validator

from . import config, db, discovery, ingestion, metadata as metadata_svc

logger = logging.getLogger(__name__)

_EXECUTE_TIMEOUT_S = float(os.environ.get("AGENT_EXECUTE_TIMEOUT_S", "60"))


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


def _build_execute_body(body: DelegatePlanningRequest) -> dict[str, Any]:
    return {
        "agent_routing_target": "cloudera-task-planner",
        "instruction_intent": "COMPILE_AUTOMATION_DESIGN",
        "payload": {
            "final_activity_routine": body.final_activity,
            "final_artifact_target": body.finalized_artifact,
            "subtask_manifest": [row.model_dump() for row in body.subtasks],
        },
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


async def submit_planning_request(
    body: DelegatePlanningRequest,
    user_token: Optional[str],
) -> dict[str, Any]:
    """Persist task metadata locally, then proxy to Cloudera Agent Studio."""
    token, token_source = discovery.resolve_platform_token(user_token)
    if not token:
        return {
            "ok": False,
            "toast_message": (
                "Cloudera authentication required. Sign in to Cloudera AI or set "
                "CLOUDERA_AI_TOKEN on the backend before delegating."
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

    execute_body = _build_execute_body(body)
    gateway = config.DISCOVERY_BASE_URL.rstrip("/") + config.EXECUTE_AGENT_PATH

    logger.info(
        "TaskPlanner dispatch process=%s node=%s subtasks=%d gateway=%s",
        body.process_instance_id,
        body.target_node_id,
        len(body.subtasks),
        gateway,
    )
    logger.debug("TaskPlanner payload=%s", execute_body)

    try:
        agent_raw = await discovery.post_platform_json(
            config.EXECUTE_AGENT_PATH,
            token,
            execute_body,
            timeout=_EXECUTE_TIMEOUT_S,
        )
        return {
            "ok": True,
            "status": "dispatched",
            "toast_message": "Task design submitted to Cloudera TaskPlanner.",
            "process_instance_id": body.process_instance_id,
            "target_node_id": body.target_node_id,
            "metadata": saved_metadata,
            "agent_gateway": gateway,
            "token_source": token_source,
            "agent_response": agent_raw,
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            detail_body = exc.response.json()
            message = detail_body.get("reason") or detail_body.get("error") or str(detail_body)[:200]
        except Exception:
            message = exc.response.text[:200]
        logger.warning("TaskPlanner gateway HTTP %s: %s", status, message)
        return {
            "ok": False,
            "toast_message": (
                f"Cloudera TaskPlanner gateway error (HTTP {status}). "
                "Confirm Agent Studio is running and reachable."
            ),
            "detail": f"gateway_http_{status}",
            "gateway_message": message,
            "metadata": saved_metadata,
        }
    except httpx.TimeoutException:
        logger.warning("TaskPlanner gateway timeout after %.0fs", _EXECUTE_TIMEOUT_S)
        return {
            "ok": False,
            "toast_message": (
                f"Cloudera TaskPlanner timed out after {_EXECUTE_TIMEOUT_S:.0f}s. "
                "Try again or check Agent Studio load."
            ),
            "detail": "gateway_timeout",
            "metadata": saved_metadata,
        }
    except httpx.RequestError as exc:
        logger.warning("TaskPlanner gateway unreachable: %s", exc)
        return {
            "ok": False,
            "toast_message": (
                "Cloudera TaskPlanner is unreachable. Verify DISCOVERY_BASE_URL and "
                "network connectivity to Agent Studio."
            ),
            "detail": "gateway_unreachable",
            "metadata": saved_metadata,
        }
    except Exception as exc:
        logger.exception("TaskPlanner dispatch failed")
        return {
            "ok": False,
            "toast_message": (
                "Cloudera TaskPlanner encountered an unexpected error during dispatch."
            ),
            "detail": f"gateway_error:{type(exc).__name__}",
            "metadata": saved_metadata,
        }

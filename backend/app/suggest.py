"""Step 5c — optimization via Cloudera Agent Studio executeAgent gateway."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

import httpx

from . import config, discovery, groups, ingestion, metadata as metadata_svc, overrides
from .schemas import validate_workflow_oracle
from .schemas.optimization import (
    ExecuteAgentRequest,
    INSTRUCTION_INTENT,
    OptimizationDataset,
    SYSTEM_MANDATE,
)

_BBOX_PAD = 20.0
_NODE_W = 180.0
_NODE_H = 80.0

_AGENT_EXECUTE_TIMEOUT_S = float(
    __import__("os").environ.get("AGENT_EXECUTE_TIMEOUT_S", "60")
)

_WORKFLOW_FIELDS = frozenset(
    {
        "workflow_name",
        "type",
        "manager_agent",
        "planning",
        "agents",
        "tasks",
        "confidence",
        "rationale",
    }
)

_TARGET_ID_KEYS = (
    "target_node_ids",
    "node_ids",
    "optimization_targets",
    "group_node_ids",
)


def _compile_graph_nodes(graph: dict) -> list[dict[str, Any]]:
    return [
        {
            "id": n["id"],
            "label": n.get("label"),
            "type": n.get("type"),
            "x": n["x"],
            "y": n["y"],
            "lane_id": n.get("lane_id"),
            "group_id": n.get("group_id"),
            "metadata": n.get("metadata") or {},
        }
        for n in graph["nodes"]
    ]


def _compile_graph_edges(graph: dict) -> list[dict[str, Any]]:
    return [
        {
            "id": e["id"],
            "source_node_id": e["source_node_id"],
            "target_node_id": e["target_node_id"],
            "label": e.get("label"),
        }
        for e in graph["edges"]
    ]


def _compile_active_capabilities(
    capabilities: discovery.DiscoveryResponse,
) -> dict[str, Any]:
    return {
        "discovery_active": capabilities.discovery_active,
        "source": capabilities.source,
        "default_model": capabilities.default_model,
        "models": capabilities.models,
        "tools": [entry.model_dump() for entry in capabilities.tools],
        "mcp_servers": [entry.model_dump() for entry in capabilities.mcp_servers],
    }


def _build_execute_agent_request(
    process_id: str,
    graph: dict,
    capabilities: discovery.DiscoveryResponse,
    forbidden_node_ids: list[str],
) -> dict[str, Any]:
    payload = ExecuteAgentRequest(
        instruction_intent=INSTRUCTION_INTENT,
        system_mandate=SYSTEM_MANDATE,
        dataset=OptimizationDataset(
            process_id=process_id,
            graph_nodes=_compile_graph_nodes(graph),
            graph_edges=_compile_graph_edges(graph),
            active_capabilities=_compile_active_capabilities(capabilities),
            strategic_overrides=forbidden_node_ids,
        ),
    )
    return payload.model_dump()


def _collect_target_ids(container: dict[str, Any]) -> list[str]:
    for key in _TARGET_ID_KEYS:
        value = container.get(key)
        if isinstance(value, list):
            ids = [str(item) for item in value if item]
            if ids:
                return ids
    return []


def _extract_workflow_dict(container: dict[str, Any]) -> dict[str, Any]:
    for key in ("blueprint", "workflow", "workflow_definition", "result"):
        nested = container.get(key)
        if isinstance(nested, dict) and nested.get("workflow_name"):
            return nested
    if container.get("workflow_name"):
        return {key: container[key] for key in _WORKFLOW_FIELDS if key in container}
    return {}


def _parse_agent_response(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize executeAgent response into workflow dict and target node IDs."""
    if not isinstance(raw, dict):
        raise ValueError("Agent gateway returned a non-object JSON payload.")

    candidates: list[dict[str, Any]] = [raw]
    for key in ("data", "result", "response", "output"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)

    workflow: dict[str, Any] = {}
    target_node_ids: list[str] = []
    for candidate in candidates:
        if not target_node_ids:
            target_node_ids = _collect_target_ids(candidate)
        if not workflow:
            workflow = _extract_workflow_dict(candidate)

    if not workflow:
        raise ValueError(
            "Agent gateway response did not include an AgentStudioWorkflow blueprint."
        )
    return workflow, target_node_ids


def _pipeline_score(metadata: dict[str, Any]) -> int:
    sources = metadata.get("data_sources") or []
    score = sum(
        1
        for entry in sources
        if isinstance(entry, dict)
        and (str(entry.get("source_name") or "").strip() or str(entry.get("human_procedure") or "").strip())
    )
    if str(metadata.get("output_end_product") or "").strip():
        score += 1
    return score


def _fallback_target_nodes(graph: dict, forbidden_node_ids: list[str]) -> list[str]:
    """Local fallback when the agent blueprint omits explicit target node IDs."""
    forbidden = set(forbidden_node_ids)
    eligible = [n for n in graph["nodes"] if n["id"] not in forbidden]
    if not eligible:
        return []

    ranked = sorted(
        eligible,
        key=lambda n: (
            _pipeline_score(n.get("metadata") or {}),
            "task" in (n.get("type") or "").lower(),
        ),
        reverse=True,
    )
    rich = [n["id"] for n in ranked if _pipeline_score(n.get("metadata") or {}) > 0]
    if rich:
        return rich[:4]

    task_nodes = [n["id"] for n in ranked if "task" in (n.get("type") or "").lower()]
    if task_nodes:
        return task_nodes[:3]

    return [n["id"] for n in ranked[:3]]


def _assert_no_forbidden_overlap(
    target_node_ids: list[str], forbidden_node_ids: list[str]
) -> None:
    overlap = set(target_node_ids) & set(forbidden_node_ids)
    if overlap:
        raise ValueError(
            f"Proposal intersects forbidden strategic boundary nodes: {sorted(overlap)}"
        )


async def _execute_optimization_agent(token: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return await discovery.post_platform_json(
            config.EXECUTE_AGENT_PATH,
            token,
            body,
            timeout=_AGENT_EXECUTE_TIMEOUT_S,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            detail = exc.response.json()
            message = detail.get("reason") or detail.get("error") or str(detail)[:200]
        except Exception:
            message = exc.response.text[:200]
        raise ValueError(
            f"Cloudera Agent gateway HTTP {status} from {config.DISCOVERY_BASE_URL}"
            f"{config.EXECUTE_AGENT_PATH}: {message}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ValueError(
            f"Cloudera Agent gateway timed out after {_AGENT_EXECUTE_TIMEOUT_S}s."
        ) from exc
    except httpx.RequestError as exc:
        raise ValueError(f"Cloudera Agent gateway unreachable: {exc}") from exc


def _compute_bbox(rows: list[sqlite3.Row]) -> dict[str, float]:
    xs = [float(r["x"]) for r in rows]
    ys = [float(r["y"]) for r in rows]
    return {
        "x": min(xs) - _BBOX_PAD,
        "y": min(ys) - _BBOX_PAD,
        "width": max(xs) - min(xs) + _NODE_W + 2 * _BBOX_PAD,
        "height": max(ys) - min(ys) + _NODE_H + 2 * _BBOX_PAD,
    }


async def generate_suggestion(
    conn: sqlite3.Connection,
    process_id: str,
    user_token: Optional[str],
) -> dict[str, Any]:
    graph = ingestion.get_graph(conn, process_id)
    if graph is None:
        raise ValueError("Process not found.")

    token, token_source = discovery.resolve_platform_token(user_token)
    if not token:
        raise ValueError(
            "No platform auth token available. Set CLOUDERA_AI_TOKEN on the backend "
            "or pass _cdswuserstoken / Authorization: Bearer from a logged-in session."
        )

    forbidden_node_ids = overrides.load_forbidden_node_ids(conn, process_id)
    capabilities = await discovery.fetch_platform_capabilities(user_token)
    allowed_tools = {t.name for t in capabilities.tools}

    execute_body = _build_execute_agent_request(
        process_id, graph, capabilities, forbidden_node_ids
    )
    agent_raw = await _execute_optimization_agent(token, execute_body)
    raw_proposal, target_node_ids = _parse_agent_response(agent_raw)

    if not target_node_ids:
        target_node_ids = _fallback_target_nodes(graph, forbidden_node_ids)
    if not target_node_ids:
        raise ValueError("Agent gateway returned no optimizable nodes for this process.")

    _assert_no_forbidden_overlap(target_node_ids, forbidden_node_ids)

    workflow = validate_workflow_oracle(
        raw_proposal,
        discovery_active=capabilities.discovery_active,
        allowed_tools=allowed_tools,
    )

    placeholders = ",".join("?" * len(target_node_ids))
    node_rows = conn.execute(
        f"SELECT id, x, y FROM node WHERE process_id = ? AND id IN ({placeholders})",
        [process_id, *target_node_ids],
    ).fetchall()
    if len(node_rows) != len(target_node_ids):
        raise ValueError("Agent gateway targeted one or more unknown node IDs.")

    bbox = _compute_bbox(node_rows)

    group = groups.create_proposed_group(
        conn,
        process_id,
        target_node_ids,
        workflow.model_dump(),
        bbox,
    )

    metadata_svc.upsert_metadata(
        conn,
        process_id,
        "group",
        group["id"],
        {
            "name": workflow.workflow_name,
            "description": workflow.rationale,
        },
    )

    return {
        "discovery_active": capabilities.discovery_active,
        "agent_gateway": config.DISCOVERY_BASE_URL.rstrip("/") + config.EXECUTE_AGENT_PATH,
        "token_source": token_source,
        "group_id": group["id"],
        "node_ids": target_node_ids,
        "bbox": bbox,
        "deployment_status": "proposed",
        "forbidden_node_ids": forbidden_node_ids,
        **workflow.model_dump(),
    }

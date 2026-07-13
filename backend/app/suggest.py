"""Step 5c — draft optimization generation with dynamic context injection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from . import discovery, groups, ingestion, metadata as metadata_svc, overrides
from .schemas import validate_workflow_oracle

_BBOX_PAD = 20.0
_NODE_W = 180.0
_NODE_H = 80.0

_AUX_LABEL_KEYWORDS = ("document", "notify", "archive", "email", "script", "report")


def _duration_minutes(meta: dict) -> float:
    value = meta.get("duration_value")
    if value is None:
        return 0.0
    unit = meta.get("duration_unit") or "minutes"
    if unit == "hours":
        return float(value) * 60.0
    if unit == "days":
        return float(value) * 1440.0
    return float(value)


def _eligible_nodes(graph: dict, forbidden_node_ids: list[str]) -> list[dict]:
    forbidden = set(forbidden_node_ids)
    return [n for n in graph["nodes"] if n["id"] not in forbidden]


def _select_target_nodes(graph: dict, forbidden_node_ids: list[str]) -> list[str]:
    """Prefer high-duration / SLA-heavy nodes that are not strategically forbidden."""
    nodes = _eligible_nodes(graph, forbidden_node_ids)
    ranked = sorted(
        nodes,
        key=lambda n: (
            _duration_minutes(n.get("metadata") or {}),
            "task" in (n.get("type") or "").lower(),
        ),
        reverse=True,
    )
    with_duration = [
        n["id"]
        for n in ranked
        if _duration_minutes(n.get("metadata") or {}) > 0
    ]
    if with_duration:
        return with_duration[:4]

    task_nodes = [
        n["id"] for n in ranked if "task" in (n.get("type") or "").lower()
    ]
    if task_nodes:
        return task_nodes[:3]

    return [n["id"] for n in ranked[:3]]


def _select_alternative_nodes(graph: dict, forbidden_node_ids: list[str]) -> list[str]:
    """Divert to auxiliary/documentation-style nodes when SLA targets are forbidden."""
    candidates = _eligible_nodes(graph, forbidden_node_ids)
    if not candidates:
        return []

    auxiliary = [
        n
        for n in candidates
        if any(
            keyword in (n.get("label") or n.get("source_ref") or "").lower()
            for keyword in _AUX_LABEL_KEYWORDS
        )
    ]
    if auxiliary:
        return [auxiliary[0]["id"]]

    task_nodes = [
        n["id"] for n in candidates if "task" in (n.get("type") or "").lower()
    ]
    if task_nodes:
        return task_nodes[:2]

    return [candidates[0]["id"]]


def _build_governance_block(forbidden_node_ids: list[str]) -> str:
    if not forbidden_node_ids:
        return ""
    return (
        "=== HUMAN GOVERNANCE & STRATEGIC BOUNDARY OVERRIDES ===\n"
        "The human system architect has explicitly forbidden autonomous automation "
        "across the following node configurations.\n"
        "You are STRICTLY PROHIBITED from proposing agentic groups, tasks, or bounding "
        "boxes that completely or partially include any of these node IDs:\n"
        f"{json.dumps(forbidden_node_ids)}\n"
        "If your optimization would intersect any forbidden node, gracefully divert to "
        "alternative, non-overlapping task patterns (such as auxiliary text processing "
        "or adjacent documentation nodes) instead.\n\n"
    )


def _build_prompt_context(
    graph: dict,
    capabilities: discovery.DiscoveryResponse,
    forbidden_node_ids: list[str],
) -> str:
    layout = {
        "process": graph["process"],
        "nodes": [
            {
                "id": n["id"],
                "label": n.get("label"),
                "type": n.get("type"),
                "x": n["x"],
                "y": n["y"],
                "metadata": n.get("metadata"),
            }
            for n in graph["nodes"]
        ],
        "edges": graph["edges"],
    }
    telemetry = {
        "total_nodes": len(graph["nodes"]),
        "nodes_with_duration": sum(
            1
            for n in graph["nodes"]
            if (n.get("metadata") or {}).get("duration_value") is not None
        ),
        "sum_duration_minutes": sum(
            _duration_minutes(n.get("metadata") or {}) for n in graph["nodes"]
        ),
        "forbidden_node_count": len(forbidden_node_ids),
    }
    capabilities_block = capabilities.model_dump()
    governance = _build_governance_block(forbidden_node_ids)
    return (
        governance
        + "Analyze the BPMN subprocess and propose a Cloudera Agent Studio workflow.\n\n"
        f"LAYOUT_JSON:\n{json.dumps(layout, indent=2)}\n\n"
        f"TELEMETRY:\n{json.dumps(telemetry, indent=2)}\n\n"
        f"CAPABILITY_MATRIX:\n{json.dumps(capabilities_block, indent=2)}"
    )


def _pick_tools(capabilities: discovery.DiscoveryResponse) -> list[str]:
    names = [t.name for t in capabilities.tools]
    if names:
        return names[:2]
    return ["code_execution", "vector_search"]


def _assert_no_forbidden_overlap(
    target_node_ids: list[str], forbidden_node_ids: list[str]
) -> None:
    overlap = set(target_node_ids) & set(forbidden_node_ids)
    if overlap:
        raise ValueError(
            f"Proposal intersects forbidden strategic boundary nodes: {sorted(overlap)}"
        )


def _mock_inference_response(
    graph: dict,
    target_node_ids: list[str],
    capabilities: discovery.DiscoveryResponse,
    forbidden_node_ids: list[str],
    *,
    system_prompt: str,
) -> dict[str, Any]:
    """Phase 1 stub — respects governance block and excludes forbidden nodes."""
    del system_prompt  # reserved for live Azure OpenAI / Cloudera AI Inference wiring
    _assert_no_forbidden_overlap(target_node_ids, forbidden_node_ids)

    id_to_node = {n["id"]: n for n in graph["nodes"]}
    labels = [
        id_to_node[nid].get("label") or id_to_node[nid].get("source_ref", nid)
        for nid in target_node_ids
        if nid in id_to_node
    ]
    tools = _pick_tools(capabilities)
    task_summary = ", ".join(labels) if labels else "selected subprocess"
    diverted = bool(forbidden_node_ids)

    workflow_name = (
        "Auxiliary Processing Agent Workflow"
        if diverted
        else "SLA Triage Agent Workflow"
    )
    goal = (
        f"Automate adjacent auxiliary steps without touching forbidden nodes: {task_summary}"
        if diverted
        else f"Automate high-duration steps: {task_summary}"
    )
    task_desc = (
        f"Process documentation and auxiliary tasks covering: {task_summary}. "
        "Remain strictly outside all forbidden strategic boundary node IDs."
        if diverted
        else (
            f"Review and automate the grouped subprocess covering: {task_summary}. "
            "Prioritize nodes with the highest expected duration and SLA exposure."
        )
    )
    rationale = (
        f"Strategic boundary overrides forbid {len(forbidden_node_ids)} node(s). "
        f"Diverted optimization to non-overlapping alternative pattern: {task_summary}."
        if diverted
        else (
            f"Selected {len(target_node_ids)} node(s) with the highest duration telemetry "
            f"({task_summary}). These steps dominate labor-hours and are prime candidates "
            "for Agent Studio task automation using discovered platform tools."
        )
    )

    return {
        "workflow_name": workflow_name,
        "type": "task",
        "manager_agent": True,
        "planning": True,
        "agents": [
            {
                "name": "Triage Orchestrator",
                "role": "Process Automation Lead",
                "goal": goal,
                "backstory": (
                    "Specializes in insurance claims triage while honoring human "
                    "governance boundaries on forbidden automation zones."
                ),
                "tools": tools,
            }
        ],
        "tasks": [
            {
                "description": task_desc,
                "agent": "Triage Orchestrator",
            }
        ],
        "confidence": 0.82,
        "rationale": rationale,
    }


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

    forbidden_node_ids = overrides.load_forbidden_node_ids(conn, process_id)

    capabilities = await discovery.fetch_platform_capabilities(user_token)
    allowed_tools = {t.name for t in capabilities.tools}

    target_node_ids = _select_target_nodes(graph, forbidden_node_ids)
    if not target_node_ids and forbidden_node_ids:
        target_node_ids = _select_alternative_nodes(graph, forbidden_node_ids)
    if not target_node_ids:
        raise ValueError("Process has no nodes to optimize.")

    _assert_no_forbidden_overlap(target_node_ids, forbidden_node_ids)

    system_prompt = _build_prompt_context(graph, capabilities, forbidden_node_ids)
    raw_proposal = _mock_inference_response(
        graph,
        target_node_ids,
        capabilities,
        forbidden_node_ids,
        system_prompt=system_prompt,
    )

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
        "group_id": group["id"],
        "node_ids": target_node_ids,
        "bbox": bbox,
        "deployment_status": "proposed",
        "forbidden_node_ids": forbidden_node_ids,
        **workflow.model_dump(),
    }

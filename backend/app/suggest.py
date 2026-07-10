"""Step 5c — draft optimization generation with dynamic context injection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from . import discovery, groups, ingestion, metadata as metadata_svc
from .schemas import validate_workflow_oracle

_BBOX_PAD = 20.0
_NODE_W = 180.0
_NODE_H = 80.0


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


def _select_target_nodes(graph: dict) -> list[str]:
    """Prefer high-duration / SLA-heavy nodes; fall back to user tasks."""
    nodes = graph["nodes"]
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

    return [n["id"] for n in nodes[:3]]


def _build_prompt_context(graph: dict, capabilities: discovery.DiscoveryResponse) -> str:
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
    }
    capabilities_block = capabilities.model_dump()
    return (
        "Analyze the BPMN subprocess and propose a Cloudera Agent Studio workflow.\n\n"
        f"LAYOUT_JSON:\n{json.dumps(layout, indent=2)}\n\n"
        f"TELEMETRY:\n{json.dumps(telemetry, indent=2)}\n\n"
        f"CAPABILITY_MATRIX:\n{json.dumps(capabilities_block, indent=2)}"
    )


def _pick_tools(capabilities: discovery.DiscoveryResponse) -> list[str]:
    names = [t.name for t in capabilities.tools]
    if names:
        return names[:2]
    return ["code_execution", "vector_search"]


def _mock_inference_response(
    graph: dict,
    target_node_ids: list[str],
    capabilities: discovery.DiscoveryResponse,
    *,
    system_prompt: str,
) -> dict[str, Any]:
    """Phase 1 stub — isolates high-duration nodes and returns a schema-shaped proposal."""
    del system_prompt  # reserved for live Cloudera AI Inference wiring
    id_to_node = {n["id"]: n for n in graph["nodes"]}
    labels = [
        id_to_node[nid].get("label") or id_to_node[nid].get("source_ref", nid)
        for nid in target_node_ids
        if nid in id_to_node
    ]
    tools = _pick_tools(capabilities)
    task_summary = ", ".join(labels) if labels else "selected subprocess"

    return {
        "workflow_name": "SLA Triage Agent Workflow",
        "type": "task",
        "manager_agent": True,
        "planning": True,
        "agents": [
            {
                "name": "Triage Orchestrator",
                "role": "Process Automation Lead",
                "goal": f"Automate high-duration steps: {task_summary}",
                "backstory": (
                    "Specializes in insurance claims triage, prioritizing SLA-bound "
                    "human tasks for agentic execution."
                ),
                "tools": tools,
            }
        ],
        "tasks": [
            {
                "description": (
                    f"Review and automate the grouped subprocess covering: {task_summary}. "
                    "Prioritize nodes with the highest expected duration and SLA exposure."
                ),
                "agent": "Triage Orchestrator",
            }
        ],
        "confidence": 0.82,
        "rationale": (
            f"Selected {len(target_node_ids)} node(s) with the highest duration telemetry "
            f"({task_summary}). These steps dominate labor-hours and are prime candidates "
            "for Agent Studio task automation using discovered platform tools."
        ),
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

    capabilities = await discovery.fetch_platform_capabilities(user_token)
    allowed_tools = {t.name for t in capabilities.tools}

    target_node_ids = _select_target_nodes(graph)
    if not target_node_ids:
        raise ValueError("Process has no nodes to optimize.")

    system_prompt = _build_prompt_context(graph, capabilities)
    raw_proposal = _mock_inference_response(
        graph,
        target_node_ids,
        capabilities,
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
        name=workflow.workflow_name,
        description=workflow.rationale,
    )

    return {
        "discovery_active": capabilities.discovery_active,
        "group_id": group["id"],
        "node_ids": target_node_ids,
        "bbox": bbox,
        "deployment_status": "proposed",
        **workflow.model_dump(),
    }

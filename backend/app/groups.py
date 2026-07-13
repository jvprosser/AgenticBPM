"""Agentic underlay groups (BLUEPRINT Step 4).

Enforces many-nodes → one group: each node has at most one ``group_id``. Creating a
group assigns selected nodes and stores a bounding-box geometry JSON payload.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from .metadata import get_node_task_metadata
from .schemas.metadata import AggregatedPipeline

# Approximate BPMN node footprint for server-side bbox when the client omits one.
_NODE_W = 180.0
_NODE_H = 80.0
_BBOX_PAD = 24.0


def _parse_bbox(raw: str | None) -> dict | None:
    if not raw:
        return None
    return json.loads(raw)


def create_proposed_group(
    conn: sqlite3.Connection,
    process_id: str,
    node_ids: list[str],
    workflow: dict,
    bbox: dict | None = None,
) -> dict:
    """Step 5c: persist an AI-proposed group after oracle validation."""
    result = _create_group_inner(
        conn,
        process_id,
        node_ids,
        bbox=bbox,
        deployment_status="proposed",
        workflow_definition_json=json.dumps(workflow),
    )
    return result


def create_group(
    conn: sqlite3.Connection,
    process_id: str,
    node_ids: list[str],
    bbox: dict | None = None,
) -> dict:
    """Create an agentic underlay group and assign nodes (strict 1 group per node)."""
    return _create_group_inner(
        conn, process_id, node_ids, bbox=bbox, deployment_status="unlinked"
    )


def _create_group_inner(
    conn: sqlite3.Connection,
    process_id: str,
    node_ids: list[str],
    *,
    bbox: dict | None,
    deployment_status: str,
    workflow_definition_json: str | None = None,
) -> dict:
    if not node_ids:
        raise ValueError("At least one node is required to create a group.")

    unique_ids = list(dict.fromkeys(node_ids))
    placeholders = ",".join("?" * len(unique_ids))
    rows = conn.execute(
        f"SELECT id, x, y, group_id FROM node "
        f"WHERE process_id = ? AND id IN ({placeholders})",
        [process_id, *unique_ids],
    ).fetchall()
    if len(rows) != len(unique_ids):
        raise ValueError("One or more nodes were not found for this process.")

    old_group_ids = {r["group_id"] for r in rows if r["group_id"]}

    if bbox is None:
        xs = [float(r["x"]) for r in rows]
        ys = [float(r["y"]) for r in rows]
        bbox = {
            "x": min(xs) - _BBOX_PAD,
            "y": min(ys) - _BBOX_PAD,
            "width": max(xs) - min(xs) + _NODE_W + 2 * _BBOX_PAD,
            "height": max(ys) - min(ys) + _NODE_H + 2 * _BBOX_PAD,
        }

    group_id = uuid.uuid4().hex
    conn.execute(
        'INSERT INTO "group" '
        "(id, process_id, bbox_geometry, deployment_status, workflow_definition_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (group_id, process_id, json.dumps(bbox), deployment_status, workflow_definition_json),
    )
    conn.executemany(
        "UPDATE node SET group_id = ? WHERE id = ? AND process_id = ?",
        [(group_id, nid, process_id) for nid in unique_ids],
    )

    _delete_empty_groups(conn, old_group_ids - {group_id})

    return {
        "id": group_id,
        "process_id": process_id,
        "node_ids": unique_ids,
        "bbox": bbox,
        "deployment_status": deployment_status,
    }


def _delete_empty_groups(conn: sqlite3.Connection, group_ids: set[str]) -> None:
    for gid in group_ids:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM node WHERE group_id = ?", (gid,)
        ).fetchone()["c"]
        if count == 0:
            conn.execute('DELETE FROM "group" WHERE id = ?', (gid,))


def aggregate_group_pipeline(
    conn: sqlite3.Connection, process_id: str, node_ids: list[str]
) -> dict:
    """Harvest child task metadata into a deduplicated functional pipeline view."""
    scope_tasks: list[dict] = []
    sources_map: dict[str, list[str]] = {}
    output_products: list[str] = []
    seen_outputs: set[str] = set()

    for node_id in node_ids:
        row = conn.execute(
            "SELECT id, label, source_ref FROM node WHERE id = ? AND process_id = ?",
            (node_id, process_id),
        ).fetchone()
        if row is None:
            continue
        scope_tasks.append(
            {
                "id": row["id"],
                "label": row["label"] or row["source_ref"],
            }
        )
        meta = get_node_task_metadata(conn, node_id)
        for entry in meta.get("data_sources", []):
            if not isinstance(entry, dict):
                continue
            source_name = str(entry.get("source_name") or "").strip()
            procedure = str(entry.get("human_procedure") or "").strip()
            if not source_name and not procedure:
                continue
            key = source_name or "(unnamed source)"
            bucket = sources_map.setdefault(key, [])
            if procedure and procedure not in bucket:
                bucket.append(procedure)
        output = str(meta.get("output_end_product") or "").strip()
        if output and output not in seen_outputs:
            seen_outputs.add(output)
            output_products.append(output)

    data_sources = [
        {"source_name": name, "human_procedures": procedures}
        for name, procedures in sorted(sources_map.items())
    ]
    return AggregatedPipeline(
        scope_tasks=scope_tasks,
        data_sources=data_sources,
        output_products=output_products,
    ).model_dump()


def list_groups(conn: sqlite3.Connection, process_id: str) -> list[dict]:
    rows = conn.execute(
        'SELECT id, bbox_geometry, deployment_status, workflow_definition_json '
        'FROM "group" WHERE process_id = ?',
        (process_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        workflow = None
        if r["workflow_definition_json"]:
            workflow = json.loads(r["workflow_definition_json"])
        node_rows = conn.execute(
            "SELECT id FROM node WHERE process_id = ? AND group_id = ?",
            (process_id, r["id"]),
        ).fetchall()
        node_ids = [nr["id"] for nr in node_rows]
        out.append(
            {
                "id": r["id"],
                "bbox": _parse_bbox(r["bbox_geometry"]),
                "deployment_status": r["deployment_status"],
                "workflow_definition": workflow,
                "node_ids": node_ids,
                "aggregated_pipeline": aggregate_group_pipeline(
                    conn, process_id, node_ids
                ),
            }
        )
    return out


def delete_group(conn: sqlite3.Connection, process_id: str, group_id: str) -> bool:
    row = conn.execute(
        'SELECT id FROM "group" WHERE id = ? AND process_id = ?',
        (group_id, process_id),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE node SET group_id = NULL WHERE group_id = ? AND process_id = ?",
        (group_id, process_id),
    )
    conn.execute('DELETE FROM "group" WHERE id = ?', (group_id,))
    return True

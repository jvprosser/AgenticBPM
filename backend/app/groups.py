"""Agentic underlay groups (BLUEPRINT Step 4).

Enforces many-nodes → one group: each node has at most one ``group_id``. Creating a
group assigns selected nodes and stores a bounding-box geometry JSON payload.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

# Approximate BPMN node footprint for server-side bbox when the client omits one.
_NODE_W = 180.0
_NODE_H = 80.0
_BBOX_PAD = 24.0


def _parse_bbox(raw: str | None) -> dict | None:
    if not raw:
        return None
    return json.loads(raw)


def create_group(
    conn: sqlite3.Connection,
    process_id: str,
    node_ids: list[str],
    bbox: dict | None = None,
) -> dict:
    """Create an agentic underlay group and assign nodes (strict 1 group per node)."""
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
        'INSERT INTO "group" (id, process_id, bbox_geometry, deployment_status) '
        "VALUES (?, ?, ?, 'unlinked')",
        (group_id, process_id, json.dumps(bbox)),
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
        "deployment_status": "unlinked",
    }


def _delete_empty_groups(conn: sqlite3.Connection, group_ids: set[str]) -> None:
    for gid in group_ids:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM node WHERE group_id = ?", (gid,)
        ).fetchone()["c"]
        if count == 0:
            conn.execute('DELETE FROM "group" WHERE id = ?', (gid,))


def list_groups(conn: sqlite3.Connection, process_id: str) -> list[dict]:
    rows = conn.execute(
        'SELECT id, bbox_geometry, deployment_status FROM "group" WHERE process_id = ?',
        (process_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "bbox": _parse_bbox(r["bbox_geometry"]),
                "deployment_status": r["deployment_status"],
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

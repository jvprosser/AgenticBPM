"""Strategic boundary overrides — user rejections of AI-proposed automations."""

from __future__ import annotations

import json
import sqlite3
import uuid


def record_override(
    conn: sqlite3.Connection, process_id: str, node_ids: list[str]
) -> dict:
    """Log forbidden nodes and purge any matching ``proposed`` agentic group."""
    if not node_ids:
        raise ValueError("At least one node_id is required.")

    unique_ids = list(dict.fromkeys(node_ids))
    placeholders = ",".join("?" * len(unique_ids))

    proc = conn.execute(
        "SELECT id FROM process WHERE id = ?", (process_id,)
    ).fetchone()
    if proc is None:
        raise ValueError("Process not found.")

    rows = conn.execute(
        f"SELECT id FROM node WHERE process_id = ? AND id IN ({placeholders})",
        [process_id, *unique_ids],
    ).fetchall()
    if len(rows) != len(unique_ids):
        raise ValueError("One or more nodes were not found for this process.")

    proposed_rows = conn.execute(
        f'SELECT DISTINCT g.id AS group_id '
        f'FROM node n '
        f'JOIN "group" g ON g.id = n.group_id '
        f"WHERE n.process_id = ? AND n.id IN ({placeholders}) "
        f"AND g.process_id = ? AND g.deployment_status = 'proposed'",
        [process_id, *unique_ids, process_id],
    ).fetchall()
    proposed_group_ids = [r["group_id"] for r in proposed_rows]

    override_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO strategic_override (id, process_id, node_ids) VALUES (?, ?, ?)",
        (override_id, process_id, json.dumps(unique_ids)),
    )

    conn.executemany(
        "UPDATE node SET group_id = NULL WHERE id = ? AND process_id = ?",
        [(nid, process_id) for nid in unique_ids],
    )

    for group_id in proposed_group_ids:
        conn.execute(
            'DELETE FROM "group" WHERE id = ? AND process_id = ? AND deployment_status = ?',
            (group_id, process_id, "proposed"),
        )

    created = conn.execute(
        "SELECT id, process_id, node_ids, created_at FROM strategic_override WHERE id = ?",
        (override_id,),
    ).fetchone()

    return {
        "id": created["id"],
        "process_id": created["process_id"],
        "node_ids": json.loads(created["node_ids"]),
        "created_at": created["created_at"],
        "purged_proposed_groups": proposed_group_ids,
    }

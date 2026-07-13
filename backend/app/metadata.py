"""Metadata persistence for nodes and groups (BLUEPRINT Step 5a).

Node task metadata is stored as JSON on ``node.metadata_json``. Group charter
metadata remains in the ``metadata`` table keyed by (owner_type, owner_id).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal, Optional

from .schemas.metadata import GroupMetadata, NodeTaskMetadata

OwnerType = Literal["node", "group"]


def _empty_node_task_metadata() -> dict[str, Any]:
    return NodeTaskMetadata().model_dump()


def _empty_group_metadata() -> dict[str, Any]:
    return GroupMetadata().model_dump()


def _row_to_group_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return _empty_group_metadata()
    return GroupMetadata(
        name=row["name"],
        owner=row["owner"],
        description=row["description"],
    ).model_dump()


def _validate_owner(
    conn: sqlite3.Connection, process_id: str, owner_type: OwnerType, owner_id: str
) -> None:
    if owner_type == "node":
        row = conn.execute(
            "SELECT id FROM node WHERE id = ? AND process_id = ?",
            (owner_id, process_id),
        ).fetchone()
    else:
        row = conn.execute(
            'SELECT id FROM "group" WHERE id = ? AND process_id = ?',
            (owner_id, process_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"{owner_type} '{owner_id}' not found for this process.")


def get_node_task_metadata(conn: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT metadata_json FROM node WHERE id = ?", (node_id,)
    ).fetchone()
    if row is None or not row["metadata_json"]:
        return _empty_node_task_metadata()
    try:
        payload = json.loads(row["metadata_json"])
    except (TypeError, json.JSONDecodeError):
        return _empty_node_task_metadata()
    if not isinstance(payload, dict):
        return _empty_node_task_metadata()
    return NodeTaskMetadata.from_payload(payload).model_dump()


def get_group_metadata(conn: sqlite3.Connection, group_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT name, owner, description "
        "FROM metadata WHERE owner_type = ? AND owner_id = ?",
        ("group", group_id),
    ).fetchone()
    return _row_to_group_dict(row)


def get_metadata(
    conn: sqlite3.Connection, owner_type: OwnerType, owner_id: str
) -> dict[str, Any]:
    if owner_type == "node":
        return get_node_task_metadata(conn, owner_id)
    return get_group_metadata(conn, owner_id)


def get_metadata_for_process(conn: sqlite3.Connection, process_id: str) -> dict[str, dict]:
    """Return ``{owner_type:owner_id -> metadata dict}`` for all owners in the process."""
    node_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM node WHERE process_id = ?", (process_id,)
        ).fetchall()
    ]
    group_ids = [
        r["id"]
        for r in conn.execute(
            'SELECT id FROM "group" WHERE process_id = ?', (process_id,)
        ).fetchall()
    ]
    out: dict[str, dict] = {}
    for nid in node_ids:
        out[f"node:{nid}"] = get_node_task_metadata(conn, nid)
    for gid in group_ids:
        out[f"group:{gid}"] = get_group_metadata(conn, gid)
    return out


def upsert_node_task_metadata(
    conn: sqlite3.Connection,
    process_id: str,
    node_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _validate_owner(conn, process_id, "node", node_id)
    validated = NodeTaskMetadata.from_payload(payload)
    conn.execute(
        "UPDATE node SET metadata_json = ? WHERE id = ? AND process_id = ?",
        (json.dumps(validated.model_dump()), node_id, process_id),
    )
    return validated.model_dump()


def upsert_group_metadata(
    conn: sqlite3.Connection,
    process_id: str,
    group_id: str,
    *,
    name: Optional[str] = None,
    owner: Optional[str] = None,
    description: Optional[str] = None,
) -> dict[str, Any]:
    _validate_owner(conn, process_id, "group", group_id)
    validated = GroupMetadata(name=name, owner=owner, description=description)
    conn.execute(
        "INSERT INTO metadata "
        "(owner_type, owner_id, name, owner, duration_value, duration_unit, description) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?) "
        "ON CONFLICT(owner_type, owner_id) DO UPDATE SET "
        "name = excluded.name, "
        "owner = excluded.owner, "
        "description = excluded.description",
        ("group", group_id, validated.name, validated.owner, validated.description),
    )
    return get_group_metadata(conn, group_id)


def upsert_metadata(
    conn: sqlite3.Connection,
    process_id: str,
    owner_type: OwnerType,
    owner_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if owner_type == "node":
        return upsert_node_task_metadata(conn, process_id, owner_id, payload)
    return upsert_group_metadata(
        conn,
        process_id,
        owner_id,
        name=payload.get("name"),
        owner=payload.get("owner"),
        description=payload.get("description"),
    )

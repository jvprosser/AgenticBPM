"""Metadata persistence for nodes and groups (BLUEPRINT Step 5a).

Each metadata row is keyed by (owner_type, owner_id). Upserts are immediate — no
client-side queue beyond optional debounce in the UI.
"""

from __future__ import annotations

import sqlite3
from typing import Literal, Optional

OwnerType = Literal["node", "group"]

_DURATION_UNITS = frozenset({"minutes", "hours", "days"})


def _empty_metadata() -> dict:
    return {
        "name": None,
        "owner": None,
        "duration_value": None,
        "duration_unit": None,
        "description": None,
    }


def _row_to_dict(row: sqlite3.Row | None) -> dict:
    if row is None:
        return _empty_metadata()
    return {
        "name": row["name"],
        "owner": row["owner"],
        "duration_value": row["duration_value"],
        "duration_unit": row["duration_unit"],
        "description": row["description"],
    }


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


def get_metadata(
    conn: sqlite3.Connection, owner_type: OwnerType, owner_id: str
) -> dict:
    row = conn.execute(
        "SELECT name, owner, duration_value, duration_unit, description "
        "FROM metadata WHERE owner_type = ? AND owner_id = ?",
        (owner_type, owner_id),
    ).fetchone()
    return _row_to_dict(row)


def get_metadata_for_process(conn: sqlite3.Connection, process_id: str) -> dict:
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
        key = f"node:{nid}"
        out[key] = get_metadata(conn, "node", nid)
    for gid in group_ids:
        key = f"group:{gid}"
        out[key] = get_metadata(conn, "group", gid)
    return out


def upsert_metadata(
    conn: sqlite3.Connection,
    process_id: str,
    owner_type: OwnerType,
    owner_id: str,
    *,
    name: Optional[str] = None,
    owner: Optional[str] = None,
    duration_value: Optional[int] = None,
    duration_unit: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    _validate_owner(conn, process_id, owner_type, owner_id)

    if duration_unit is not None and duration_unit not in _DURATION_UNITS:
        raise ValueError(
            f"duration_unit must be one of {sorted(_DURATION_UNITS)}, got '{duration_unit}'."
        )
    if duration_value is not None and duration_value < 0:
        raise ValueError("duration_value must be non-negative.")

    conn.execute(
        "INSERT INTO metadata "
        "(owner_type, owner_id, name, owner, duration_value, duration_unit, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(owner_type, owner_id) DO UPDATE SET "
        "name = excluded.name, "
        "owner = excluded.owner, "
        "duration_value = excluded.duration_value, "
        "duration_unit = excluded.duration_unit, "
        "description = excluded.description",
        (
            owner_type,
            owner_id,
            name,
            owner,
            duration_value,
            duration_unit,
            description,
        ),
    )
    return get_metadata(conn, owner_type, owner_id)

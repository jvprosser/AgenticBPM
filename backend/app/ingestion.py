"""Ingestion service: parse a BPMN file, lay it out, and persist to SQLite.

Node/lane primary keys are namespaced as ``{process_id}:{bpmn_ref}`` so multiple
uploads never collide and edges can reference endpoints deterministically without a
lookup table.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import db, parser
from .groups import list_groups
from .layout import apply_cascade_layout
from .metadata import get_metadata


def _qualify(process_id: str, ref: str) -> str:
    return f"{process_id}:{ref}"


def _insert_process_row(
    conn: sqlite3.Connection,
    process_id: str,
    process_name: str,
    filename: str,
    xml_text: str,
) -> None:
    """Insert a process row, compatible with legacy columns until migration compacts."""
    cols = db.process_column_names(conn)
    fields = ["id", "process_name", "filename", "description", "raw_bpmn_xml"]
    values: list[object] = [process_id, process_name, filename, None, xml_text]
    if "raw_xml" in cols:
        fields.append("raw_xml")
        values.append(xml_text)
    if "format" in cols:
        fields.append("format")
        values.append("bpmn")
    if "created_at" in cols and "raw_xml" in cols:
        fields.append("created_at")
        values.append(datetime.now(timezone.utc).isoformat())
    placeholders = ", ".join("?" * len(fields))
    conn.execute(
        f"INSERT INTO process ({', '.join(fields)}) VALUES ({placeholders})",
        values,
    )


def ingest_bpmn(conn: sqlite3.Connection, filename: str, xml_text: str) -> dict:
    """Parse + persist a BPMN document. Returns an ingestion summary."""
    parsed = parser.parse_bpmn(xml_text)
    process_name = parser.extract_process_name(xml_text, filename)

    layout_source = "diagram-interchange"
    if not parsed.has_di:
        apply_cascade_layout(parsed.nodes, parsed.edges)
        layout_source = "cascade-fallback"

    process_id = uuid.uuid4().hex

    _insert_process_row(conn, process_id, process_name, filename, xml_text)

    conn.executemany(
        'INSERT INTO lane (id, process_id, source_ref, label) VALUES (?, ?, ?, ?)',
        [
            (_qualify(process_id, ln.source_ref), process_id, ln.source_ref, ln.label)
            for ln in parsed.lanes
        ],
    )

    conn.executemany(
        "INSERT INTO node "
        "(id, process_id, source_ref, type, label, x, y, lane_id, parent_ref, attached_to_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                _qualify(process_id, n.source_ref),
                process_id,
                n.source_ref,
                n.type,
                n.label,
                n.x,
                n.y,
                _qualify(process_id, n.lane_ref) if n.lane_ref else None,
                n.parent_ref,
                n.attached_to_ref,
            )
            for n in parsed.nodes
        ],
    )

    conn.executemany(
        "INSERT INTO edge (id, process_id, source_node_id, target_node_id, label) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                _qualify(process_id, e.source_ref),
                process_id,
                _qualify(process_id, e.source_node_ref),
                _qualify(process_id, e.target_node_ref),
                e.label,
            )
            for e in parsed.edges
        ],
    )

    row = conn.execute(
        "SELECT created_at, updated_at FROM process WHERE id = ?",
        (process_id,),
    ).fetchone()

    return {
        "id": process_id,
        "process_id": process_id,
        "process_name": process_name,
        "filename": filename,
        "counts": {
            "nodes": len(parsed.nodes),
            "edges": len(parsed.edges),
            "lanes": len(parsed.lanes),
        },
        "layout_source": layout_source,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_graph(conn: sqlite3.Connection, process_id: str) -> dict | None:
    """Return the full graph for the React canvas, or None if unknown."""
    proc = conn.execute(
        "SELECT id, process_name, filename, description, created_at, updated_at "
        "FROM process WHERE id = ?",
        (process_id,),
    ).fetchone()
    if proc is None:
        return None

    lanes = conn.execute(
        "SELECT id, source_ref, label FROM lane WHERE process_id = ?",
        (process_id,),
    ).fetchall()
    nodes = conn.execute(
        "SELECT id, source_ref, type, label, x, y, lane_id, group_id, "
        "parent_ref, attached_to_ref "
        "FROM node WHERE process_id = ?",
        (process_id,),
    ).fetchall()
    edges = conn.execute(
        "SELECT id, source_node_id, target_node_id, label FROM edge WHERE process_id = ?",
        (process_id,),
    ).fetchall()

    node_list = []
    for r in nodes:
        d = dict(r)
        d["metadata"] = get_metadata(conn, "node", r["id"])
        node_list.append(d)

    group_list = []
    for g in list_groups(conn, process_id):
        g["metadata"] = get_metadata(conn, "group", g["id"])
        group_list.append(g)

    return {
        "process": dict(proc),
        "lanes": [dict(r) for r in lanes],
        "nodes": node_list,
        "edges": [dict(r) for r in edges],
        "groups": group_list,
    }


def update_node_position(
    conn: sqlite3.Connection, process_id: str, node_id: str, x: float, y: float
) -> bool:
    """Persist a node's X/Y (drag-end). Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE node SET x = ?, y = ? WHERE id = ? AND process_id = ?",
        (x, y, node_id, process_id),
    )
    if cur.rowcount > 0:
        db.touch_process_updated_at(conn, process_id)
        return True
    return False


def update_process_fields(
    conn: sqlite3.Connection,
    process_id: str,
    *,
    process_name: Optional[str] = None,
    description: Optional[str] = None,
) -> dict | None:
    """Patch registry fields on a saved process. Returns the updated row or None."""
    proc = conn.execute(
        "SELECT id FROM process WHERE id = ?", (process_id,)
    ).fetchone()
    if proc is None:
        return None

    sets: list[str] = []
    params: list[object] = []
    if process_name is not None:
        name = process_name.strip()
        if not name:
            raise ValueError("process_name cannot be empty.")
        sets.append("process_name = ?")
        params.append(name)
    if description is not None:
        sets.append("description = ?")
        params.append(description.strip() if description else None)

    if not sets:
        row = conn.execute(
            "SELECT id, process_name, filename, description, created_at, updated_at "
            "FROM process WHERE id = ?",
            (process_id,),
        ).fetchone()
        return dict(row) if row else None

    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(process_id)
    conn.execute(
        f"UPDATE process SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    row = conn.execute(
        "SELECT id, process_name, filename, description, created_at, updated_at "
        "FROM process WHERE id = ?",
        (process_id,),
    ).fetchone()
    return dict(row) if row else None


def list_processes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, p.process_name, p.filename, p.description, "
        "p.created_at, p.updated_at, "
        "  (SELECT COUNT(*) FROM node n WHERE n.process_id = p.id) AS node_count "
        "FROM process p ORDER BY p.updated_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]

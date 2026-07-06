"""Ingestion service: parse a BPMN file, lay it out, and persist to SQLite.

Node/lane primary keys are namespaced as ``{process_id}:{bpmn_ref}`` so multiple
uploads never collide and edges can reference endpoints deterministically without a
lookup table.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from . import parser
from .layout import apply_cascade_layout


def _qualify(process_id: str, ref: str) -> str:
    return f"{process_id}:{ref}"


def ingest_bpmn(conn: sqlite3.Connection, filename: str, xml_text: str) -> dict:
    """Parse + persist a BPMN document. Returns an ingestion summary."""
    parsed = parser.parse_bpmn(xml_text)

    layout_source = "diagram-interchange"
    if not parsed.has_di:
        apply_cascade_layout(parsed.nodes, parsed.edges)
        layout_source = "cascade-fallback"

    process_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO process (id, filename, format, raw_xml, created_at) "
        "VALUES (?, ?, 'bpmn', ?, ?)",
        (process_id, filename, xml_text, created_at),
    )

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

    return {
        "process_id": process_id,
        "process_name": parsed.process_name,
        "filename": filename,
        "counts": {
            "nodes": len(parsed.nodes),
            "edges": len(parsed.edges),
            "lanes": len(parsed.lanes),
        },
        "layout_source": layout_source,
        "created_at": created_at,
    }


def get_graph(conn: sqlite3.Connection, process_id: str) -> dict | None:
    """Return the full graph for the React canvas, or None if unknown."""
    proc = conn.execute(
        "SELECT id, filename, format, created_at FROM process WHERE id = ?",
        (process_id,),
    ).fetchone()
    if proc is None:
        return None

    lanes = conn.execute(
        "SELECT id, source_ref, label FROM lane WHERE process_id = ?",
        (process_id,),
    ).fetchall()
    nodes = conn.execute(
        "SELECT id, source_ref, type, label, x, y, lane_id, parent_ref, attached_to_ref "
        "FROM node WHERE process_id = ?",
        (process_id,),
    ).fetchall()
    edges = conn.execute(
        "SELECT id, source_node_id, target_node_id, label FROM edge WHERE process_id = ?",
        (process_id,),
    ).fetchall()

    return {
        "process": dict(proc),
        "lanes": [dict(r) for r in lanes],
        "nodes": [dict(r) for r in nodes],
        "edges": [dict(r) for r in edges],
    }


def update_node_position(
    conn: sqlite3.Connection, process_id: str, node_id: str, x: float, y: float
) -> bool:
    """Persist a node's X/Y (drag-end). Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE node SET x = ?, y = ? WHERE id = ? AND process_id = ?",
        (x, y, node_id, process_id),
    )
    return cur.rowcount > 0


def list_processes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, p.filename, p.created_at, "
        "  (SELECT COUNT(*) FROM node n WHERE n.process_id = p.id) AS node_count "
        "FROM process p ORDER BY p.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]

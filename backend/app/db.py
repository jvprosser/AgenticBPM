"""SQLite operational store.

Single embedded database in CML project storage. WAL mode + a busy timeout keep it
robust on NFS-backed storage under the single-writer FastAPI process (see BLUEPRINT
guardrails). The schema mirrors BLUEPRINT §3; `group` and `metadata` tables are created
up front so later steps need no migration, even though Step 2 only populates
`process` / `lane` / `node` / `edge`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS process (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    format      TEXT NOT NULL DEFAULT 'bpmn',
    raw_xml     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lane (
    id          TEXT PRIMARY KEY,
    process_id  TEXT NOT NULL REFERENCES process(id) ON DELETE CASCADE,
    source_ref  TEXT,
    label       TEXT
);

CREATE TABLE IF NOT EXISTS "group" (
    id                      TEXT PRIMARY KEY,
    process_id              TEXT NOT NULL REFERENCES process(id) ON DELETE CASCADE,
    bbox_geometry           TEXT,
    deployment_status       TEXT NOT NULL DEFAULT 'unlinked'
        CHECK (deployment_status IN ('unlinked','proposed','draft','linked','deployed')),
    workflow_definition_json TEXT,
    agent_studio_workflow_id TEXT,
    agent_studio_url        TEXT,
    inference_endpoint_url  TEXT
);

CREATE TABLE IF NOT EXISTS node (
    id              TEXT PRIMARY KEY,
    process_id      TEXT NOT NULL REFERENCES process(id) ON DELETE CASCADE,
    source_ref      TEXT NOT NULL,
    type            TEXT NOT NULL,
    label           TEXT,
    x               REAL NOT NULL,
    y               REAL NOT NULL,
    lane_id         TEXT REFERENCES lane(id) ON DELETE SET NULL,
    group_id        TEXT REFERENCES "group"(id) ON DELETE SET NULL,
    parent_ref      TEXT,
    attached_to_ref TEXT
);

CREATE TABLE IF NOT EXISTS edge (
    id              TEXT PRIMARY KEY,
    process_id      TEXT NOT NULL REFERENCES process(id) ON DELETE CASCADE,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    label           TEXT
);

CREATE TABLE IF NOT EXISTS metadata (
    owner_type      TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    name            TEXT,
    owner           TEXT,
    duration_value  INTEGER,
    duration_unit   TEXT,
    description     TEXT,
    PRIMARY KEY (owner_type, owner_id)
);

CREATE TABLE IF NOT EXISTS strategic_override (
    id          TEXT PRIMARY KEY,
    process_id  TEXT NOT NULL,
    node_ids    TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (process_id) REFERENCES process(id)
);

CREATE INDEX IF NOT EXISTS idx_node_process ON node(process_id);
CREATE INDEX IF NOT EXISTS idx_edge_process ON edge(process_id);
CREATE INDEX IF NOT EXISTS idx_lane_process ON lane(process_id);
"""


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Transactional connection: commits on success, rolls back on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

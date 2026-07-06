"""FastAPI entrypoint for the Cloudera AI Process Mapper.

Runs as a single Cloudera AI (CML) Application process: it serves the JSON API and,
when a built frontend is present, the static React app on the same origin.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, ingestion

config.ensure_dirs()
db.init_db()

app = FastAPI(
    title="Cloudera AI Process Mapper",
    version="0.1.0",
    description="Backend API for BPMN ingestion, editing, and agentic mapping.",
)

# Permissive CORS for local dev (Vite dev server on a different port). In the
# packaged CML Application the frontend is same-origin, so this is a no-op there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness probe used by the CML Application health check."""
    return {"status": "ok", "service": "process-mapper", "version": app.version}


@app.post("/api/upload", tags=["ingestion"])
async def upload_process(file: UploadFile = File(...)) -> dict:
    """Steps 1-2 [Upload + Ingestion]: accept a BPMN file, store it verbatim, then
    parse it into a graph (nodes/edges/lanes) and persist to SQLite.

    The raw XML is kept on disk *and* in the ``process`` row for provenance. Layout
    comes from Diagram Interchange when present, otherwise the cascade fallback.
    """
    filename = file.filename or "upload.xml"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in config.ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(config.ALLOWED_SUFFIXES)}",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds max size of {config.MAX_UPLOAD_BYTES} bytes.",
        )

    upload_id = uuid.uuid4().hex
    stored_path = config.UPLOAD_DIR / f"{upload_id}_{filename}"
    stored_path.write_bytes(contents)

    try:
        xml_text = contents.decode("utf-8")
    except UnicodeDecodeError:
        xml_text = contents.decode("utf-8", errors="replace")

    try:
        with db.get_conn() as conn:
            summary = ingestion.ingest_bpmn(conn, filename, xml_text)
    except ET.ParseError as exc:
        raise HTTPException(status_code=422, detail=f"Malformed XML: {exc}") from exc

    return {
        "upload_id": upload_id,
        "size_bytes": len(contents),
        "stored_path": str(stored_path.relative_to(config.REPO_ROOT)),
        "received_at": datetime.now(timezone.utc).isoformat(),
        **summary,
    }


@app.get("/api/processes", tags=["ingestion"])
def list_processes() -> dict:
    with db.get_conn() as conn:
        return {"processes": ingestion.list_processes(conn)}


@app.get("/api/processes/{process_id}", tags=["ingestion"])
def get_process_graph(process_id: str) -> dict:
    """Return the persisted graph (nodes with X/Y, edges, lanes) for the canvas."""
    with db.get_conn() as conn:
        graph = ingestion.get_graph(conn, process_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Process not found.")
    return graph


class NodePosition(BaseModel):
    x: float
    y: float


@app.patch("/api/processes/{process_id}/nodes/{node_id}/position", tags=["editor"])
def update_node_position(process_id: str, node_id: str, pos: NodePosition) -> dict:
    """Step 3 [Constrained Editor]: persist a dragged node's X/Y coordinates."""
    with db.get_conn() as conn:
        ok = ingestion.update_node_position(conn, process_id, node_id, pos.x, pos.y)
    if not ok:
        raise HTTPException(status_code=404, detail="Node not found for this process.")
    return {"node_id": node_id, "x": pos.x, "y": pos.y}


# --- Static frontend (served only when a build exists) -----------------------
# Mounted last so it never shadows the API routes above.
if config.FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(config.FRONTEND_DIST), html=True),
        name="frontend",
    )
else:

    @app.get("/", tags=["ops"])
    def frontend_missing() -> FileResponse | dict:
        return {
            "message": "Frontend build not found. Run `npm run build` in ./frontend, "
            "or use the Vite dev server during development.",
            "health": "/health",
            "docs": "/docs",
        }

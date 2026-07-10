"""FastAPI entrypoint for the Cloudera AI Process Mapper.

Runs as a single Cloudera AI (CML) Application process: it serves the JSON API and,
when a built frontend is present, the static React app on the same origin.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import Cookie, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, discovery, groups, ingestion, metadata as metadata_svc, suggest

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


class BboxGeometry(BaseModel):
    x: float
    y: float
    width: float
    height: float


class CreateGroupRequest(BaseModel):
    node_ids: list[str]
    bbox: Optional[BboxGeometry] = None


@app.post("/api/processes/{process_id}/groups", tags=["groups"])
def create_group(process_id: str, body: CreateGroupRequest) -> dict:
    """Step 4 [Agentic Underlay]: bounding-box group — assign nodes to one group."""
    bbox = body.bbox.model_dump() if body.bbox else None
    try:
        with db.get_conn() as conn:
            if ingestion.get_graph(conn, process_id) is None:
                raise HTTPException(status_code=404, detail="Process not found.")
            return groups.create_group(conn, process_id, body.node_ids, bbox)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/processes/{process_id}/groups/{group_id}", tags=["groups"])
def delete_group(process_id: str, group_id: str) -> dict:
    with db.get_conn() as conn:
        ok = groups.delete_group(conn, process_id, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Group not found.")
    return {"deleted": group_id}


class MetadataPayload(BaseModel):
    """Step 5a fields — all optional on PATCH; omitted fields are stored as null."""

    name: Optional[str] = None
    owner: Optional[str] = None
    duration_value: Optional[int] = None
    duration_unit: Optional[Literal["minutes", "hours", "days"]] = None
    description: Optional[str] = None


class MetadataUpsertRequest(BaseModel):
    owner_type: Literal["node", "group"]
    owner_id: str
    metadata: MetadataPayload


@app.get("/api/discovery", tags=["discovery"])
async def get_discovery(
    _cdswuserstoken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> discovery.DiscoveryResponse:
    """Step 5b [Discovery Auth Passthrough]: platform capability matrix with sandbox fallback."""
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip() or None
    if not token:
        token = _cdswuserstoken
    return await discovery.fetch_platform_capabilities(token)


@app.post("/api/processes/{process_id}/suggest", tags=["agentic"])
async def suggest_optimization(
    process_id: str,
    _cdswuserstoken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Step 5c [Draft Optimization]: infer, validate, and persist a proposed agentic group."""
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip() or None
    if not token:
        token = _cdswuserstoken
    try:
        with db.get_conn() as conn:
            return await suggest.generate_suggestion(conn, process_id, token)
    except ValueError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg) from exc


@app.patch("/api/processes/{process_id}/metadata", tags=["metadata"])
def upsert_metadata(process_id: str, body: MetadataUpsertRequest) -> dict:
    """Step 5a [Metadata Persistence]: upsert metadata for a node or group."""
    try:
        with db.get_conn() as conn:
            if ingestion.get_graph(conn, process_id) is None:
                raise HTTPException(status_code=404, detail="Process not found.")
            saved = metadata_svc.upsert_metadata(
                conn,
                process_id,
                body.owner_type,
                body.owner_id,
                **body.metadata.model_dump(),
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "owner_type": body.owner_type,
        "owner_id": body.owner_id,
        "metadata": saved,
    }


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

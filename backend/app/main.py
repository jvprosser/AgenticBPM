"""FastAPI entrypoint for the Cloudera AI Process Mapper.

Runs as a single Cloudera AI (CML) Application process: it serves the JSON API and,
when a built frontend is present, the static React app on the same origin.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Literal, Optional

from fastapi import Cookie, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import config, db, analytics, discovery, groups, ingestion, metadata as metadata_svc, overrides, suggest
from .schemas.metadata import GroupMetadata, NodeTaskMetadata

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


class ProcessSummary(BaseModel):
    id: str
    process_name: str
    filename: str
    description: Optional[str] = None
    created_at: str
    updated_at: str
    leverage_multiplier: float
    node_count: int


class ProcessListResponse(BaseModel):
    processes: list[ProcessSummary]


class ProcessPatchRequest(BaseModel):
    process_name: Optional[str] = None
    description: Optional[str] = None


async def _ingest_upload(file: UploadFile) -> dict:
    """Shared BPMN upload handler for registry ingest routes."""
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

    try:
        xml_text = contents.decode("utf-8")
    except UnicodeDecodeError:
        xml_text = contents.decode("utf-8", errors="replace")

    try:
        with db.get_conn() as conn:
            summary = ingestion.ingest_bpmn(conn, filename, xml_text)
    except ET.ParseError as exc:
        raise HTTPException(status_code=422, detail=f"Malformed XML: {exc}") from exc

    summary["size_bytes"] = len(contents)
    return summary


@app.post("/api/processes", tags=["ingestion"])
async def create_process(file: UploadFile = File(...)) -> dict:
    """Ingest a BPMN file into the Process Registry and return the new process id."""
    return await _ingest_upload(file)


@app.post("/api/upload", tags=["ingestion"])
async def upload_process(file: UploadFile = File(...)) -> dict:
    """Legacy upload route — delegates to ``POST /api/processes``."""
    summary = await _ingest_upload(file)
    return {
        **summary,
        "upload_id": summary["id"],
        "stored_path": None,
        "received_at": summary.get("created_at"),
    }


@app.get("/api/processes", tags=["ingestion"], response_model=ProcessListResponse)
def list_processes() -> ProcessListResponse:
    with db.get_conn() as conn:
        rows = ingestion.list_processes(conn)
        enriched = [
            {
                **row,
                "leverage_multiplier": analytics.calculate_leverage_multiplier(
                    row["id"], conn
                ),
            }
            for row in rows
        ]
        return ProcessListResponse(processes=enriched)


@app.get("/api/processes/{process_id}", tags=["ingestion"])
def get_process_graph(process_id: str) -> dict:
    """Restore a saved process: full graph (nodes, edges, lanes, groups) for the canvas."""
    with db.get_conn() as conn:
        graph = ingestion.get_graph(conn, process_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Process not found.")
    return graph


@app.patch("/api/processes/{process_id}", tags=["ingestion"])
def patch_process(process_id: str, body: ProcessPatchRequest) -> dict:
    """Update registry metadata such as ``process_name`` or ``description``."""
    if body.process_name is None and body.description is None:
        raise HTTPException(status_code=400, detail="No fields to update.")
    try:
        with db.get_conn() as conn:
            updated = ingestion.update_process_fields(
                conn,
                process_id,
                process_name=body.process_name,
                description=body.description,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Process not found.")
    return updated


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


class StrategicOverrideRequest(BaseModel):
    node_ids: list[str]


@app.post("/api/processes/{process_id}/overrides", tags=["overrides"])
def create_strategic_override(process_id: str, body: StrategicOverrideRequest) -> dict:
    """Record a strategic boundary override and purge matching proposed agentic groups."""
    try:
        with db.get_conn() as conn:
            result = overrides.record_override(conn, process_id, body.node_ids)
            db.touch_process_updated_at(conn, process_id)
            return result
    except ValueError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg) from exc


@app.post("/api/processes/{process_id}/groups", tags=["groups"])
def create_group(process_id: str, body: CreateGroupRequest) -> dict:
    """Step 4 [Agentic Underlay]: bounding-box group — assign nodes to one group."""
    bbox = body.bbox.model_dump() if body.bbox else None
    try:
        with db.get_conn() as conn:
            if ingestion.get_graph(conn, process_id) is None:
                raise HTTPException(status_code=404, detail="Process not found.")
            result = groups.create_group(conn, process_id, body.node_ids, bbox)
            db.touch_process_updated_at(conn, process_id)
            return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/processes/{process_id}/groups/{group_id}", tags=["groups"])
def delete_group(process_id: str, group_id: str) -> dict:
    with db.get_conn() as conn:
        ok = groups.delete_group(conn, process_id, group_id)
        if ok:
            db.touch_process_updated_at(conn, process_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Group not found.")
    return {"deleted": group_id}


class MetadataUpsertRequest(BaseModel):
    owner_type: Literal["node", "group"]
    owner_id: str
    metadata: dict


def _resolve_user_token(
    _cdswuserstoken: Optional[str],
    authorization: Optional[str],
) -> Optional[str]:
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip() or None
    if not token:
        token = _cdswuserstoken
    return token


@app.get("/api/discovery", tags=["discovery"])
async def get_discovery(
    _cdswuserstoken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> discovery.DiscoveryResponse:
    """Step 5b [Discovery Auth Passthrough]: platform capability matrix with sandbox fallback."""
    token = _resolve_user_token(_cdswuserstoken, authorization)
    return await discovery.fetch_platform_capabilities(token)


@app.post("/api/processes/{process_id}/suggest", tags=["agentic"])
async def suggest_optimization(
    process_id: str,
    _cdswuserstoken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Step 5c [Draft Optimization]: dispatch process state to Cloudera executeAgent."""
    token = _resolve_user_token(_cdswuserstoken, authorization)
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
        if body.owner_type == "node":
            validated = NodeTaskMetadata.from_payload(body.metadata)
            payload = validated.model_dump()
        else:
            validated = GroupMetadata.model_validate(body.metadata)
            payload = validated.model_dump()
        with db.get_conn() as conn:
            if ingestion.get_graph(conn, process_id) is None:
                raise HTTPException(status_code=404, detail="Process not found.")
            saved = metadata_svc.upsert_metadata(
                conn,
                process_id,
                body.owner_type,
                body.owner_id,
                payload,
            )
            db.touch_process_updated_at(conn, process_id)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
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

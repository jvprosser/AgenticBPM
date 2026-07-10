# Cloudera AI Process Mapper

A demo application showcasing Cloudera AI + the Cloudera Data Platform: a business
process mapping tool that ingests BPMN, lets users visually wrap subprocesses in
proposed Cloudera Agent Studio workflows, and quantifies the automation payoff.

See [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md) for the full technical design.

## Status

Implemented so far (per the blueprint execution plan):

- **Step 0 — Scaffold:** FastAPI backend with a `/health` probe, packaged to run as a
  single Cloudera AI (CML) Application that also serves the built React frontend.
- **Step 1 — Upload:** React file dropzone → `POST /api/upload` validates and stores
  the raw BPMN/XPDL file verbatim (provenance).
- **Step 2 — Ingestion:** namespace-tolerant BPMN parser extracts nodes, edges, lanes,
  nested subprocess children, and boundary-event links; uses Diagram Interchange
  coordinates when present, otherwise a deterministic "dumb cascade" layout. Persists
  to SQLite (WAL mode). The graph is served for the canvas via `GET /api/processes/{id}`.
- **Step 3 — Constrained Editor:** React Flow canvas renders the graph from SQLite with
  category-colored BPMN nodes and **locked topology** (no connect/delete; drag-to-
  reposition only). Drag-end persists X/Y back to SQLite via
  `PATCH /api/processes/{id}/nodes/{node_id}/position`, debounced per node (~250ms).
- **Step 4 — Agentic Underlay:** box-select → create group; purple bbox overlays.
- **Step 5a — Metadata Persistence:** click node or group overlay → metadata popover;
  debounced PATCH to SQLite; survives page refresh.

## Architecture

- **Backend:** FastAPI (`backend/`). Binds `CDSW_APP_PORT` on CML, `8000` locally.
- **Frontend:** React + Vite + TypeScript (`frontend/`). Built output is served by
  FastAPI so the whole app is one process.
- **Storage:** uploaded files land in `data/uploads/`; the parsed graph is persisted
  to embedded SQLite at `data/mapper.sqlite` (both git-ignored). Iceberg sync arrives
  in a later step.

## Local development

Two options.

### Single process (mirrors CML)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
(cd frontend && npm install && npm run build)
cd backend && python main.py
# open http://localhost:8000
```

### Hot-reload dev (two terminals)

```bash
# terminal 1 — backend
source .venv/bin/activate && cd backend && python main.py

# terminal 2 — frontend (Vite proxies /api and /health to :8000)
cd frontend && npm run dev
# open http://localhost:5173
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe (used by the CML Application health check). |
| POST | `/api/upload` | Multipart upload of a BPMN file; validates, stores raw XML, parses + persists the graph. |
| GET | `/api/processes` | List ingested processes. |
| GET | `/api/processes/{id}` | Full graph (nodes with X/Y, edges, lanes) for the canvas. |
| PATCH | `/api/processes/{id}/nodes/{node_id}/position` | Persist a dragged node's X/Y. |
| POST | `/api/processes/{id}/groups` | Create agentic underlay group (node_ids + optional bbox). |
| DELETE | `/api/processes/{id}/groups/{group_id}` | Remove a group and clear node membership. |
| PATCH | `/api/processes/{id}/metadata` | Upsert metadata for a node or group. |
| GET | `/docs` | Auto-generated OpenAPI docs. |

Quick check:

```bash
curl http://localhost:8000/health
curl -F "file=@docs/Claims_process.xml" http://localhost:8000/api/upload
```

## Deploying as a Cloudera AI Application

1. Push this repo into a Cloudera AI (CML) Project.
2. In a **Session**, run the one-command build (installs Node into `$HOME` — no root
   needed — builds the frontend, and installs backend deps):
   ```bash
   bash scripts/cml_build.sh
   ```
   (Override the Node version with `NODE_VERSION=v22.x.x bash scripts/cml_build.sh`.)
3. Create an **Application** with:
   - **Script / command:** `python backend/main.py`
   - The app binds `CDSW_APP_PORT` automatically and listens on `0.0.0.0`.
4. CML routes the Application subdomain to the process; `/health` backs the check.

> Single-replica by design (embedded SQLite in project storage, later steps). Do not
> scale the Application horizontally.

### Troubleshooting

- **`asyncio.run() cannot be called from a running event loop`** — CML Applications run
  inside a notebook kernel. `backend/main.py` spawns uvicorn as a **child subprocess**
  (`python -m uvicorn …`), so the server runs outside the kernel's event loop entirely.
- **`No module named 'app'`** — the entrypoint sets `BACKEND_DIR` / `sys.path` and runs
  uvicorn with `cwd=backend/`. Override with `BACKEND_DIR=...` if your layout differs.
- **`[Errno 98] address already in use`** — re-running the entrypoint cell used to start
  a second server. `run()` now detects an already-healthy server and **attaches** to the
  existing subprocess (no second bind). To force a restart, interrupt the cell or restart
  the kernel first.

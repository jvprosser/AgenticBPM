"""Runtime configuration for the Cloudera AI Process Mapper backend.

Values resolve from environment variables so the same code runs locally and as a
Cloudera AI (CML) Application. CML injects ``CDSW_APP_PORT`` and expects the app to
bind it; locally we fall back to 8000.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = two levels up from this file (backend/app/config.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]

# CML sets CDSW_APP_PORT for Applications; default to 8000 for local dev.
APP_PORT = int(os.environ.get("CDSW_APP_PORT", "8000"))

# Bind all interfaces so the CML Application proxy can reach the process.
APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")

# Where uploaded process files are persisted. Under CML this lives in project
# storage so it survives Application restarts.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(REPO_ROOT / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"

# Embedded SQLite operational store (lives in CML project storage).
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR / "mapper.sqlite")))

# Built React frontend (Vite output). Served by FastAPI when present so the whole
# app runs as a single CML Application process.
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# Guardrail: reject absurdly large uploads early (BPMN/XPDL are small XML files).
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

ALLOWED_SUFFIXES = {".xml", ".bpmn", ".bpmn20.xml", ".xpdl"}

# Runtime handle for the uvicorn subprocess (set by backend/main.py). Stored here so it
# survives notebook cell re-runs — imported modules are not re-executed.
_pm_proc = None


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

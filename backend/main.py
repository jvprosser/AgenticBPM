"""Process entrypoint. `python backend/main.py` (or the CML Application command).

Binds CDSW_APP_PORT when running as a Cloudera AI Application; 8000 locally.
"""

from __future__ import annotations

import uvicorn

from app import config


def run() -> None:
    uvicorn.run(
        "app.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    run()

"""Process entrypoint. `python backend/main.py` (or the CML Application command).

Binds CDSW_APP_PORT when running as a Cloudera AI Application; 8000 locally.

Cloudera AI Applications execute this script inside a notebook/IPython kernel that
already owns a running asyncio event loop. uvicorn's ``uvicorn.run()`` (and
``asyncio.run()``) cannot be called from within a running loop, so we always serve
uvicorn on a dedicated thread that gets its own fresh event loop. This works whether
the entrypoint is invoked from a notebook cell or a plain shell process.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

# Make the `app` package importable regardless of the current working directory
# (CML notebook engines may launch this from the project root, not backend/).
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import uvicorn  # noqa: E402  (import after sys.path fix)

from app import config  # noqa: E402


def run() -> None:
    """Start the API server on a dedicated thread and block until it stops.

    Serving on a separate thread (with its own event loop) sidesteps the
    "asyncio.run() cannot be called from a running event loop" error raised when
    this runs inside the CML/IPython engine loop. ``thread.join()`` keeps the call
    blocking, so the process behaves like a normal long-running server.
    """
    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host=config.APP_HOST,
            port=config.APP_PORT,
            log_level="info",
        )
    )

    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve()),
        name="uvicorn",
        daemon=True,
    )
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    run()

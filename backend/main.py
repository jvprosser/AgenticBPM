"""Process entrypoint. `python backend/main.py` (or the CML Application command).

Binds CDSW_APP_PORT when running as a Cloudera AI Application; 8000 locally.

Cloudera AI Applications execute this script inside a notebook/IPython kernel that
already owns a running asyncio event loop, so we always serve uvicorn on a dedicated
thread with its own fresh event loop (``uvicorn.run()``/``asyncio.run()`` cannot be
called from within a running loop).

Because that kernel is long-lived, re-running the entrypoint must not leave an old
server bound to the port (which causes "[Errno 98] address already in use"). We stash
the live server on the cached ``config`` module so a re-run can stop the previous one
first — this survives even a full re-execution of this file as a notebook cell, since
imported modules are not re-executed.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time

# Resolve the backend dir robustly. In a pasted notebook cell __file__ is undefined,
# so fall back to the CDSW project layout / cwd. Override with BACKEND_DIR if needed.
try:
    _BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _cwd = os.getcwd()
    _BACKEND_DIR = _cwd if os.path.basename(_cwd) == "backend" else os.path.join(_cwd, "backend")
_BACKEND_DIR = os.environ.get("BACKEND_DIR", _BACKEND_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import uvicorn  # noqa: E402  (import after sys.path fix)

from app import config  # noqa: E402


def _stop_existing() -> None:
    """Stop a server started by a previous run in this (persistent) kernel."""
    prev_server = getattr(config, "_pm_server", None)
    prev_thread = getattr(config, "_pm_thread", None)
    if prev_server is not None:
        prev_server.should_exit = True
    if prev_thread is not None and prev_thread.is_alive():
        prev_thread.join(timeout=10)
    config._pm_server = None  # type: ignore[attr-defined]
    config._pm_thread = None  # type: ignore[attr-defined]


def _wait_port_free(host: str, port: int, timeout: float = 10.0) -> bool:
    """Poll until nothing is listening on host:port (or timeout). Returns True if free."""
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex((probe_host, port)) != 0:
                return True
        time.sleep(0.25)
    return False


def run() -> None:
    """Start the API server on a dedicated thread and block until it stops."""
    _stop_existing()
    if not _wait_port_free(config.APP_HOST, config.APP_PORT):
        print(
            f"WARNING: {config.APP_HOST}:{config.APP_PORT} still in use after waiting; "
            "an external process may hold it. Restart the kernel or free the port.",
            file=sys.stderr,
        )

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
    config._pm_server = server  # type: ignore[attr-defined]
    config._pm_thread = thread  # type: ignore[attr-defined]
    thread.start()

    try:
        thread.join()
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    run()

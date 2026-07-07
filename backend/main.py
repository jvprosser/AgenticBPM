"""Process entrypoint. `python backend/main.py` (or the CML Application command).

Binds CDSW_APP_PORT when running as a Cloudera AI Application; 8000 locally.

Cloudera AI Applications execute this script inside a notebook/IPython kernel that
already owns a running asyncio event loop, so we always serve uvicorn on a dedicated
thread with its own fresh event loop.

Re-running the entrypoint cell must not attempt a second bind on the same port. If our
server is already healthy, we attach to the existing thread (no-op restart). Otherwise
we stop any prior instance and wait until the port is actually free before binding.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

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

_SHUTDOWN_TIMEOUT_S = 30.0
_PORT_FREE_TIMEOUT_S = 20.0


def _probe_host(host: str) -> str:
    return "127.0.0.1" if host in ("", "0.0.0.0") else host


def _health_ok(host: str, port: int) -> bool:
    url = f"http://{_probe_host(host)}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((_probe_host(host), port)) == 0


def _wait_port_free(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_in_use(host, port):
            return True
        time.sleep(0.25)
    return False


def _already_running() -> bool:
    thread = config._pm_thread
    if thread is None or not thread.is_alive():
        return False
    return _health_ok(config.APP_HOST, config.APP_PORT)


def _stop_existing() -> None:
    """Stop a server started by a previous run in this (persistent) kernel."""
    server = config._pm_server
    thread = config._pm_thread
    if server is not None:
        server.should_exit = True
    if thread is not None and thread.is_alive():
        thread.join(timeout=_SHUTDOWN_TIMEOUT_S)
        if thread.is_alive():
            raise RuntimeError(
                f"Previous server thread did not stop within {_SHUTDOWN_TIMEOUT_S:.0f}s. "
                f"Restart the CML kernel to free port {config.APP_PORT}."
            )
    config._pm_server = None
    config._pm_thread = None
    if not _wait_port_free(config.APP_HOST, config.APP_PORT, _PORT_FREE_TIMEOUT_S):
        raise RuntimeError(
            f"Port {config.APP_PORT} is still in use after shutdown. "
            "Another process may hold it — restart the CML kernel, or stop the "
            "other Application using this port."
        )


def run() -> None:
    """Start the API server on a dedicated thread and block until it stops."""
    if _already_running():
        print(
            f"Process Mapper already serving on port {config.APP_PORT} "
            f"(re-run attaches to the running server; no restart)."
        )
        try:
            config._pm_thread.join()
        except KeyboardInterrupt:
            _stop_existing()
        return

    if _port_in_use(config.APP_HOST, config.APP_PORT) and not _health_ok(
        config.APP_HOST, config.APP_PORT
    ):
        raise RuntimeError(
            f"Port {config.APP_PORT} is in use but is not our /health endpoint. "
            "Restart the CML kernel or free the port before starting."
        )

    _stop_existing()

    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host=config.APP_HOST,
            port=config.APP_PORT,
            log_level="info",
            timeout_graceful_shutdown=10,
        )
    )
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve()),
        name="uvicorn",
        daemon=True,
    )
    config._pm_server = server
    config._pm_thread = thread
    thread.start()

    # Give uvicorn a moment to bind before we declare success.
    deadline = time.time() + 10
    while time.time() < deadline:
        if not thread.is_alive():
            raise RuntimeError(
                f"Server failed to start on port {config.APP_PORT}. "
                "Check logs above for bind errors (e.g. address already in use)."
            )
        if _health_ok(config.APP_HOST, config.APP_PORT):
            print(f"Process Mapper serving on port {config.APP_PORT}")
            break
        time.sleep(0.2)
    else:
        raise RuntimeError(
            f"Server thread started but /health did not respond on port {config.APP_PORT}."
        )

    try:
        thread.join()
    except KeyboardInterrupt:
        _stop_existing()


if __name__ == "__main__":
    run()

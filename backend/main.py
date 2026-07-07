"""Process entrypoint. `python backend/main.py` (or the CML Application command).

Binds CDSW_APP_PORT when running as a Cloudera AI Application; 8000 locally.

Cloudera AI Applications execute this script inside a notebook/IPython kernel. Rather
than starting uvicorn in that kernel (asyncio/event-loop conflicts), we spawn uvicorn
as a **child process** with ``subprocess.Popen`` — the same pattern used for
``streamlit run`` in CML Applications.

Re-running the entrypoint cell must not start a second server on the same port. If our
server is already healthy, we attach to the existing subprocess (no-op restart).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
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
BACKEND_DIR = os.environ.get("BACKEND_DIR", _BACKEND_DIR)

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app import config  # noqa: E402  (import after sys.path fix)

_SHUTDOWN_TIMEOUT_S = 30.0
_PORT_FREE_TIMEOUT_S = 20.0
_STARTUP_TIMEOUT_S = 15.0


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


def _proc_alive() -> bool:
    proc = config._pm_proc
    return proc is not None and proc.poll() is None


def _already_running() -> bool:
    return _proc_alive() and _health_ok(config.APP_HOST, config.APP_PORT)


def _stop_existing() -> None:
    """Stop a uvicorn subprocess started by a previous run in this kernel."""
    proc = config._pm_proc
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=_SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    config._pm_proc = None
    if not _wait_port_free(config.APP_HOST, config.APP_PORT, _PORT_FREE_TIMEOUT_S):
        raise RuntimeError(
            f"Port {config.APP_PORT} is still in use after shutdown. "
            "Restart the CML kernel, or stop the other Application using this port."
        )


def _uvicorn_cmd() -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        config.APP_HOST,
        "--port",
        str(config.APP_PORT),
        "--log-level",
        "info",
    ]


def run() -> None:
    """Spawn uvicorn in a subprocess and block until it exits."""
    if _already_running():
        print(
            f"Process Mapper already serving on port {config.APP_PORT} "
            f"(re-run attaches to the running server; no restart)."
        )
        try:
            config._pm_proc.wait()
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

    proc = subprocess.Popen(
        _uvicorn_cmd(),
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
    )
    config._pm_proc = proc

    deadline = time.time() + _STARTUP_TIMEOUT_S
    while time.time() < deadline:
        code = proc.poll()
        if code is not None:
            raise RuntimeError(
                f"Uvicorn exited during startup (code {code}). "
                "Check stderr above for bind errors (e.g. address already in use)."
            )
        if _health_ok(config.APP_HOST, config.APP_PORT):
            print(f"Process Mapper serving on port {config.APP_PORT} (pid {proc.pid})")
            break
        time.sleep(0.2)
    else:
        _stop_existing()
        raise RuntimeError(
            f"Uvicorn started (pid {proc.pid}) but /health did not respond "
            f"within {_STARTUP_TIMEOUT_S:.0f}s on port {config.APP_PORT}."
        )

    try:
        proc.wait()
    except KeyboardInterrupt:
        _stop_existing()


if __name__ == "__main__":
    run()

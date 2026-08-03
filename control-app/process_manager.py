"""Core start/stop/status logic for the STLVault backend + frontend dev
servers. No GUI code here on purpose — this module is unit tested with
mocked subprocess/urllib calls; stlvault_control.pyw wires it to buttons.
"""

import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
DEV_DATA_DIR = REPO_ROOT / "dev-data"
RUN_DIR = REPO_ROOT / "control-app" / ".run"

BACKEND_PORT = 8998
FRONTEND_PORT = 5173

BACKEND_PIDFILE = RUN_DIR / "backend.pid"
FRONTEND_PIDFILE = RUN_DIR / "frontend.pid"
BACKEND_LOG = RUN_DIR / "backend.log"
FRONTEND_LOG = RUN_DIR / "frontend.log"

# CREATE_NO_WINDOW so launching from the (windowed, console-less) control
# app doesn't pop up a cmd window for every child process.
_NO_WINDOW = 0x08000000


class SetupIncomplete(Exception):
    """Raised when a prerequisite (venv, node_modules, bun on PATH) is
    missing, with a message telling the user exactly what to run."""


class PortInUse(Exception):
    """Raised instead of starting a second server on a port something is
    already listening on. Confirmed live: without this guard, clicking
    Start while a server was already running in a separate terminal spawned
    a second backend that got stuck before binding (port taken) and a
    second frontend that silently fell back to the next free port — two
    untracked, confusing duplicate processes instead of one clear error."""


def _port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _find_pid_on_port(port: int) -> int | None:
    """Looks up the PID actually LISTENING on `port`, regardless of whether
    we started it. Lets Stop/status handle a server someone launched by hand
    in a separate terminal, not just ones we tracked via our own pidfile."""
    result = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    needle = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 5 or parts[0] != "TCP":
            continue
        local_addr, state, pid = parts[1], parts[3], parts[4]
        if local_addr.endswith(needle) and state == "LISTENING":
            try:
                return int(pid)
            except ValueError:
                continue
    return None


def venv_python() -> Path:
    return BACKEND_DIR / ".venv" / "Scripts" / "python.exe"


def setup_status() -> dict:
    return {
        "venv": venv_python().exists(),
        "dev_data": (DEV_DATA_DIR / "data").is_dir() and (DEV_DATA_DIR / "uploads").is_dir(),
        "node_modules": (FRONTEND_DIR / "node_modules").is_dir(),
    }


def ensure_dev_data_dirs() -> None:
    (DEV_DATA_DIR / "uploads" / "manuals").mkdir(parents=True, exist_ok=True)
    (DEV_DATA_DIR / "data").mkdir(parents=True, exist_ok=True)


def _write_pid(pidfile: Path, pid: int) -> None:
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(pid))


def _read_pid(pidfile: Path):
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text().strip())
    except ValueError:
        return None


def is_pid_running(pid) -> bool:
    if pid is None:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return str(pid) in result.stdout


def start_backend(log_fn=lambda msg: None) -> int:
    if not venv_python().exists():
        raise SetupIncomplete(
            f"Backend venv not found at {venv_python()} — run:\n"
            f"  cd backend && python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt"
        )
    if _port_in_use(BACKEND_PORT):
        raise PortInUse(
            f"Port {BACKEND_PORT} is already in use — a backend may already be running "
            f"(check another terminal window) or click Stop first."
        )

    ensure_dev_data_dirs()
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["DB_PATH"] = str(DEV_DATA_DIR / "data" / "data.db")
    env["FILE_STORAGE"] = str(DEV_DATA_DIR / "uploads")

    log_file = open(BACKEND_LOG, "a", buffering=1)
    proc = subprocess.Popen(
        [str(venv_python()), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=_NO_WINDOW,
    )
    _write_pid(BACKEND_PIDFILE, proc.pid)
    log_fn(f"Backend started (pid {proc.pid}) - http://localhost:{BACKEND_PORT}")
    return proc.pid


def start_frontend(log_fn=lambda msg: None) -> int:
    if not (FRONTEND_DIR / "node_modules").is_dir():
        raise SetupIncomplete(
            f"Frontend dependencies not found at {FRONTEND_DIR / 'node_modules'} — run:\n"
            f"  cd frontend && bun install"
        )
    bun = shutil.which("bun")
    if not bun:
        raise SetupIncomplete("bun was not found on PATH — install it from https://bun.sh")
    if _port_in_use(FRONTEND_PORT):
        raise PortInUse(
            f"Port {FRONTEND_PORT} is already in use — a frontend may already be running "
            f"(check another terminal window) or click Stop first."
        )

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(FRONTEND_LOG, "a", buffering=1)
    proc = subprocess.Popen(
        [bun, "run", "dev"],
        cwd=str(FRONTEND_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=_NO_WINDOW,
    )
    _write_pid(FRONTEND_PIDFILE, proc.pid)
    log_fn(f"Frontend started (pid {proc.pid}) - http://localhost:{FRONTEND_PORT}")
    return proc.pid


def _kill_pid(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, creationflags=_NO_WINDOW,
    )


def stop_process(pidfile: Path, port: int, log_fn=lambda msg: None, name: str = "process") -> bool:
    pid = _read_pid(pidfile)
    if pid is not None and is_pid_running(pid):
        _kill_pid(pid)
        pidfile.unlink(missing_ok=True)
        log_fn(f"Stopped {name} (pid {pid})")
        return True
    pidfile.unlink(missing_ok=True)

    # Nothing we tracked ourselves — but something may still be listening on
    # the port (started by hand in a terminal). Reset that too.
    fallback_pid = _find_pid_on_port(port)
    if fallback_pid is not None:
        _kill_pid(fallback_pid)
        log_fn(f"Stopped untracked {name} on port {port} (pid {fallback_pid})")
        return True

    log_fn(f"{name} is not running")
    return False


def backend_health() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/api/folders", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


def frontend_health() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{FRONTEND_PORT}/", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _service_status(pidfile: Path, port: int, health_fn) -> dict:
    pid = _read_pid(pidfile)
    tracked = pid is not None and is_pid_running(pid)
    if not tracked:
        pid = _find_pid_on_port(port)
    return {
        "pid": pid,
        "process_running": pid is not None,
        "healthy": health_fn(),
        "tracked": tracked,
    }


def status() -> dict:
    return {
        "backend": _service_status(BACKEND_PIDFILE, BACKEND_PORT, backend_health),
        "frontend": _service_status(FRONTEND_PIDFILE, FRONTEND_PORT, frontend_health),
    }

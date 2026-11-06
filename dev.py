#!/usr/bin/env python3
"""Run the TA Platform backend and frontend together.

Usage:
    python dev.py

The launcher:
  * reads `.env` from the repository root and `backend/.env` when present;
  * builds DATABASE_URL from the TA_DB_* values when it is not set;
  * creates the configured PostgreSQL database if it is missing (local/dev/test
    environments only);
  * applies Alembic migrations;
  * starts uvicorn on http://localhost:8000 and Next.js on http://localhost:3000;
  * streams both logs with [backend] / [frontend] prefixes;
  * stops both child processes on Ctrl+C.

Prerequisites: a Python environment for the backend (preferably
`backend/.venv`), Bun, and a reachable PostgreSQL configured through
DATABASE_URL or TA_DB_* in `.env`.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
BACKEND_URL = "http://localhost:8000"
BACKEND_HEALTH = BACKEND_URL + "/health/live"
FRONTEND_URL = "http://localhost:3000"
FRONTEND_PORT = 3000
LOCAL_ENVS = {"local", "development", "test"}


def log(message: str) -> None:
    print(f"[dev] {message}", flush=True)


def load_env_file(path: Path, env: dict[str, str]) -> None:
    """Load simple KEY=VALUE lines without overriding real environment vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in env:
            env[key] = value


def find_backend_python() -> Path:
    candidates = [
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
        BACKEND_DIR / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("[dev] No Python interpreter found.")


def build_database_url(env: dict[str, str]) -> tuple[str, dict[str, str | int]]:
    """Return the SQLAlchemy URL and the parts needed to create the database."""
    configured = env.get("DATABASE_URL", "").strip()
    if configured:
        parsed = urllib.parse.urlparse(configured)
        if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
            raise SystemExit(
                "[dev] DATABASE_URL must be a PostgreSQL URL "
                "(postgresql:// or postgresql+asyncpg://)."
            )
        query = urllib.parse.parse_qs(parsed.query)
        host = query.get("host", [parsed.hostname or "127.0.0.1"])[0]
        database = urllib.parse.unquote(parsed.path.lstrip("/"))
        if not database:
            raise SystemExit("[dev] DATABASE_URL does not contain a database name.")
        return configured, {
            "host": host,
            "port": parsed.port or 5432,
            "user": urllib.parse.unquote(parsed.username or ""),
            "password": urllib.parse.unquote(parsed.password or ""),
            "name": database,
        }

    user = env.get("TA_DB_USER", "ta_user")
    password = env.get("TA_DB_PASSWORD", "")
    host = env.get("TA_DB_HOST", "127.0.0.1")
    port = env.get("TA_DB_PORT", "5432")
    database = env.get("TA_DB_NAME", "ta_platform")
    if not user or not database:
        raise SystemExit(
            "[dev] DATABASE_URL is not set and TA_DB_USER/TA_DB_NAME are missing. "
            "Copy backend/.env.example to backend/.env and configure PostgreSQL."
        )

    # Allow TA_DB_HOST=host:port as a convenience.
    if ":" in host and not host.startswith("["):
        maybe_host, _, maybe_port = host.rpartition(":")
        if maybe_port.isdigit():
            host, port = maybe_host, maybe_port

    user_q = urllib.parse.quote(user, safe="")
    password_q = urllib.parse.quote(password, safe="")
    database_q = urllib.parse.quote(database, safe="")
    url = f"postgresql+asyncpg://{user_q}:{password_q}@{host}:{port}/{database_q}"
    return url, {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "name": database,
    }


DB_HELPER = r"""
import asyncio
import os

import asyncpg


async def main() -> None:
    host = os.environ["DEV_DB_HOST"]
    port = int(os.environ.get("DEV_DB_PORT") or "5432")
    user = os.environ["DEV_DB_USER"]
    password = os.environ.get("DEV_DB_PASSWORD") or None
    name = os.environ["DEV_DB_NAME"]
    options = dict(
        host=host,
        port=port,
        user=user,
        password=password,
        timeout=5,
    )

    # Prefer the target database so an existing database does not require the
    # role to have access to the maintenance database.
    try:
        conn = await asyncpg.connect(database=name, **options)
    except asyncpg.InvalidCatalogNameError:
        pass
    else:
        await conn.close()
        print(f"[dev] database {name!r} already exists", flush=True)
        return

    conn = await asyncpg.connect(database="postgres", **options)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", name
        )
        if exists:
            print(f"[dev] database {name!r} already exists", flush=True)
            return
        quoted = '"' + name.replace('"', '""') + '"'
        await conn.execute(f"CREATE DATABASE {quoted}")
        print(f"[dev] created database {name!r}", flush=True)
    finally:
        await conn.close()


asyncio.run(main())
"""


def ensure_database(
    python: Path,
    env: dict[str, str],
    parts: dict[str, str | int],
) -> None:
    name = str(parts["name"])
    if name in {"postgres", "template0", "template1"}:
        return
    if env.get("APP_ENV", "local").lower() not in LOCAL_ENVS:
        log("Skipping automatic database creation outside local/development/test.")
        return

    helper_env = env.copy()
    helper_env.update(
        {
            "DEV_DB_HOST": str(parts["host"]),
            "DEV_DB_PORT": str(parts["port"]),
            "DEV_DB_USER": str(parts["user"]),
            "DEV_DB_PASSWORD": str(parts["password"]),
            "DEV_DB_NAME": name,
        }
    )
    result = subprocess.run(
        [str(python), "-c", DB_HELPER],
        env=helper_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        raise SystemExit(
            "[dev] Could not connect to PostgreSQL. Check DATABASE_URL or the "
            "TA_DB_* values in .env. The target database must exist or the "
            "configured role must have CREATEDB permission."
        )


def check_backend_dependencies(python: Path, env: dict[str, str]) -> None:
    code = "import fastapi, uvicorn, sqlalchemy, alembic, asyncpg"
    result = subprocess.run(
        [str(python), "-c", code],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(
            f"[dev] Backend dependencies are missing for {python}.\n"
            f"      Run: {python} -m pip install -r backend/requirements.txt"
        )


def check_bun() -> str:
    bun = shutil.which("bun")
    if not bun:
        raise SystemExit(
            "[dev] Bun was not found on PATH. Install it from https://bun.sh "
            "and run this script again."
        )
    return bun


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def url_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def spawn(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    label: str,
) -> subprocess.Popen[str]:
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **kwargs,
    )
    threading.Thread(
        target=pump_output,
        args=(process, label),
        daemon=True,
    ).start()
    return process


def pump_output(process: subprocess.Popen[str], label: str) -> None:
    if process.stdout is None:
        return
    for line in process.stdout:
        print(f"[{label}] {line.rstrip()}", flush=True)


def stop(process: subprocess.Popen[str], label: str) -> None:
    if process.poll() is not None:
        return
    log(f"Stopping {label} ...")
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def configure_console() -> None:
    """Keep child output (colors, box-drawing characters) printable on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def main() -> int:
    configure_console()
    env = os.environ.copy()
    # Backend-specific values win over the repository defaults; real
    # environment variables win over both.
    load_env_file(BACKEND_DIR / ".env", env)
    load_env_file(ROOT / ".env", env)
    env.setdefault("PYTHONUNBUFFERED", "1")

    database_url, parts = build_database_url(env)
    env["DATABASE_URL"] = database_url

    python = find_backend_python()
    bun = check_bun()
    check_backend_dependencies(python, env)
    ensure_database(python, env, parts)

    log(f"Using database {parts['name']!r} on {parts['host']}:{parts['port']}")
    log("Applying Alembic migrations ...")
    migration = subprocess.run(
        [str(python), "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
    )
    if migration.returncode != 0:
        raise SystemExit("[dev] Database migrations failed.")

    backend: subprocess.Popen[str] | None = None
    frontend: subprocess.Popen[str] | None = None

    if url_ready(BACKEND_HEALTH):
        log(f"Backend already running at {BACKEND_URL}; reusing it.")
    else:
        log("Starting backend on http://localhost:8000 ...")
        backend = spawn(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--reload",
            ],
            BACKEND_DIR,
            env,
            "backend",
        )

    if port_open(FRONTEND_PORT):
        log(f"Frontend already running at {FRONTEND_URL}; reusing it.")
    else:
        log("Starting frontend on http://localhost:3000 ...")
        # Run Next.js directly instead of `bun run dev`: that package script
        # pipes through `tee`, which is not available in a default Windows
        # shell. `bun x next` works on every supported platform.
        frontend = spawn(
            [bun, "x", "next", "dev", "-p", str(FRONTEND_PORT)],
            ROOT,
            env,
            "frontend",
        )

    if backend is None and frontend is None:
        log("Backend and frontend are already running.")
        return 0

    log(f"Backend:  {BACKEND_URL}  (docs: {BACKEND_URL}/docs)")
    log(f"Frontend: {FRONTEND_URL}")
    log("Press Ctrl+C to stop.")
    try:
        while True:
            for label, process in (("backend", backend), ("frontend", frontend)):
                if process is not None and process.poll() is not None:
                    log(f"{label} exited with code {process.returncode}.")
                    return process.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("Shutting down ...")
        return 0
    finally:
        if frontend is not None:
            stop(frontend, "frontend")
        if backend is not None:
            stop(backend, "backend")


if __name__ == "__main__":
    raise SystemExit(main())

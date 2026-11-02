#!/usr/bin/env bash
# =============================================================================
# TA Platform backend launcher (foreground).
#
# Used by:
#   - mini-services/ta-api (sandbox / `bun run dev` chain)
#   - `make backend` (manual local development)
#   - docker-compose (backend service)
#
# Responsibilities:
#   1. pick a Python interpreter (sandbox venv if present)
#   2. install runtime deps if missing
#   3. ensure a PostgreSQL DATABASE_URL:
#        - postgres://... DATABASE_URL from the environment is respected
#        - otherwise boot a local no-Docker PostgreSQL (pgserver) and use it
#   4. apply Alembic migrations (idempotent)
#   5. exec uvicorn (foreground)
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT/backend"
cd "$BACKEND_DIR"

# --- 1. Python interpreter ---------------------------------------------------
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$HOME/.venv/bin/python3" ]; then
    PYTHON="$HOME/.venv/bin/python3"
  else
    PYTHON="$(command -v python3 || true)"
  fi
fi
if [ -z "$PYTHON" ]; then
  echo "[ta-api] ERROR: python3 not found. Install Python 3.12+ and retry." >&2
  exit 1
fi

# --- 2. Dependencies -----------------------------------------------------------
if ! "$PYTHON" -c "import fastapi, uvicorn, sqlalchemy, alembic, asyncpg" >/dev/null 2>&1; then
  echo "[ta-api] Installing backend dependencies (requirements.txt)..."
  "$PYTHON" -m pip install -q -r requirements.txt
fi

# --- 3. Database -----------------------------------------------------------------
case "${DATABASE_URL:-}" in
  postgresql*)
    echo "[ta-api] Using DATABASE_URL from environment."
    ;;
  *)
    # No usable PostgreSQL URL — boot the local no-Docker PostgreSQL.
    echo "[ta-api] No postgres DATABASE_URL provided; booting local PostgreSQL (pgserver)..."
    URL="$("$PYTHON" scripts/dev_postgres.py 2>/dev/null | tail -n 1 || true)"
    if [ -z "$URL" ]; then
      echo "[ta-api] ERROR: could not provision a local PostgreSQL." >&2
      echo "[ta-api]        Install requirements-dev.txt (pgserver) or set DATABASE_URL" >&2
      echo "[ta-api]        to a reachable postgresql:// instance (see docker-compose.yml)." >&2
      exit 1
    fi
    export DATABASE_URL="$URL"
    ;;
esac

# --- 4. Migrations ------------------------------------------------------------------
echo "[ta-api] Applying database migrations (alembic upgrade head)..."
"$PYTHON" -m alembic upgrade head

# --- 5. Serve --------------------------------------------------------------------------
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8000}"
echo "[ta-api] Starting uvicorn on ${API_HOST}:${API_PORT} ..."
exec "$PYTHON" -m uvicorn app.main:app --host "$API_HOST" --port "$API_PORT"

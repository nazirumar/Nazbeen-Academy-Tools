#!/usr/bin/env bash
# Background variant of the backend launcher for manual use (`make backend`).
# Skips startup when the API is already answering on the target port.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${API_PORT:-8000}"
LOG_FILE="$ROOT/backend/uvicorn.log"

if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/health/live" >/dev/null 2>&1; then
  echo "[ta-api] Backend already running on port ${PORT} — nothing to do."
  exit 0
fi

echo "[ta-api] Starting backend in background (log: ${LOG_FILE})..."
nohup bash "$ROOT/scripts/start-backend.sh" >>"$LOG_FILE" 2>&1 &
echo "[ta-api] PID: $!"

# Wait briefly for readiness.
for _ in $(seq 1 45); do
  if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/health/live" >/dev/null 2>&1; then
    echo "[ta-api] Backend is live at http://127.0.0.1:${PORT} (docs: /docs)"
    exit 0
  fi
  sleep 1
done

echo "[ta-api] WARNING: backend did not report ready within 45s; check ${LOG_FILE}" >&2
exit 1

#!/usr/bin/env bash
# =============================================================================
# TA Platform — PostgreSQL backup script (Phase 9)
# =============================================================================
# Creates a compressed, timestamped logical backup (pg_dump custom format)
# of the platform database.
#
# Usage:
#   DATABASE_URL="postgresql://user:pass@host:5432/ta_platform" \
#     ./scripts/backup-db.sh [-o BACKUP_DIR] [-k KEEP_COUNT]
#
# - Reads the connection string from DATABASE_URL (never from arguments,
#   never written to disk, never logged).
# - Default backup dir: ./backups (git-ignored).
# - Retention: keeps the newest KEEP_COUNT backups per database name
#   (default 14), deletes older ones it created.
# - File permissions: 0600 (the dump contains user data).
#
# Scheduling (operational choice, documented in docs/operations.md):
#   daily cron example:  15 3 * * *  cd /opt/ta-platform && ./scripts/backup-db.sh
#
# Exit codes: 0 success, 1 configuration/backup failure.
# =============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_COUNT="${KEEP_COUNT:-14}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL must be set (postgresql://user:pass@host:5432/db)}"

while getopts "o:k:" opt; do
  case "$opt" in
    o) BACKUP_DIR="$OPTARG" ;;
    k) KEEP_COUNT="$OPTARG" ;;
    *) echo "Usage: $0 [-o BACKUP_DIR] [-k KEEP_COUNT]" >&2; exit 1 ;;
  esac
done

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# --- Parse the connection string (without ever printing it) ------------------
# Supported: postgresql://user:pass@host:port/db, +driver variants, and
# unix-socket URLs (?host=/path/to/socket). Parsing is done by Python's
# urllib — the password is exported to PGPASSWORD, never echoed or logged.
if [[ "$DATABASE_URL" != postgresql*://* ]]; then
  echo "backup: DATABASE_URL must be a PostgreSQL URL" >&2
  exit 1
fi
eval "$(python3 - "$DATABASE_URL" << 'PY'
import shlex
import sys
import urllib.parse as u

url = u.urlparse(sys.argv[1])
query = u.parse_qs(url.query)
host = query.get("host", [url.hostname or ""])[0]
port = str(url.port or (query.get("port", ["5432"])[0]))
user = url.username or ""
password = u.unquote(url.password or "")
dbname = url.path.lstrip("/").split("/")[0]
print(f"DB_USER={shlex.quote(user)}")
print(f"DB_PASSWORD={shlex.quote(password)}")
print(f"DB_HOST={shlex.quote(host)}")
print(f"DB_PORT={shlex.quote(port)}")
print(f"DB_NAME={shlex.quote(dbname)}")
PY
)"

if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_HOST" ]]; then
  echo "backup: could not parse DATABASE_URL (refusing to guess)" >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/${DB_NAME}_${STAMP}.dump"

# --- Dump ---------------------------------------------------------------------
export PGPASSWORD="$DB_PASSWORD"
if ! pg_dump \
  --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
  --format=custom --compress=6 --no-owner --no-privileges \
  --file="$OUT" "$DB_NAME"; then
  echo "backup: pg_dump FAILED (no file is written on failure)" >&2
  rm -f "$OUT"
  unset PGPASSWORD
  exit 1
fi
unset PGPASSWORD

chmod 600 "$OUT"

# --- Integrity check: the archive must be readable and list its catalog -------
if ! pg_restore --list "$OUT" > /dev/null 2>&1; then
  echo "backup: written archive failed integrity check — REMOVING it" >&2
  rm -f "$OUT"
  exit 1
fi

SIZE="$(du -h "$OUT" | cut -f1)"
echo "backup: OK $OUT ($SIZE)"

# --- Retention ----------------------------------------------------------------
ls -1t "$BACKUP_DIR"/"${DB_NAME}_"*.dump 2>/dev/null | tail -n +"$((KEEP_COUNT + 1))" | while read -r old; do
  rm -f "$old"
  echo "backup: pruned old backup $old"
done

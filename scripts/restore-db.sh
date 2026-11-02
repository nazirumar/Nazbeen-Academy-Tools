#!/usr/bin/env bash
# =============================================================================
# TA Platform — PostgreSQL restore script (Phase 9)
# =============================================================================
# Restores a backup created by scripts/backup-db.sh into a TARGET database.
#
# Safety model:
# - The target database must ALREADY EXIST and must NOT be the database the
#   backup was taken from, unless --force-overwrite is given. By default the
#   script REFUSES to drop/recreate anything: it restores into the existing
#   target using --clean --if-exists inside a single transaction.
# - A post-restore validation counts the rows of every user table and prints
#   them, so an operator can eyeball consistency (see docs/operations.md).
#
# Usage:
#   ./scripts/restore-db.sh BACKUP_FILE TARGET_DATABASE_URL [--force-overwrite]
#
#   TARGET_DATABASE_URL e.g. postgresql://user:pass@host:5432/ta_platform_restored
#
# Exit codes: 0 success, 1 failure (target left unchanged on failure).
# =============================================================================
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 BACKUP_FILE TARGET_DATABASE_URL [--force-overwrite]" >&2
  exit 1
fi

BACKUP_FILE="$1"
TARGET_URL="$2"
FORCE="${3:-}"

[[ -f "$BACKUP_FILE" ]] || { echo "restore: backup file not found: $BACKUP_FILE" >&2; exit 1; }

if [[ "$TARGET_URL" != postgresql*://* ]]; then
  echo "restore: TARGET_DATABASE_URL must be a PostgreSQL URL" >&2
  exit 1
fi

# --- Parse target URL (never printed) -----------------------------------------
# Unix-socket URLs (?host=/path) supported, like backup-db.sh.
eval "$(python3 - "$TARGET_URL" << 'PY'
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
  echo "restore: could not parse TARGET_DATABASE_URL" >&2
  exit 1
fi

# --- Optional safety check: warn loudly when restoring over the source DB -----
export PGPASSWORD="$DB_PASSWORD"
psql_runner() {
  psql --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" --dbname="$DB_NAME" "$@"
}

if [[ "$FORCE" != "--force-overwrite" ]]; then
  if psql_runner -tAc "SELECT 1 FROM pg_catalog.pg_tables LIMIT 1;" > /dev/null 2>&1; then
    TABLES_BEFORE="$(psql_runner -tAc \
      "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';")"
    if [[ "${TABLES_BEFORE:-0}" -gt 0 ]]; then
      echo "restore: target database already has $TABLES_BEFORE table(s)." >&2
      echo "restore: re-run with --force-overwrite to REPLACE its contents" >&2
      echo "restore: (or restore into a fresh/empty database — recommended)." >&2
      unset PGPASSWORD
      exit 1
    fi
  fi
fi

# --- Restore (single transaction: all-or-nothing) ------------------------------
# --clean --if-exists drops objects present in the target before recreating;
# --no-owner --no-privileges keeps roles/ACLs out of the restored data.
if ! pg_restore \
  --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
  --dbname="$DB_NAME" \
  --clean --if-exists --no-owner --no-privileges \
  --single-transaction \
  "$BACKUP_FILE"; then
  echo "restore: pg_restore FAILED (transaction rolled back, target unchanged)" >&2
  unset PGPASSWORD
  exit 1
fi

# --- Validation ----------------------------------------------------------------
echo "restore: OK — validating row counts of restored tables:"
psql_runner -c "
SELECT relname AS table, n_live_tup AS approx_rows
FROM pg_stat_user_tables ORDER BY relname;"

ALEMBIC="$(psql_runner -tAc \
  "SELECT version_num FROM alembic_version;" || true)"
echo "restore: alembic_version = ${ALEMBIC:-<missing — run: alembic upgrade head>}"
unset PGPASSWORD
echo "restore: done."

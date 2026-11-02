#!/usr/bin/env bash
# =============================================================================
# TA Platform — backup & restore VERIFICATION (Phase 9)
# =============================================================================
# Proves the backup/restore toolchain actually recovers data, using DISPOSABLE
# databases on a throwaway local PostgreSQL cluster (pgserver binaries — no
# Docker, no root, nothing existing is ever touched or deleted):
#
#   1. boot a temporary PostgreSQL cluster
#   2. create database SRC, apply Alembic migrations, insert sample rows
#   3. run scripts/backup-db.sh against SRC
#   4. create database DST (empty) and run scripts/restore-db.sh into it
#   5. compare table-by-table row counts SRC vs DST (must be identical)
#   6. confirm the safety check refuses restoring into a NON-empty target
#   7. destroy the temporary cluster (dispose: safe — created by this run)
#
# Usage:  ./scripts/verify-backup-restore.sh
# Requires: pip install -r backend/requirements-dev.txt  (pgserver), pg_dump/pg_restore
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d /tmp/ta-restore-verify-XXXXXX)"
trap 'python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" "$WORK"' EXIT

# pg_dump/pg_restore/psql are bundled with the pgserver package (no root, no
# system install needed); prefer them when no system client is on PATH.
if ! command -v pg_dump > /dev/null 2>&1; then
  PGBIN="$(python3 -c 'import pgserver, os; print(os.path.join(os.path.dirname(pgserver.__file__), "pginstall", "bin"))')"
  export PATH="$PGBIN:$PATH"
fi

echo "== booting disposable PostgreSQL cluster =="
PGDATA="$WORK/pgdata"
export PYTHONPATH="$ROOT/backend"
SERVER_URL="$(python3 - "$PGDATA" << 'PY'
import sys
import pgserver

server = pgserver.get_server(sys.argv[1], cleanup_mode=None)
server.psql("CREATE DATABASE ta_restore_src;")
server.psql("CREATE DATABASE ta_restore_dst;")
print(f"postgresql+asyncpg://postgres@/ta_restore_src?host={sys.argv[1]}")
PY
)"
SOCKET_HOST="${SERVER_URL##*host=}"
SRC_URL="postgresql://postgres@/ta_restore_src?host=$SOCKET_HOST"
DST_URL="postgresql://postgres@/ta_restore_dst?host=$SOCKET_HOST"

echo "== applying migrations to SRC =="
(cd "$ROOT/backend" && ALEMBIC_DATABASE_URL="$SERVER_URL" python3 -m alembic upgrade head > /dev/null 2>&1)

echo "== inserting sample rows into SRC =="
psql "$SRC_URL" -q << 'SQL'
INSERT INTO strategy_categories (id, owner_id, name, mode, description, is_archived, created_at, updated_at)
VALUES
  ('11111111-1111-1111-1111-111111111111', 'verify-owner', 'RestoreCheck-Forex', 'forex', 'backup verification row', false, now(), now()),
  ('22222222-2222-2222-2222-222222222222', 'verify-owner', 'RestoreCheck-Binary', 'binary_options', 'backup verification row', false, now(), now());
SQL

echo "== backup =="
BACKUP_DIR="$WORK/backups" DATABASE_URL="$SRC_URL" "$ROOT/scripts/backup-db.sh"
DUMP_FILE="$(ls -1t "$WORK"/backups/*.dump | head -1)"

echo "== restore into empty DST =="
DATABASE_URL_UNUSED=x "$ROOT/scripts/restore-db.sh" "$DUMP_FILE" "$DST_URL" > "$WORK/restore.log"
tail -5 "$WORK/restore.log"

echo "== comparing row counts SRC vs DST =="
TABLES="strategy_categories strategies strategy_versions candles analysis_reports backtest_runs research_runs"
FAILED=0
for t in $TABLES; do
  SRC_COUNT="$(psql "$SRC_URL" -tAc "SELECT count(*) FROM $t;" 2>/dev/null || echo "n/a")"
  DST_COUNT="$(psql "$DST_URL" -tAc "SELECT count(*) FROM $t;" 2>/dev/null || echo "n/a")"
  STATUS="OK"; [[ "$SRC_COUNT" == "$DST_COUNT" ]] || { STATUS="MISMATCH"; FAILED=1; }
  echo "   $t: src=$SRC_COUNT dst=$DST_COUNT [$STATUS]"
done
if [[ "$FAILED" -ne 0 ]]; then
  echo "VERIFY: FAILED — row counts differ" >&2
  exit 1
fi

SAMPLE_SRC="$(psql "$SRC_URL" -tAc "SELECT name FROM strategy_categories WHERE id='11111111-1111-1111-1111-111111111111';")"
SAMPLE_DST="$(psql "$DST_URL" -tAc "SELECT name FROM strategy_categories WHERE id='11111111-1111-1111-1111-111111111111';")"
[[ "$SAMPLE_SRC" == "$SAMPLE_DST" ]] || { echo "VERIFY: FAILED — row content differs" >&2; exit 1; }
echo "   content check: '$SAMPLE_DST' restored byte-identical"

echo "== safety check: refuse non-empty target =="
if DATABASE_URL_UNUSED=x "$ROOT/scripts/restore-db.sh" "$DUMP_FILE" "$DST_URL" > "$WORK/refusal.log" 2>&1; then
  echo "VERIFY: FAILED — non-empty target was overwritten without --force-overwrite" >&2
  exit 1
fi
grep -q "already has" "$WORK/refusal.log" && echo "   refusal message shown — OK"

echo "== VERIFY: PASSED — backup+restore toolchain recovers data correctly =="

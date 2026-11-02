"""Boot a real local PostgreSQL without Docker (dev convenience).

Uses the ``pgserver`` package (bundles PostgreSQL 16 binaries, no root
required). Idempotent: starting twice reuses the same data directory and
reuses the role/database if they already exist.

Usage:
    python scripts/dev_postgres.py          # prints ONE line: the DATABASE_URL

The printed URL is a unix-socket connection string like:
    postgresql+asyncpg://ta_user:ta_dev_local@/ta_platform?host=/path/to/pgdata
"""

from __future__ import annotations

import sys
from pathlib import Path

PG_USER = "ta_user"
PG_PASSWORD = "ta_dev_local"  # local-only convenience credential, NOT a secret
PG_DB = "ta_platform"
PGDATA = Path(__file__).resolve().parent.parent / ".pgserver-data"


def main() -> int:
    try:
        import pgserver
    except ImportError:
        print(
            "pgserver is not installed. Run: pip install -r requirements-dev.txt "
            "(or use Docker Compose for PostgreSQL).",
            file=sys.stderr,
        )
        return 1

    PGDATA.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(str(PGDATA), cleanup_mode=None)

    # Create role + database if missing (idempotent).
    server.psql(
        f"DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{PG_USER}') "
        f"THEN CREATE ROLE {PG_USER} WITH LOGIN PASSWORD '{PG_PASSWORD}'; "
        f"ELSE ALTER ROLE {PG_USER} WITH LOGIN PASSWORD '{PG_PASSWORD}'; END IF; "
        f"END $$;"
    )
    exists = server.psql(
        "SELECT 1 FROM pg_database WHERE datname = "
        f"'{PG_DB}';"
    )
    if "1" not in exists:
        server.psql(f"CREATE DATABASE {PG_DB} OWNER {PG_USER};")

    host = str(PGDATA)
    url = (
        f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}"
        f"@/{PG_DB}?host={host}"
    )
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

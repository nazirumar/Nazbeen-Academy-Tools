# TA Platform — Production Operations Guide (Phase 9)

Audience: the operator deploying and running this repository.
Everything here reflects the **actual** architecture in the repo: a FastAPI
backend (SQLAlchemy async + Alembic, PostgreSQL 16), a Next.js 16 standalone
frontend, an optional MT5 terminal integration (Windows host only) and an
optional OpenAI-compatible AI provider. No Kubernetes, no distributed queue —
the documented minimal architecture.

---

## 1. Architecture and deployment prerequisites

```
┌────────────┐   HTTP    ┌──────────────────┐    SQL     ┌──────────────┐
│  Browser   │──────────▶│  Next.js (3000)  │            │ PostgreSQL16 │
└────────────┘           └──────────────────┘            └──────▲───────┘
       │                    (static/standalone)                 │
       │  REST /api/v1, /health/*                               │
       ▼                                                        │
┌──────────────────┐   async pool   ┌───────────────────────────┴──┐
│ FastAPI (8000)   │───────────────▶│ SQLAlchemy async engine      │
│  - analysis      │                └──────────────────────────────┘
│  - strategies    │   serialized worker thread
│  - backtest      │───────────────▶ MT5 terminal (Windows host only)
│  - research      │   HTTPS (bounded retries)
│  - AI (optional) │───────────────▶ OpenAI-compatible provider (optional)
└──────────────────┘
```

Prerequisites:

- Linux x86-64 host or Windows host (the latter is required for **live MT5
  data** — the `MetaTrader5` Python package does not run on Linux; containers
  run in the honest `MT5_ENABLED=false` mode).
- Python 3.12 (backend), Bun 1.x (frontend build/runtime) or Node for the
  standalone output, PostgreSQL 16.
- Docker + Compose v2 for the container deployment path.

## 2. Supported OS / runtime versions

| Component | Version |
|---|---|
| Backend | Python 3.12 (`python:3.12-slim` image or native) |
| Database | PostgreSQL 16 (`postgres:16-alpine`) |
| Frontend build/runtime | Bun 1.x (`oven/bun:1`), Node-compatible standalone output |
| MT5 terminal | MetaTrader 5, logged-in, on the same Windows host as the backend |

## 3. Required environment variables

Backend (see `backend/.env.example` for the full annotated template):

| Variable | Required | Notes |
|---|---|---|
| `APP_ENV` | yes | `production` unlocks the fail-fast rules below |
| `DATABASE_URL` | yes | `postgresql+asyncpg://user:pass@host:5432/db` — placeholder credentials are refused outside local/test |
| `API_AUTH_TOKEN` | **yes in production** | deployment-wide bearer token (min 12 chars). `/api/v1/*` requires `Authorization: Bearer <token>` |
| `API_AUTH_TOKENS` | optional | CSV of `token` or `token:owner` pairs; a bound token locks requests to that owner and **ignores `X-User-Id`** |
| `CORS_ORIGINS` | recommended | comma-separated absolute origins (e.g. `https://app.example.com`); empty = same-origin only |
| `LOG_LEVEL` / `LOG_FORMAT` | optional | `INFO` / `json` in production |
| `MT5_ENABLED` | optional | `false` in Linux containers (default in compose) |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | only if explicit login | leave empty to attach to the terminal's own login (recommended — nothing stored) |
| `AI_ENABLED` + `AI_BASE_URL` + `AI_MODEL` + `AI_API_KEY` | only if AI on | key is server-side only, never returned/logged |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | optional | pool sizing (default 5/10) |
| `BACKTEST_MAX_*` / `RESEARCH_MAX_*` | optional | resource limits (documented in `.env.example`) |

Frontend build args (`Dockerfile.frontend`):

| Variable | Notes |
|---|---|
| `NEXT_PUBLIC_BACKEND_ORIGIN` | empty = gateway/same-origin mode; or e.g. `https://api.example.com` (direct mode; backend CORS must allow it) |
| `NEXT_PUBLIC_API_TOKEN` | optional **deployment access token** — see the honest security note in §4 |
| `NEXT_PUBLIC_BACKEND_PORT` | backend port for gateway mode (default 8000) |

Docker Compose extras: `TA_DB_USER`, `TA_DB_PASSWORD`, `TA_DB_NAME`,
`TA_DB_HOST` (default `127.0.0.1`), `TA_API_HOST`, `TA_FRONTEND_HOST`.

### Fail-fast production rules (enforced at startup)

When `APP_ENV=production` the backend **refuses to start** if:

- `DEBUG=true`;
- `DATABASE_URL` contains a placeholder (`CHANGE_ME`, `PLACEHOLDER`, …);
- **no API auth token is configured** (the API is never served unauthenticated).

Dependency outages do NOT crash the app: they are reported honestly by
`/health/ready` (503 only when PostgreSQL is down).

## 4. Safe secret setup

1. Secrets live **only** in the environment (or `backend/.env`, git-ignored).
   `.env.example` files are the only tracked templates — placeholders only.
2. Generate tokens: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. Docker Compose: put secrets in a compose-adjacent `.env` file
   (`TA_DB_PASSWORD=…`, `API_AUTH_TOKEN=…`, `AI_API_KEY=…`). Never commit it
   (`.gitignore` covers `.env*`; a legacy tracked `.env` was untracked in Phase 9).
4. The frontend `NEXT_PUBLIC_API_TOKEN` is **part of the public bundle**: it
   gates the UI like a site password but is NOT a per-user secret. For
   per-owner control use server-side `API_AUTH_TOKENS` bindings; for fully
   private deployments keep the backend off the public internet (VPN /
   reverse-proxy allow-list / bind `TA_API_HOST=127.0.0.1`).
5. **Secret rotation**: change the env value(s) and restart the backend
   (`docker compose up -d backend`). Rotate: DB password (update
   `DATABASE_URL` + the DB role together), `API_AUTH_TOKEN(S)` (clients pick
   up the new token after the frontend is rebuilt with the new
   `NEXT_PUBLIC_API_TOKEN`), AI key (restart only). Rotation is disruptive by
   design — there is no dual-token grace window; document a change window.
6. Never log secrets: the log pipeline redacts credential-shaped keys/values
   (`app/core/logging.py`), and DB URLs are masked by
   `database_url_safe()`. If you ever see a secret in a log, treat it as
   compromised and rotate.

## 5. Build and deployment commands

```bash
# full stack (recommended)
cp .env.example .env            # then fill in real values (also backend/.env)
docker compose build
docker compose up -d            # backend waits for db health; frontend waits for backend health

# native (no Docker) — Linux host
make backend                    # provisions local PostgreSQL via pgserver, migrates, runs uvicorn
bun install --frozen-lockfile && bun run build && bun start

# native — Windows host with live MT5
cd backend && pip install -r requirements.txt -r requirements-mt5.txt
set MT5_ENABLED=true && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 6. Database migration commands (documented, controlled step)

```bash
cd backend
python -m alembic upgrade head          # apply pending migrations
python -m alembic history               # inspect the chain
python -m alembic current               # what the DB is at now
python -m alembic downgrade -1          # ⚠ see rollback caveats below
```

- The container image runs `alembic upgrade head` before serving. Migrations
  are **additive only** (verified: no `DROP`/recreate of existing tables in
  the Phase 1–8 chain) — startup never destroys data.
- One process runs migrations at a time: scale `backend` to 1 replica, or run
  the migration step separately (`docker compose run --rm backend alembic
  upgrade head`) and then start app replicas.
- **Rollback reality check**: `alembic downgrade` is *not* guaranteed
  lossless. Inspect `down_revision` chains before downgrading; a downgrade
  that removes columns/tables destroys the data they held. The supported
  rollback strategy is **restore from backup** (§9), not schema downgrade.
- Check migration status operationally: `curl -s localhost:8000/health/db`
  reports `migrated: true/false`.

## 7. HTTPS and domain configuration

The repo contains no production TLS layer (the `Caddyfile` is the sandbox
dev-transform gateway on :81). Options, in order of preference:

1. **Managed reverse proxy / load balancer** with TLS termination (cloud LB,
   Caddy, nginx, Traefik) in front of the frontend on 3000.
2. Terminate TLS at the proxy and forward to `frontend:3000` and
   `backend:8000`. Set `X-Forwarded-Proto` correctly.
3. Then restrict CORS (`CORS_ORIGINS=https://your-domain`) and consider
   binding service ports to localhost (`TA_API_HOST=127.0.0.1`,
   `TA_FRONTEND_HOST=127.0.0.1`, `TA_DB_HOST=127.0.0.1` — compose defaults)
   so only the proxy is exposed.

Trusted-proxy note: the backend does not read client-IP headers for any
security decision (rate limits are per-owner/per-token, not per-IP), so no
`ProxyHeadersMiddleware` is required.

## 8. Health-check verification

| Endpoint | Meaning | Healthy response |
|---|---|---|
| `GET /health/live` | process is up (no dependency checks) | 200 `{"status":"live"}` |
| `GET /health/ready` | **PostgreSQL** reachable + provider status reported transparently | 200 when DB is up; **503** only when DB is down |
| `GET /health/db` | DB ping + migration status (`migrated: true/false`) | 200 |

- Market data / AI outages **never** block readiness — they are reported
  inside `checks.market_data` / AI status with `blocks_readiness: false`.
- MT5 reports real connection state (`connected: false` when the terminal is
  absent — never faked because configuration exists) and the data endpoints
  return `STALE_DATA` when the newest closed candle is older than
  `MT5_STALE_AFTER_INTERVALS` intervals.
- Verify after deploy: `curl -fsS http://HOST:8000/health/live && curl -fsS
  http://HOST:8000/health/ready | python3 -m json.tool`.
- Container-level: both images define Docker `HEALTHCHECK`s; compose
  orchestrates startup with `condition: service_healthy`.

## 9. Backup and restoration

**Backup** (logical, compressed, integrity-checked):

```bash
DATABASE_URL="postgresql://user:pass@host:5432/ta_platform" ./scripts/backup-db.sh \
  [-o /secure/backups] [-k 14]
```

- Custom-format `pg_dump`, gzip-compressed, `0600` permissions, `pg_restore
  --list` integrity check; retention prunes to the newest N files.
- **Frequency**: daily minimum; before every migration. **Retention**: ≥14
  daily + ≥4 weekly + ≥3 monthly (adjust to your policy). **Encryption**:
  store on an encrypted volume or encrypt per-file
  (`openssl enc -aes-256-cbc -pbkdf2 -in X.dump -out X.dump.enc`), keys in
  your secret manager. **Off-server**: copy dumps off the DB host (object
  storage / another machine) — a backup on the same disk is not a backup.
- Dump files contain user data — never commit them (`.gitignore` covers
  `/backups/`).

**Restore** (into an existing, preferably empty target DB):

```bash
./scripts/restore-db.sh /secure/backups/ta_platform_20261102T031500Z.dump \
  "postgresql://user:pass@host:5432/ta_platform_restored"
```

- Refuses a non-empty target unless `--force-overwrite`; single-transaction
  (all-or-nothing); prints post-restore row counts and the `alembic_version`.
- **Restored-data validation** (required by procedure): run the restore into
  an isolated database first, then point a test instance at it
  (`DATABASE_URL=… ta_platform_restored make backend`) and check
  `/health/db` + a few API reads. Only then switch production over.
- Application config & uploaded files to recover: the app keeps everything in
  PostgreSQL; there are no uploads. Recovery = DB + the env file(s)
  (`backend/.env`, compose `.env`) — keep THOSE backed up separately
  (encrypted) or recreate from your secret manager.

**Restore verification status (Phase 9)**: `scripts/verify-backup-restore.sh`
was executed successfully in this repository (2026-11-02): it creates a
disposable cluster, migrates it, inserts sample rows, backs up, restores into
an empty database, compares table-by-table row counts, verifies row content,
and proves the non-empty-target refusal. It runs in CI. **Recovery of THIS
deployment's real data has not been exercised** — schedule a drill per §16.

**RPO / RTO (operational goals, not guarantees)**: with daily backups the
practical RPO is ≤ 24 h (tighten with cron frequency + WAL archiving if
needed); the scripted restore path gives an RTO of minutes for the data plus
application restart time. These are goals you configure, not claims the
software enforces.

## 10. Application update and rollback

```bash
git fetch && git checkout <new-tag>
docker compose build && ./scripts/backup-db.sh   # ALWAYS backup first
docker compose up -d                             # runs alembic upgrade head
curl -fsS localhost:8000/health/ready            # verify
```

Rollback: redeploy the previous image/tag; if the new migration must be
reverted, prefer **restore from the pre-update backup** (§6 caveats) over
`alembic downgrade`.

## 11. MT5 terminal requirements and limitations

- The `MetaTrader5` Python package only runs on **Windows** (or Wine): in
  Linux containers `MT5_ENABLED=false` is the honest default and the API
  reports the provider as unavailable — it does not fake data.
- Recommended: leave `MT5_LOGIN/PASSWORD/SERVER` empty and log the terminal
  in manually (no credentials stored anywhere). All three are only needed for
  backend-driven login, and they are read from configuration only — no
  endpoint accepts them.
- Terminal access is serialized on a worker thread with bounded retries and
  capped exponential backoff (never a tight reconnect loop); disconnects are
  surfaced as `STALE_DATA`/unavailable status, not fabricated candles.
- The platform never places orders. MT5 is a read-only data source here.

## 12. AI provider setup and failure behavior

- `AI_ENABLED=true` requires `AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`
  (fail-fast validation). Any OpenAI-compatible `/chat/completions` endpoint
  works; the key never leaves the server process.
- Failures are bounded: per-request timeout, ≤ `AI_MAX_RETRIES` retries with
  backoff, per-owner rate limit, prompt/output caps. A provider outage
  degrades ONLY the AI explanations/chat — the deterministic analysis,
  strategies, backtests and research keep working, and `/health/ready`
  stays green.
- User chat text is redacted before it reaches the provider; AI output is
  validated against an evidence schema and never written to the database.

## 13. Log inspection

- Format: one JSON object per line on stdout (`LOG_FORMAT=json`), carried by
  your container runtime's log driver (`docker compose logs -f backend`,
  `journalctl -u ta-backend` for systemd, etc.).
- Every line has `timestamp`, `level`, `logger`, `message`, optional
  `request_id` (also returned in the `X-Request-ID` response header and API
  envelope `meta`), operation fields and duration where relevant
  (`request_completed` carries `http_method`, `path`, `status_code`,
  `duration_ms`).
- Sensitive values are redacted at the formatter level; access logs never
  include query strings of POST bodies; market-data ticks are not logged.
- Retention/rotation: delegate to your platform (Docker json-file with
  `max-size`/`max-file`, journald limits, or a collector). Example:
  `/etc/docker/daemon.json` → `"log-driver": {"log-opts": {"max-size": "10m",
  "max-file": "5"}}`.

## 14. Common deployment errors and troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Backend exits immediately, log says `APP_ENV=production requires API authentication` | Set `API_AUTH_TOKEN`/`API_AUTH_TOKENS` (intended fail-fast). |
| `DATABASE_URL still contains a placeholder` | Fill real credentials in `backend/.env` / compose env. |
| `/health/ready` → 503, `database.status: down` | DB unreachable/credentials wrong. Check `docker compose logs db`, `pg_isready`, then `alembic upgrade head` state via `/health/db`. |
| Frontend shows `BACKEND_UNREACHABLE` | Direct mode without backend reachability, or backend down. Check `NEXT_PUBLIC_BACKEND_ORIGIN`, CORS, `curl HOST:8000/health/live`. |
| 401 `UNAUTHORIZED` from every API call | Frontend was built without `NEXT_PUBLIC_API_TOKEN` while the backend has auth enabled — rebuild the frontend with the token. |
| 413 on save/paste | Body over `REQUEST_MAX_BODY_BYTES` (default 2 MB) — intended. |
| 429 on backtest/research/AI | Per-owner rate limit hit — intended; wait or raise the documented setting. |
| MT5 status `connected: false` in compose | Expected on Linux (no MetaTrader5 package). Run the backend natively on Windows for live data. |
| `migrated: false` on `/health/db` | Migrations not applied: `docker compose run --rm backend alembic upgrade head`. |

## 15. Resource limits and where to adjust them

All limits are env-tunable and documented in `backend/.env.example`:
request body cap (`REQUEST_MAX_BODY_BYTES`), candle caps
(`MT5_MAX_CANDLES`, `BACKTEST_MAX_CANDLES`), research computation caps
(`RESEARCH_MAX_COMBINATIONS`, `RESEARCH_MAX_WALKS`, `RESEARCH_MAX_WINDOWS`,
…), concurrency (`BACKTEST_MAX_CONCURRENT`, `RESEARCH_MAX_CONCURRENT`),
creation rate limits (`BACKTEST_MAX_CREATES_PER_MINUTE`,
`RESEARCH_MAX_CREATES_PER_MINUTE`), AI bounds (`AI_TIMEOUT_SECONDS`,
`AI_MAX_RETRIES`, `AI_RATE_LIMIT_PER_MINUTE`, `AI_MAX_INPUT_CHARS`,
`AI_MAX_OUTPUT_TOKENS`). DB pool: `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`
(guideline: pool ≈ expected concurrent request handlers; workers and web
share the same single-process engine in the documented architecture).
Large list endpoints are paginated (page_size ≤ 100); backtests/research
never load unbounded history (candle caps + coverage checks).

## 16. Post-deployment verification checklist

1. `curl -fsS HOST:8000/health/live` → 200.
2. `curl -fsS HOST:8000/health/ready` → 200, `database.status=up`,
   `migrated=true` (via `/health/db`).
3. Unauthenticated API probe returns **401** (production): `curl -i
   HOST:8000/api/v1/meta` → `HTTP/1.1 401` with `WWW-Authenticate: Bearer`.
4. Authenticated probe returns 200 with the configured token.
5. Frontend loads over HTTPS, security headers present
   (`curl -sI https://your-domain | grep -i content-security`).
6. Create a category + strategy through the UI; run an analysis — data
   round-trips (DB writable, ownership scoping intact).
7. `/docs` NOT reachable in production (disabled by default).
8. Logs show `application_started` with `auth: {"enabled": true, …}` and no
   secrets anywhere in the log stream.
9. Backup scheduled (`cron`/systemd timer running `scripts/backup-db.sh`),
   first dump verified with `pg_restore --list`.
10. MT5 (if used): status panel shows real `connected` state matching the
    terminal; AI (if used): one chat exchange succeeds; disable flags
    otherwise.

## 17. Explicit non-goals (unchanged)

- No automatic trade execution — permanently out of scope.
- No guaranteed profitability anywhere in the product or docs.
- No fabricated market data: MT5 outages are reported, never papered over.
- No production deployment is performed by this repository's tooling without
  explicit operator authorization.

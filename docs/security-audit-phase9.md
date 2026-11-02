# Phase 9 — Security, Reliability & Deployment Readiness Audit

**Date:** 2026-11-02 · **Scope:** full repository (`backend/` FastAPI, `src/` Next.js,
deployment configs, CI, tests) · **Method:** manual inspection of every core module +
baseline test execution (no code changed during the audit).

---

## 1. Baseline test results (recorded before any change)

| Suite | Command | Result |
|---|---|---|
| Backend (unit + integration, real PostgreSQL via pgserver) | `python3 -m pytest tests/ -q` | **731 passed, 3 skipped** (68.6s). Skips = `tests/integration/test_mt5_live.py` live-terminal tests, gated behind `RUN_MT5_LIVE=1` on a Windows host — correct behaviour. |
| Frontend (vitest) | `bun run test` | **143 passed** (13 files, 20s) |
| Frontend lint | `bun run lint` | **0 errors** |

---

## 2. Findings (prioritized)

### CRITICAL

| # | Finding | Evidence | Files | Remediation |
|---|---|---|---|---|
| C1 | **No real authentication.** Ownership is resolved from a client-supplied `X-User-Id` header; any caller can impersonate any owner and read/modify every private resource (strategies, analyses, backtests, research runs, AI conversations). The module itself documents this: "it scopes data, it does not authenticate. Real authentication is documented as future work." | `backend/app/core/ownership.py` (docstring); README lists "built-in authentication" as a Phase 9 candidate | `app/core/ownership.py`, all domain routers | Add a bearer-token authentication layer applied to every `/api/v1` endpoint: optional in `local`/`test`, **mandatory in `production` (fail-fast at startup)**, with optional `token→owner` bindings so a bound token cannot be spoofed via `X-User-Id`. Health endpoints stay unauthenticated. |
| C2 | **Production does not fail safely on missing access control** — today a production deployment without any auth setting simply starts wide open. | Same as C1 | `app/core/config.py` | Config validator: `APP_ENV=production` requires at least one API auth token. |

### HIGH

| # | Finding | Evidence | Files | Remediation |
|---|---|---|---|---|
| H1 | **`.env` is tracked in git** (`.gitignore` has `.env*`, but the file was committed before the ignore rule — ignore rules do not untrack files). Currently it holds only a local SQLite URL, but the pattern invites committing real secrets. | `git ls-files` → `.env` | `.env` | `git rm --cached .env`; keep `.env.example` templates only. |
| H2 | **`db/custom.db` (SQLite, Prisma boilerplate) is committed** — a binary data file in VCS; any future Prisma use would put user rows into git history. | `git ls-files` → `db/custom.db` | `db/custom.db` | Untrack; ignore `*.db`. |
| H3 | **Internal junk tracked:** 16 `tool-results/read_*.txt` files and `.zscripts/dev.pid`. | `git ls-files` | `tool-results/`, `.zscripts/dev.pid` | Untrack; ignore. |
| H4 | **No CI at all** — no `.github/` directory; nothing validates tests, lint, builds, migrations or secret hygiene on push. | repository tree | — | Add GitHub Actions workflow: backend tests (real PostgreSQL), frontend lint+test+build, migration validation, secret scanning, dependency audit. |
| H5 | **No global log secret redaction.** The JSON/console formatters write every `extra` field verbatim. Only the AI layer redacts (`app/domains/ai/secrets.py`); a stray `extra={"password": …}` anywhere else would leak into logs. | `app/core/logging.py` formatters | `app/core/logging.py` | Add a redaction layer used by both formatters (sensitive key names + secret-shaped values), with tests. |
| H6 | **Backtest creation is not rate-limited** (AI requests and research-run creation are). One client can flood the bounded in-process runner. | `app/domains/backtest/router.py` POST has no `check_rate_limit`; research router has one | `app/domains/backtest/router.py`, `app/core/config.py` | Reuse the existing per-owner sliding-window limiter + new `BACKTEST_MAX_CREATES_PER_MINUTE` setting. |
| H7 | **Containers run as root.** Neither `backend/Dockerfile` nor `Dockerfile.frontend` declares `USER`. | Dockerfiles | `backend/Dockerfile`, `Dockerfile.frontend` | Non-root users in both images. |
| H8 | **PostgreSQL port published on `0.0.0.0:5432`** by docker-compose — the DB listens on every host interface. | `docker-compose.yml` → `ports: ["5432:5432"]` | `docker-compose.yml` | Bind to `127.0.0.1` by default. |
| H9 | **Frontend build silently skips type checking** — `typescript.ignoreBuildErrors: true`. | `next.config.ts` | `next.config.ts` | Disable and fix any type errors surfaced by a real production build. |

### MEDIUM

| # | Finding | Evidence | Files | Remediation |
|---|---|---|---|---|
| M1 | **No security headers / CSP** on backend responses or the Next.js app. | no `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` anywhere | `app/main.py`, `next.config.ts` | Add headers middleware (backend, JSON-safe CSP) and Next.js `headers()` config. |
| M2 | **No backup/restore procedure** — no scripts, no documentation, no tested restore. | `scripts/`, `docs/` | — | Add `scripts/backup-db.sh` + `scripts/restore-db.sh`, verify restore on disposable databases, document RPO/RTO as operational goals. |
| M3 | **No production operations guide** (env vars, migrations, rollback, HTTPS, troubleshooting, post-deploy checklist). | `docs/` | — | `docs/operations.md`. |
| M4 | **Frontend container has no healthcheck** and compose waits only for `service_started`. | `Dockerfile.frontend`, `docker-compose.yml` | both | Add `HEALTHCHECK`; make compose wait for backend `service_healthy`. |
| M5 | **Destructive convenience scripts**: `db:push --accept-data-loss`, `db:reset` target the `DATABASE_URL` of the SQLite boilerplate. The backend never runs destructive DDL at startup (migrations are `upgrade head` only — verified). | `package.json` | — | Document clearly; leave untouched (Prisma is unused boilerplate, see M6). |
| M6 | **Unused Prisma/SQLite boilerplate** (`src/lib/db.ts` is imported by nothing; schema holds demo `User`/`Post` models). Maintenance surface only. | grep | `src/lib/db.ts`, `prisma/` | Document in the audit; not removed to keep the diff reviewable. |
| M7 | **Docs endpoints can be force-enabled in production** (`DOCS_ENABLED=true` overrides the production default-off). | `app/core/config.py enable_docs` | — | Keep as-is (operator escape hatch), document the risk. |
| M8 | **No dependency-vulnerability or secret scanning** anywhere. | repo | — | Add `pip-audit` + gitleaks to CI (record sandbox limitation). |

### LOW

| # | Finding | Evidence | Remediation |
|---|---|---|---|
| L1 | Base images pinned by tag only (`python:3.12-slim`, `oven/bun:1`, `postgres:16-alpine`). | Dockerfiles | Document; digest pinning is an operator hardening step. |
| L2 | `Caddyfile` is the sandbox dev-transform gateway (:81), **not** a production TLS termination layer; production HTTPS is undocumented. | `Caddyfile` | Cover HTTPS/reverse-proxy requirements in `docs/operations.md`. |
| L3 | `DB_ECHO=true` would emit SQL with bound parameters (default `false`). | `app/core/config.py` | Document "never enable in production". |

---

## 3. Verified sound (reused, not rewritten)

- **Configuration**: fail-fast validation, placeholder credentials refused outside local/test, `DEBUG=true` forbidden in production, docs/OpenAPI disabled by default in production, DB URL password masking helper. (`app/core/config.py`)
- **Error handling**: one envelope, no stack traces to clients, `request_id` correlation; unhandled errors logged server-side only. (`app/core/errors.py`)
- **Structured logging**: JSON lines, timestamps, levels, request ids. (H5 adds redaction.)
- **API hardening already present**: explicit CORS allow-list, request body-size limit (413), bounded MT5 candle limits, bounded research computation caps, per-owner rate limits (AI, research creation).
- **Ownership scoping is already server-side on every router** (404-instead-of-403 pattern, no existence leak) — C1 adds authentication *in front of* this existing scoping rather than replacing it.
- **Background jobs**: in-process runners with bounded concurrency, startup recovery of interrupted runs (backtest + research), cancellation support, honest failure states.
- **MT5 integration**: serialized terminal access on a worker thread (event loop never blocked), bounded reconnect with capped exponential backoff (no tight loop), honest `STALE_DATA` reporting, credentials accepted from configuration only, never from endpoints.
- **AI layer**: key server-side only, timeout + bounded retries + per-owner rate limit, chat text redaction before it reaches the provider, deterministic analysis fully usable during outages (`AI_ENABLED=false` default).
- **Database**: async engine with `pool_pre_ping`, honest `/health/ready` (503 only when PostgreSQL is down; market-data/AI outages do **not** block readiness), `/health/db` reports migration status; Alembic migrations are additive — startup never drops or recreates tables.
- **Tests**: 731 backend + 143 frontend, real PostgreSQL for integration tests, external dependencies mocked (`fakes/fake_mt5.py`, `fakes/fake_ai.py`), MT5 live tests honestly gated.

---

## 4. Remediation implemented in Phase 9

C1, C2 → `app/core/auth.py` + config validators + tests · H1–H3 → git untracking + `.gitignore` ·
H4 → `.github/workflows/ci.yml` · H5 → redaction in `app/core/logging.py` · H6 → backtest
creation limiter · H7 → non-root Dockerfiles · H8 → `127.0.0.1` DB port binding ·
H9 → `ignoreBuildErrors: false` + type fixes · M1 → security headers (backend + frontend) ·
M2 → backup/restore scripts + verified restore test · M3 → `docs/operations.md` ·
M4 → frontend healthcheck + compose ordering · M8 → CI audit jobs.

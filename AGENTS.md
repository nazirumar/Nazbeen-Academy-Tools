# AGENTS.md

TA Workbench — a user-defined forex/binary-options analysis platform. Two
independently buildable apps: a Next.js frontend at the repo root and a FastAPI
backend in `backend/`. Read `README.md` for architecture, API and phase history.

## Layout

- `src/` — Next.js 16 App Router frontend (TypeScript, Tailwind v4, shadcn/ui).
- `backend/` — FastAPI + async SQLAlchemy + Alembic + PostgreSQL 16.
- `docs/` — authoritative specs for rule sets, reports, backtests, research, AI,
  operations, market replay, release readiness and the Phase 9 security audit.
- `scripts/`, `Makefile`, `docker-compose.yml` — local/dev/deploy orchestration.
- Legacy template leftovers: `prisma/`, `src/lib/db.ts` and the `db:*` package
  scripts are an unused Prisma/SQLite scaffold. The real database is PostgreSQL
  via `backend/alembic`. Do not build features on Prisma.

## Commands

Run backend commands from `backend/`: `Settings` loads `.env` relative to the
current directory (`env_file=".env"`).

```bash
# frontend (root) — Bun
bun install
bun run dev                 # :3000
bun run test                # vitest, all src/**/*.test.{ts,tsx}
bunx vitest run src/tests/api-client.test.ts   # single file
bun run lint
bunx tsc --noEmit

# backend
cd backend
python -m pip install -r requirements-dev.txt   # required before pytest
python -m pytest tests/ -q
python -m pytest tests/unit/test_rules.py -q -k <pattern>
```

- `make` targets require bash (Git Bash/WSL on Windows) and `python3`; with the
  Windows venv run the underlying commands directly via
  `backend\.venv\Scripts\python.exe`.
- `make backend` / `scripts/start-backend.sh` provision a local PostgreSQL via
  `pgserver` when `DATABASE_URL` is not a `postgresql*` URL, then apply Alembic
  migrations and start uvicorn on `:8000`.
- `package.json`'s `build`/`start` scripts use Unix commands (`cp -r`,
  `NODE_ENV=...`) and will fail in PowerShell; use `bunx next build` and set
  environment variables separately on Windows.
- CI verification order: `bun run lint` -> `bunx tsc --noEmit` -> `bun run test`
  -> `bun run build` (frontend), plus the backend pytest suite and
  `scripts/verify-backup-restore.sh`. `make verify` only runs lint + tests, so
  run the type check yourself.
- Backend integration tests use a real temporary PostgreSQL from `pgserver`
  and run Alembic once; they skip with a message if `pgserver` is unavailable.
  On this Windows host (no `pgserver` wheel), set `TA_TEST_DATABASE_URL` to a
  disposable PostgreSQL URL whose database name contains `test`; the fixtures
  truncate its tables. MT5 and LLM providers are always fakes in tests — no
  terminal or network.
- Live MT5 testing is opt-in and only on a Windows host with the terminal
  running: `RUN_MT5_LIVE=1`, `-m live`. Docker Compose intentionally starts the
  backend with `MT5_ENABLED=false` (MT5 cannot run in Linux containers).
- The sandbox chain `.zscripts/dev.sh` runs `bun run db:push` (legacy Prisma
  SQLite) before starting Next.js and the backend mini-service. That database is
  unrelated to the platform; do not rely on it.

## Repo-specific gotchas

- Use Bun, not npm: `bun.lock` is canonical and `package-lock.json` is
  untracked/stale.
- `package.json` (working tree) declares ancient `eslint`/`eslint-config-next`
  versions while `bun.lock` pins ESLint 9 / Next 16. CI installs from
  `bun.lock`. If lint fails with "couldn't find a configuration file", the
  installed `node_modules` is stale — fix the install, don't downgrade tooling.
- This Windows checkout has no `.gitattributes`; `git status` shows hundreds of
  mode/CRLF-only changes. Inspect `git diff --numstat` before committing and do
  not commit mode-only churn.
- `eslint.config.mjs` disables nearly every rule; lint is not a correctness gate.
  Type checking (`tsc --noEmit`) is the real frontend gate.
- Vitest only includes `src/**/*.test.{ts,tsx}` (tests live in `src/tests/`);
  jsdom/Radix shims are in `src/tests/setup.ts`, not per-test.
- Frontend API mode (see `src/lib/api/client.ts`): empty
  `NEXT_PUBLIC_BACKEND_ORIGIN` = same-origin gateway with `XTransformPort=8000`;
  setting an origin = direct mode, which requires that origin in backend
  `CORS_ORIGINS`.

## Backend invariants

- Every response uses `{success, data, error, meta}` and structured errors with
  `details[].path` + `request_id`; use `core/responses.py` and `core/errors.py`.
- Ownership is `X-User-Id` scoping (default `local`), not authentication.
  Foreign-owned records must return 404 exactly like missing ones. Optional
  bearer auth is `API_AUTH_TOKEN`/`API_AUTH_TOKENS`; production refuses to start
  without one.
- `market_data/mt5_adapter.py` is the ONLY module allowed to touch MetaTrader5.
  Candle timestamps stay in broker-server (`MT5_SERVER`) epoch seconds; never
  silently re-base them. Missing/gapped data is reported, never fabricated.
- `strategies/rule_registry.py` is the single source of truth for rule types.
  Planned types must stay rejected. A new operational type needs registry +
  evaluator + tests + regenerated `docs/strategy-rule-set.schema.json`
  (`cd backend && python ../scripts/generate_rule_schema.py`).
- Condition outcomes are four-state (`met` / `not_met` / `inconclusive` /
  `unsupported`). Never collapse unknown/unsupported into success or failure.
  The evaluator is shared by analysis, backtest and research.
- No look-ahead: evaluation at candle *i* may use only candles whose close time
  is <= candle *i*'s close, across every timeframe. Preserve this in any new
  engine/backtest/replay code.
- Market replay (`domains/replay/`) is a persisted cursor over the PostgreSQL
  candle cache. It must only read stored closed candles, never fetch live data
  during playback, never return a candle with `open_time > cursor_time`, and
  treat playback speed as presentation-only. Forming/invalid rows are excluded
  and reported; gaps are never filled. The first slice has no replay
  execution/journal — do not describe it as having one.
- Strategy versions are immutable: publishing freezes a version and supersedes
  the previous one. Never mutate a published/superseded rule set — create a new
  draft version.
- Schema changes need a NEW Alembic revision; do not edit applied migrations.
  In-process backtest/research runs interrupted by a restart are marked failed
  (`RUNNER_RESTARTED`) at startup, never completed.

## Design docs

- `docs/strategy-rule-schema.md`, `docs/analysis-report-schema.md`,
  `docs/backtest-schema.md`, `docs/research-optimization-schema.md`,
  `docs/ai-research.md`
- `docs/operations.md` (deployment, env vars, backups/restores),
  `docs/security-audit-phase9.md`
- `docs/replay.md` (Phase 11 replay cursor semantics, API, data quality and
  deferred work), `docs/phase10-release-readiness.md` (audit, evidence,
  known gaps and status)

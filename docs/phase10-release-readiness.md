# Phase 10 — Release Readiness Report

Date: **2026-11-05**
Scope: Phase 10 production/release hardening plus the Phase 11 historical
market replay vertical slice.
Status: **READY FOR STAGING**

> A release-candidate tag is **not** recommended yet. The local correctness
> gates are green and the replay slice is complete for its declared scope, but
> container/deployment verification and a live-MT5 validation run could not be
> performed in this environment, and the full originally requested replay
> feature set (simulated replay trades/exits, journal, CSV import/provenance)
> is not implemented. Details and exit criteria are in §10.

---

## 1. Executive summary

### Completed

- Repository audited before implementation; the Phase 6 backtest engine,
  Phase 2 candle cache and Phase 4/6 data-quality helpers were reused. No
  duplicate replay/backtest engine was introduced.
- Phase 10 baseline hardening completed: package/lock restoration, removal of
  the unused `src/lib/db.ts` Prisma client helper, test isolation, honest MT5
  disabled-state reporting, broker-clock skew correction, cross-platform
  standalone build/start scripts, API version `0.2.1` and the Phase 9
  security layer preserved.
- Phase 11 replay first vertical slice implemented end-to-end: PostgreSQL
  persistence, deterministic cursor lifecycle, cursor-bounded candle reads,
  owner-scoped API, Next.js Replay workspace, tests and browser E2E.
- Startup no longer blocks on a wedged MT5 terminal:
  `MT5_WARMUP_ON_STARTUP=false` is the default; the setting is documented.
- Pre-existing ORM/schema metadata drift in older tables was reconciled in the
  models without editing applied migrations. `alembic check` is clean.
- Backup/restore was re-verified against disposable databases, including the
  new `replay_sessions` table.

### Not completed / not verified

- Docker/Compose build and deployment smoke tests (Docker unavailable here).
- Live MetaTrader 5 terminal validation (environment-dependent; the terminal
  intermittently timed out with `MT5 last_error -10005: IPC timeout`).
- GitHub Actions execution (workflow is validated by running the same
  commands locally, not by executing Actions in this sandbox).
- Replay simulated execution, trade journal, CSV dataset import/provenance
  and shared interactive execution (explicitly deferred; see §9).
- No deployment, publishing, tagging, push or order execution was performed.

---

## 2. Audit scope and findings

### What was inspected

| Area | Finding |
|---|---|
| Backtest engine | Already provides immutable strategy/config snapshots, chronological setup/trade records, metrics, cancellation, CSV export, data-quality warnings, restart recovery and no-look-ahead semantics. **Reused, not duplicated.** |
| Market data | PostgreSQL `candles` cache with validated closed/forming rows, provenance and gap reporting. **Reused as the only replay source.** |
| Data quality | `assess_series_quality` already reports coverage, gaps, duplicates, invalid OHLC, forming rows and staleness. **Reused.** |
| Existing replay/journal/CSV | None existed before Phase 11. No hidden replay engine or dataset-provenance workflow was found. |
| Frontend chart | Lightweight Charts v5 wrapper already handles cleanup, resize, incremental updates and historical re-sync. Extended with an optional `followLatest` mode. |
| Persistence | Alembic chain ended at Phase 8 (`c9a1e5d7b324`). A new Phase 11 revision was required. |
| Testing | Backend uses real PostgreSQL integration fixtures; Windows lacks `pgserver`, so `TA_TEST_DATABASE_URL` is required. Playwright/Chrome was used because the desktop browser bridge was unavailable. |
| Deployment | Docker is not available locally; the user's existing PostgreSQL service and `local` data were left untouched. |

### Gaps found and disposition

| Gap | Disposition |
|---|---|
| No historical replay feature | Implemented the first vertical slice (Phase 11). |
| No replay simulated execution/journal | Deferred; documented as not implemented. |
| No CSV dataset import/provenance | Deferred; replay uses the existing stored candle cache only. |
| MT5 startup could block HTTP readiness on a wedged terminal | Fixed: warm-up is opt-in, default off. Lazy connection and cached serving remain. |
| ORM models omitted DB indexes / declared a redundant one | Fixed in models only; applied migrations untouched; `alembic check` clean. |
| Stale testing instructions / counts | Updated README/AGENTS/operations to current commands and results. |
| Docker/live-MT5 verification unavailable here | Recorded as release gates in §10, not silently waived. |

---

## 3. What changed in this work

### Phase 10 baseline (already in the working tree)

- Restored a canonical Bun lockfile/package state and removed the unused
  `src/lib/db.ts` Prisma client helper. The legacy `prisma/schema.prisma` and
  `db:*` package scripts remain documented leftovers; no platform runtime,
  build or test path uses them (the real database is PostgreSQL via Alembic).
- Isolated integration tests from real data; added the Windows
  `TA_TEST_DATABASE_URL` path.
- Reported MT5 disabled/unavailable states honestly instead of failing or
  fabricating data.
- Fixed broker-clock skew correction and cache self-healing.
- Made standalone build/start scripts cross-platform.
- Set API version `0.2.1`; preserved the Phase 9 authentication, redaction,
  security-header, rate-limit and backup/restore work.
- Added `MT5_WARMUP_ON_STARTUP` (default `false`) so terminal IPC problems do
  not block HTTP startup.

### Phase 11 replay slice

- `backend/app/domains/replay/` — data helpers, schemas, service and router.
- `backend/app/db/models/replay.py` and Alembic revision `e2c7a91b4d30`.
- `/api/v1/replay` endpoints: coverage, create, list, get, candles, step,
  seek, resume, pause, reset, speed, delete.
- Frontend: `src/components/replay/*`, Replay tab registration, API types and
  tests.
- `docs/replay.md`, README/AGENTS/operations updates and this report.
- Schema metadata reconciliation for older models (no new migration needed).

### Explicit non-goals preserved

No order placement, no broker execution, no profitability claims, no
fabricated candles, no live-data fetch during replay, no arbitrary strategy
execution, no destructive database reset.

---

## 4. Architecture and data flow

Full detail: [`docs/replay.md`](replay.md).

```
MT5 (optional live source) → Phase 2 validation/cache → PostgreSQL candles
                                                            │
                                      replay creation loads closed/valid rows
                                                            ▼
                                              PostgreSQL replay_sessions
                                    (window · cursor · counters · quality)
                                                            │
                          cursor-bounded reads / step / seek / lifecycle
                                                            ▼
                                        FastAPI /api/v1/replay (owner-scoped)
                                                            ▼
                                  Next.js Replay tab (chart + saved history)
```

Key invariants:

- `cursor_time` is the open time of the newest visible candle;
  `current_time = cursor_time + timeframe duration`.
- Chart reads return only candles with `open_time <= cursor_time`; step reads
  only rows strictly after the cursor.
- Only stored closed, valid, deduplicated candles are used. Forming/invalid
  rows are excluded and reported; gaps are never filled.
- Playback speed is presentation-only and cannot change chronology or fills.
- Replay never fetches live MT5 data and never places orders.
- Foreign-owned sessions return 404 exactly like missing sessions.
- Creation is rate-limited per owner; window/response sizes are capped.

---

## 5. Verification evidence

All commands were run on this checkout unless marked otherwise.

### Backend

| Check | Result |
|---|---|
| Full suite (`python -m pytest tests/ -q`, `TA_TEST_DATABASE_URL` set) | **786 passed, 3 skipped** in 533.65s (0:08:53) |
| Config/limit unit tests after adding warm-up/replay defaults | **30 passed** in 0.99s |
| Replay integration suite | **11 passed** in 32.05s (earlier run); included in the full suite |
| Security regression (Phase 9 suites) | Included in the full suite; no failures |

The 3 skips are exactly the optional live-MT5 tests in
`backend/tests/integration/test_mt5_live.py` (`RUN_MT5_LIVE` was not set);
they are not failures.

### Frontend

| Check | Result |
|---|---|
| `bun run test` (Vitest 5.0.5) | **153 passed across 14 files** in 116.10s |
| `bun run lint` | exit 0 |
| `bunx tsc --noEmit` | exit 0 |
| `bunx next build` (Next.js 16.1.3, Turbopack) | compiled successfully in 2.1min; TypeScript passed; 4/4 static pages generated; standalone output prepared |

### Browser E2E

Playwright with installed Chrome, backend on `:8000`, standalone frontend on
`:3000`, isolated PostgreSQL E2E database:

| Check | Result |
|---|---|
| `bunx playwright test` | **8 passed** in 1.6m |
| Replay workflow | create → coverage → step → full reload → cursor resume → API cleanup |

The replay E2E test passed alongside strategy, analysis, backtest, research,
persistence and honest-AI-disabled workflows.

### Migrations

Executed on a clean disposable database on a local PostgreSQL 18 cluster:

| Check | Result |
|---|---|
| `alembic upgrade head` from empty DB | passed through `e2c7a91b4d30` |
| `alembic check` | **no new upgrade operations detected** |
| `alembic downgrade base` | passed through all revisions |
| `alembic upgrade head` again | passed |
| Final `alembic current` | `e2c7a91b4d30 (head)` |

No applied migration was edited. The only metadata change was in ORM models
so they now match the indexes already created by earlier migrations.

### Backup / restore

Executed with the real scripts from Git Bash against disposable databases on
the local PostgreSQL cluster:

| Check | Result |
|---|---|
| `scripts/backup-db.sh` | OK, 60 KB custom-format dump |
| `scripts/restore-db.sh` into empty target | OK |
| Table row-count comparison | all matched, including `candles=2`, `replay_sessions=1` |
| Restored Alembic version | `e2c7a91b4d30` |
| Non-empty-target refusal | exit code 1, as designed |

The Phase 9 `scripts/verify-backup-restore.sh` path remains the CI check; the
Phase 11 run above additionally covers the new replay table.

### Performance smoke (local, not a capacity guarantee)

In-process ASGI client against the isolated E2E PostgreSQL cache; one process,\nlocalhost, no network. Dataset: **698 closed EURUSD M1 candles** (real cache\nrows in the platform's sync format, not synthetic test data).\n\n| Operation | Latest run (median) | Observed warm range across repeat runs |\n|---|---|---|\n| Coverage check (698 candles) | 99.5 ms | 48–229 ms |\n| Create session (698 candles) | 148.1 ms | 98–212 ms |\n| Get session | 34.7 ms | 24–46 ms |\n| Chart (≤500/5,000 requested) | 69.8 ms | 36–84 ms |\n| Step (51 samples) | 51.7 ms | 22–52 ms |\n| Seek | 62.3 ms | 27–71 ms |\n| List sessions | 47.4 ms | 26–61 ms |\n| First coverage request (cold) | 0.87 s | 0.39–1.58 s |\n\nInterpretation: interactive replay is responsive at this dataset size, but\nthis is **not** a capacity or production-SLA measurement. Windows host load,\nfirst-use DB/ORM setup and local disk caused large run-to-run variance. The\nresponse cap (5,000) was not exercised because the dataset has only 698\ncandles; run a dedicated load test with a larger cache before sizing a\ndeployment.

### Security

- The Phase 9 security test suites passed as part of the full backend suite
  (auth, ownership isolation, redaction, headers, rate limits).
- No new secrets, credentials or executable code paths were added by replay.
- Replay is owner-scoped and exposes no future candles.
- `gitleaks`/`pip-audit` GitHub Actions were **not executed locally**; the CI
  workflow retains them. Run CI before an RC tag.

---

## 6. Setup / operating instructions

### Backend

```bash
cd backend
python -m pip install -r requirements.txt          # + requirements-dev.txt for tests
# set DATABASE_URL, APP_ENV, CORS_ORIGINS, etc. in backend/.env
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Windows/live MT5 notes:

- Run natively on the Windows host with the terminal logged in.
- Leave `MT5_WARMUP_ON_STARTUP=false` unless you explicitly want startup to
  probe the terminal.
- The terminal's candle cache must contain the requested symbol/timeframe
  before a replay window can be created.

### Frontend

```bash
bun install
# empty NEXT_PUBLIC_BACKEND_ORIGIN = same-origin gateway; or set the backend origin
bun run build
bun start        # or the prepared standalone output
```

### Replay smoke test

1. Open the Replay tab.
2. Choose symbol/timeframe/window; press **Check stored data**.
3. Create the session; exactly one candle is visible and the status is
   `paused`.
4. Step/play; the chart must never show a candle after the displayed cursor.
5. Reload the page and reopen the saved session; the cursor must resume.
6. Delete the test session when done.

Full API and semantics: [`docs/replay.md`](replay.md).

---

## 7. Data safety

- No database was reset, dropped or modified outside disposable test/backup
  databases.
- The user's existing PostgreSQL service and owner `local` data were not
  touched.
- The new migration is additive (`replay_sessions` only).
- No Git commit, tag, push, deployment or publishing was performed.
- The working tree still contains unrelated mode/CRLF-only churn; do not
  commit it blindly.

---

## 8. Compatibility and rollback

- Existing analysis, strategy, backtest, research and AI behaviour is
  unchanged; replay is additive.
- Strategy version immutability, four-state outcomes, no-look-ahead and
  ownership semantics are preserved.
- Rollback of the Phase 11 migration is technically verified
  (`downgrade base` → `upgrade head`), but the supported operational rollback
  remains **restore from the pre-update backup**, not schema downgrade.
- Deleting a replay session is a hard delete of that session row only; candle
  rows are shared and untouched.

---

## 9. Known limitations and remaining gaps

### Release/deployment gaps

1. **Docker/Compose not verified here.** Docker is unavailable in this
   environment; image builds, healthchecks, non-root users, bind addresses and
   compose orchestration were not exercised in this run.
2. **Live MT5 not re-verified here.** Automated tests use the fake adapter.
   The terminal intermittently returned IPC timeouts during this work. Startup
   no longer blocks on it, but live reliability remains host/broker-dependent.
3. **GitHub Actions not executed.** CI commands were run locally; gitleaks and
   pip-audit remain CI-only checks.
4. **Original `backend/.env` missing.** A temporary local PostgreSQL cluster
   was used for verification; no credentials for the user's existing service
   were available and none were modified.
5. **No release tag/push/deploy.** Intentionally not performed.

### Replay feature gaps (deferred, not hidden)

1. No simulated replay orders/fills/exits/P&L.
2. No replay trade journal (notes/tags/review).
3. No CSV dataset import or dataset-provenance workflow.
4. No shared interactive execution with the backtest engine.
5. No multi-timeframe replay or strategy evaluation during playback.
6. No WebSocket/streaming; playback is one API step at a time.
7. No automatic session retention/cleanup.
8. Session quality is frozen at creation; later cache backfills require a new
   session.
9. Chart responses cap at 5,000 candles; session windows cap at 20,000.
10. Ownership is `X-User-Id` scoping, not authentication; use the Phase 9
    bearer layer or an external auth proxy for internet-facing deployments.

### Platform limitations carried forward

- No automatic trading/execution and no profitability guarantees.
- No probability/confidence calibration.
- Some advanced rule types remain planned and rejected by validation.
- Candle history depth is bounded by the MT5 terminal/cache.
- The legacy `prisma/schema.prisma` file and `db:*` package scripts remain in
  the repository as unused template leftovers. They are documented in
  `AGENTS.md`; remove them only in a dedicated cleanup change, not as part of
  this release.

---

## 10. Readiness decision and release-candidate criteria

### Decision

**READY FOR STAGING.** The local functional, type, lint, build, migration,
security-regression and backup/restore gates are green, and the replay slice
is safe/deterministic within its declared scope. It is **not** a
release-candidate sign-off because deployment/container behaviour and live
MT5 were not verified, and the full replay feature set is incomplete.

### Conditions for a release-candidate tag (recommended order)

1. Build and smoke-test the Docker images and Compose stack on a Docker-capable
   host (backend health, frontend health, DB migration, authenticated API,
   replay smoke test, backup mount).
2. Run the live-MT5 opt-in test on a Windows host with a healthy terminal and
   verify cached/live provenance and replay coverage against that terminal.
3. Decide the release scope explicitly:
   - **Option A (platform + replay v1):** accept items §9 as documented
     limitations and tag an RC with the replay tab labelled a first slice.
   - **Option B (full replay objective):** implement simulated replay
     execution, journal notes and CSV import/provenance first, then re-run the
     gates.
4. Run the full GitHub Actions pipeline (including gitleaks/pip-audit) and a
   backup restore drill on a non-production copy.
5. Review the mode/CRLF-only working-tree churn and commit only intentional
   changes.
6. Obtain human sign-off, then tag/push/deploy as a separate authorized step.

### What would make the status BLOCKED

- A failing backend/frontend gate or migration check on the release commit.
- A discovered look-ahead leak, fabricated data path, ownership leak or
  secret exposure.
- A deployment build that cannot start or migrate its database.

None of these were observed in this run.

---

## 11. Evidence artifacts

Local logs (this machine, outside the repository):

- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase11-backend-pytest-final.log`
- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase11-frontend-vitest.log`
- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase11-lint.log`
- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase11-tsc.log`
- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase11-frontend-build.log`
- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase10-e2e.log`
- `C:\Users\Nazbeen\AppData\Local\Temp\opencode\phase11-backup-verify.log`

Design/API documentation: [`docs/replay.md`](replay.md).
Operations: [`docs/operations.md`](operations.md).

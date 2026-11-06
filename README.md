# TA Workbench — Forex & Binary Options Technical Analysis Platform

A user-defined technical-analysis platform: **you** create, organize, version and
evaluate your own trading strategies. The platform is *not* a signal generator,
has no built-in mandatory indicators or trading rules, executes **no orders**,
and **never fabricates market data**.

**Phase 5** added the **Live Analysis Dashboard** — a
professional, responsive surface for viewing real market data and explainable
analysis:

* an interactive **candlestick chart** (TradingView Lightweight Charts v5,
  Apache-2.0): crosshair OHLC readout, zoom/scroll, visible price & time
  scales, theme-aware colours, responsive resizing, and honest "forming —
  not final" labelling of the in-progress candle;
* a **polling candle feed** (`useCandleFeed`) on the documented candle API
  (no WebSocket exists in the backend): configurable intervals with
  visibility-aware pausing, duplicate/out-of-order protection
  (closed candles replace their forming predecessor), at most one request
  in flight, and full cleanup on unmount;
* honest **connection & freshness indicators**: Live / Partial data / Stale /
  Not connected / No candles / Failed — "live" is claimed only while the last
  fetch succeeded and the backend's own data-quality gate says the data is
  fresh; "partial" means real candles were returned but the backend flagged
  short history or excluded invalid rows, so they stay on the chart with the
  warning visible; after a failure the last REAL candles stay visible but the
  chart stops claiming to be live;
* **chart overlays derived exclusively from the analysis engine's output**
  (`src/lib/chart/overlay-model.ts`): swing highs/lows with confirmation
  timing, HH/LH/HL/LL labels, BOS segments, MSS/CHoCH levels and FVG zones
  from condition evidence, user-defined strategy price zones — each overlay
  traceable to the report that produced it. Concepts the engine does not
  detect (order blocks, breaker blocks) are listed as engine-unsupported and
  are never drawn; overlays are per-timeframe and the frontend never
  re-computes market geometry;
* the **strategy evaluation panel**: strategy + immutable version selection,
  four-state condition sections (met / not met / inconclusive / unsupported),
  regime + timeframe agreement, invalidation & confirmation rules, data
  warnings and the full persisted report — no probabilities, no confidence
  percentages, no profitability claims;
* mode separation end-to-end: **Forex** defaults to D1/H4/H1/M15/M5/M1;
  **Binary Options** defaults to M1-only (higher timeframes appear only when
  the evaluated strategy explicitly requires them) and states plainly that
  MT5 data is not the broker's execution feed and OTC prices are never
  simulated.

**Phase 3** built the **Custom Strategy Builder**: user-managed categories,
subcategories and strategies with ownership scoping (`X-User-Id` header,
default `local`), a **draft → validate → publish** workflow with immutable,
versioned rule sets, a versioned rule-set schema 2.0
([`docs/strategy-rule-schema.md`](docs/strategy-rule-schema.md)), a rule-type
registry and a professional Strategy Builder UI.

**Phase 4** adds the **Explainable Technical Analysis
Engine** on top of that foundation:

* a deterministic, documented **market feature library** (`features.py`):
  candle anatomy, SMA/EMA/ATR, confirmed swing points with EXPLICIT
  confirmation timing, HH/HL/LH/LL structure, historical structure breaks,
  zone interactions and data completeness — every feature publishes its
  definition, required history and limitations (`GET /api/v1/analysis/features`);
* a configurable **market regime classifier** (`regime.py`): uptrend,
  downtrend, range, breakout, transition and uncertain — explicit,
  testable heuristics with full evidence, never probabilities;
* a **four-state rule engine**: every condition reports
  `met` / `not_met` / `inconclusive` / `unsupported` — AND never converts
  unknown or unsupported conditions into success, OR never conceals
  unavailable conditions; closed candles by default with an explicit,
  labelled opt-in for forming-candle (provisional) analysis;
* four **advanced rule types promoted to operational** with precise
  documented definitions and tests: `mss_choch`, `bos`, `liquidity_sweep`,
  `fvg` (order blocks, breaker blocks and S/R reactions remain PLANNED and
  rejected by validation);
* the **engine run + persisted reports**: `POST /api/v1/analysis/run` fetches
  real candles for exactly the strategy's required timeframes, classifies the
  regime (with higher/lower-timeframe agreement — never forced), evaluates
  every condition and stores a versioned, auditable report (schema 1.0)
  with per-timeframe provenance, staleness and warnings. Binary Options
  runs state clearly that MT5 is not the broker's execution feed and that
  OTC data is never simulated;
* an **Engine run (MT5)** tab in the frontend: strategy + symbol, run,
  distinct accessible visual states for all four condition outcomes, regime
  and data panels, limitations, and full analysis history.

Automatic order execution and profitability claims remain **out of scope
forever**. The report describes technical conditions on historical data —
it is not a prediction and carries no probability of profit.

---

## Product principles

| Principle | Implementation |
|---|---|
| User-defined strategies | Users build their own categories, strategies, conditions, AND/OR groups, confirmation & invalidation rules. Nothing is imposed. |
| Explainable analysis | Every evaluation returns a per-condition tree: met / missing, numeric evidence, criteria and data-quality warnings. |
| Explicit whitelist | Condition types, operators, fields, group logic, modes and timeframes are enumerated server-side (`/api/v1/meta`); anything else is rejected with precise error paths. |
| Versioned rule sets | Saving a rule set creates a NEW immutable **draft** version (`strategy_versions.status`); publishing freezes a definition and supersedes the previous one. History is never rewritten — ready for later backtesting. |
| Ownership | Every category/strategy is scoped to an owner (`X-User-Id` header; default `local`). Foreign-owned records are indistinguishable from missing ones (404 — no existence leak). |
| No arbitrary code execution | Rule sets are pure data interpreted by a deterministic evaluator. No `eval`, no user expressions. |
| Forex / Binary Options separation | Separate modes everywhere: API filtering, rule validation and default timeframe profiles (Forex: D1, H4, H1, M15, M5, M1). Binary Options strategies **default to M1**; higher timeframes are used only when the user explicitly declares them — never forced. |
| Honest market data | If MetaTrader 5 is not connected the API and UI say so plainly and serve NOTHING. Every candle payload carries `data_provenance` (`mt5_live` / `postgres_cache`), `data_status` and data-quality warnings (gaps, duplicates, invalid OHLC, staleness). Forming candles are flagged and can be excluded with `closed_only=true`. |
| No fabricated prices | The MT5 adapter is the only data source; missing history is reported, never back-filled, interpolated or synthesized. |

---

## Architecture

```
┌────────────────────────┐        ┌──────────────────────────────────┐
│  Next.js 16 frontend   │  HTTP  │  FastAPI backend (uvicorn)       │
│  TypeScript, Tailwind  │ ─────► │  app/                            │
│  shadcn/ui, recharts   │        │   ├ core/      config, log,      │
│                        │        │   │             errors, envelope │
│  System dashboard      │        │   ├ db/        SQLAlchemy async  │
│  MT5 status panel      │        │   │             + Alembic        │
│  Market preview        │        │   ├ domains/                     │
└────────────────────────┘        │   │   ├ market_data/             │
                                  │   │   │   ├ mt5_adapter.py   ONLY module that
        ┌─────────────────────────┤   │   │   ├ mt5_timeframes.py talks to MetaTrader5
        │  PostgreSQL 16          │   │   │   ├ validation.py     (dedicated thread,
        │  strategies, versions,  │◄──┤   │   ├ store.py           bounded, serialized)
        │  candles cache          │   │   │   ├ schemas.py              │
        └─────────────────────────┘   │   │   └ mt5_provider.py         │
                                      │   │   ├ strategies/  rules, CRUD │
                                      │   │   └ analysis/    evaluator   │
                                      │   └ api/        health + v1     │
                                      └ MetaTrader 5 terminal (same host,
                                        Windows; terminal NEVER exposed
                                        to the public internet)
```

### MT5 integration topology (important)

The `MetaTrader5` Python package only runs where the terminal runs.

- **Recommended**: run the backend **natively on the Windows host** with the
  MT5 terminal installed, running and logged in. `pip install -r
  requirements-mt5.txt`, set `DATABASE_URL` (plus optional `MT5_*` settings),
  start uvicorn. The backend attaches to the terminal via its IPC bridge.
- **Wine**: possible with Windows Python under Wine, unsupported here — test
  carefully before relying on it.
- **Docker Compose (Linux containers)**: MT5 **cannot** run inside the
  container. Compose therefore starts the backend with `MT5_ENABLED=false`
  (safe disabled mode — API and UI report it honestly). PostgreSQL and the
  rest of the platform keep working; alternatively point the frontend at a
  backend running on a Windows machine via `NEXT_PUBLIC_BACKEND_ORIGIN`.
- **Security model**: credentials (if any) exist ONLY in environment
  variables; no endpoint accepts credentials, terminal paths or anything
  executable; the terminal is never exposed to the public internet; if the
  API is reachable remotely, put it behind your own auth proxy / VPN
  (built-in authentication is a later phase) and restrict the network.
- **Timestamps**: candle times are preserved exactly as MT5 reports them —
  epoch seconds whose wall-clock value is the BROKER SERVER time (the MT5
  convention). Every response also renders the same epoch as ISO-8601 UTC
  (`time_utc`) and carries `timezone: "MT5_SERVER"` with a human-readable
  `timezone_note`. Nothing is ever re-based silently.

### Directory tree (Phase 2)

```
.
├── backend/                        # Python FastAPI backend
│   ├── app/
│   │   ├── main.py                 # app factory + MT5 lifecycle wiring
│   │   ├── core/
│   │   │   ├── config.py           # pydantic-settings incl. MT5_* (fail-fast)
│   │   │   ├── logging.py          # structured JSON/console logging + request_id
│   │   │   ├── errors.py           # AppError hierarchy + global handlers (envelope)
│   │   │   ├── responses.py        # {success, data, error, meta} convention
│   │   │   └── middleware.py       # request-id correlation + access logging
│   │   ├── db/
│   │   │   ├── session.py          # async engine/session, readiness probe
│   │   │   └── models/             # strategy.py + candle.py (Phase 2 cache)
│   │   ├── domains/
│   │   │   └── market_data/
│   │   │       ├── mt5_adapter.py      # ONLY module that touches MetaTrader5
│   │   │       ├── mt5_timeframes.py   # extensible timeframe→MT5 mapping
│   │   │       ├── validation.py       # data-quality gate (OHLC, gaps, staleness)
│   │   │       ├── store.py            # PostgreSQL candle cache (upsert/read)
│   │   │       ├── schemas.py          # response models
│   │   │       ├── mt5_provider.py     # composition + cache/freshness policies
│   │   │       └── router.py           # /mt5/status + /market/* endpoints
│   │   ├── domains/strategies/     # rules.py (whitelist), schemas, service, router
│   │   ├── domains/analysis/       # evaluator.py, quality.py, schemas, router
│   │   └── api/                    # /health + /api/v1 aggregation + /meta
│   ├── alembic/                    # migrations (candles table added in Phase 2)
│   ├── tests/                      # pytest: unit (faked MT5) + integration (real PG)
│   │   └── fakes/fake_mt5.py       # faithful in-memory MetaTrader5 fake
│   ├── requirements.txt / requirements-dev.txt / requirements-mt5.txt
│   ├── .env.example                # placeholders ONLY
│   └── Dockerfile
├── src/                            # Next.js 16 frontend (App Router)
│   ├── app/                        # layout (theme) + dashboard (System/Market/…)
│   ├── components/
│   │   ├── market/                 # MT5 status panel + market preview (Phase 2)
│   │   ├── layout/  status/  strategies/  analysis/  ui/
│   ├── lib/
│   │   ├── api/                    # typed client (envelope unwrap) + DTO types
│   │   └── platform/               # mode profiles, market store
│   └── tests/                      # vitest + Testing Library
├── mini-services/ta-api/           # auto-starts the backend with the dev chain
├── scripts/start-backend.sh        # backend launcher (deps → PG → migrations → uvicorn)
├── scripts/start-backend-bg.sh     # background variant used by `make backend`
├── docker-compose.yml              # db + backend + frontend
├── Dockerfile.frontend
├── .env.example                    # frontend/compose placeholders ONLY
└── Makefile
```

> Note on layout: the frontend lives at the repository root (it is the app the
> sandbox/dev server runs on port 3000); the backend is fully self-contained in
> `backend/`. Both are independently buildable and dockerized.

---

## API conventions

Every response uses the same envelope (`app/core/responses.py`):

```json
// success
{ "success": true,  "data": { ... }, "error": null,
  "meta": { "request_id": "…", "timestamp": "…", "api_version": "v1" } }

// error
{ "success": false, "data": null,
  "error": { "code": "RULE_VALIDATION_FAILED", "message": "…", "details": [ { "path": ".conditions[0].operator", "message": "…", "code": "unsupported_operator" } ] },
  "meta": { "request_id": "…" } }
```

The `request_id` is also returned in the `X-Request-ID` header and appears in
the server's structured logs — quote it when reporting issues.

### Endpoints

| Method & path | Purpose |
|---|---|
| `GET /health/live` | Liveness (no dependencies). |
| `GET /health/ready` | Readiness: PostgreSQL probe + market-data status (503 when DB down). |
| `GET /api/v1/meta` | Modes, default timeframes, rule-format whitelists, market-data info. |
| `GET /api/v1/market-data/status` | Honest MT5 availability report (live check). |
| `GET /api/v1/mt5/status` | MT5 terminal/account connection report + safe config summary. **No credentials ever.** |
| `GET /api/v1/market/symbols` | Symbols known to the terminal (`?search=EUR*&limit=100`). |
| `GET /api/v1/market/candles` | Validated OHLCV series (`?symbol=&timeframe=&limit=&closed_only=&refresh=`). |
| `GET /api/v1/market/tick` | Latest tick / bid-ask / spread for one symbol. |
| `GET /api/v1/market/diagnostics` | End-to-end diagnostics: connection + live candle probe + capabilities. |
| `GET/POST /api/v1/strategy-categories` | List (nested, `?mode=&include_archived=`) / create categories & subcategories (`parent_id`). |
| `GET/PATCH/DELETE /api/v1/strategy-categories/{id}` | Manage a category (delete refused while dependents exist). |
| `POST /api/v1/strategy-categories/{id}/archive` \| `/unarchive` | Non-destructive organisation (archive cascades to subcategories). |
| `GET /api/v1/strategies` | Paginated listing (`?mode=&category_id=&status=&include_archived=&page=&page_size=`) → `{items, page:{page,page_size,total,pages}}`. |
| `POST /api/v1/strategies` | Create a strategy (owner-scoped; archived categories refused). |
| `GET/PATCH/DELETE /api/v1/strategies/{id}` | Detail (current rule set + human-readable summary) / update / delete. |
| `POST /api/v1/strategies/{id}/duplicate` | Deep-copy metadata + latest rule set as a new draft (`{name?, include_rule_set?}`). |
| `POST /api/v1/strategies/{id}/archive` \| `/unarchive` | Archive / restore (publish is blocked while archived). |
| `POST /api/v1/strategies/{id}/validate` | Validate a rule set against the schema; the result is persisted (audit trail). |
| `POST /api/v1/strategies/{id}/versions` | Validate + save a NEW immutable **draft** version. |
| `GET /api/v1/strategies/{id}/versions[/{n}]` | Version history (with `status`: draft/published/superseded) / specific version. |
| `POST /api/v1/strategies/{id}/versions/{n}/publish` | Publish a draft version: freezes the definition, supersedes the previous published one, strategy → `active`. |
| `POST /api/v1/rule-sets/validate` | Validate a rule set without saving (editor support). |
| `GET /api/v1/rule-types[/{type}]` | The rule-type registry: parameter schemas, semantics, availability (`available:false` = planned, NOT operational). |
| `POST /api/v1/analysis/evaluate` | Explainable evaluation on user-supplied candles (accepts stored versions or inline rule sets; schema 1.0 and 2.0). |
| `POST /api/v1/analysis/run` | **Phase 4 engine run**: fetch real candles for the strategy's required timeframes, classify the regime, evaluate every condition, persist a versioned report (201). Refuses with 503 when MT5 cannot supply data — never fabricates. |
| `GET /api/v1/analysis/reports` | Analysis history for the owner (`?symbol=&strategy_id=&mode=&setup_status=&page=&page_size=`). |
| `GET /api/v1/analysis/reports/{id}` | Retrieve one persisted report (full payload; owner-scoped 404 for foreign owners). |
| `GET /api/v1/analysis/features` | Documented catalogs: market features, regimes, volatility states, strategy buckets. |
| `GET /api/v1/analysis/readiness` | Engine readiness: DB, rule-registry coherence, live provider status (`ready` vs `live_analysis_ready`). |

**Ownership.** Every strategy/category endpoint is scoped to the requester.
The owner is taken from the optional `X-User-Id` header (1–64 chars,
`[A-Za-z0-9._@-]`; absent/empty → `local`). Requests for another owner's
records return 404, exactly like missing records — no existence oracle.
This is data scoping, not authentication (see Known limitations).

Structured errors from the market endpoints:

| HTTP | Code | When |
|---|---|---|
| 422 | `VALIDATION_ERROR` | Bad symbol/timeframe/limit (precise `details[].path`). |
| 404 | `SYMBOL_NOT_FOUND` | Symbol unknown to the terminal. |
| 502 | `MARKET_DATA_ERROR` | Terminal returned an unusable/empty response. |
| 503 | `MARKET_DATA_UNAVAILABLE` | MT5 disabled, package missing or terminal unreachable. |

#### Example: `GET /api/v1/market/candles?symbol=EURUSD&timeframe=M1&limit=3&closed_only=true`

```jsonc
{
  "success": true,
  "data": {
    "symbol": "EURUSD", "timeframe": "M1",
    "timezone": "MT5_SERVER",
    "timezone_note": "Candle timestamps are preserved exactly as reported by MetaTrader 5 …",
    "candles": [
      { "time": 1731608940, "time_utc": "2024-11-14T17:09:00+00:00",
        "open": 1.05651, "high": 1.05662, "low": 1.0564, "close": 1.05655,
        "volume": 812, "forming": false }
      // … newest last; forming candles flagged with "forming": true
    ],
    "count": 3, "requested_count": 3, "closed_only": true, "includes_forming": false,
    "first_time": 1731608940, "last_time": 1731609120,
    "newest_closed_time": 1731609120, "newest_closed_age_seconds": 42.1,
    "data_status": "ok",              // ok | partial | stale | insufficient | empty
    "data_provenance": "mt5_live",    // mt5_live | postgres_cache
    "retrieved_at": "2024-11-14T17:09:37.123+00:00",
    "warnings": [                     // e.g. GAPS_DETECTED, STALE_DATA, INVALID_OHLC…
      { "code": "GAPS_DETECTED", "severity": "warning", "message": "1 gap(s) … NOT back-filled" }
    ]
  },
  "error": null,
  "meta": { "request_id": "…", "timestamp": "…", "api_version": "v1" }
}
```

**Caching & data distinction.** Raw MT5 responses are validated in-process
(`validation.py`) and — when fresh enough per timeframe — served from the
PostgreSQL `candles` cache (`data_provenance: "postgres_cache"`); otherwise
they are fetched live (`mt5_live`) and persisted. Duplicate inserts are
impossible (composite PK + upsert); the full history is never loaded per
request; a dedicated `refresh=true` request bypasses the cache. No Redis.

Interactive docs: `http://localhost:8000/docs` (disabled when `APP_ENV=production`).

### The versioned rule format (schema `2.0`, legacy `1.0` supported)

Full specification: [`docs/strategy-rule-schema.md`](docs/strategy-rule-schema.md)
(+ generated JSON Schema at `docs/strategy-rule-set.schema.json`).

```jsonc
{
  "schema_version": "2.0",
  "mode": "forex",                          // forex | binary_options
  "timeframes": ["H1", "M15"],              // must be declared here first
  "direction": "bullish",                   // bullish | bearish | both
  "symbols": ["EURUSD"],                    // optional scope (absent = any)
  "market_regime": "trending",              // optional applicability
  "evaluation": { "on_close_only": true },  // optional settings
  "conditions": {                           // root group (required)
    "logic": "AND",                          // AND | OR
    "conditions": [
      { "id": "trend-hold", "type": "moving_average", "timeframe": "H1",
        "lookback": 60, "optional": false,
        "params": { "ma_type": "ema", "period": 20, "compare_to": "value",
                     "value": 1.08, "operator": "above" } }
    ],
    "groups": [ /* nested AND/OR groups (max depth 4) */ ]
  },
  "confirmation": { "logic": "AND", "conditions": [ /* … */ ] },  // optional
  "invalidation": { "logic": "AND", "conditions": [ /* … */ ] },  // optional
  "notes": "free-form user documentation (optional)"
}
```

**Rule-type registry** (single source of truth: `rule_registry.py`, served at
`/api/v1/rule-types`). Operational in Phase 3: `price_level`,
`candle_property`, `candle_pattern`, `swing_point`, `structure_pattern`
(HH/HL/LH/LL), `market_structure_break`, `moving_average`, `price_zone`.
Planned — documented but **NOT operational**, rejected with
`rule_type_not_available`: `mss_choch`, `liquidity_sweep`, `fvg`,
`order_block`, `breaker_block`, `support_resistance_reaction`. Every
operational type has defined semantics, input validation and tests; a unit
test guarantees registry ↔ evaluator consistency.

**Legacy 1.0** rule sets (flat `price_level` conditions, direction without
`both`) remain valid and evaluatable — stored strategies from earlier phases
keep working unchanged.

**Evaluation semantics** (deterministic, documented, tested — see the schema
doc for per-type details):

- Group `AND` = every **required** child satisfied (unmet *optional* rules
  are reported but never block); `OR` = at least one child satisfied.
- Result status: `conditions_met`, `conditions_missing`, `invalidated`
  (invalidation group satisfied), or `insufficient_data` (referenced
  timeframe has no usable candles — the platform never invents data).
- **No look-ahead bias**: evaluators only use information contained in the
  supplied candles. Swing confirmation requires `strength` subsequent
  candles *inside the data*; a candidate near the right edge never counts.

---

## Prerequisites

- **Python 3.12+**
- **Bun** (frontend dev server & tests) or Node 20+
- **Docker + Docker Compose** *or* a reachable PostgreSQL 16
  (a no-Docker local PostgreSQL is provisioned automatically on Linux via
  `pgserver` — bundled binaries, no root required)
- For LIVE market data only: **a Windows host with MetaTrader 5 installed,
  running and logged in** (or Wine), plus the `MetaTrader5` Python package
  (`pip install -r backend/requirements-mt5.txt`). Everywhere else the
  platform runs in the honest disabled/disconnected mode.

## Installation

```bash
# 1. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
pip install -r requirements-dev.txt                   # for tests
pip install -r requirements-mt5.txt                   # Windows host only (MT5 live data)

# 2. Frontend
cd ..
bun install                                           # or: npm install

# 3. Environment (placeholders only — never commit real values)
cp backend/.env.example backend/.env                  # then edit DATABASE_URL (+ MT5_* if live)
cp .env.example .env                                  # docker-compose settings
```

## Running

### Option A — Docker Compose (recommended for a real PostgreSQL)

```bash
docker compose up --build -d
# frontend  → http://localhost:3000
# backend   → http://localhost:8000  (docs at /docs)
# migrations run automatically before the backend starts
```

### Option B — Manual / no Docker

```bash
make backend     # provisions local PostgreSQL (pgserver) if no DATABASE_URL,
                 # applies migrations, starts uvicorn on :8000 (background)
bun run dev      # frontend on :3000 (proxies /api/* to :8000 in gateway mode)
```

The frontend calls the backend through one of two modes
(`src/lib/api/client.ts`):

- **Gateway mode** (default, `NEXT_PUBLIC_BACKEND_ORIGIN` empty): requests ride
  the same origin with `?XTransformPort=8000` — used by the sandbox preview and
  single-domain deployments.
- **Direct mode**: set `NEXT_PUBLIC_BACKEND_ORIGIN=http://localhost:8000`
  (already the default in `.env.example` for Compose) and add the frontend
  origin to backend `CORS_ORIGINS`.

### Option C — Windows host with a live MT5 terminal (live market data)

1. Install & start the MetaTrader 5 terminal; log in to your broker.
2. `pip install -r backend/requirements-mt5.txt` in the backend venv.
3. Configure `backend/.env`: `DATABASE_URL` (a reachable PostgreSQL),
   `MT5_ENABLED=true`. Prefer leaving `MT5_LOGIN/PASSWORD/SERVER` empty so the
   backend attaches to the terminal's own login — no credentials stored.
4. `cd backend && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`
5. Verify: `curl http://localhost:8000/api/v1/mt5/status` →
   `"connected": true`, then open the frontend's **Market** tab.

## Environment variables

### Backend (`backend/.env.example`)

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `local` | `local/development/test/staging/production`; placeholders rejected outside local/test. |
| `DEBUG` | `false` | Forbidden in production (fail-fast). |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | HTTP bind. |
| `DATABASE_URL` | — (required) | `postgresql+asyncpg://user:pass@host:5432/db`. App refuses to start if missing/invalid; runtime outages are reported by `/health/ready`. |
| `DB_ECHO`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | `false`, `5`, `10` | SQLAlchemy tuning. |
| `CORS_ORIGINS` | empty | Comma-separated absolute origins. |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` | `json` for collectors, `console` for humans. |
| `MARKET_DATA_PROVIDER` | `mt5` | The only provider. |
| **MetaTrader 5** | | See “MT5 integration topology” above. |
| `MT5_ENABLED` | `true` | `false` = safe disabled mode (default in Linux containers). |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | empty | Optional explicit terminal login — all three together or none (validator enforces). Prefer NONE: attach to the already logged-in terminal. **Never committed, never logged, never returned by any API.** |
| `MT5_TERMINAL_PATH` | empty | Optional absolute `terminal64.exe` path. Configuration-only; no API accepts paths. |
| `MT5_TIMEOUT_SECONDS` | `10` | Bounded timeout for every terminal call. |
| `MT5_RETRY_ATTEMPTS` / `MT5_RETRY_BACKOFF_SECONDS` | `3` / `1.5` | Bounded reconnect with capped exponential backoff (+30s cooldown after a failed attempt). |
| `MT5_MAX_CANDLES` / `MT5_DEFAULT_CANDLES` | `5000` / `200` | API limit (422 above max) and default request size. |
| `MT5_CACHE_ENABLED` | `true` | PostgreSQL historical candle cache. |
| `MT5_CACHE_MAX_AGE_SECONDS` | `90` | Serve-from-cache freshness bound (per-timeframe grace applies). |
| `MT5_STALE_AFTER_INTERVALS` | `3` | Closed candles older than N intervals (plus weekend allowance) are flagged `STALE_DATA`. |
| `MT5_WARMUP_ON_STARTUP` | `false` | Opt-in terminal warm-up at startup. Left off so a wedged terminal cannot make the HTTP server unresponsive; candle sync and lazy connection still work. |
| **AI research layer (Phase 7, optional)** | | |
| `AI_ENABLED` | `false` | Master switch. `true` requires `AI_BASE_URL`, `AI_MODEL` and `AI_API_KEY` (fail-fast otherwise). The deterministic platform is fully usable without it. |
| `AI_BASE_URL` / `AI_MODEL` | empty | Any OpenAI-compatible chat-completions endpoint + model name. No specific model is assumed to exist. |
| `AI_API_KEY` | empty | Server-side only: never accepted by endpoints, never logged, never returned (`/api/v1/ai/status` reports booleans only). |
| `AI_TIMEOUT_SECONDS` / `AI_MAX_RETRIES` / `AI_RETRY_BACKOFF_SECONDS` | `45` / `2` / `1.5` | Bounded provider calls (retries only for 429/5xx/timeouts). |
| `AI_RATE_LIMIT_PER_MINUTE` | `10` | Per-owner sliding window (in-process). |
| `AI_MAX_INPUT_CHARS` / `AI_MAX_OUTPUT_TOKENS` / `AI_TEMPERATURE` | `24000` / `1400` / `0.1` | Bounded prompts/outputs, low temperature by design. |

### Frontend / Compose (`.env.example`)

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_BACKEND_ORIGIN` | Compose: `http://localhost:8000` | Empty = gateway mode (`XTransformPort`). |
| `NEXT_PUBLIC_BACKEND_PORT` | `8000` | Used in gateway mode. |
| `TA_DB_USER` / `TA_DB_PASSWORD` / `TA_DB_NAME` | `ta_user` / `CHANGE_ME…` / `ta_platform` | Compose PostgreSQL. **Change the password.** |

## Migrations

```bash
cd backend
alembic upgrade head          # apply (done automatically by docker/backend scripts)
alembic revision --autogenerate -m "describe change"
alembic downgrade -1          # roll back one step
alembic current               # show applied revision
```

Migration URLs resolve from `ALEMBIC_DATABASE_URL` (override) or the app's
`DATABASE_URL`. Migrations in this repo:

1. `9c7783174bf9` — `strategy_categories`, `strategies`, `strategy_versions`
   (JSONB rule sets, CHECK constraints, immutable `(strategy_id, version)`).
2. `c5f21a0b93d2` — `candles` cache table: composite PK
   `(symbol, timeframe, open_time)` (duplicate inserts impossible at the DB
   level), positive-price & `high >= low` CHECK constraints, newest-first
   index per series.
3. `a3f8c1d92e47` — Phase 3 ownership columns, category archiving, version
   lifecycle (draft → published → superseded), validation audit table.
4. `b7e4d2c9a510` — Phase 4 `analysis_reports`: immutable versioned report
   payloads (owner-scoped, rule-set snapshot inside, candle references only).
5. `d8e5f3a1b620` — Phase 6 `backtest_runs` + `backtest_results`:
   validated run configurations with lifecycle states, chronological
   result records (`seq` order, unique per run), realized P&L / equity
   path points, full evaluation payloads in JSONB, owner-scoped indexes.
6. `f4b8e6c2a917` — Phase 7 `ai_conversations` + `ai_messages`:
   owner-scoped research conversations (loose artefact references
   validated per access) and auditable messages storing the exact
   minimized context each answer was built from, plus its validation
   summary. The AI layer writes ONLY these rows.
7. `c9a1e5d7b324` — Phase 8 research optimization: runs, candidates,
   walk-forward windows and robustness scenarios with full configuration
   snapshots and owner-scoped indexes.
8. `e2c7a91b4d30` — Phase 11 historical replay: `replay_sessions`, a saved
   deterministic cursor over the existing candle cache (requested/resolved
   window, quality snapshot, cursor and visibility counters). No candle rows
   are duplicated and no execution/journal table is added.

## Testing

```bash
# Backend — current checkout: 786 passed, 3 skipped. Unit tests use a faked
# MetaTrader5 (no terminal needed); integration tests use a REAL PostgreSQL
# server. External LLM providers are ALWAYS scripted fakes — no network, no
# paid API calls. On Windows (no pgserver wheel) set TA_TEST_DATABASE_URL to
# a disposable PostgreSQL database whose name contains "test".
cd backend && python3 -m pytest tests/ -q

# Frontend — current checkout: 153 tests across 14 files (vitest + Testing
# Library). Includes the replay workspace/playback tests.
bun run test          # or: bunx vitest
bun run lint          # ESLint
```

What the suites actually verify: configuration fail-fast behaviour (incl. the
MT5 credential consistency rule), the response envelope & error codes,
rule-format whitelist validation (every rejection path), evaluation semantics,
data-quality warnings, category & strategy CRUD, immutable versioning, the
analysis API — plus the Phase 2 suite: **connection lifecycle** (connect,
disconnect, stale-session reconnect, bounded retries + cooldown,
timeout handling), **missing package / missing symbol / empty & None rate
responses**, **candle validation** (invalid OHLC, NaN/garbage prices,
duplicates with last-wins, unsorted input, gaps reported but never filled,
forming vs closed, staleness incl. weekend-tolerant D1, insufficient history),
**API contracts** (structured 422/404/502/503, closed-only filtering, cache
provenance switch from `mt5_live` to `postgres_cache`, credential-free
status payloads), **PostgreSQL persistence** (upsert idempotency, duplicate
protection, forming-candle refresh, large batches, freshness policy) and the
frontend **connected / disconnected / disabled / loading / error** states.

The **Phase 3 suite** adds: rule-registry integrity (every operational type
has an evaluator, every planned type has none), parameter-schema validation
(unknown/missing/out-of-range/cross-field), schema 2.0 parsing with legacy
1.0 round-trips, per-type evaluator semantics on deterministic candles
(swing confirmation without look-ahead, MA warm-up and fresh-cross rules,
pattern geometry, zone interactions), optional-rule and direction-`both`
semantics, and integration coverage of the whole builder workflow:
category/subcategory archiving, pagination, draft → publish → supersede
(with immutability assertions), duplication, strategy archiving, the
validation audit trail, **ownership isolation** (foreign reads/edits → 404,
same names allowed across owners, invalid header → 422), and the frontend
builder (loading, registry-driven parameter forms, inline validation
errors, publish preview, unsaved-changes guard, planned types disabled,
JSON tab).

The **Phase 4 suite** adds: the deterministic feature library (candle
anatomy, SMA/EMA/ATR arithmetic, swing confirmation TIMING — a swing known
at its confirmation candle is provably absent one candle earlier —,
structure-break adoption rules, zone interactions, gap/completeness
reporting), the regime classifier (uptrend/downtrend/range/breakout/
transition/uncertain on constructed series, volatility expansion/compression,
applicability buckets, agreement/conflict reporting that is never forced),
the four-state engine semantics (AND never converts unknown/unsupported into
success, OR never conceals unavailable conditions, `inconclusive` vs
`not_met` distinction, forming-candle exclusion + provisional opt-in,
regime-gated `regime_mismatch`), the advanced rule types (mss_choch / bos /
liquidity_sweep / fvg semantics, edge cases and look-ahead guards) and
integration coverage of the engine API: run + persistence + retrieval,
history filters, ownership isolation, honest 503 refusal without a provider,
symbol scope, staleness, binary-mode disclaimers, features/readiness
endpoints and the Phase 1 inline-evaluation regression.

The **Phase 5 suite** adds: the overlay derivation model (every overlay
traces to report observations/condition evidence; timeframe filtering;
HH/LH/HL/LL label alignment for full AND truncated swing lists; BOS
segments; FVG zones that stop at their origin when filled; MSS/CHoCH levels;
strategy zones; honest engine-unsupported reporting), the candle feed
(dedup by time, closed-over-forming replacement, out-of-order drops,
reference-stability when nothing changed, interval polling with fake
timers, visibility pausing, in-flight suppression, unmount cleanup, honest
status transitions incl. stale / no-data / disconnected-with-retained-data,
exact query contracts), the chart component against a mocked
Lightweight Charts (one chart per mount, sorted `setData`, incremental
`update()` for tail-only changes, full resync on history change or series
identity change, `remove()` exactly once on unmount, malformed/empty
candle handling, overlay visibility gating), and dashboard integration
(strategy/version loading, four-state condition sections with no fake
probabilities, overlay toggles traceable to the report, stale/disconnected
states, history access, accessible controls, Binary-mode M1-only + broker-
feed disclaimer).

### Phase 5 — dashboard architecture notes

```
src/components/dashboard/   market-dashboard.tsx   composition + state
                            connection-status.tsx  honest freshness indicator
                            symbol-select.tsx      backend-populated combobox
                            overlay-controls.tsx   per-kind visibility toggles
                            condition-sections.tsx four-state flattening
                            analysis-history.tsx   persisted reports browser
src/components/chart/       candle-chart.tsx       Lightweight Charts wrapper
src/lib/chart/              overlay-model.ts       report → overlay derivation (pure)
                            band-primitive.ts      rectangle/level canvas primitive
src/hooks/                  use-candle-feed.ts     polling feed (dedup, stale, cleanup)
src/components/error-boundary.tsx                   dashboard crash isolation
```

Key behaviours (all enforced by tests):

- **No WebSocket exists in the backend** — the dashboard polls the
  documented `GET /api/v1/market/candles` endpoint at a configurable
  interval (Off / 10 s / 30 s / 60 s), pauses while the tab is hidden,
  resumes on visibility, and stops cleanly on unmount.
- **Merge semantics**: candles are keyed by open time; a closed candle
  replaces its forming predecessor at the same time; out-of-order
  arrivals are sorted; the chart is fed ascending data only.
- **Overlays are per-timeframe and per-report**: `buildOverlays(report,
  displayedTimeframe)` returns empty collections unless the report really
  contains observations for the displayed timeframe; the UI shows a hint
  naming the timeframes the report DOES cover instead of drawing anything.
- **Theme colours**: Tailwind v4 computes theme colours as `oklch/lab`,
  which Lightweight Charts cannot parse. The chart normalises every CSS
  colour through a 1×1 canvas pixel round-trip before handing it to the
  library (fallback to a safe default if resolution fails).
- **Dependencies added**: `lightweight-charts@^5.2.1` only (Apache-2.0).
  Everything else reuses the existing stack (shadcn/ui, zustand,
  lucide-react, the typed API client).

### Optional live-terminal test (real Windows machine)

The suite NEVER claims a real MT5 connection was tested — that only happens
with the opt-in live module, on a host where the terminal actually runs:

```bat
:: on the Windows machine with MT5 running & logged in
set RUN_MT5_LIVE=1
set MT5_ENABLED=true
pip install -r backend/requirements-mt5.txt
cd backend && python -m pytest tests/integration/test_mt5_live.py -v -m live
```

It performs a real connect, fetches EURUSD M1 candles and lists real symbols.
Without `RUN_MT5_LIVE=1` the module skips itself everywhere.

## Phase 6 — Historical analysis, backtesting & strategy performance

The Backtests tab runs a saved, immutable strategy version over the stored
historical candle cache and reports setups and (only where explicitly
configured) simulated trades. Full design notes: `docs/backtest-schema.md`.

Core guarantees (enforced by tests):

- **Chronological, zero look-ahead** — the evaluation of candle *i* sees
  only candles whose CLOSE time ≤ the close time of candle *i* on every
  timeframe (higher-timeframe candles still forming at that instant are
  invisible). Swing/structure confirmation timing is inherited from the
  shared Phase 4 evaluator, so a break can never be backdated before it
  was knowable. The evaluator's wall-clock stamp is replaced with the
  simulated instant, making runs byte-for-byte reproducible.
- **Setup-only by default** — trades are simulated only when the request
  carries explicit execution assumptions (Forex: spread/slippage/
  commission/SL/TP/sizing/swap + same-candle SL/TP policy; Binary:
  expiry candle count, payout, stake, tie policy). `direction: both`
  strategies never get a fabricated trade side — setups are recorded,
  trades are not.
- **Declared assumptions only** — every fill derives from stored candle
  prices plus the persisted cost configuration; a candle touching both
  stop and target follows the configured policy (conservative default /
  optimistic / ambiguous-and-excluded). Nothing favourable is assumed
  silently.
- **Metrics come from records** — `win_rate` = wins / (wins + losses)
  (denominator documented in the payload); missing data renders as
  "Not available", never zero; `profit_factor` is undefined (null) without
  both gross sides; equity/drawdown derive from closed-trade balances only.
- **No profitability claims** — every metrics payload states these are
  historical simulation statistics, not expected future performance; MT5
  data is never described as a broker's execution feed and OTC feeds are
  never simulated.

API (owner-scoped, standard envelope):

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/backtests` | validate + queue a run |
| GET | `/api/v1/backtests` | paginated run history (filters: mode, strategy, status, symbol) |
| GET | `/api/v1/backtests/{id}` | configuration, status, metrics |
| GET | `/api/v1/backtests/{id}/results` | paginated chronological records (filters: kind, setup_status, outcome) |
| GET | `/api/v1/backtests/{id}/results/{seq}` | one record with the full evaluation tree |
| GET | `/api/v1/backtests/{id}/equity` | realized equity path (empty for setup-only runs) |
| POST | `/api/v1/backtests/{id}/cancel` | cooperative cancellation (next evaluation boundary) |
| GET | `/api/v1/backtests/{id}/export` | CSV export (results or summary) with formula-injection protection |
| GET | `/api/v1/backtests/data-coverage` | pre-run candle availability check |

Execution architecture: runs execute as in-process `asyncio` tasks
(`BACKTEST_MAX_CONCURRENT`, default 1) — a documented, minimal runner, no
external task queue. Runs left queued/running by a restart are marked
`failed` with code `RUNNER_RESTARTED` at startup (never silently "done").

Relevant settings: `BACKTEST_MAX_CANDLES` (window cap, default 20 000),
`BACKTEST_WARMUP_CANDLES` (lookback warm-up before `start`, default 150),
`BACKTEST_MAX_EXPIRY_CANDLES` (binary expiry cap), `BACKTEST_MAX_CONCURRENT`.

## Phase 7 — AI-assisted strategy analysis & explainable research

Phase 7 adds an **evidence-first AI research layer** that explains the
deterministic engines' recorded results in plain language — and nothing
more. Full design notes: `docs/ai-research.md`.

Architecture (strictly separated):

- **Deterministic engines stay authoritative** — the analysis engine and
  backtest engine are the only source of conditions, four-state statuses,
  evidence and metrics. The LLM never calculates values, never evaluates
  rules, never decides whether an unsupported rule "passed".
- **Minimized, validated context** — the model receives a pydantic-validated
  JSON context: mode/symbol/timeframes, data freshness and quality warnings,
  regime + evidence, the exact immutable strategy version, every condition
  with its recorded status and a stable evidence ref, backtest metrics
  verbatim (`null` = "not available", never zero-filled) and explicit
  `unavailable` items. No credentials, no raw candles, no other users' data.
- **Fail-closed validation** — every answer passes schema validation, an
  evidence-reference check (dangling refs → discarded), numeric
  traceability (invented numbers → discarded) and safety guards
  (guarantees / confidence percentages / order advice / code → rejected).
  Failed answers are never shown as success: `502 AI_OUTPUT_INVALID` plus
  an honest persisted `error` message for chats.
- **Prompt-injection protection** — user text is untrusted data confined in
  `<user_message>` delimiters, control-character-stripped, size-capped and
  secret-redacted before it reaches the provider or logs. The LLM has no
  tools, no code execution and no database access.
- **Provider abstraction** — any OpenAI-compatible endpoint works
  (`AI_BASE_URL` + `AI_MODEL`, key server-side only); bounded timeouts,
  retries and rate limits; quota/outage/malformed responses mapped to
  honest errors. `AI_ENABLED=false` (default) keeps the whole deterministic
  platform fully usable; the UI explains that AI is unavailable and why.

API (owner-scoped, standard envelope):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/ai/status` | honest AI-layer status (booleans only — no secrets) |
| POST | `/api/v1/ai/explanations` | explain one authorized analysis report |
| POST | `/api/v1/ai/research/chat` | one research-chat turn (persisted, auditable) |
| GET | `/api/v1/ai/research/conversations` | list the owner's conversations |
| POST | `/api/v1/ai/research/conversations` | create a conversation |
| GET | `/api/v1/ai/research/conversations/{id}` | retrieve a conversation + messages |
| DELETE | `/api/v1/ai/research/conversations/{id}` | delete an authorized conversation |

Frontend: a violet-accented **AI explanation panel** under every engine
report (AI-generated interpretation visually distinct from deterministic
evidence, every cited item shows its evidence refs) and an **AI Research**
tab with owner-scoped conversations, visible research context and
loading/empty/error states.

Passing validation proves grounding to the supplied evidence — **not**
factual market truth. No confidence percentages are produced (qualitative
uncertainty only), and no statement is ever a prediction or advice.

---

## Phase 8 — Strategy optimization, robustness testing & walk-forward validation

Phase 8 adds a **research workspace** (frontend "Research Lab" tab) that
tests explicitly selected strategy parameters over the stored candle
cache, separates in-sample from out-of-sample evidence, and reports
sensitivity honestly. It reuses the Phase 6 engine (`run_walk`), the
Phase 3 immutable strategy versions and the in-process runner
architecture — there is no second simulation engine and no task queue.

Core guarantees (enforced end-to-end, tested):

* **Explicit parameters only** — a parameter varies one EXISTING field
  (lookback or an existing `params` key) of the immutable rule-set
  snapshot with user-selected allowed values. Optimization can never
  invent rules or execute code; every patched candidate is revalidated
  with the standard rule validator before any walk.
* **Leakage prevention** — holdout runs select candidates on the
  in-sample window and walk the held-out tail EXACTLY ONCE with the
  frozen winner (boundary exclusion: OOS starts one interval after the
  split). Walk-forward windows are chronological with
  `train_end == test_start` and non-overlapping test windows
  (`step >= test`), selection consumes training data only, and every
  walk executes against a series clipped at its phase end (purge:
  trades needing future candles become open/unresolved and are excluded).
* **Honest metrics** — undefined metrics rank as `NULL` with a reason,
  never zero; sample sizes sit next to every score; win-rate-only
  ranking carries a warning; failed/invalid candidates are recorded.
* **Reproducibility** — every run stores its full configuration
  (search space, objective, split, walk-forward settings, cost
  scenarios, seed, limits, software versions, immutable rule-set
  snapshot). Two identical runs produce identical results.
* **No automation** — nothing places orders, publishes strategies, or
  claims profitability. Saving a candidate as a NEW DRAFT strategy
  version is an explicit action through the existing Strategy Builder.

Robustness scenarios (documented assumptions + deltas + limits):
execution-cost variants (Forex spread/slippage/commission/swap, Binary
payout/fees/tie policy), parameter-neighbour sensitivity, an OPTIONAL
seeded i.i.d. bootstrap (bounded, not a significance test), and
walk-forward selection stability. Derived time-period / market-regime /
data-quality views come from the engine's own persisted breakdowns.

Bounded computation via env settings (`RESEARCH_MAX_COMBINATIONS` 200,
`RESEARCH_MAX_WALKS` 1500, `RESEARCH_MAX_WINDOWS` 40,
`RESEARCH_MAX_COST_SCENARIOS` 6, `RESEARCH_BOOTSTRAP_SAMPLES` 200,
`RESEARCH_MAX_CONCURRENT` 1) — configurations beyond the budget are
rejected at creation with precise error codes.

API (owner-scoped, paginated, documented in
`docs/research-optimization-schema.md`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/research/optimization` | validate + queue a research run |
| GET | `/api/v1/research/optimization` | paginated run history |
| GET | `/api/v1/research/optimization/parameter-schema` | optimizable-parameter inventory of a strategy version |
| GET | `/api/v1/research/optimization/{run_id}` | config, progress, aggregate |
| GET | `/api/v1/research/optimization/{run_id}/candidates` | ranked candidates (+ `/{index}` detail) |
| GET | `/api/v1/research/optimization/{run_id}/walk-forward` | chronological window results |
| GET | `/api/v1/research/optimization/{run_id}/robustness` | robustness scenarios |
| POST | `/api/v1/research/optimization/{run_id}/cancel` | cooperative cancellation |

Fixes applied to previous phases during Phase 8 (minimal, documented in
code): the Phase 6 engine never called `forex.settle()` after exit
resolution, so realized P&L stayed `NULL` and every Forex trade was
recorded as a tie (equity never moved); and both runner singletons
could crash with "bound to a different event loop" when their
semaphore was first contended from a different loop — both runners are
now loop-scoped. All pre-existing suites still pass.

---

## Phase 9 — Production security, monitoring, reliability & deployment readiness

Phase 9 hardens the platform for real deployments **without changing any
analysis behaviour**: no new trading capability, no Phase 10 work, no
automatic execution. The full audit (findings, severity, evidence) lives in
`docs/security-audit-phase9.md`; the runbook in `docs/operations.md`.

What was added / changed (all enforced by tests):

* **Real API authentication (opt-in locally, mandatory in production)** —
  every `/api/v1` endpoint requires `Authorization: Bearer <token>` when
  `API_AUTH_TOKEN`/`API_AUTH_TOKENS` is configured; `APP_ENV=production`
  **refuses to start without one** (fail fast). Optional `token:owner`
  bindings pin a request to its owner and ignore a spoofed `X-User-Id`.
  Health endpoints stay open for infrastructure probes; local single-user
  mode behaves exactly as before when no token is configured. Tokens live
  only in server-side configuration, are never logged, and comparison is
  constant-time.
* **Log secret redaction** — every log record's payload passes a redaction
  layer (credential-shaped keys and values) before reaching the handlers;
  DB URLs were already masked.
* **Security headers** — `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, `Cache-Control: no-store` and a restrictive CSP on API
  responses (backend) and on the Next.js app (frontend `next.config.ts`).
* **Rate limit on backtest creation** — the last unthrottled
  resource-intensive operation now uses the same per-owner sliding-window
  limiter as research/AI (429 with honest message).
* **Frontend type gate** — `ignoreBuildErrors: true` removed; `tsc --noEmit`
  and the production build now fail on type errors (several latent type
  errors fixed; a stale `ParamFieldSchema` union now mirrors the backend's
  `boolean` param type).
* **Container hardening** — backend and frontend images run as non-root
  users; the frontend image gained a healthcheck; compose binds PostgreSQL
  (and by default API/frontend) to `127.0.0.1`, waits on real health
  conditions, and passes the auth environment through.
* **Backup & disaster recovery** — `scripts/backup-db.sh` (pg_dump custom
  format, retention, integrity check), `scripts/restore-db.sh` (single
  transaction, refuses non-empty targets without `--force-overwrite`,
  post-restore validation) and `scripts/verify-backup-restore.sh`, which was
  **actually executed**: disposable cluster → migrate → insert → backup →
  restore → row-by-row verification PASSED. RPO/RTO are documented as
  operational goals, not guarantees.
* **CI (`.github/workflows/ci.yml`)** — backend suite on real PostgreSQL
  (migrations validated by running Alembic against an empty DB), app-factory
  startup validation, backup/restore verification, frontend lint + type
  check + vitest + production build, gitleaks secret scanning and pip-audit.
  (GitHub Actions cannot be executed inside this sandbox — the workflow is
  validated by inspection + the same commands run locally.)
* **Repository hygiene** — a tracked `.env`, the SQLite `db/custom.db`,
  `.zscripts/dev.pid` and 16 `tool-results/*.txt` artifacts were untracked
  and ignored.
* **Production operations guide** — `docs/operations.md`: prerequisites,
  env vars, secret rotation, migration/rollback reality (restore-from-backup
  is the supported rollback; downgrades are not guaranteed lossless), HTTPS,
  health verification, logs, MT5/AI failure behaviour, troubleshooting,
  resource limits and a post-deployment checklist.

New/changed configuration is documented in both `.env.example` templates.
Existing behaviour is preserved: with no tokens configured (local/test) the
API is open exactly as in Phases 1–8. The current suites remain green
(786 backend tests passed, 3 skipped; 153 frontend tests passed).

---

## Phase 11 — Historical market replay (first vertical slice)

The **Replay** tab walks stored PostgreSQL candles candle-by-candle through a
persisted, deterministic server-side cursor. It is an original implementation
built on the platform's own candle cache and data-quality helpers; no
third-party replay product code, branding or assets were used.

What is implemented:

* **Saved sessions** — owner-scoped (`X-User-Id`) history with symbol,
  timeframe, requested window, status, visible/total counts, progress,
  data-quality badge and creation time. Reloading the page resumes at the
  saved cursor.
* **Deterministic no-look-ahead playback** — creation resolves only closed,
  valid, deduplicated candles. The chart endpoint returns only candles with
  `open_time <= cursor_time`; `step` loads only rows strictly after the
  cursor. A defensive `REPLAY_LOOKAHEAD_GUARD` rejects any future candle.
* **Honest data quality** — forming rows and invalid OHLC rows are excluded
  and counted; duplicates are removed once and reported; gaps are reported,
  never filled. The quality snapshot is persisted with the session.
* **Lifecycle** — create (paused at the first candle), step (1–100), seek,
  play/pause, reset, speed and delete. Step is allowed while paused for
  single-bar inspection. Completion is explicit; resume-after-end returns
  `409 REPLAY_AT_END`.
* **Presentation-only speed** — the backend accepts `0 < speed <= 60`; the
  UI offers 0.5× through 30×. Speed changes the delay between server steps,
  never chronology or future fills.
* **Offline-first** — replay never fetches live MT5 data while playing. It
  reads the existing PostgreSQL candle cache; missing history is an honest
  422 (`NO_HISTORICAL_DATA`), never a fabricated candle.

API (owner-scoped, standard envelope; full contract in
[`docs/replay.md`](docs/replay.md)):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/replay/data-coverage` | Read-only stored-candle coverage check. |
| POST | `/api/v1/replay/sessions` | Create a paused session (rate-limited). |
| GET | `/api/v1/replay/sessions` | Paginated saved-session history. |
| GET | `/api/v1/replay/sessions/{id}` | Saved cursor, progress and quality. |
| GET | `/api/v1/replay/sessions/{id}/candles` | Newest visible candles, bounded by the cursor. |
| POST | `/api/v1/replay/sessions/{id}/step` | Advance closed candles. |
| POST | `/api/v1/replay/sessions/{id}/seek` | Jump to the latest candle at/before a time. |
| POST | `/api/v1/replay/sessions/{id}/resume` \| `/pause` \| `/reset` | Presentation lifecycle. |
| PATCH | `/api/v1/replay/sessions/{id}` | Presentation speed. |
| DELETE | `/api/v1/replay/sessions/{id}` | Delete this owner's session. |

Settings: `REPLAY_MAX_CANDLES=20000` (session window cap),
`REPLAY_MAX_CANDLES_RETURNED=5000` (chart response cap) and
`REPLAY_MAX_CREATES_PER_MINUTE=30`. Binary Options replay is M1-only.

**Not in this slice** (deferred, not silently implied): simulated replay
orders/fills/exits/P&L, the replay trade journal, CSV dataset import and
provenance workflows, shared interactive execution with the backtest engine,
multi-timeframe replay, strategy evaluation during playback, streaming and
automatic session retention. See `docs/replay.md` for the complete list.

---

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Backend exits: `DATABASE_URL …` | Config invalid/missing. Copy `backend/.env.example` → `backend/.env` and set a real PostgreSQL URL. |
| Backend exits: `MT5_LOGIN … MT5_PASSWORD/MT5_SERVER` | Partial MT5 credentials. Provide all three or none (recommended: none — attach to the logged-in terminal). |
| `/health/ready` returns 503, database `down` | PostgreSQL unreachable. `docker compose up -d db`, or let `make backend` provision a local pgserver cluster. The app keeps running and reports honestly. |
| Frontend shows “backend unreachable” | Backend not started. Run `make backend` (or `docker compose up -d backend`). |
| `/api/v1/mt5/status`: package “not installed” | The `MetaTrader5` package is missing or you are on Linux/macOS. Install `backend/requirements-mt5.txt` on the Windows host with the terminal. The API stays honest — no data is faked. |
| `/api/v1/mt5/status`: “Could not connect … after N attempt(s)” | Terminal not running/not logged in. Start & log into MT5, wait ~30s (reconnect cooldown), then press “Check now”. |
| `MARKET_DATA_UNAVAILABLE` (503) with `MT5_ENABLED=false` | Disabled mode (e.g. Docker Compose default). Enable MT5 on a Windows host or run analysis on user-supplied candles. |
| `SYMBOL_NOT_FOUND` | The symbol is unknown to this terminal. Check `GET /api/v1/market/symbols`; some brokers suffix symbols (`EURUSD.i`). |
| `MARKET_DATA_ERROR` “no rate history” | The symbol exists but has no data for that timeframe/history depth. Try another timeframe or a smaller `limit`. |
| Candle response says `STALE_DATA` / `GAPS_DETECTED` | The terminal is serving old or gapped history (market closed, low liquidity, or symbol recently selected). Warnings are informational; nothing is back-filled. |
| Port 8000 already in use | Another backend instance is running — `make backend` detects and skips. Change `API_PORT` if needed. |
| `rule-sets/validate` returns `RULE_VALIDATION_FAILED` | Read `error.details[].path` — each entry pinpoints the exact violation against the whitelist. |
| Rule save rejected: `rule_type_not_available` | The rule set references a PLANNED type (e.g. `fvg`, `mss_choch`). These are documented but not operational. Pick an available type from `GET /api/v1/rule-types`. |
| `strategy_mismatch` on validate | The rule set's mode/direction disagrees with the strategy (a `both` strategy accepts any rule-set direction; a directional one does not). |
| Publish returns `ALREADY_PUBLISHED` / `VERSION_SUPERSEDED` | That version is already the live definition, or a historical one. Save a new draft version instead. |
| Publish returns `STRATEGY_ARCHIVED` | Unarchive the strategy first. |
| Every API call returns 401 `UNAUTHORIZED` | API authentication is enabled but the frontend was built without the token. Rebuild with `NEXT_PUBLIC_API_TOKEN` set (see docs/operations.md). |
| `INVALID_OWNER_ID` | The `X-User-Id` header must be 1–64 chars from `[A-Za-z0-9._@-]`. |
| A strategy “disappeared” from the list | It (or its category) is archived. Enable “Show archived”, or `?include_archived=true` / `?status=archived`. |

## Known limitations (Phase 4)

1. **Ownership is scoping, not authentication** — the `X-User-Id` header
   separates data between users but does not verify identity. Suitable for
   local/single-operator deployments; if the API is reachable beyond
   localhost, front it with your own auth proxy. Built-in authentication
   remains future work.
2. **Real-terminal behaviour is implemented but was NOT exercised** — the
   sandbox cannot run MetaTrader 5. All automated tests use a faithful fake;
   use the documented live procedure above to validate a specific broker
   terminal. Until then, treat live behaviour as unverified.
3. **WebSockets/streaming** are not implemented — the frontend polls every
   15–30 s. Planned next.
4. **Three advanced concepts remain intentionally non-operational** —
   `order_block`, `breaker_block` and `support_resistance_reaction` are
   documented in the registry, shown as unavailable in the UI and rejected
   by validation. Their precise definitions, edge cases and tests are
   prerequisite work before activation (the Phase 4 promotions of
   `mss_choch`/`bos`/`liquidity_sweep`/`fvg` show the exact path).
5. **Regime classification is a documented heuristic** — the six regimes
   describe the recent past on one timeframe with configurable thresholds;
   they are not predictions and carry no confidence numbers. Transition and
   uncertain regimes never satisfy a strategy regime requirement.
6. **No probability or confidence percentages** — the report states
   technical conditions and evidence only; a percentage would require a
   defined, validated calibration methodology that does not exist yet.
7. **No automatic trading or order execution** — permanently out of scope;
   nothing here sends orders anywhere, and no profitability is claimed or
   implied.
8. The cache serves at most the configured `limit` of latest candles per
   series; deep historical backfill (beyond the terminal's own depth) is not
   attempted.
9. `docker compose` builds the frontend in dev mode; the production build
   path (`Dockerfile.frontend`) was not exercised inside the sandbox.

## Recommended next steps (after Phase 10/11)

Phase 10 release-readiness evidence and the remaining gaps are recorded in
[`docs/phase10-release-readiness.md`](docs/phase10-release-readiness.md).
Replay's implemented scope and deferred items are in
[`docs/replay.md`](docs/replay.md).

1. Complete the replay feature set in explicit slices: simulated
   orders/fills/exits, a trade journal, then CSV dataset import with
   provenance. Reuse the Phase 6 execution primitives rather than creating a
   second simulation engine.
2. WebSocket streaming of candle updates + status changes; the frontend
   client already isolates all backend I/O in one module, and the backtest
   runner exposes the same status lifecycle that a push channel would need.
3. Advance the remaining planned rule types (order blocks, breaker blocks,
   S/R reactions) from registry entries to operational, tested evaluators —
   Phase 4's promotions document the exact recipe (spec + evaluator + tests);
   each new evaluator immediately becomes backtestable with zero engine
   changes.
4. Deeper backfill tooling for the historical candle cache (the backtest and
   replay engines run on whatever window the cache covers and report coverage
   honestly; they never fetch mid-walk to stay deterministic).
5. Optional: per-user account system with sessions/revocation on top of the
   Phase 9 bearer-token layer (for multi-tenant internet-facing deployments).
6. Optional: a calibration methodology IF confidence scores are ever
   requested — never before it exists.

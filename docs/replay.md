# Historical Market Replay (Phase 11)

Status: **first vertical slice implemented and tested**. This document is the
authoritative description of the replay data model, cursor semantics, API,
data-quality rules, configuration and known limitations. Simulated replay
trades, the trade journal, CSV dataset import/provenance and shared
interactive execution are **not** part of this slice.

The implementation is intentionally small and reuses the existing platform:

- the Phase 2 PostgreSQL candle cache is the only market-data source;
- the Phase 6 data-quality helpers (`assess_series_quality`) classify the
  replay window exactly like backtests;
- the Phase 6 backtest engine remains the only simulated-execution engine;
- no second candle store, no live MT5 reads during playback and no order
  placement.

---

## 1. What a replay session is

A replay session is a **saved, deterministic cursor** over stored closed
candles. It is *not* a downloaded candle array held in the browser and it is
*not* a client-side timer over future data.

At creation the backend:

1. validates mode, symbol, timeframe, timezone-aware window and speed;
2. loads the stored rows for the window through the existing candle store;
3. keeps only closed, valid, deduplicated rows;
4. assesses data quality (coverage, gaps, duplicates, invalid OHLC, forming
   rows);
5. persists the requested window, resolved first/last candle, quality
   payload, configuration snapshot, total candle count and cursor at the
   first candle;
6. starts the session **paused** with exactly one visible candle.

Playback then only moves the server-side cursor. Every chart response is
read from PostgreSQL and bounded by that cursor. Closing the tab and
reloading the page resumes at the persisted cursor.

### Cursor semantics

| Field | Meaning |
|---|---|
| `cursor_time` | Open time of the newest **visible** candle, UTC. |
| `current_time` | Simulated "now": `cursor_time + timeframe duration`. This is the close time of the newest visible candle. |
| `current_time_unix` | `current_time` as epoch seconds. |
| `visible_candles` | Number of candles visible from the first candle through the cursor. |
| `total_candles` | Number of usable closed candles in the resolved window. |
| `cursor_index` | `visible_candles - 1` (zero-based). |
| `remaining_candles` | `total_candles - visible_candles`. |
| `at_end` | `visible_candles >= total_candles`. |

`step` loads only rows with `open_time > cursor_time`; the chart endpoint
returns only rows with `open_time <= cursor_time`. A defensive check in the
service raises `REPLAY_LOOKAHEAD_GUARD` if a selected candle ever exceeds the
cursor.

Playback speed is **presentation-only**. It changes the delay between
frontend steps; it never changes candle order, visible counts, cursor
arithmetic or any future fill logic.

---

## 2. Architecture and data flow

```
MT5 terminal (Windows, optional live source)
        │  existing Phase 2 validation + cache pipeline
        ▼
PostgreSQL `candles` (closed + forming rows, provenance, fetched_at)
        │
        │  replay creation: load window, closed/valid/dedup, quality
        ▼
PostgreSQL `replay_sessions`
  requested window · resolved first/last · cursor · counters · quality
        │
        │  GET /candles (bounded by cursor)   POST /step|seek|...
        ▼
FastAPI replay router ── owner scoping (X-User-Id) ── rate limit on create
        ▼
Next.js Replay tab
  saved-session history · coverage check · chart · step/play/pause/reset
```

There is no background runner for replay. Cursor transitions are synchronous
HTTP requests; this keeps the first slice simple and auditable. The frontend
timer issues one `step` at a time and waits for the response.

### Ownership

Every session route is scoped by `X-User-Id` (default `local`). A session
belonging to another owner returns the same `404` as a missing session. There
is no existence oracle.

---

## 3. API

Base path: `/api/v1/replay`. All responses use the platform envelope
`{success, data, error, meta}`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/data-coverage` | Read-only coverage check for a symbol/timeframe/window. Never creates a session. |
| `POST` | `/sessions` | Create a paused session. `201`. Rate-limited per owner. |
| `GET` | `/sessions` | Paginated owner-scoped history. Filters: `mode`, `status`, `symbol`, `page`, `page_size`. |
| `GET` | `/sessions/{id}` | Saved cursor, progress, configuration and quality. |
| `GET` | `/sessions/{id}/candles` | Newest visible closed candles, bounded by the cursor. |
| `POST` | `/sessions/{id}/step` | Advance 1–100 closed candles. |
| `POST` | `/sessions/{id}/seek` | Jump to the latest closed candle at or before a target time. |
| `POST` | `/sessions/{id}/resume` | Mark the presentation timer active. |
| `POST` | `/sessions/{id}/pause` | Mark the presentation timer paused. |
| `POST` | `/sessions/{id}/reset` | Return to the first candle, paused. |
| `PATCH` | `/sessions/{id}` | Change presentation speed. |
| `DELETE` | `/sessions/{id}` | Delete this owner's session. |

### Create

```http
POST /api/v1/replay/sessions
Content-Type: application/json

{
  "mode": "forex",
  "symbol": "EURUSD",
  "timeframe": "M1",
  "start": "2026-10-01T00:00:00Z",
  "end": "2026-10-02T00:00:00Z",
  "playback_speed": 1.0
}
```

Rules:

- `start`/`end` must be timezone-aware and `start < end`;
- the requested window may not exceed 366 days;
- the resolved window may not exceed `REPLAY_MAX_CANDLES`;
- Forex supports the advertised timeframes; Binary Options replay is `M1`
  only;
- at least one usable closed candle must exist, otherwise
  `NO_HISTORICAL_DATA` (422). Replay never fabricates a first candle.

The response includes `status: "paused"`, `visible_candles: 1`,
`cursor_time`, `current_time`, `total_candles`, `progress`, `data_quality`,
the immutable `config` snapshot and `no_lookahead: true`.

### Step / seek / lifecycle

```http
POST /api/v1/replay/sessions/{id}/step
{ "count": 5 }
```

`step` is allowed while paused (single-bar inspection) and active. It is
bounded by the remaining candles; once the last candle becomes visible the
status is `completed`. Stepping a completed session is idempotent, not an
error.

```http
POST /api/v1/replay/sessions/{id}/seek
{ "target_time": "2026-10-01T12:34:56Z" }
```

`seek` selects the latest closed candle at or before the target. A target
before the first candle is rejected with `SEEK_OUT_OF_WINDOW`; a target after
the last candle clamps to the last candle. Seek sets the session to `paused`
(or `completed` at the end).

`resume` from a completed session returns `409 REPLAY_AT_END`; `reset` clears
that condition. `pause` on a completed session returns `409
REPLAY_COMPLETED`.

### Chart candles

```http
GET /api/v1/replay/sessions/{id}/candles?limit=500
```

- `limit` is capped by `REPLAY_MAX_CANDLES_RETURNED` (default 5,000).
- The response returns the newest visible candles in ascending time order,
  with `count`, `requested_limit`, `returned_limit`, `cursor_time`,
  `current_time`, `visible_candles`, `total_candles`, `progress`,
  `data_quality`, `no_lookahead: true` and `future_candles_excluded: true`.
- Every candle has `forming: false`. A replay window contains only closed
  rows.

### Errors

| Code | HTTP | Meaning |
|---|---|---|
| `INVALID_TIMEWINDOW` | 422 | `start >= end`, window over 366 days, or a non-timezone-aware timestamp. |
| `INVALID_SYMBOL` | 422 | Empty/invalid symbol. |
| `UNSUPPORTED_TIMEFRAME` | 422 | Not in the platform timeframe whitelist. |
| `MODE_TIMEFRAME_MISMATCH` | 422 | Binary Options replay requested on a non-M1 timeframe. |
| `NO_HISTORICAL_DATA` | 422 | No usable closed stored candles in the window. |
| `REPLAY_RANGE_TOO_LARGE` | 422 | Window exceeds `REPLAY_MAX_CANDLES`. |
| `SEEK_OUT_OF_WINDOW` | 422 | Target before the first resolved candle, or no candle at/before it. |
| `REPLAY_AT_END` | 409 | Resume attempted after completion. |
| `REPLAY_COMPLETED` | 409 | Pause attempted on a completed session. |
| `REPLAY_LOOKAHEAD_GUARD` | 409 | Defensive invariant violation: a future candle was selected. |
| `RATE_LIMITED` | 429 | Per-owner replay creation limit exceeded. |

---

## 4. Data-quality rules

The replay window is assessed with the same helper used by backtests. The
payload is persisted with the session and returned with every chart read.

- **Only closed candles are used.** Rows with `forming: true` (or
  `is_closed=false` in storage) are excluded and counted.
- **Invalid OHLC rows are excluded and reported.** A row needs numeric
  OHLC, positive prices and `high >= low`.
- **Duplicate timestamps are removed once and reported.**
- **Rows are sorted ascending by open time for playback.** Out-of-order
  counts are reported.
- **Gaps are reported, never filled.** Calendar/session gaps are normal in FX
  and are a warning; they do not by themselves make the window `partial`.
  Duplicate, unsorted, forming or invalid rows do set `overall_status` to
  `partial`.
- **Provenance is explicit:** `source: "postgres_candle_cache"` with a note
  explaining that the cache is synced from MetaTrader 5 and never fabricated.
- **The quality snapshot is frozen at creation.** Backfilling the cache later
  does not grow an existing session; create a new session to include newly
  stored history.

The quality payload contains at least: `candles_available`,
`candles_available_raw`, `expected_candles`, `first_time`, `last_time`,
`gap_count`, `largest_gap_intervals`, `duplicate_count`,
`out_of_order_count`, `forming_count`, `invalid_ohlc_count`,
`coverage_ratio`, `warnings`, `overall_status`, `closed_candles_only`,
`forming_candles_used`, `source`, `source_note` and `note`.

---

## 5. Persistence

Alembic revision `e2c7a91b4d30` (`phase 11: historical market replay
sessions`) adds one table after `c9a1e5d7b324`:

- `replay_sessions` with the owner, mode/symbol/timeframe, status, requested
  window, resolved first/last candle, cursor, visible/total counters,
  playback speed, JSONB `config` and `data_quality`, schema version and
  `last_advanced_at`;
- check constraints for valid mode/status, `visible_candles >= 1`,
  `total_candles >= 1`, `visible_candles <= total_candles` and
  `playback_speed > 0`;
- owner/created-at and owner/status indexes plus mode/symbol/status indexes.

No candle rows are duplicated. No execution/journal table exists in this
slice; the Phase 6 backtest tables remain the only simulated-trade records.

Upgrade and downgrade were verified on a clean disposable PostgreSQL
database; `alembic check` reports no metadata drift.

---

## 6. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `REPLAY_MAX_CANDLES` | `20000` | Hard cap on usable candles loaded into one session window. |
| `REPLAY_MAX_CANDLES_RETURNED` | `5000` | Hard cap on candles returned by one chart request. |
| `REPLAY_MAX_CREATES_PER_MINUTE` | `30` | Per-owner session-creation rate limit. |

Request validation additionally caps `step.count` at 100 and the replay
window at 366 days. The API accepts playback speeds `0 < speed <= 60`; the
delivered frontend offers `0.5, 1, 2, 4, 8, 16, 30`.

`MT5_WARMUP_ON_STARTUP` (default `false`) is related operationally: replay
itself never touches MT5, and a wedged terminal must not block the HTTP
server at startup. Candle sync is still performed by the existing market-data
paths.

---

## 7. Frontend

The **Replay** tab (visible in both Forex and Binary Options modes) contains:

- **Saved replays** — owner-scoped history with symbol/timeframe, requested
  window, status, visible/total counts, progress, data-quality badge and
  creation time. Selecting a row resumes at its saved cursor.
- **New replay session** — symbol, timeframe, UTC start/end, speed, a real
  `Check stored data` query and `Create replay`. Coverage is shown from stored
  closed candles only.
- **Playback workspace** — candlestick chart (Lightweight Charts v5) using
  only the backend-returned visible series, step/play/pause/reset controls,
  seek by timestamp, speed selector, progress, remaining count, data-quality
  card and delete. A completion toast fires once at the last stored candle.

The chart uses the existing `CandleChart` component with `followLatest`, so
newly visible candles are appended without rebuilding the chart. The frontend
never holds or renders future candles: every request is server-bounded.

---

## 8. Testing

Backend integration tests (`backend/tests/integration/test_replay_api.py`,
11 tests) cover:

- create paused at the first candle; exactly one visible candle;
- no-history rejection, binary M1-only rule and inverted windows;
- coverage endpoint is read-only and creates nothing;
- step never exposes future candles, including a bounded step past the end;
- pause/resume/reset/speed and invalid speed;
- seek to a timestamp between bars and out-of-window rejection;
- resume-after-end conflict and reset recovery;
- foreign-owner 404s on read/step/delete and owner-scoped history;
- mode filtering and delete.

Frontend tests (`src/tests/replay-workspace.test.tsx`, 8 tests) cover the
saved-session list, create/coverage flow, playback stepping, reload/resume,
quality display and error states. The Playwright workflow covers create →
coverage → step → reload → cursor resume → API cleanup.

Commands:

```bash
# backend (from backend/)
python -m pytest tests/integration/test_replay_api.py -q

# frontend (from repo root)
bunx vitest run src/tests/replay-workspace.test.tsx

# browser E2E (requires running backend/frontend and seeded E2E DB)
RUN_E2E=1 PLAYWRIGHT_BASE_URL=http://127.0.0.1:3000 \
  PLAYWRIGHT_API_URL=http://127.0.0.1:8000 bunx playwright test
```

---

## 9. Known limitations / deferred work

1. **No simulated replay execution.** There are no replay orders, fills,
   exits, SL/TP, P&L or equity records. The Phase 6 backtest engine is still
   the only place trades are simulated.
2. **No replay trade journal.** Notes, tags, screenshots and per-trade review
   are deferred.
3. **No CSV dataset import or dataset-provenance workflow.** Replay uses only
   the PostgreSQL candle cache synced through the existing market-data path.
4. **No shared interactive execution** between replay and the backtest engine.
5. **Single timeframe per session.** Multi-timeframe replay is not
   implemented; the session records one symbol/timeframe.
6. **No strategy evaluation during playback.** Replay shows candles and
   cursor state only.
7. **No streaming/WebSocket.** The frontend steps through the API one request
   at a time; playback speed is a presentation delay, not a server timer.
8. **No automatic retention or cleanup.** Saved sessions remain until deleted
   by the owner.
9. **No authentication.** Ownership is `X-User-Id` scoping, exactly like the
   rest of the platform; use the Phase 9 bearer-token layer or an external
   auth proxy for internet-facing deployments.
10. **Chart/session caps.** A session is limited to 20,000 usable candles and
    a chart request to 5,000; larger windows must be narrowed.
11. **Frozen quality snapshot.** A session does not automatically incorporate
    candles cached after it was created.
12. **Live MT5 reliability is environment-dependent.** Replay works offline
    from stored candles; syncing those candles still depends on the existing
    MT5 integration.

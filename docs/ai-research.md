# AI Research Layer (Phase 7) — version `1.0`

Phase 7 adds an **AI-assisted, evidence-first research layer** on top of the
existing deterministic engines. The AI **explains** recorded analysis reports
and backtest results in plain language. It never replaces the engines, never
evaluates rules, never invents market facts, never writes to the database and
never guarantees profitable outcomes.

## Architecture (strictly separated layers)

```
A. Data & feature layer        market-data service + stored candles
                               (existing, unchanged)
B. Deterministic layer         analysis engine (Phase 4) + backtest engine
                               (Phase 6) — the ONLY source of conditions,
                               four-state statuses, evidence and metrics
C. AI explanation layer        app/domains/ai/context.py     minimized, validated context
                               app/domains/ai/prompts.py     injection-hardened prompts
                               app/domains/ai/provider.py    OpenAI-compatible client
D. Validation layer            app/domains/ai/validation.py  fail-closed output checks
```

The LLM is **never** responsible for calculating OHLC values, evaluating
mathematical conditions or deciding whether an unsupported rule passed. It
receives a minimized, validated JSON context and must explain exactly that.

## AI context (what the model may see)

Built by `build_analysis_context` / `build_backtest_context` and validated by
pydantic (`context.py`). It contains **only**:

* trading mode, symbol, timeframes, data source;
* candle freshness / staleness / closed-vs-forming policy (per timeframe);
* data-quality warnings;
* market regime + its evidence (as recorded by the engine);
* strategy id, name, direction and the **exact immutable version** evaluated;
* every condition with its four-state status (`met` / `not_met` /
  `inconclusive` / `unsupported`) and a stable evidence ref
  (`core:<id>`, `confirm:<id>`, `invalid:<id>`, `regime:primary`, `data:<tf>`);
* for backtests: the **verbatim stored metrics** (`null` stays "not
  available" — never zero-filled), the simulator assumptions and the
  platform honesty limitations;
* explicit `unavailable` items (e.g. "no tick data", "forming candle excluded").

It can never contain credentials, raw candle payloads, other users' data or
anything the engines did not record.

## Validation layer (fail-closed)

Every provider answer passes `validate_explanation` / `validate_chat_answer`:

1. **Schema** — strict pydantic models, bounded lengths, unknown keys rejected.
2. **Evidence references** — every cited item must reference at least one
   evidence ref that exists in the supplied context. Dangling refs → the
   answer is discarded (`AI_OUTPUT_INVALID`).
3. **Numeric traceability** — every number in the output must appear in the
   supplied context. Invented statistics are rejected. *Documented limit:*
   this is a deterministic token-presence check — it blocks fabricated
   numbers but cannot prove a number is used with the correct meaning.
   Passing validation is **not** a factual-verification of the statement.
4. **Safety** — profitability guarantees, confidence percentages, order
   advice and code blocks fail validation unconditionally.

Failed answers are never shown as success: the client receives a 502
`AI_OUTPUT_INVALID` and (for chat) an honest `error` message is persisted.

## Security & prompt-injection model

* User text, strategy names and any other user-controlled strings are
  **untrusted data**: the system prompt confines them inside
  `<user_message>` delimiters and forbids following instructions found
  there. Nested delimiter smuggling is stripped; control characters are
  removed; input is capped at 4 000 chars per message.
* User text is **redacted** (`secrets.py`) before it is sent to the provider
  or logged: bearer tokens, `sk-…` keys, `password=…` assignments and URL
  credentials are replaced with `[REDACTED]`. (Pattern-based — users must
  still never paste credentials into the chat; the platform never asks.)
* The LLM has **no tools, no code execution and no database access**. Its
  only capability is producing text that must pass validation.
* API keys live in server-side environment variables only; no endpoint
  accepts or returns them; `GET /api/v1/ai/status` reports booleans and the
  model name only.
* Authorization is enforced before every fetch (analyses, backtests,
  conversations are owner-scoped; foreign records are 404s) and the response
  returns only the requesting owner's data.
* Per-owner sliding-window rate limit (`AI_RATE_LIMIT_PER_MINUTE`,
  in-process — a documented single-process limitation) plus bounded prompt
  and output sizes.

## Provider abstraction

`provider.py` supports any **OpenAI-compatible** chat-completions endpoint:

* configuration-only vendor choice (`AI_BASE_URL` + `AI_MODEL`);
* per-request timeout, bounded retries (429/5xx/timeouts only), quota/auth
  errors mapped to `AI_PROVIDER_QUOTA`;
* strict JSON-object responses (`response_format: json_object`, tolerant of
  code fences) — anything else is `LLMBadResponse`;
* provider outages surface as `502 AI_PROVIDER_ERROR` /
  `AI_PROVIDER_TIMEOUT`; **the deterministic platform is never affected**.

When the provider is not configured (`AI_ENABLED=false`, the default), the
AI endpoints return `503 AI_PROVIDER_NOT_CONFIGURED` with a clear message,
and the frontend shows an explicit "AI explanations are unavailable" panel
while every deterministic feature keeps working.

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/ai/status` | Honest AI-layer status (no secrets) |
| POST | `/api/v1/ai/explanations` | Explain one authorized analysis report (stateless) |
| POST | `/api/v1/ai/research/chat` | One research-chat turn (owner-scoped, persisted) |
| GET | `/api/v1/ai/research/conversations` | List the owner's conversations (paginated) |
| POST | `/api/v1/ai/research/conversations` | Create a conversation (optional artefact refs) |
| GET | `/api/v1/ai/research/conversations/{id}` | Retrieve a conversation + messages |
| DELETE | `/api/v1/ai/research/conversations/{id}` | Delete an authorized conversation |

Explanations reference analyses by the same id as
`GET /api/v1/analysis/reports/{id}`; since Phase 7 that id equals the
payload's `analysis_id` (one id everywhere).

Every answer carries: the generated explanation/answer, the validation
summary (`schema_ok`, `evidence_refs_ok`, `numeric_trace_ok`, `safety_ok`,
`checked_refs`), provider/model metadata and generation timestamps.

## Persistence (migration `f4b8e6c2a917`)

* `ai_conversations` — owner-scoped; loose (non-FK) references to one
  analysis and/or backtest, validated against the owner on every access.
* `ai_messages` — user questions, validated AI answers and honest `error`
  markers, each with the **exact minimized context** supplied to the
  provider (`context` JSONB) and the validation summary — every AI
  statement stays auditable against the evidence it was given.

The AI layer's ONLY writes are its own conversation/message rows, created by
ordinary authenticated services. Strategies, analyses, backtests and candles
are never touched.

## Frontend

* **AI explanation panel** (`src/components/ai/ai-explanation-panel.tsx`) on
  the engine-report view: violet-accented, badged "AI-generated
  interpretation", every cited item shows its deterministic evidence refs.
  Distinct states: unconfigured / loading / fail-closed error / validated
  result — the deterministic report stays authoritative and visually separate.
* **AI Research tab** (`src/components/ai/research-chat.tsx`): owner-scoped
  conversation list, visible research context (attached analysis/backtest
  with symbol, strategy version and timestamps), loading/empty/error states,
  suggestion prompts, and a constant reminder of what the assistant cannot do.
* Footer and navigation updated; no existing page was redesigned.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_ENABLED` | `false` | Master switch (fail-safe off) |
| `AI_BASE_URL` | — | OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` |
| `AI_MODEL` | — | Model name (no specific model is assumed to exist) |
| `AI_API_KEY` | — | Server-side secret; never logged or returned |
| `AI_TIMEOUT_SECONDS` | `45` | Per-request timeout |
| `AI_MAX_RETRIES` | `2` | Bounded retries for transient failures |
| `AI_RETRY_BACKOFF_SECONDS` | `1.5` | Linear backoff base |
| `AI_RATE_LIMIT_PER_MINUTE` | `10` | Per-owner request window |
| `AI_MAX_INPUT_CHARS` | `24000` | Context prompt budget |
| `AI_MAX_OUTPUT_TOKENS` | `1400` | Completion cap |
| `AI_TEMPERATURE` | `0.1` | Low-temperature by design |

Enabling with an incomplete configuration fails fast at startup
(`AI_ENABLED=true` requires base URL, model and key).

## Known limitations (honest by design)

* Validation proves grounding to the supplied evidence, **not** factual
  market truth; users must always cross-check against the deterministic
  report.
* No numerical confidence scores are produced anywhere (qualitative
  uncertainty only) — a calibrated, documented score is future work.
* The rate limiter is per-process; multi-process deployments need a shared
  store (documented, out of scope).
* MT5 data is not a broker's execution feed and OTC prices are never
  presented as verified data — AI answers repeat these limitations and the
  validation layer enforces the related honesty rules.
* Secrets redaction is pattern-based and cannot catch every conceivable
  credential format.

/**
 * Browser-level mock fixtures for the LOCAL visual smoke test only.
 * These never ship inside the app: the frontend always uses the real
 * backend. They exist so the chart/overlay rendering can be inspected in
 * an environment without a Windows MT5 terminal.
 */
const T0 = Math.floor(Date.now() / 1000 / 900) * 900 - 900 * 120;

function candles() {
  const out = [];
  let price = 1.085;
  for (let i = 0; i < 120; i++) {
    const t = T0 + i * 900;
    const open = price;
    const wave = Math.sin(i / 9) * 0.0035;
    const close = open + wave + (i % 11 === 0 ? 0.0015 : -0.0008);
    const high = Math.max(open, close) + 0.0009;
    const low = Math.min(open, close) - 0.0011;
    out.push({
      time: t,
      time_utc: new Date(t * 1000).toISOString(),
      open: +open.toFixed(5),
      high: +high.toFixed(5),
      low: +low.toFixed(5),
      close: +close.toFixed(5),
      volume: 100 + i,
      forming: i === 119,
    });
    price = close;
  }
  return out;
}

const series = (c, status = "ok") => ({
  success: true,
  data: {
    symbol: "EURUSD",
    timeframe: "M1",
    timezone: "MT5_SERVER",
    timezone_note: "Fixture",
    candles: c,
    count: c.length,
    requested_count: 200,
    closed_only: false,
    includes_forming: c.some((x) => x.forming),
    first_time: c[0]?.time ?? null,
    last_time: c[c.length - 1]?.time ?? null,
    newest_closed_time: c[c.length - 2]?.time ?? null,
    newest_closed_age_seconds: 42.5,
    data_status: status,
    data_provenance: "mt5_live",
    retrieved_at: new Date().toISOString(),
    warnings: [],
  },
  error: null,
  meta: { request_id: "fixture", timestamp: new Date().toISOString(), api_version: "v1" },
});

const envelope = (data) => ({
  success: true,
  data,
  error: null,
  meta: { request_id: "fixture", timestamp: new Date().toISOString(), api_version: "v1" },
});

const CANDLES = candles();

const REPORT = {
  analysis_id: "fixture-0001-fixture-0001",
  report_schema_version: "1.0",
  engine_version: "4.0.0-fixture",
  symbol: "EURUSD",
  mode: "forex",
  strategy: {
    id: "strat-fixture",
    name: "[Fixture] Trend pullback",
    mode: "forex",
    direction: "bullish",
    strategy_status: "active",
    version: 2,
    version_status: "published",
    immutable_definition: true,
    rule_set: {
      schema_version: "2.0",
      mode: "forex",
      timeframes: ["M1"],
      direction: "bullish",
      conditions: { logic: "AND", conditions: [], groups: [] },
      confirmation: null,
      invalidation: null,
    },
  },
  required_timeframes: ["M1"],
  evaluated_timeframes: ["M1"],
  data: {
    provider: "mt5",
    quality_status: "ok",
    closed_candles_only: true,
    forming_candle_used: false,
    timeframes: [],
    warnings: [],
    source_note: "Fixture (visual smoke test only)",
  },
  evaluated_at: new Date().toISOString(),
  regime: {
    primary_timeframe: "M1",
    primary: {
      timeframe: "M1",
      regime: "uptrend",
      volatility_state: "normal",
      directional_bias: "bullish",
      applicable_buckets: [],
      evidence: {},
    },
    by_timeframe: [],
    agreement: { status: "single_timeframe", detail: "one timeframe", biases: {} },
    applicability: { required: "any", classified: "uptrend", applicable: true, matched_buckets: [], note: "" },
    note: "",
  },
  observations: (() => {
    // Derive swing fixtures from the candle series deterministically.
    const highs = [];
    const lows = [];
    for (let i = 2; i < CANDLES.length - 2; i += 9) {
      const c = CANDLES[i];
      const isHigh = c.high >= CANDLES[i - 1].high && c.high >= CANDLES[i - 2].high && c.high > CANDLES[i + 1].high && c.high > CANDLES[i + 2].high;
      const isLow = c.low <= CANDLES[i - 1].low && c.low <= CANDLES[i - 2].low && c.low < CANDLES[i + 1].low && c.low < CANDLES[i + 2].low;
      if (isHigh) highs.push({ time: c.time, price: c.high, confirmed_at_time: CANDLES[i + 2].time });
      if (isLow) lows.push({ time: c.time, price: c.low, confirmed_at_time: CANDLES[i + 2].time });
    }
    const labels = (arr) => arr.slice(1).map((s, i) => (s.price > arr[i].price ? "HH" : "LH"));
    const labelsLow = (arr) => arr.slice(1).map((s, i) => (s.price > arr[i].price ? "HL" : "LL"));
    return {
      by_timeframe: [
        {
          timeframe: "M1",
          completeness: {
            timeframe: "M1",
            total: CANDLES.length,
            closed: CANDLES.length - 1,
            forming: 1,
            first_time: CANDLES[0].time,
            last_time: CANDLES[CANDLES.length - 1].time,
            gap_count: 0,
            largest_gap_intervals: 0,
          },
          latest_candle: null,
          swings: {
            strength: 2,
            highs: highs.slice(-5),
            lows: lows.slice(-5),
            high_structure: { kind: "high", labels: labels(highs.slice(-6)), structure_trend: "mixed", swing_count: highs.length },
            low_structure: { kind: "low", labels: labelsLow(lows.slice(-6)), structure_trend: "mixed", swing_count: lows.length },
          },
          structure_breaks: highs.length > 1 && lows.length ? [{
            time: CANDLES[CANDLES.length - 10].time,
            side: "bullish",
            level: highs[highs.length - 2].price,
            reference_time: highs[highs.length - 2].time,
            close: CANDLES[CANDLES.length - 10].close,
          }] : [],
          zones: lows.length ? [{ zone_low: Math.min(...lows.map((l) => l.price)) - 0.0005, zone_high: Math.min(...lows.map((l) => l.price)) + 0.0005, position: "above", distance: 0.001, touch_count: 3, first_touch_time: lows[0].time, last_touch_time: lows[lows.length - 1].time }] : [],
        },
      ],
      note: "Fixture observations",
    };
  })(),
  evaluation: {
    direction: "bullish",
    setup_status: "conditions_met",
    core: {
      kind: "group",
      id: "root",
      satisfied: true,
      status: "met",
      description: "AND group",
      logic: "AND",
      evidence: {},
      children: [
        { kind: "condition", id: "swing-high", satisfied: true, status: "met", description: "Confirmed swing high exists on M1", evidence: { condition_type: "swing_point", timeframe: "M1" } },
        { kind: "condition", id: "ma-hold", satisfied: false, status: "not_met", description: "EMA20 above value on M1", evidence: { condition_type: "moving_average", timeframe: "M1" } },
        { kind: "condition", id: "zone-touch", satisfied: false, status: "inconclusive", description: "Zone touch cannot be evaluated (no H4 candles)", evidence: { condition_type: "price_zone", timeframe: "H4" } },
      ],
    },
    confirmation: null,
    invalidation: null,
    conditions_summary: { met: 1, not_met: 1, inconclusive: 1, unsupported: 0 },
    warnings: [],
    summary: ["1 of 3 core conditions met."],
    evaluated_at: new Date().toISOString(),
  },
  limitations: [
    "Fixture report for local visual smoke testing only — not real analysis.",
    "No probability of profit is implied.",
  ],
};

const HANDLERS = [
  {
    pattern: "**/api/v1/market/candles*",
    body: series(CANDLES),
  },
  {
    pattern: "**/api/v1/market/symbols*",
    body: envelope({
      symbols: [
        { name: "EURUSD", description: "Euro vs US Dollar (fixture)", visible: true, selectable: true, trade_mode: 0, digits: 5, point: 0.00001, spread: 10, currency_base: "EUR", currency_profit: "USD" },
        { name: "GBPUSD", description: "Pound vs US Dollar (fixture)", visible: true, selectable: true, trade_mode: 0, trade_mode_label: "", digits: 5, point: 0.00001, spread: 12, currency_base: "GBP", currency_profit: "USD" },
      ],
      count: 2,
      search: "EUR*",
      truncated: false,
    }),
  },
  {
    pattern: "**/api/v1/strategies*",
    body: envelope({
      items: [
        { id: "strat-fixture", name: "[Fixture] Trend pullback", mode: "forex", direction: "bullish", status: "active", description: null, category_id: null, current_version: 2, published_version_number: 2, created_at: "", updated_at: "" },
      ],
      page: { page: 1, page_size: 100, total: 1, pages: 1 },
    }),
  },
  {
    pattern: "**/api/v1/strategies/strat-fixture/versions*",
    body: envelope([
      {
        id: "v2", strategy_id: "strat-fixture", version: 2, status: "published", changelog: null,
        rule_set: REPORT.strategy.rule_set, created_at: "",
      },
    ]),
  },
  {
    pattern: "**/api/v1/analysis/reports/fixture-0001-fixture-0001*",
    body: envelope(REPORT),
  },
  {
    pattern: "**/api/v1/analysis/reports*",
    body: envelope({
      items: [
        {
          id: "fixture-0001-fixture-0001", symbol: "EURUSD", mode: "forex", strategy_id: "strat-fixture",
          strategy_name: "[Fixture] Trend pullback", strategy_version: 2, setup_status: "conditions_met",
          direction: "bullish", regime: "uptrend", report_schema_version: "1.0", engine_version: "4.0.0-fixture",
          evaluated_at: new Date().toISOString(), created_at: new Date().toISOString(),
        },
      ],
      page: { page: 1, page_size: 10, total: 1, pages: 1 },
    }),
  },
];

window.__FIXTURE_HANDLERS__ = HANDLERS;
window.__FIXTURE_REPORT__ = REPORT;
// Fixtures are consumed by scripts that evaluate this file in a sandboxed
// window context; no module export is required here.

/**
 * Typed contracts mirroring the backend API (FastAPI) envelope + payloads.
 * The backend is the single source of truth; these types mirror
 * `backend/app/core/responses.py` and the domain schemas.
 */

export interface ApiMeta {
  request_id: string | null;
  timestamp: string | null;
  api_version: string;
}

export interface ApiEnvelope<T> {
  success: boolean;
  data: T | null;
  error: ApiErrorPayload | null;
  meta: ApiMeta;
}

export interface ApiErrorDetail {
  path?: string;
  code?: string;
  message?: string;
  input?: unknown;
  constraint?: unknown;
}

export interface ApiErrorPayload {
  code: string;
  message: string;
  details: ApiErrorDetail[];
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: ApiErrorDetail[];
  readonly requestId: string | null;

  constructor(params: {
    code: string;
    message: string;
    status: number;
    details?: ApiErrorDetail[];
    requestId?: string | null;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.code = params.code;
    this.status = params.status;
    this.details = params.details ?? [];
    this.requestId = params.requestId ?? null;
  }
}

// --------------------------------------------------------------------------- //
// Platform metadata
// --------------------------------------------------------------------------- //

export interface ModeProfile {
  name: "forex" | "binary_options";
  default_timeframes: string[];
}

export interface RuleFormatMeta {
  schema_version: string;
  condition_types: string[];
  operators: string[];
  price_fields: string[];
  group_logic: string[];
  directions: string[];
  limits: Record<string, number>;
  code_execution: boolean;
  note: string;
}

export interface PlatformMeta {
  app: string;
  version: string;
  modes: ModeProfile[];
  rule_format: RuleFormatMeta;
}

// --------------------------------------------------------------------------- //
// Health & market data
// --------------------------------------------------------------------------- //

export interface HealthCheck {
  status: string;
  latency_ms?: number;
  error?: string;
  engine?: string;
  migrated?: boolean;
}

export interface MarketDataCheck extends ProviderStatus {
  blocks_readiness?: boolean;
}

export interface ProviderStatus {
  provider: string;
  available: boolean;
  connected: boolean;
  detail: string;
  checked_at: string | null;
}

export interface ReadyPayload {
  status: "ready" | "not_ready";
  checks: {
    database: HealthCheck;
    market_data: MarketDataCheck;
  };
}

// --------------------------------------------------------------------------- //
// Market data (Phase 2: MetaTrader 5 integration)
// --------------------------------------------------------------------------- //

export interface MT5TerminalInfo {
  accessible: boolean;
  name: string | null;
  build: number | null;
  broker_connected: boolean | null;
  trade_allowed: boolean | null;
}

export interface MT5AccountInfo {
  accessible: boolean;
  login: number | null;
  server: string | null;
  company: string | null;
  currency: string | null;
  leverage: number | null;
  trade_allowed: boolean | null;
}

export interface MT5Status {
  provider: string;
  enabled: boolean;
  package_available: boolean;
  initialized: boolean;
  connected: boolean;
  package_version: string | null;
  terminal: MT5TerminalInfo | null;
  account: MT5AccountInfo | null;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  reconnect_attempts: number;
  detail: string;
  checked_at: string;
  configuration: Record<string, unknown>;
}

export interface MarketDataStatusPayload extends MT5Status {
  live_data_enabled: boolean;
  note: string;
}

export interface CandlePoint {
  time: number;
  time_utc: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  forming: boolean;
}

export type CandleDataStatus =
  | "ok"
  | "partial"
  | "stale"
  | "insufficient"
  | "empty";

export type DataProvenance = "mt5_live" | "postgres_cache";

export interface DataQualityWarning {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
}

export interface CandleSeries {
  symbol: string;
  timeframe: string;
  timezone: "MT5_SERVER";
  timezone_note: string;
  candles: CandlePoint[];
  count: number;
  requested_count: number;
  closed_only: boolean;
  includes_forming: boolean;
  first_time: number | null;
  last_time: number | null;
  newest_closed_time: number | null;
  newest_closed_age_seconds: number | null;
  data_status: CandleDataStatus;
  data_provenance: DataProvenance;
  retrieved_at: string;
  warnings: DataQualityWarning[];
}

export interface SymbolSummary {
  name: string;
  description: string | null;
  visible: boolean | null;
  selectable: boolean | null;
  trade_mode: number | null;
  digits: number | null;
  point: number | null;
  spread: number | null;
  currency_base: string | null;
  currency_profit: string | null;
}

export interface SymbolList {
  symbols: SymbolSummary[];
  count: number;
  search: string | null;
  truncated: boolean;
}

export interface TickSpread {
  points: number | null;
  from_bid_ask: number | null;
  floating: boolean | null;
}

export interface TickPayload {
  symbol: string;
  time: number;
  time_utc: string;
  time_msc: number | null;
  bid: number | null;
  ask: number | null;
  last: number | null;
  volume: number | null;
  spread: TickSpread | null;
  symbol_digits: number | null;
  retrieved_at: string;
}

export interface DiagnosticsProbe {
  symbol?: string;
  timeframe?: string;
  count?: number;
  data_status?: string;
  data_provenance?: string;
  first_time?: number | null;
  last_time?: number | null;
  warnings?: DataQualityWarning[];
  skipped?: boolean;
  reason?: string;
  error?: string;
  code?: string;
}

export interface MarketDiagnostics {
  status: MT5Status;
  configuration: Record<string, unknown>;
  probe: DiagnosticsProbe | null;
  capabilities: {
    timeframes: string[];
    max_candles: number;
    default_candles: number;
    cache_enabled: boolean;
    binary_options_timeframes: string[];
    order_execution: boolean;
    note: string;
  };
  notes: string[];
}

// --------------------------------------------------------------------------- //
// Strategies
// --------------------------------------------------------------------------- //

export type PlatformMode = "forex" | "binary_options";
export type StrategyDirection = "bullish" | "bearish" | "both";
export type StrategyStatus = "draft" | "active" | "archived";
export type VersionStatus = "draft" | "published" | "superseded";

export interface Category {
  id: string;
  name: string;
  mode: PlatformMode;
  description: string | null;
  parent_id: string | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
  strategy_count: number | null;
  archived_subcategories?: number | null;
  children?: Category[];
}

export interface Strategy {
  id: string;
  name: string;
  mode: PlatformMode;
  direction: StrategyDirection;
  status: StrategyStatus;
  description: string | null;
  category_id: string | null;
  current_version: number;
  published_version_number: number | null;
  created_at: string;
  updated_at: string;
}

export interface StrategyDetail extends Strategy {
  rule_set: RuleSet | null;
  summary: string[];
}

export interface StrategyVersion {
  id: string;
  strategy_id: string;
  version: number;
  status: VersionStatus;
  changelog: string | null;
  rule_set: RuleSet;
  created_at: string;
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface PaginatedStrategies {
  items: Strategy[];
  page: PageMeta;
}

// --------------------------------------------------------------------------- //
// Rule set format (versioned, whitelisted — mirrors backend rules.py)
// --------------------------------------------------------------------------- //

export interface RuleCondition {
  id: string;
  type: string;
  timeframe: string;
  lookback: number;
  optional?: boolean;
  /** Schema 2.0: per-rule-type parameters. */
  params: Record<string, unknown>;
}

export interface RuleGroup {
  logic: "AND" | "OR";
  conditions: RuleCondition[];
  groups: RuleGroup[];
}

export type MarketRegime = "any" | "trending" | "ranging" | "volatile" | "quiet";

export interface RuleSet {
  schema_version: string;
  mode: PlatformMode;
  timeframes: string[];
  direction: StrategyDirection;
  conditions: RuleGroup;
  confirmation: RuleGroup | null;
  invalidation: RuleGroup | null;
  notes?: string | null;
  symbols?: string[];
  market_regime?: MarketRegime | null;
  evaluation?: { on_close_only?: boolean };
}

export interface RuleValidationError {
  path: string;
  message: string;
  code: string;
}

export interface RuleSetValidation {
  valid: boolean;
  errors: RuleValidationError[];
  normalized: RuleSet | null;
}

export interface StrategyValidateResponse extends RuleSetValidation {
  summary: string[];
  validation_id: string | null;
}

// --------------------------------------------------------------------------- //
// Rule types (registry)
// --------------------------------------------------------------------------- //

export interface ParamFieldSchema {
  type: "string" | "number" | "integer";
  enum?: string[];
  description?: string;
  minimum?: number;
  maximum?: number;
}

export interface RuleTypeSpec {
  type: string;
  title: string;
  description: string;
  semantics: string;
  available: boolean;
  status: "operational" | "planned";
  supported_modes: PlatformMode[];
  default_lookback: number;
  data_note: string;
  required_params: string[];
  params_schema: Record<string, ParamFieldSchema>;
}

// --------------------------------------------------------------------------- //
// Analysis
// --------------------------------------------------------------------------- //

export interface CandleRow {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
}

export interface ConditionEvidence {
  condition_type?: string;
  timeframe?: string;
  field?: string;
  operator?: string;
  threshold?: number;
  lookback?: number;
  candles_evaluated?: number;
  candles_available?: number;
  current_value?: number;
  previous_value?: number;
  matched_count?: number;
  first_satisfied_time?: number | null;
  last_satisfied_time?: number | null;
  crosses_in_window?: { time: number; value: number }[];
  total_crosses_in_window?: number;
  last_cross_time?: number | null;
  criterion?: string;
  reason?: string;
  missing_timeframes?: string[];
  last_candle_time?: number | null;
  last_candle_status?: "closed" | "forming" | "no_data";
  forming_candle_used?: boolean;
  status_reason?: string;
}

export type ConditionStatus = "met" | "not_met" | "inconclusive" | "unsupported";

export interface EvaluationNode {
  kind: "condition" | "group";
  id: string;
  satisfied: boolean;
  status?: ConditionStatus;
  description: string;
  evidence: ConditionEvidence;
  logic?: "AND" | "OR";
  children?: EvaluationNode[];
}

export type SetupStatus =
  | "conditions_met"
  | "conditions_missing"
  | "invalidated"
  | "insufficient_data"
  | "inconclusive"
  | "regime_mismatch";

export interface QualityWarning {
  code: string;
  timeframe: string;
  message: string;
}

export interface EvaluationResult {
  direction: StrategyDirection;
  setup_status: SetupStatus;
  core: EvaluationNode;
  confirmation: EvaluationNode | null;
  invalidation: EvaluationNode | null;
  core_status?: ConditionStatus;
  confirmation_status?: ConditionStatus | null;
  invalidation_status?: ConditionStatus | null;
  conditions_summary?: Record<ConditionStatus, number>;
  closed_candles_only?: boolean;
  forming_candle_used?: boolean;
  warnings: QualityWarning[];
  summary: string[];
  evaluated_at: string;
}

export interface EvaluationResponse {
  source: { strategy_id?: string; version?: number; inline?: boolean };
  data_provenance: string;
  market_data_provider: ProviderStatus;
  rule_set: RuleSet;
  evaluation: EvaluationResult;
  note: string;
}

/* ------------------------------------------------------------------------ */
/* Phase 4: analysis engine (provider-backed, persisted reports)             */
/* ------------------------------------------------------------------------ */

export interface RunAnalysisRequest {
  strategy_id: string;
  version?: number;
  symbol: string;
  include_forming?: boolean;
  candle_limit?: number;
}

export interface ReportTimeframeData {
  timeframe: string;
  requested_candles: number;
  returned_candles: number;
  provider_data_status: "ok" | "partial" | "stale" | "insufficient" | "empty";
  provenance: "mt5_live" | "postgres_cache";
  provider_warnings: { code?: string; message?: string }[];
  first_time: number | null;
  last_time: number | null;
  newest_closed_time: number | null;
  stale?: boolean;
  staleness_seconds?: number | null;
  note?: string;
  used_candle_count?: number;
  used_first_time?: number | null;
  used_last_time?: number | null;
}

export interface RegimeResultView {
  timeframe: string;
  regime: string;
  volatility_state: string;
  directional_bias: string;
  applicable_buckets: string[];
  evidence: Record<string, unknown>;
}

export interface RegimeApplicability {
  required: string;
  classified: string;
  applicable: boolean;
  matched_buckets: string[];
  note: string;
}

export interface AnalysisReport {
  analysis_id: string;
  report_schema_version: string;
  engine_version: string;
  symbol: string;
  mode: PlatformMode;
  strategy: {
    id: string;
    name: string;
    mode: PlatformMode;
    direction: StrategyDirection;
    strategy_status: string;
    version: number;
    version_status: string;
    immutable_definition: boolean;
    rule_set: RuleSet;
  };
  required_timeframes: string[];
  evaluated_timeframes: string[];
  data: {
    provider: string;
    quality_status: "ok" | "partial" | "stale" | "insufficient";
    closed_candles_only: boolean;
    forming_candle_used: boolean;
    timeframes: ReportTimeframeData[];
    warnings: QualityWarning[];
    source_note: string;
  };
  evaluated_at: string;
  regime: {
    primary_timeframe: string;
    primary: RegimeResultView;
    by_timeframe: RegimeResultView[];
    agreement: { status: string; detail: string; biases: Record<string, string> };
    applicability: RegimeApplicability;
    note: string;
  };
  observations: AnalysisObservations;
  evaluation: EvaluationResult;
  limitations: string[];
}

export interface AnalysisReportSummary {
  id: string;
  symbol: string;
  mode: PlatformMode;
  strategy_id: string | null;
  strategy_name: string | null;
  strategy_version: number;
  setup_status: SetupStatus;
  direction: StrategyDirection;
  regime: string | null;
  report_schema_version: string;
  engine_version: string;
  evaluated_at: string;
  created_at: string;
}

export interface PaginatedAnalysisReports {
  items: AnalysisReportSummary[];
  page: { page: number; page_size: number; total: number; pages: number };
}

export interface FeatureCatalogEntry {
  name: string;
  summary: string;
  definition: string;
  required_history: string;
  parameters: string;
  limitations: string;
}

export interface RegimeCatalogEntry {
  name: string;
  definition: string;
  strategy_buckets?: string[];
  limitations?: string;
}

export interface AnalysisFeaturesPayload {
  features: FeatureCatalogEntry[];
  regimes: RegimeCatalogEntry[];
  volatility_states: RegimeCatalogEntry[];
  strategy_regime_buckets: Record<string, string[]>;
  note: string;
}

export interface AnalysisReadiness {
  ready: boolean;
  live_analysis_ready: boolean;
  engine_version: string;
  report_schema_version: string;
  checks: {
    database: { status: string; latency_ms?: number; error?: string };
    rule_registry: {
      available: number;
      planned: number;
      evaluators: number;
      coherent: boolean;
    };
    feature_count: number;
    regimes: string[];
    market_data_provider: ProviderStatus;
  };
  note: string;
}

// --------------------------------------------------------------------------- //
// Phase 5: report observations (structure/zone facts the engine already
// computed — the ONLY source for chart overlays; the frontend never
// re-computes market geometry).
// --------------------------------------------------------------------------- //

export interface SwingObservation {
  time: number;
  price: number;
  confirmed_at_time: number;
}

export interface SwingStructureObservation {
  kind: "high" | "low";
  labels: ("HH" | "LH" | "HL" | "LL")[];
  structure_trend: "bullish" | "bearish" | "mixed";
  swing_count: number;
}

export interface SwingsObservation {
  strength: number;
  highs: SwingObservation[];
  lows: SwingObservation[];
  high_structure: SwingStructureObservation;
  low_structure: SwingStructureObservation;
}

export interface StructureBreakObservation {
  time: number;
  side: "bullish" | "bearish";
  level: number;
  reference_time: number;
  close: number;
}

export interface ZoneObservation {
  zone_low: number;
  zone_high: number;
  position: "above" | "below" | "inside" | null;
  distance: number | null;
  touch_count: number;
  first_touch_time: number | null;
  last_touch_time: number | null;
}

export interface DataCompleteness {
  timeframe: string;
  total: number;
  closed: number;
  forming: number;
  first_time: number | null;
  last_time: number | null;
  gap_count: number;
  largest_gap_intervals: number;
}

export interface TimeframeObservations {
  timeframe: string;
  completeness: DataCompleteness;
  latest_candle: Record<string, unknown> | null;
  swings: SwingsObservation | null;
  structure_breaks: StructureBreakObservation[];
  zones?: ZoneObservation[];
}

export interface AnalysisObservations {
  by_timeframe: TimeframeObservations[];
  note: string;
}

/** FVG evidence emitted by the engine's `fvg` rule evaluator (per condition). */
export interface FvgGapEvidence {
  gap_low: number;
  gap_high: number;
  direction: "bullish" | "bearish";
  created_at_time: number;
  left_time: number;
  right_time: number;
  mitigation: "full" | "partial" | "none";
  filled: boolean;
}

/**
 * The base `AnalysisReport` type already carries the typed `observations`
 * block (Phase 5). Kept as an alias so dashboard code can express intent.
 */
export type AnalysisReportWithObservations = AnalysisReport;

export interface ConditionEvidenceWithFvg extends ConditionEvidence {
  recent_gaps?: FvgGapEvidence[];
  reference_swing_price?: number;
  reference_swing_time?: number;
  structure_shifted?: boolean;
  prior_character?: string;
  direction?: string;
}

// --------------------------------------------------------------------------- //
// Phase 6: backtesting (deterministic historical evaluation)
// --------------------------------------------------------------------------- //

export type BacktestRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type TradeOutcome =
  | "win"
  | "loss"
  | "tie"
  | "open"
  | "unresolved"
  | "ambiguous"
  | "skipped";

export interface ForexExecutionConfig {
  enabled: boolean;
  spread_pips: number;
  slippage_pips: number;
  commission_per_lot_per_side: number;
  stop_loss_pips: number | null;
  take_profit_pips: number | null;
  risk_mode: "fixed_fractional" | "fixed_lot";
  risk_pct_per_trade: number;
  fixed_lot_size: number;
  pip_value_per_lot: number;
  pip_size: number | null;
  swap_per_lot_per_night: number | null;
  account_start_balance: number;
  sl_tp_same_candle_policy: "conservative" | "optimistic" | "ambiguous";
}

export interface BinaryOptionsConfig {
  expiry_candles: number;
  payout_pct: number;
  stake: number;
  tie_policy: "refund" | "loss";
  fees_per_trade: number;
  account_start_balance: number;
}

export interface BacktestConfigPayload {
  mode: PlatformMode;
  symbol: string;
  timeframe: string;
  start: string;
  end: string;
  warmup_candles: number;
  max_candles: number;
  data_source: string;
  strategy_id: string;
  strategy_name: string;
  strategy_version: number;
  strategy_version_status: string;
  rule_set: RuleSet;
  direction: string;
  notes: string | null;
  forex_execution: ForexExecutionConfig | null;
  binary: BinaryOptionsConfig | null;
  simulation_mode: "setup_only" | "trade_simulation";
}

export interface BacktestMetricBreakdownEntry {
  key: string;
  count: number;
  net_pnl?: number | null;
  wins?: number;
  losses?: number;
  win_rate?: number | null;
}

export interface BacktestMetrics {
  note: string;
  win_rate_note: string;
  simulation_mode: "setup_only" | "trade_simulation";
  data: {
    total_candles_evaluated: number;
    setup_records: number;
    trade_records: number;
  };
  setups: {
    total_evaluated: number;
    by_status: Record<string, number>;
    conditions_summary_totals: Record<string, number>;
    first_evaluation_time: number | null;
    last_evaluation_time: number | null;
    breakdowns: {
      by_regime: BacktestMetricBreakdownEntry[];
      by_direction: BacktestMetricBreakdownEntry[];
      by_month: BacktestMetricBreakdownEntry[];
      by_session?: BacktestMetricBreakdownEntry[];
    };
  };
  trades: {
    total: number;
    by_outcome: Record<TradeOutcome, number>;
    decided_trades: number;
    win_rate: number | null;
    win_rate_denominator: string;
    net_pnl: number | null;
    gross_profit: number | null;
    gross_loss: number | null;
    avg_win: number | null;
    avg_loss: number | null;
    expectancy: number | null;
    expectancy_note: string;
    profit_factor: number | null;
    profit_factor_note: string;
    avg_return_pct_per_trade: number | null;
    avg_r_multiple: number | null;
    avg_r_note: string;
    avg_holding_candles: number | null;
    exposure_pct: number | null;
    planned_risk_reward: { stop_loss_pips: number; take_profit_pips: number; planned_rr: number } | null;
    breakdowns: {
      by_regime: BacktestMetricBreakdownEntry[];
      by_direction: BacktestMetricBreakdownEntry[];
      by_month: BacktestMetricBreakdownEntry[];
      by_session?: BacktestMetricBreakdownEntry[];
    };
  };
  equity: {
    points_count: number;
    start_balance: number | null;
    end_balance: number | null;
    max_drawdown: { max_drawdown: number; max_drawdown_pct: number; points: number } | null;
    note: string;
  };
}

export interface BacktestRun {
  id: string;
  owner_id?: string;
  mode: PlatformMode;
  symbol: string;
  timeframe: string;
  strategy_id: string | null;
  strategy_name: string | null;
  strategy_version: number;
  strategy_version_id?: string | null;
  status: BacktestRunStatus;
  start_time: string;
  end_time: string;
  simulation_mode: "setup_only" | "trade_simulation" | null;
  candles_evaluated: number;
  records_total: number;
  engine_version: string;
  backtest_schema_version: string;
  error: { code: string; message: string; details: ApiErrorDetail[] } | null;
  metrics: BacktestMetrics | null;
  data_quality: {
    pre_run_coverage?: DataCoverage;
    source?: string;
    source_note?: string;
    overall_status?: string;
    timeframes?: {
      timeframe: string;
      candles_available: number;
      expected_candles?: number | null;
      gap_count?: number;
      duplicate_count?: number;
      out_of_order_count?: number;
      forming_count?: number;
      invalid_ohlc_count?: number;
      coverage_ratio?: number | null;
      warnings: { code: string; timeframe?: string; message: string }[];
    }[];
    forming_candles_used?: boolean;
    closed_candles_only?: boolean;
    note?: string;
  } | null;
  config: BacktestConfigPayload | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface BacktestRunListItem {
  id: string;
  mode: PlatformMode;
  symbol: string;
  timeframe: string;
  strategy_id: string | null;
  strategy_name: string | null;
  strategy_version: number;
  status: BacktestRunStatus;
  simulation_mode: string | null;
  start_time: string;
  end_time: string;
  candles_evaluated: number;
  records_total: number;
  win_rate: number | null;
  net_pnl: number | null;
  conditions_met: number | null;
  error: { code: string } | null;
  created_at: string;
  completed_at: string | null;
}

export interface PaginatedBacktestRuns {
  items: BacktestRunListItem[];
  page: PageMeta;
}

export interface BacktestResultSummary {
  id: string;
  run_id: string;
  seq: number;
  kind: "setup" | "trade";
  result_time: string;
  result_time_unix: number;
  setup_status: SetupStatus | null;
  direction: string | null;
  regime: string | null;
  outcome: TradeOutcome | null;
  pnl: number | null;
  return_pct: number | null;
  equity_after: number | null;
  conditions_summary: Record<string, number> | null;
  execution_summary: Record<string, unknown> | null;
  warning_count: number;
  note: string | null;
}

export interface BacktestResultDetail extends BacktestResultSummary {
  evaluation: EvaluationResult | null;
  execution: Record<string, unknown> | null;
  warnings: QualityWarning[];
}

export interface PaginatedBacktestResults {
  items: BacktestResultSummary[];
  page: PageMeta;
}

export interface BacktestEquityPoint {
  seq: number;
  time: string;
  time_unix: number;
  equity: number;
  pnl: number | null;
  outcome: string | null;
}

export interface BacktestEquityPayload {
  run_id: string;
  simulation_mode: string | null;
  points: BacktestEquityPoint[];
  note: string;
}

export interface DataCoverage {
  primary_timeframe: string;
  candles_in_range: number;
  expected_candles: number;
  coverage_ratio: number;
  checked_at: string;
  note: string;
}

/* -------------------------------------------------------------------------- */
/* Phase 7: AI research layer                                                 */
/* -------------------------------------------------------------------------- */

export interface AIStatus {
  enabled: boolean;
  provider_configured: boolean;
  provider: string;
  model: string | null;
  timeout_seconds: number;
  max_retries: number;
  rate_limit_per_minute: number;
  max_input_chars: number;
  max_output_tokens: number;
  explanations_endpoint: string;
  chat_endpoint: string;
  note: string;
}

export interface CitedItem {
  text: string;
  refs: string[];
}

export interface AIValidationSummary {
  schema_ok: boolean;
  evidence_refs_ok: boolean;
  numeric_trace_ok: boolean;
  safety_ok: boolean;
  checks: string[];
  checked_refs: number;
}

export interface AIExplanation {
  summary: string;
  market_context: string;
  conditions_met: CitedItem[];
  conditions_not_met: CitedItem[];
  conditions_inconclusive_or_unsupported: CitedItem[];
  evidence_support: CitedItem[];
  data_quality_notes: string[];
  invalidation_notes: string[];
  uncertainty: string[];
  suggested_observations: string[];
  unavailable_information: string[];
}

export interface AIExplanationResponse {
  analysis_id: string;
  strategy: {
    id: string | null;
    name: string;
    version: number;
    version_status: string | null;
    direction: string;
  };
  mode: PlatformMode;
  symbol: string;
  evaluated_at: string | null;
  explanation: AIExplanation;
  validation: AIValidationSummary;
  provider: { name: string; model: string; elapsed_seconds: number; attempts: number };
  generated_at: string;
  ai_layer_version: string;
  note: string;
}

export interface AIConversationSummary {
  id: string;
  title: string;
  analysis_id: string | null;
  backtest_id: string | null;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AIMessageView {
  id: string;
  conversation_id: string;
  seq: number;
  role: 'user' | 'assistant' | 'system' | 'error';
  content: string;
  validation: AIValidationSummary | null;
  provider: string | null;
  model: string | null;
  refs: { artefacts?: { kind: string; id: string }[] } | null;
  created_at: string;
}

export interface AIChatResponse {
  conversation: AIConversationSummary;
  messages: AIMessageView[];
  refs: { kind: string; id: string }[];
  ai_layer_version: string;
  note: string;
}

export interface AIConversationDetail {
  conversation: AIConversationSummary;
  messages: AIMessageView[];
}

export interface PaginatedAIConversations {
  items: AIConversationSummary[];
  page: PageMeta;
}

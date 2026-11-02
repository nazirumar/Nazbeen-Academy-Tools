"""Analysis domain: explainable rule evaluation over user-supplied candles.

Phase 1 evaluates rules against candle data supplied IN the request (manual
paste / CSV import). Live MetaTrader 5 data arrives in Phase 2; the evaluator
is provider-agnostic by design.
"""

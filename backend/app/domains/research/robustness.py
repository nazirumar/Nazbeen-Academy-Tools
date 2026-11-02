"""Robustness scenarios (Phase 8) — built on the SAME validated
simulation services (no perturbed candles, no second engine).

Scenario catalogue:

* ``cost_variant``        — re-runs the configured evaluation with EXPLICIT
                            execution-cost overrides (spread/slippage/
                            commission/swap for Forex; payout/fees/tie
                            policy for Binary Options). Baseline deltas
                            show how sensitive results are to friction.
* ``param_neighbor``      — re-runs the SELECTION window for nearby
                            permitted parameter values (one step per
                            dimension) to expose cliff-like optima.
* ``bootstrap``           — OPTIONAL seeded i.i.d. resampling of the
                            recorded trade P&L list (documented limits).
* ``selection_stability`` — walk-forward only: how consistently the
                            optimizer picked the same parameters across
                            training windows.

Every scenario documents what it changes and what it cannot establish.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db.models import ResearchCandidate, ResearchWindow
from app.domains.research.grid import generate_windows, neighbour_patches
from app.domains.research.objectives import (
    aggregate_oos,
    bootstrap_stats,
    rank_scored,
    select_candidate,
)
from app.domains.research.params import apply_patch, patch_key


_COST_NOTE = (
    "Only the listed execution assumptions change; strategy logic, data, "
    "symbols and periods are identical to the baseline. This shows "
    "sensitivity to friction — it does NOT prove robustness and a "
    "surviving scenario is not evidence of profitability."
)
_NEIGHBOUR_NOTE = (
    "Nearby permitted parameter values re-evaluated on the selection "
    "window. A sharp performance collapse between neighbours indicates "
    "an unstable optimum (possible overfit); smooth behaviour does NOT "
    "prove out-of-sample robustness."
)
_BOOTSTRAP_NOTE = (
    "Seeded i.i.d. resampling of the recorded trade P&L list. It shows "
    "how much the aggregate depends on which trades happened and their "
    "order — it never generates prices, preserves chronological "
    "clustering, or constitutes a significance test."
)
_STABILITY_NOTE = (
    "Walk-forward selection stability: if each training window selects "
    "different parameters, the 'optimum' is not stable across time. "
    "Stable selections do not prove future profitability."
)


def _baseline_of(executor: Any, mode: str) -> ResearchCandidate | None:
    return executor._baseline_candidate(mode)


def _current_value(rule_set_dict: dict[str, Any], spec: dict[str, Any]) -> Any:
    """The snapshot's current value of a parameter field (for labels and
    original-baseline neighbour construction)."""

    from app.domains.research.params import conditions_index

    cond = conditions_index(rule_set_dict).get(spec["condition_id"])
    if cond is None:
        return None
    field = spec["field"]
    if field == "lookback":
        return cond.get("lookback")
    if field.startswith("params."):
        return (cond.get("params") or {}).get(field[len("params."):])
    return None


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _selection_window(cfg: Any, mode: str, start_epoch: int, end_epoch: int, interval: int) -> tuple[int, int, int | None]:
    """(walk_start, walk_end, oos_start|None) for selection-window work."""

    if mode == "holdout":
        is_end = _epoch(str(cfg.in_sample_end))
        return start_epoch, is_end, is_end + interval
    return start_epoch, end_epoch, None


def build_scenarios(
    cfg: Any,
    *,
    candidate_rows: list[ResearchCandidate],
    specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Plan the scenario list (bounded, deterministic order)."""

    mode = cfg.validation_mode
    scenarios: list[dict[str, Any]] = []

    # ---- B: execution-cost variants ---------------------------------------
    forex_variants = cfg.forex_cost_variants or []
    binary_variants = cfg.binary_cost_variants or []
    for i, variant in enumerate(forex_variants + binary_variants, start=1):
        is_forex = i <= len(forex_variants)
        changes = {k: v for k, v in variant.items() if v is not None}
        if not changes:
            continue
        scenarios.append(
            {
                "category": "cost_variant",
                "label": (
                    "Cost variant "
                    + f"{i}: " + ", ".join(f"{k}={v}" for k, v in changes.items())
                ),
                "assumptions": {
                    "kind": "execution_cost",
                    "market": "forex" if is_forex else "binary_options",
                    "overrides": changes,
                    "fixed": (
                        "SL/TP, position sizing, pip value and all strategy "
                        "logic stay identical to the baseline."
                        if is_forex
                        else "Expiry, stake and all strategy logic stay "
                        "identical to the baseline."
                    ),
                },
                "note": _COST_NOTE,
                "exec": {
                    "kind": "cost",
                    "forex_override": None if is_forex else None,
                    "variant": variant,
                },
            }
        )

    # ---- A: parameter neighbours (not in walk-forward mode; WF gets the
    #      selection-stability view instead) ---------------------------------
    if cfg.param_sensitivity and mode in ("in_sample_only", "holdout"):
        baseline = _baseline_of_exec(cfg, candidate_rows)
        if baseline is not None:
            patch = list(baseline.params.get("patch") or [])
            neighbour_specs: list[tuple[dict[str, Any], Any]] = []
            if patch:
                for nb in neighbour_patches(patch, specs, max_neighbours=16):
                    changed = [
                        f"{e['condition_id']}.{e['field']}={e['value']!r}"
                        for e in nb
                        if e not in patch
                    ]
                    neighbour_specs.append(
                        (nb, "Neighbour: " + ", ".join(changed))
                    )
            else:
                # Baseline is the ORIGINAL parameterization: build
                # one-dimension variants from every allowed value that
                # differs from the snapshot's current value.
                for spec in specs:
                    current = _current_value(cfg.rule_set, spec)
                    for value in spec["values"]:
                        if value == current:
                            continue
                        single = [
                            {
                                "condition_id": spec["condition_id"],
                                "field": spec["field"],
                                "value": value,
                            }
                        ]
                        if len(neighbour_specs) >= 16:
                            break
                        neighbour_specs.append(
                            (
                                single,
                                f"From original: {spec['condition_id']}."
                                f"{spec['field']}={value!r}",
                            )
                        )
            for nb, label in neighbour_specs:
                scenarios.append(
                    {
                        "category": "param_neighbor",
                        "label": label,
                        "assumptions": {
                            "kind": "param_neighbour",
                            "baseline_candidate_index": baseline.candidate_index,
                            "neighbour_patch": nb,
                            "selection_window_only": True,
                        },
                        "note": _NEIGHBOUR_NOTE,
                        "exec": {"kind": "neighbour", "patch": nb},
                    }
                )

    # ---- F: optional seeded bootstrap ---------------------------------------
    samples = int(cfg.limits.get("bootstrap_samples", 0))
    if cfg.run_bootstrap and samples > 0:
        scenarios.append(
            {
                "category": "bootstrap",
                "label": f"Bootstrap stress test ({samples} resamples, seed {cfg.seed})",
                "assumptions": {
                    "kind": "iid_bootstrap",
                    "samples": samples,
                    "seed": cfg.seed,
                    "input": "recorded simulated trade P&L (win/loss) of the "
                    "baseline candidate; no prices are generated",
                },
                "note": _BOOTSTRAP_NOTE,
                "exec": {"kind": "bootstrap"},
            }
        )

    # ---- WF selection stability ---------------------------------------------
    if mode == "walk_forward":
        scenarios.append(
            {
                "category": "selection_stability",
                "label": "Walk-forward selection stability",
                "assumptions": {
                    "kind": "selection_stability",
                    "source": "per-window selected parameters of this run",
                },
                "note": _STABILITY_NOTE,
                "exec": {"kind": "stability"},
            }
        )
    return scenarios


def _baseline_of_exec(
    cfg: Any, candidate_rows: list[ResearchCandidate]
) -> ResearchCandidate | None:
    ranked = [r for r in candidate_rows if r.rank is not None]
    if ranked:
        return min(ranked, key=lambda r: r.rank)
    if candidate_rows:
        return candidate_rows[0]
    return None


async def execute_scenario(
    executor: Any,
    cfg: Any,
    scenario: dict[str, Any],
    start_epoch: int,
    end_epoch: int,
    interval: int,
) -> dict[str, Any]:
    """Execute one planned scenario; returns {metrics, delta, status, error}."""

    kind = scenario["exec"]["kind"]
    mode = cfg.validation_mode
    if kind == "cost":
        return await _run_cost(executor, cfg, scenario, start_epoch, end_epoch, interval, mode)
    if kind == "neighbour":
        return await _run_neighbour(executor, cfg, scenario, start_epoch, end_epoch, mode)
    if kind == "bootstrap":
        return await _run_bootstrap(executor, cfg, scenario, start_epoch, end_epoch, interval, mode)
    if kind == "stability":
        return await _run_stability(executor, cfg)
    return {
        "metrics": None,
        "delta": None,
        "status": "failed",
        "error": {"code": "UNKNOWN_SCENARIO", "message": f"Unknown scenario kind '{kind}'."},
    }


# ---- cost variants -----------------------------------------------------------


async def _run_cost(
    executor: Any,
    cfg: Any,
    scenario: dict[str, Any],
    start_epoch: int,
    end_epoch: int,
    interval: int,
    mode: str,
) -> dict[str, Any]:
    variant = scenario["exec"]["variant"]
    forex_override = variant if cfg.mode == "forex" else None
    binary_override = variant if cfg.mode == "binary_options" else None
    baseline = _baseline_of(executor, mode)
    if baseline is None:
        return _failed("NO_BASELINE", "No baseline candidate exists for this run.")

    if mode == "walk_forward":
        aggregate = await _wf_aggregate_with_overrides(
            executor, cfg, forex_override, binary_override
        )
        baseline_block = await _wf_baseline_aggregate(executor)
        return {
            "metrics": {"out_of_sample_aggregate": aggregate},
            "delta": _delta_block(baseline_block, aggregate),
            "status": "completed",
        }

    walk_start, walk_end, oos_start = _selection_window(
        cfg, mode, start_epoch, end_epoch, interval
    )
    is_info = executor._eval_detached(
        cfg,
        baseline.rule_set,
        start_epoch=walk_start,
        end_epoch=walk_end,
        forex_override=forex_override,
        binary_override=binary_override,
    )
    metrics: dict[str, Any] = {"selection_window": is_info["digest"]}
    baseline_digest = baseline.in_sample or {}
    if oos_start is not None:
        oos_info = executor._eval_detached(
            cfg,
            baseline.rule_set,
            start_epoch=oos_start,
            end_epoch=end_epoch,
            forex_override=forex_override,
            binary_override=binary_override,
        )
        metrics["out_of_sample"] = oos_info["digest"]
        baseline_digest = baseline.out_of_sample or {}
    return {
        "metrics": metrics,
        "delta": _delta_block(baseline_digest, metrics.get("out_of_sample") or is_info["digest"]),
        "status": "completed",
    }


# ---- parameter neighbours ----------------------------------------------------


async def _run_neighbour(
    executor: Any,
    cfg: Any,
    scenario: dict[str, Any],
    start_epoch: int,
    end_epoch: int,
    mode: str,
) -> dict[str, Any]:
    baseline = _baseline_of(executor, mode)
    if baseline is None:
        return _failed("NO_BASELINE", "No baseline candidate exists for this run.")
    nb_patch = scenario["exec"]["patch"]
    nb_key = patch_key(nb_patch)
    rule_set_dict = None
    for row in executor.candidate_rows:
        if row.params.get("patch") and patch_key(row.params["patch"]) == nb_key:
            rule_set_dict = row.rule_set
            break
    if rule_set_dict is None:
        rule_set_dict = apply_patch(cfg.rule_set, nb_patch)

    walk_start, walk_end, _oos = _selection_window(
        cfg, mode, start_epoch, end_epoch, executor._interval
    )
    info = executor._eval_detached(
        cfg, rule_set_dict, start_epoch=walk_start, end_epoch=walk_end
    )
    baseline_digest = baseline.in_sample or {}
    return {
        "metrics": {"selection_window": info["digest"]},
        "delta": _delta_block(baseline_digest, info["digest"]),
        "status": "completed",
    }


# ---- bootstrap -----------------------------------------------------------------


def _decided_pnls(output: Any) -> list[float]:
    return [
        float(r["pnl"])
        for r in getattr(output, "records", [])
        if r.get("kind") == "trade"
        and r.get("outcome") in ("win", "loss")
        and r.get("pnl") is not None
    ]


async def _run_bootstrap(
    executor: Any,
    cfg: Any,
    scenario: dict[str, Any],
    start_epoch: int,
    end_epoch: int,
    interval: int,
    mode: str,
) -> dict[str, Any]:
    samples = int(cfg.limits.get("bootstrap_samples", 0))
    seed = int(cfg.seed)
    baseline = _baseline_of(executor, mode)
    if baseline is None:
        return _failed("NO_BASELINE", "No baseline candidate exists for this run.")
    metrics: dict[str, Any] = {}
    if mode == "walk_forward":
        pnls = await _wf_oos_pnls(executor, cfg)
        metrics["out_of_sample"] = bootstrap_stats(pnls, samples=samples, seed=seed)
    else:
        walk_start, walk_end, oos_start = _selection_window(
            cfg, mode, start_epoch, end_epoch, executor._interval
        )
        is_out = executor._eval_detached(
            cfg, baseline.rule_set, start_epoch=walk_start, end_epoch=walk_end
        )
        metrics["selection_window"] = bootstrap_stats(
            _decided_pnls(is_out["output"]), samples=samples, seed=seed
        )
        if oos_start is not None:
            oos_out = executor._eval_detached(
                cfg, baseline.rule_set, start_epoch=oos_start, end_epoch=end_epoch
            )
            metrics["out_of_sample"] = bootstrap_stats(
                _decided_pnls(oos_out["output"]), samples=samples, seed=seed
            )
    return {"metrics": metrics, "delta": None, "status": "completed"}


# ---- walk-forward selection stability -------------------------------------------


async def _run_stability(executor: Any, cfg: Any) -> dict[str, Any]:
    windows = await _windows(executor)
    completed = [
        w
        for w in windows
        if w.status == "completed" and w.selected_candidate_index is not None
    ]
    frequency: dict[str, int] = {}
    for w in completed:
        label = (w.selected_params or {}).get("label") or "unknown"
        frequency[label] = frequency.get(label, 0) + 1
    unique = len(frequency)
    most = max(frequency.items(), key=lambda kv: kv[1]) if frequency else None
    return {
        "metrics": {
            "windows_with_selection": len(completed),
            "windows_total": len(windows),
            "unique_selections": unique,
            "most_selected": {"label": most[0], "count": most[1]} if most else None,
            "frequency": frequency,
            "stability_ratio": (
                round(most[1] / len(completed), 4)
                if most and completed
                else None
            ),
        },
        "delta": None,
        "status": "completed",
    }


# ---- walk-forward re-runs (robustness only; nothing persisted) ------------------


async def _windows(executor: Any) -> list[ResearchWindow]:
    result = await executor.session.scalars(
        select(ResearchWindow)
        .where(ResearchWindow.run_id == executor.run.id)
        .order_by(ResearchWindow.window_index.asc())
    )
    return list(result.all())


async def _wf_aggregate_with_overrides(
    executor: Any,
    cfg: Any,
    forex_override: dict[str, Any] | None,
    binary_override: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Re-run the FULL walk-forward procedure with variant costs (no writes)."""

    mode_cfg = cfg
    start_epoch = executor._start_epoch
    end_epoch = executor._end_epoch
    interval = executor._interval
    wf = dict(cfg.walk_forward or {})
    windows, _warnings = generate_windows(
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        interval_seconds=interval,
        train_candles=wf["train_candles"],
        test_candles=wf["test_candles"],
        step_candles=wf["step_candles"],
        window_mode=wf["window_mode"],
        max_windows=int(cfg.limits.get("max_windows", 40)),
    )
    digests: list[dict[str, Any] | None] = []
    for spec in windows:
        train_count = executor._primary_candle_count(spec.train_start, spec.train_end)
        test_count = executor._primary_candle_count(spec.test_start, spec.test_end)
        if train_count < wf["train_candles"] or test_count == 0:
            digests.append(None)
            continue
        scored = []
        train_info: dict[int, dict[str, Any]] = {}
        for row in executor.candidate_rows:
            info = executor._eval_detached(
                mode_cfg,
                row.rule_set,
                start_epoch=spec.train_start,
                end_epoch=spec.train_end,
            )
            train_info[row.candidate_index] = info
            scored.append(
                {
                    "candidate_index": row.candidate_index,
                    "score": info["score"],
                    "constraint_violations": info["violations"],
                }
            )
        from app.domains.research.objectives import rank_scored, select_candidate as _sel

        rank_scored(scored, cfg.objective_direction)
        selected = _sel(scored)
        if selected is None:
            digests.append(None)
            continue
        selected_row = executor.candidate_rows[selected["candidate_index"]]
        test_info = executor._eval_detached(
            mode_cfg,
            selected_row.rule_set,
            start_epoch=spec.test_start,
            end_epoch=spec.test_end,
            forex_override=forex_override,
            binary_override=binary_override,
        )
        digests.append(test_info["digest"])
    return aggregate_oos(digests)


async def _wf_baseline_aggregate(executor: Any) -> dict[str, Any] | None:
    windows = await _windows(executor)
    digests = [
        w.test_metrics
        for w in windows
        if w.status == "completed" and w.selected_candidate_index is not None
    ]
    return aggregate_oos(digests) if digests else None


async def _wf_oos_pnls(executor: Any, cfg: Any) -> list[float]:
    """Decided-trade P&Ls of the run's OWN out-of-sample test windows."""

    windows = await _windows(executor)
    pnls: list[float] = []
    for w in windows:
        if w.status != "completed" or w.selected_candidate_index is None:
            continue
        # Re-walk the frozen winner on its test window to recover records.
        row_index = w.selected_candidate_index
        row = executor.candidate_rows[row_index]
        info = executor._eval_detached(
            cfg,
            row.rule_set,
            start_epoch=int(w.test_start.timestamp()),
            end_epoch=int(w.test_end.timestamp()),
        )
        pnls.extend(_decided_pnls(info["output"]))
    return pnls


# ---- deltas & derived stability views -------------------------------------------


_DELTA_KEYS = ("net_pnl", "expectancy", "win_rate", "profit_factor", "decided_trades")


def _digest_trades(digest: dict[str, Any] | None) -> dict[str, Any]:
    return (digest or {}).get("trades") or {}


def _delta_block(baseline_digest: dict[str, Any] | None, scenario_digest: dict[str, Any] | None) -> dict[str, Any] | None:
    base = _digest_trades(baseline_digest)
    scen = _digest_trades(scenario_digest)
    if not base and not scen:
        return None
    delta: dict[str, Any] = {}
    for key in _DELTA_KEYS:
        b = base.get(key)
        s = scen.get(key)
        if b is None or s is None:
            delta[key] = {"baseline": b, "scenario": s, "change": None}
        elif key in ("net_pnl", "expectancy"):
            delta[key] = {
                "baseline": b,
                "scenario": s,
                "change": round(float(s) - float(b), 8),
            }
        elif key in ("win_rate",):
            delta[key] = {
                "baseline": b,
                "scenario": s,
                "change_pp": round((float(s) - float(b)) * 100.0, 4),
            }
        else:
            delta[key] = {
                "baseline": b,
                "scenario": s,
                "change": round(float(s) - float(b), 8),
            }
    return delta


def _failed(code: str, message: str) -> dict[str, Any]:
    return {
        "metrics": None,
        "delta": None,
        "status": "failed",
        "error": {"code": code, "message": message},
    }


def stability_views(cfg: Any, executor: Any, mode: str) -> dict[str, Any]:
    """Derived C/D/E views computed from ALREADY-persisted digests."""

    baseline = _baseline_of_exec(cfg, executor.candidate_rows)
    views: dict[str, Any] = {}

    # C: time-period stability (from monthly breakdowns / per-window OOS).
    time_view: dict[str, Any] = {"note": None}
    if baseline is not None and mode != "walk_forward":
        in_digest = baseline.in_sample or {}
        out_digest = baseline.out_of_sample
        time_view = {
            "selection_window_by_month": (_digest_trades(in_digest).get("breakdowns") or {}).get("by_month"),
            "out_of_sample_by_month": (
                (_digest_trades(out_digest).get("breakdowns") or {}).get("by_month")
                if out_digest
                else None
            ),
            "note": (
                "Monthly breakdowns of the baseline candidate. Compare "
                "in-sample vs out-of-sample months; a strategy that only "
                "works in some months is period-dependent."
            ),
        }
    elif mode == "walk_forward":
        time_view = {
            "note": (
                "Chronological out-of-sample results per walk-forward "
                "window (see the walk-forward view for full details)."
            ),
            "see": "/walk-forward endpoint (per-window test metrics)",
        }
    views["time_periods"] = time_view

    # D: market-regime stability (only from the engine's own classifier).
    regime_view: dict[str, Any] = {"note": None}
    if baseline is not None:
        in_digest = baseline.in_sample or {}
        out_digest = baseline.out_of_sample
        regime_view = {
            "selection_window_by_regime": (_digest_trades(in_digest).get("breakdowns") or {}).get("by_regime"),
            "out_of_sample_by_regime": (
                (_digest_trades(out_digest).get("breakdowns") or {}).get("by_regime")
                if out_digest
                else None
            ),
            "note": (
                "Regime labels come from the platform's deterministic "
                "classifier over the same stored candles. Regime buckets "
                "with very few trades cannot support conclusions."
            ),
        }
    views["market_regimes"] = regime_view

    # E: data-quality sensitivity.
    dq = executor.data_quality or {}
    warning_codes: list[str] = []
    for tf in dq.get("timeframes", []) or []:
        for w in tf.get("warnings", []) or []:
            code = w.get("code")
            if code and code not in warning_codes:
                warning_codes.append(code)
    views["data_quality"] = {
        "overall_status": dq.get("overall_status"),
        "warning_codes": warning_codes,
        "note": (
            "Results from periods containing data gaps inherit that "
            "uncertainty; the platform never fills gaps. Treat metrics "
            "from low-coverage periods with suspicion."
            if warning_codes
            else "No data-quality warnings were raised for the loaded "
            "history used by this run."
        ),
    }
    return views


__all__ = ["build_scenarios", "execute_scenario", "stability_views"]

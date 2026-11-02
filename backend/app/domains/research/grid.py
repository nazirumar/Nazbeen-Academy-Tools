"""Bounded search-space enumeration + walk-forward window generation.

* Candidates are the deterministic cartesian product of the explicitly
  selected parameter values (itertools.product order: last parameter
  varies fastest). Candidate 0 — the ORIGINAL, unmodified
  parameterization — is prepended by the runner, not here.
* Walk-forward windows are generated chronologically from the run's
  window: each window is ``[train_start, train_end)`` training followed
  by ``[train_end, train_end + test_len)`` testing. Successive windows
  advance by ``step_candles`` (>= test_candles is enforced at the schema
  level), so test windows never overlap and out-of-sample observations
  are never double-counted.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationAppError
from app.domains.research.params import patch_key


@dataclass(frozen=True)
class WindowSpec:
    """One chronological walk-forward window (epoch seconds, UTC)."""

    index: int
    train_start: int
    train_end: int  # == test_start: contiguous, no gap, no overlap
    test_start: int
    test_end: int

    def to_dict(self) -> dict[str, int]:
        return {
            "index": self.index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
        }


def enumerate_patches(specs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Deterministic cartesian product of the selected parameter values.

    ``specs`` are the validated parameter definitions (each with
    ``condition_id``, ``field`` and ``values``). Returns patches in
    canonical product order (excluding the original parameterization —
    the runner prepends it as candidate 0).
    """

    if not specs:
        return []
    value_lists = [list(spec["values"]) for spec in specs]
    patches: list[list[dict[str, Any]]] = []
    for combo in itertools.product(*value_lists):
        patches.append(
            [
                {
                    "condition_id": spec["condition_id"],
                    "field": spec["field"],
                    "value": value,
                }
                for spec, value in zip(specs, combo)
            ]
        )
    return patches


def combination_count(specs: list[dict[str, Any]]) -> int:
    count = 1
    for spec in specs:
        count *= len(spec["values"])
    return count


def generate_windows(
    *,
    start_epoch: int,
    end_epoch: int,
    interval_seconds: int,
    train_candles: int,
    test_candles: int,
    step_candles: int,
    window_mode: str,
    max_windows: int,
) -> tuple[list[WindowSpec], list[str]]:
    """Chronological, non-overlapping walk-forward windows.

    Returns the windows plus warnings (e.g. when the configured history
    allows fewer windows than the platform cap). Raises
    ``INSUFFICIENT_HISTORY_FOR_WINDOWS`` when not even the FIRST window
    fits into the configured period.
    """

    if step_candles < test_candles:
        raise ValidationAppError(
            "step_candles must be >= test_candles so out-of-sample test "
            "windows never overlap.",
            code="OVERLAPPING_TEST_WINDOWS",
        )
    train_len = train_candles * interval_seconds
    test_len = test_candles * interval_seconds
    step_len = step_candles * interval_seconds
    span = end_epoch - start_epoch
    if span < train_len + test_len:
        raise ValidationAppError(
            f"The configured period ({span}s) cannot hold even one "
            f"walk-forward window ({train_len}s training + {test_len}s "
            "testing). Extend the date range or shrink the window sizes.",
            code="INSUFFICIENT_HISTORY_FOR_WINDOWS",
        )

    windows: list[WindowSpec] = []
    warnings: list[str] = []
    offset = 0
    while True:
        train_start = start_epoch if window_mode == "expanding" else start_epoch + offset
        train_end = start_epoch + offset + train_len
        test_start = train_end
        test_end = test_start + test_len
        if test_end > end_epoch:
            break
        windows.append(
            WindowSpec(
                index=len(windows),
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        offset += step_len
        if len(windows) >= max_windows:
            # Would at least one more window fit? Then tell the user the
            # cap truncated their configuration.
            more_train_end = start_epoch + offset + train_len
            if more_train_end + test_len <= end_epoch:
                warnings.append(
                    f"WINDOW_CAP_REACHED: the run evaluates the first "
                    f"{max_windows} chronological windows; later windows "
                    "were truncated by the platform limit."
                )
            break
    if not windows:  # defensive — guarded by the span check above
        raise ValidationAppError(
            "No walk-forward window fits into the configured period.",
            code="INSUFFICIENT_HISTORY_FOR_WINDOWS",
        )
    # Sanity: strictly chronological, contiguous train/test, no test overlap.
    for i, w in enumerate(windows):
        if w.train_end != w.test_start or w.test_start >= w.test_end:
            raise ValidationAppError(
                "Internal error: generated an invalid walk-forward window.",
                code="WINDOW_GENERATION_BUG",
            )
        if i > 0 and w.test_start < windows[i - 1].test_end:
            raise ValidationAppError(
                "Internal error: generated overlapping test windows.",
                code="WINDOW_GENERATION_BUG",
            )
    return windows, warnings


def neighbour_patches(
    selected_patch: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    max_neighbours: int,
) -> list[dict[str, Any]]:
    """Nearby permitted parameterizations of a selected candidate.

    For every parameter dimension, take the allowed value immediately
    BELOW and immediately ABOVE the selected value (canonical value-list
    order). Each neighbour differs from the selected candidate in exactly
    ONE dimension — the standard one-step sensitivity view.
    """

    spec_by_key = {
        (spec["condition_id"], spec["field"]): spec for spec in specs
    }
    neighbours: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in selected_patch:
        spec = spec_by_key.get((entry["condition_id"], entry["field"]))
        if spec is None:
            continue
        values = list(spec["values"])
        try:
            pos = values.index(entry["value"])
        except ValueError:
            continue
        for other in (pos - 1, pos + 1):
            if 0 <= other < len(values):
                # Same patch but with ONLY this dimension moved to the
                # neighbouring allowed value.
                patch = [
                    dict(x)
                    for x in selected_patch
                    if not (
                        x["condition_id"] == entry["condition_id"]
                        and x["field"] == entry["field"]
                    )
                ]
                patch.append(
                    {
                        "condition_id": entry["condition_id"],
                        "field": entry["field"],
                        "value": values[other],
                    }
                )
                key = patch_key(patch)
                if key not in seen:
                    seen.add(key)
                    neighbours.append(patch)
                if len(neighbours) >= max_neighbours:
                    return neighbours
    return neighbours


__all__ = [
    "WindowSpec",
    "combination_count",
    "enumerate_patches",
    "generate_windows",
    "neighbour_patches",
]

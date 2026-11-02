"""In-process research runner (asyncio) — Phase 8.

Mirrors the Phase 6 ``BacktestRunner`` architecture on purpose: a
single-process FastAPI app, runs executed as ``asyncio.Task`` objects
guarded by a semaphore, COOPERATIVE cancellation checked at every
engine step, one private DB session per execution, and honest startup
recovery (interrupted runs are marked failed, never faked completed).
No external task queue is introduced.

Leakage control (enforced here, tested in the suite):

* Candles are loaded ONCE for the whole configured range (plus warmup)
  and every candidate/window reuses the exact same series — candidates
  can never see different data.
* Each walk is executed against a series CLIPPED at the walk's phase
  end: candles closing after the phase end are removed, so neither the
  evaluator nor the exit resolver can touch future data. Trades that
  cannot settle inside their phase become 'open'/'unresolved' and are
  excluded from decided-trade statistics (documented purge policy).
* Walk-forward candidate selection consumes ONLY the training-window
  digests; the test window is walked once with the FROZEN winner.
* The out-of-sample period of a holdout run is walked exactly once,
  for the candidate selected on in-sample data.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    ResearchCandidate,
    ResearchRun,
    ResearchScenario,
    ResearchWindow,
)
from app.db.session import get_session_factory
from app.domains.backtest.data import load_history
from app.domains.backtest.engine import (
    CancelledError,
    EngineOutput,
    _TimeframeSeries,
    run_walk,
)
from app.domains.backtest.schemas import (
    BacktestConfigPayload,
)
from app.domains.market_data.timeframes import timeframe_seconds
from app.domains.research import RESEARCH_RUNNER_VERSION
from app.domains.research import params as params_mod
from app.domains.research import robustness as robustness_mod
from app.domains.research.grid import enumerate_patches, generate_windows
from app.domains.research.objectives import (
    aggregate_oos,
    evaluate_constraints,
    metrics_digest,
    metric_value,
    rank_scored,
    select_candidate,
)
from app.domains.research.schemas import ResearchConfigPayload
from app.domains.strategies.rules import validate_rule_set

logger = get_logger(__name__)


class ResearchBudgetError(Exception):
    """Internal guard: the run reached its bounded walk budget."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "RESEARCH_WALK_BUDGET_EXCEEDED"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def clip_series(series: _TimeframeSeries, until_epoch: int) -> _TimeframeSeries:
    """Series restricted to candles that CLOSE at or before ``until_epoch``.

    This is the purge boundary: an exit resolver working on this series
    physically cannot consult data beyond the phase end.
    """

    rows = [
        r
        for r in series.candles
        if int(r["time"]) + series.interval <= until_epoch
    ]
    return _TimeframeSeries(series.timeframe, rows)


class ResearchRunner:
    """Owns research execution tasks + cancel events (in-process)."""

    def __init__(self, max_concurrent: int = 1) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._cancel_events: dict[uuid.UUID, asyncio.Event] = {}

    # ---- submission / cancellation ----------------------------------------

    def submit(self, run_id: uuid.UUID) -> None:
        if run_id in self._tasks:
            return
        event = asyncio.Event()
        self._cancel_events[run_id] = event
        task = asyncio.create_task(self._execute(run_id, event))

        def _done(t: asyncio.Task) -> None:
            self._cleanup(run_id)
            # A task that dies OUTSIDE its internal try (e.g. loop-binding
            # or session-factory errors) would leave the row queued forever
            # — log it loudly; startup recovery marks such rows failed.
            if not t.cancelled() and t.exception() is not None:
                logger.error(
                    "research_task_crashed",
                    extra={
                        "run_id": str(run_id),
                        "error": f"{type(t.exception()).__name__}: {t.exception()}",
                    },
                )

        task.add_done_callback(_done)

    def cancel(self, run_id: uuid.UUID) -> bool:
        event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def is_tracked(self, run_id: uuid.UUID) -> bool:
        return run_id in self._tasks

    def _cleanup(self, run_id: uuid.UUID) -> None:
        self._tasks.pop(run_id, None)
        self._cancel_events.pop(run_id, None)

    # ---- execution ---------------------------------------------------------

    async def _execute(self, run_id: uuid.UUID, cancel_event: asyncio.Event) -> None:
        from app.core.config import get_settings
        from app.domains.research import service as research_service

        settings = get_settings()
        session_factory = get_session_factory()
        async with self._semaphore:
            async with session_factory() as session:
                try:
                    run = await research_service.mark_running(session, run_id)
                    await session.commit()
                    executor = _RunExecutor(session, settings, run, cancel_event)
                    await executor.execute()
                    await session.commit()
                    logger.info(
                        "research_completed",
                        extra={"run_id": str(run_id), "walks": executor.walks_executed},
                    )
                except CancelledError:
                    await session.rollback()
                    await research_service.mark_cancelled(session, run_id)
                    await session.commit()
                    logger.info("research_cancelled", extra={"run_id": str(run_id)})
                except asyncio.CancelledError:
                    await session.rollback()
                    await research_service.mark_failed(
                        session,
                        run_id,
                        {
                            "code": "RUN_CANCELLED_PROCESS",
                            "message": "The run was terminated because the "
                            "backend process is shutting down.",
                            "details": [],
                        },
                    )
                    await session.commit()
                    raise
                except Exception as exc:  # noqa: BLE001 - every failure recorded
                    await session.rollback()
                    await research_service.mark_failed(
                        session,
                        run_id,
                        {
                            "code": getattr(exc, "code", None) or "RESEARCH_FAILED",
                            "message": str(exc),
                            "details": getattr(exc, "details", []) or [],
                        },
                    )
                    await session.commit()
                    import traceback as _tb

                    logger.warning(
                        "research_failed",
                        extra={
                            "run_id": str(run_id),
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": _tb.format_exc(),
                        },
                    )


class _RunExecutor:
    """One research run's execution state (own session, own budget)."""

    _CLIP_CACHE_MAX = 6

    def __init__(
        self,
        session: AsyncSession,
        settings: Any,
        run: ResearchRun,
        cancel_event: asyncio.Event,
    ) -> None:
        self.session = session
        self.settings = settings
        self.run = run
        self.cancel_event = cancel_event
        self.walks_executed = 0
        self.walk_budget = 0
        self.series_by_tf: dict[str, _TimeframeSeries] = {}
        self.candidate_rows: list[ResearchCandidate] = []
        self.warnings: list[dict[str, Any]] = []
        self.data_quality: dict[str, Any] | None = None
        self.progress: dict[str, Any] = {
            "phase": "loading",
            "candidates_total": 0,
            "candidates_evaluated": 0,
            "candidates_failed": 0,
            "candidates_invalid": 0,
            "windows_total": 0,
            "windows_completed": 0,
            "windows_skipped": 0,
            "scenarios_total": 0,
            "scenarios_completed": 0,
            "walks_executed": 0,
        }
        self._clip_cache: OrderedDict[int, dict[str, _TimeframeSeries]] = OrderedDict()

    # ---- small helpers -----------------------------------------------------

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()

    def _config(self) -> ResearchConfigPayload:
        return ResearchConfigPayload.model_validate(self.run.config)

    async def _commit_progress(self, phase: str | None = None) -> None:
        if phase is not None:
            self.progress["phase"] = phase
        self.progress["walks_executed"] = self.walks_executed
        self.run.progress = dict(self.progress)
        self.run.walks_executed = self.walks_executed
        await self.session.commit()

    def _clipped(self, until_epoch: int) -> dict[str, _TimeframeSeries]:
        """LRU-cached series clipped at a phase boundary (purge policy)."""

        cached = self._clip_cache.get(until_epoch)
        if cached is not None:
            self._clip_cache.move_to_end(until_epoch)
            return cached
        clipped = {
            tf: clip_series(s, until_epoch) for tf, s in self.series_by_tf.items()
        }
        self._clip_cache[until_epoch] = clipped
        while len(self._clip_cache) > self._CLIP_CACHE_MAX:
            self._clip_cache.popitem(last=False)
        return clipped

    def _primary_candle_count(self, start_epoch: int, end_epoch: int) -> int:
        """Actual stored closed candles in [start, end) on the primary TF.

        Used for honest window data probes WITHOUT burning engine walks.
        """

        from bisect import bisect_left, bisect_right

        cfg = self._config()
        series = self.series_by_tf.get(cfg.timeframe)
        if series is None or not series.candles:
            return 0
        open_times = [int(r["time"]) for r in series.candles]
        # Mirrors run_walk's window selection against the CLIPPED series:
        # candles are walkable when start <= open_time < end (candles
        # opening at `end` close after the phase boundary and are purged).
        lo = bisect_left(open_times, start_epoch)
        hi = bisect_left(open_times, end_epoch)
        return max(0, hi - lo)

    def _walk_config(
        self,
        cfg: ResearchConfigPayload,
        rule_set_dict: dict[str, Any],
        *,
        start_epoch: int,
        end_epoch: int,
        forex_execution: dict[str, Any] | None,
        binary: dict[str, Any] | None,
    ) -> BacktestConfigPayload:
        """A Phase 6 config for ONE walk — same fields, adjusted window."""

        return BacktestConfigPayload(
            mode=cfg.mode,
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            start=_iso(start_epoch),
            end=_iso(end_epoch),
            warmup_candles=cfg.warmup_candles,
            max_candles=cfg.max_candles,
            data_source=cfg.data_source,
            strategy_id=cfg.strategy_id,
            strategy_name=cfg.strategy_name,
            strategy_version=cfg.strategy_version,
            strategy_version_status=cfg.strategy_version_status,
            rule_set=rule_set_dict,
            direction=cfg.direction,
            notes=None,
            forex_execution=forex_execution,
            binary=binary,
            simulation_mode=cfg.simulation_mode,
        )

    def _eval_detached(
        self,
        cfg: ResearchConfigPayload,
        rule_set_dict: dict[str, Any],
        *,
        start_epoch: int,
        end_epoch: int,
        forex_override: dict[str, Any] | None = None,
        binary_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One engine walk WITHOUT touching candidate rows.

        Raises whatever the engine raises (incl. CancelledError); the
        caller decides how failures are recorded.
        """

        if self.walks_executed >= self.walk_budget:
            raise ResearchBudgetError(
                "The run reached its engine-walk budget before finishing; "
                "this is a bounded-computation guard (the configuration "
                "should have been rejected at creation)."
            )
        self._check_cancel()
        validated = validate_rule_set(rule_set_dict)
        forex, binary = self._apply_execution_variant(
            cfg, forex_override, binary_override
        )
        config = self._walk_config(
            cfg,
            validated.to_dict(),
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            forex_execution=forex,
            binary=binary,
        )
        self.walks_executed += 1
        output = run_walk(
            config=config,
            rule_set=validated,
            series_by_tf=self._clipped(end_epoch),
            cancel_check=self._check_cancel,
        )
        if self.data_quality is None and output.data_quality:
            self.data_quality = {
                **output.data_quality,
                "note": (
                    "Data-quality assessment of the loaded history; the "
                    "same stored series is shared by every candidate and "
                    "window of this run."
                ),
            }
        return {
            "score": metric_value(output.metrics, cfg.objective["metric"]),
            "violations": evaluate_constraints(
                output.metrics, cfg.objective_constraints
            ),
            "digest": metrics_digest(output.metrics),
            "output": output,
        }

    def _apply_execution_variant(
        self,
        cfg: ResearchConfigPayload,
        forex_override: dict[str, Any] | None,
        binary_override: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        forex = dict(cfg.forex_execution) if cfg.forex_execution else None
        binary = dict(cfg.binary) if cfg.binary else None
        if forex is not None and forex_override:
            for key, value in forex_override.items():
                if value is not None:
                    forex[key] = value
        if binary is not None and binary_override:
            for key, value in binary_override.items():
                if value is not None:
                    binary[key] = value
        return forex, binary

    async def _evaluate_candidate_on_window(
        self,
        cfg: ResearchConfigPayload,
        row: ResearchCandidate,
        *,
        start_epoch: int,
        end_epoch: int,
    ) -> dict[str, Any]:
        """Evaluate one candidate on one window; update its row honestly.

        Failed/invalid candidates are RECORDED (status + error), never
        silently discarded. Cancellation always propagates.
        """

        try:
            info = self._eval_detached(
                cfg, row.rule_set, start_epoch=start_epoch, end_epoch=end_epoch
            )
        except CancelledError:
            raise
        except ResearchBudgetError as exc:
            row.status = "failed"
            row.error = {"code": exc.code, "message": str(exc)}
            self.progress["candidates_failed"] += 1
            return {"score": None, "violations": [], "digest": {}}
        except Exception as exc:  # noqa: BLE001 - record, never discard
            row.status = "failed"
            row.error = {
                "code": getattr(exc, "code", None) or "CANDIDATE_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
            }
            self.progress["candidates_failed"] += 1
            return {"score": None, "violations": [], "digest": {}}
        row.status = "evaluated"
        row.score = info["score"]
        row.constraint_violations = info["violations"] or None
        row.selectable = info["score"] is not None and not info["violations"]
        return info

    # ---- main entry --------------------------------------------------------

    async def execute(self) -> None:
        from app.domains.research import service as research_service

        cfg = self._config()
        self.walk_budget = int(cfg.limits.get("max_walks", 1_500))
        rule_set = validate_rule_set(cfg.rule_set)
        timeframes = sorted(set(rule_set.timeframes), key=timeframe_seconds)

        # Load the full history ONCE (warmup before the configured start) —
        # every candidate and every window walks the identical series.
        rows_by_tf = await load_history(
            self.session,
            symbol=cfg.symbol,
            timeframes=timeframes,
            primary_timeframe=cfg.timeframe,
            start=datetime.fromisoformat(cfg.start),
            end=datetime.fromisoformat(cfg.end),
            warmup_candles=cfg.warmup_candles,
            max_candles=cfg.max_candles,
        )
        self.series_by_tf = {
            tf: _TimeframeSeries(tf, rows) for tf, rows in rows_by_tf.items()
        }

        start_epoch = _epoch(cfg.start)
        end_epoch = _epoch(cfg.end)
        interval = timeframe_seconds(cfg.timeframe)
        # Shared state for the robustness module (documented attributes).
        self._start_epoch = start_epoch
        self._end_epoch = end_epoch
        self._interval = interval

        # Candidate 0 = original parameterization; 1..N = explicit grid.
        patches: list[list[dict[str, Any]] | None] = [None]
        patches.extend(enumerate_patches(cfg.parameters))
        self.progress["candidates_total"] = len(patches)

        self.candidate_rows = []
        for index, patch in enumerate(patches):
            rule_set_dict = (
                dict(cfg.rule_set)
                if patch is None
                else params_mod.apply_patch(cfg.rule_set, patch)
            )
            row = ResearchCandidate(
                run_id=self.run.id,
                owner_id=self.run.owner_id,
                candidate_index=index,
                status="pending",
                params={
                    "patch": patch or [],
                    "label": params_mod.patch_label(patch or [], cfg.parameter_labels),
                },
                rule_set=rule_set_dict,
            )
            self.session.add(row)
            self.candidate_rows.append(row)
        await self.session.flush()
        await self._commit_progress(phase="candidates")

        specs = cfg.parameters
        mode = cfg.validation_mode
        if mode == "walk_forward":
            await self._phase_walk_forward(cfg, specs, interval)
        elif mode == "holdout":
            await self._phase_holdout(cfg, specs, start_epoch, end_epoch, interval)
        else:
            await self._phase_in_sample_only(cfg, specs, start_epoch, end_epoch)

        # ---- robustness ----------------------------------------------------
        if not self.cancel_event.is_set():
            await self._phase_robustness(cfg, specs, start_epoch, end_epoch, interval)

        # ---- aggregate ------------------------------------------------------
        aggregate = await self._build_aggregate(cfg, mode)
        self.run.aggregate = aggregate
        # Keep the creation-time coverage block; phase data quality wins on
        # shared keys (it reflects the actual walks).
        self.run.data_quality = {
            **(self.data_quality or {}),
            **(self.run.data_quality or {}),
        }
        if self.warnings:
            merged = list(cfg.warnings) + list(self.warnings)
            self.run.config = {**cfg.model_dump(), "warnings": merged}
        await self._commit_progress(phase="completed")
        await research_service.mark_completed(self.session, self.run)

    # ---- phases ------------------------------------------------------------

    async def _phase_in_sample_only(
        self,
        cfg: ResearchConfigPayload,
        specs: list[dict[str, Any]],
        start_epoch: int,
        end_epoch: int,
    ) -> None:
        """Every candidate evaluated on the FULL window; ranking is
        explicitly an in-sample fit (no out-of-sample claims)."""

        self.warnings.append(
            {
                "code": "IN_SAMPLE_ONLY",
                "message": "No out-of-sample evaluation was configured: "
                "every ranking in this run is an in-sample fit and must "
                "not be read as expected performance.",
            }
        )
        for row in self.candidate_rows:
            self._check_cancel()
            info = await self._evaluate_candidate_on_window(
                cfg, row, start_epoch=start_epoch, end_epoch=end_epoch
            )
            row.in_sample = info.get("digest") or None
            if row.status == "evaluated":
                self.progress["candidates_evaluated"] += 1
            await self._commit_progress(phase="candidates")

        scored = [self._candidate_score_view(r) for r in self.candidate_rows]
        rank_scored(scored, cfg.objective_direction)
        self._apply_ranks(scored)
        await self.session.flush()
        await self._commit_progress(phase="candidates_ranked")

    async def _phase_holdout(
        self,
        cfg: ResearchConfigPayload,
        specs: list[dict[str, Any]],
        start_epoch: int,
        end_epoch: int,
        interval: int,
    ) -> None:
        """Selection on the in-sample window; OOS walked ONCE for the
        in-sample winner with FROZEN parameters."""

        is_end = _epoch(str(cfg.in_sample_end))
        # Documented boundary exclusion: the OOS walk starts one primary
        # interval after the in-sample end, so no candle is evaluated in
        # both periods.
        oos_start = is_end + interval
        for row in self.candidate_rows:
            self._check_cancel()
            info = await self._evaluate_candidate_on_window(
                cfg, row, start_epoch=start_epoch, end_epoch=is_end
            )
            row.in_sample = info.get("digest") or None
            if row.status == "evaluated":
                self.progress["candidates_evaluated"] += 1
            await self._commit_progress(phase="candidates_in_sample")

        scored = [self._candidate_score_view(r) for r in self.candidate_rows]
        rank_scored(scored, cfg.objective_direction)
        self._apply_ranks(scored)
        await self.session.flush()

        selected = select_candidate(scored)
        if selected is None:
            self.warnings.append(
                {
                    "code": "NO_SELECTABLE_CANDIDATE",
                    "message": "No candidate produced a rankable objective "
                    "value within the configured constraints on the "
                    "in-sample window; the out-of-sample phase was not "
                    "executed.",
                }
            )
            await self._commit_progress(phase="candidates_ranked")
            return
        selected_row = self.candidate_rows[selected["candidate_index"]]
        self._check_cancel()
        try:
            oos_info = self._eval_detached(
                cfg,
                selected_row.rule_set,
                start_epoch=oos_start,
                end_epoch=end_epoch,
            )
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(
                {
                    "code": "OUT_OF_SAMPLE_FAILED",
                    "message": f"The out-of-sample walk failed: {exc}",
                }
            )
            await self._commit_progress(phase="out_of_sample")
            return
        selected_row.out_of_sample = oos_info.get("digest") or None
        await self.session.flush()
        await self._commit_progress(phase="out_of_sample")

    def _probe_window(self, spec: Any, wf: dict[str, Any]) -> str | None:
        """Honest data probe for a walk-forward window (no engine walk).

        Returns a skip reason, or None when the window has enough stored
        history per the configured minimum requirements.
        """

        train_count = self._primary_candle_count(spec.train_start, spec.train_end)
        if train_count < wf["train_candles"]:
            return (
                f"INSUFFICIENT_WINDOW_DATA: the training window holds "
                f"{train_count} closed {self._config().timeframe} candles, "
                f"below the configured minimum of {wf['train_candles']}."
            )
        test_count = self._primary_candle_count(spec.test_start, spec.test_end)
        if test_count == 0:
            return (
                "INSUFFICIENT_WINDOW_DATA: the test window holds no closed "
                f"{self._config().timeframe} candles at all."
            )
        return None

    async def _phase_walk_forward(
        self,
        cfg: ResearchConfigPayload,
        specs: list[dict[str, Any]],
        interval: int,
    ) -> None:
        """Chronological windows: select on train, freeze, test OOS."""

        wf = dict(cfg.walk_forward or {})
        start_epoch = _epoch(cfg.start)
        end_epoch = _epoch(cfg.end)
        windows, warnings = generate_windows(
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            interval_seconds=interval,
            train_candles=wf["train_candles"],
            test_candles=wf["test_candles"],
            step_candles=wf["step_candles"],
            window_mode=wf["window_mode"],
            max_windows=int(cfg.limits.get("max_windows", 40)),
        )
        for message in warnings:
            code, _, rest = message.partition(":")
            self.warnings.append({"code": code, "message": rest.strip() or message})
        self.progress["windows_total"] = len(windows)

        wf_rows: list[ResearchWindow] = []
        for spec in windows:
            wf_rows.append(
                ResearchWindow(
                    run_id=self.run.id,
                    owner_id=self.run.owner_id,
                    window_index=spec.index,
                    status="completed",
                    train_start=datetime.fromtimestamp(spec.train_start, tz=timezone.utc),
                    train_end=datetime.fromtimestamp(spec.train_end, tz=timezone.utc),
                    test_start=datetime.fromtimestamp(spec.test_start, tz=timezone.utc),
                    test_end=datetime.fromtimestamp(spec.test_end, tz=timezone.utc),
                )
            )
        self.session.add_all(wf_rows)
        await self.session.flush()

        for wf_row, spec in zip(wf_rows, windows):
            self._check_cancel()
            skip_reason = self._probe_window(spec, wf)
            if skip_reason is not None:
                wf_row.status = "skipped"
                wf_row.error = {
                    "code": "INSUFFICIENT_WINDOW_DATA",
                    "message": skip_reason,
                }
                wf_row.note = (
                    "The run continues with later windows; aggregated "
                    "out-of-sample results exclude skipped windows and "
                    "the run is marked incomplete in its aggregate."
                )
                self.progress["windows_skipped"] += 1
                await self._commit_progress(phase="walk_forward")
                continue

            # ---- selection: TRAINING WINDOW ONLY --------------------------
            train_evaluations: list[dict[str, Any]] = []
            scored: list[dict[str, Any]] = []
            train_info_by_index: dict[int, dict[str, Any]] = {}
            for row in self.candidate_rows:
                self._check_cancel()
                try:
                    info = self._eval_detached(
                        cfg,
                        row.rule_set,
                        start_epoch=spec.train_start,
                        end_epoch=spec.train_end,
                    )
                except CancelledError:
                    raise
                except ResearchBudgetError as exc:
                    info = {"score": None, "violations": [], "digest": {}}
                    row.status = "failed"
                    row.error = {"code": exc.code, "message": str(exc)}
                    self.progress["candidates_failed"] += 1
                except Exception as exc:  # noqa: BLE001 - record, never discard
                    if self.cancel_event.is_set():
                        raise CancelledError() from exc
                    info = {"score": None, "violations": [], "digest": {}}
                    row.status = "failed"
                    row.error = {
                        "code": getattr(exc, "code", None) or "CANDIDATE_FAILED",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    self.progress["candidates_failed"] += 1
                decided = ((info.get("digest") or {}).get("trades") or {}).get(
                    "decided_trades"
                )
                train_evaluations.append(
                    {
                        "candidate_index": row.candidate_index,
                        "label": row.params.get("label"),
                        "score": info["score"],
                        "decided_trades": decided,
                        "constraint_violations": info["violations"],
                    }
                )
                scored.append(
                    {
                        "candidate_index": row.candidate_index,
                        "score": info["score"],
                        "constraint_violations": info["violations"],
                    }
                )
                train_info_by_index[row.candidate_index] = info
                if row.status == "evaluated":
                    self.progress["candidates_evaluated"] += 1

            rank_scored(scored, cfg.objective_direction)
            selected = select_candidate(scored)
            wf_row.train_evaluations = train_evaluations
            if selected is None:
                wf_row.note = (
                    "No candidate produced a rankable objective value "
                    "within the configured constraints on this training "
                    "window; the test window was not walked."
                )
                wf_row.status = "completed"
                self.progress["windows_completed"] += 1
                await self._commit_progress(phase="walk_forward")
                continue

            selected_row = self.candidate_rows[selected["candidate_index"]]
            wf_row.selected_candidate_index = selected_row.candidate_index
            wf_row.selected_params = selected_row.params
            wf_row.selection_score = selected["score"]

            # ---- testing: FROZEN winner on the held-out window -------------
            self._check_cancel()
            try:
                test_info = self._eval_detached(
                    cfg,
                    selected_row.rule_set,
                    start_epoch=spec.test_start,
                    end_epoch=spec.test_end,
                )
            except CancelledError:
                raise
            except ResearchBudgetError as exc:
                wf_row.status = "failed"
                wf_row.error = {"code": exc.code, "message": str(exc)}
                self.progress["windows_completed"] += 1
                await self._commit_progress(phase="walk_forward")
                continue
            except Exception as exc:  # noqa: BLE001
                if self.cancel_event.is_set():
                    raise CancelledError() from exc
                wf_row.status = "failed"
                wf_row.error = {
                    "code": "TEST_WALK_FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                }
                self.progress["windows_completed"] += 1
                await self._commit_progress(phase="walk_forward")
                continue
            wf_row.test_metrics = test_info.get("digest") or None
            selected_row.times_selected += 1
            wf_row.status = "completed"
            self.progress["windows_completed"] += 1
            await self._commit_progress(phase="walk_forward")

        # Candidate-level walk-forward summary (avg TRAINING score; test
        # evidence lives on the windows — the candidate row keeps only
        # selection-window information by construction).
        for row in self.candidate_rows:
            scores: list[float] = []
            for wf_row in wf_rows:
                for entry in wf_row.train_evaluations or []:
                    if (
                        entry["candidate_index"] == row.candidate_index
                        and entry["score"] is not None
                    ):
                        scores.append(float(entry["score"]))
            if scores:
                row.score = round(sum(scores) / len(scores), 8)
                row.in_sample = {
                    "walk_forward_summary": {
                        "windows_evaluated": len(scores),
                        "avg_train_score": row.score,
                        "times_selected": row.times_selected,
                    }
                }
            row.rank = None
            row.rank_note = (
                "Walk-forward run: selection happens per training window "
                "(see the walk-forward view). rank is intentionally NULL — "
                "there is no single global ranking in this mode."
            )
        await self.session.flush()
        await self._commit_progress(phase="walk_forward")

    # ---- robustness --------------------------------------------------------

    async def _phase_robustness(
        self,
        cfg: ResearchConfigPayload,
        specs: list[dict[str, Any]],
        start_epoch: int,
        end_epoch: int,
        interval: int,
    ) -> None:
        scenarios = robustness_mod.build_scenarios(
            cfg,
            candidate_rows=self.candidate_rows,
            specs=specs,
        )
        self.progress["scenarios_total"] = len(scenarios)
        for index, scenario in enumerate(scenarios):
            self._check_cancel()
            try:
                result = await robustness_mod.execute_scenario(
                    self, cfg, scenario, start_epoch, end_epoch, interval
                )
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - scenario failure recorded
                result = {
                    "metrics": None,
                    "delta": None,
                    "status": "failed",
                    "error": {
                        "code": getattr(exc, "code", None) or "SCENARIO_FAILED",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                }
            row = ResearchScenario(
                run_id=self.run.id,
                owner_id=self.run.owner_id,
                scenario_index=index,
                category=scenario["category"],
                label=scenario["label"],
                assumptions=scenario["assumptions"],
                metrics=result.get("metrics"),
                delta=result.get("delta"),
                status=result.get("status", "completed"),
                note=scenario.get("note"),
                error=result.get("error"),
            )
            self.session.add(row)
            if row.status == "completed":
                self.progress["scenarios_completed"] += 1
            await self.session.flush()
            await self._commit_progress(phase="robustness")

    # ---- helpers -------------------------------------------------------------

    def _candidate_score_view(self, row: ResearchCandidate) -> dict[str, Any]:
        return {
            "candidate_index": row.candidate_index,
            "score": row.score,
            "constraint_violations": row.constraint_violations or [],
        }

    def _apply_ranks(self, scored: list[dict[str, Any]]) -> None:
        by_index = {c["candidate_index"]: c for c in scored}
        for row in self.candidate_rows:
            view = by_index[row.candidate_index]
            row.rank = view.get("rank")
            row.rank_note = view.get("rank_note")
            if row.status == "evaluated":
                row.selectable = (
                    row.score is not None and not (row.constraint_violations or [])
                )

    def _best_ranked(self) -> ResearchCandidate | None:
        ranked = [r for r in self.candidate_rows if r.rank is not None]
        if not ranked:
            return None
        return min(ranked, key=lambda r: r.rank)

    def _most_selected(self) -> ResearchCandidate | None:
        if not self.candidate_rows:
            return None
        return max(
            self.candidate_rows,
            key=lambda r: (r.times_selected, -r.candidate_index),
        )

    def _baseline_candidate(self, mode: str) -> ResearchCandidate | None:
        return self._best_ranked() if mode != "walk_forward" else self._most_selected()

    # ---- aggregate -----------------------------------------------------------

    async def _build_aggregate(self, cfg: ResearchConfigPayload, mode: str) -> dict[str, Any]:
        best = self._best_ranked()
        disclaimers = [
            "Optimization and validation results are historical "
            "simulations over stored candles; they are not predictions "
            "and do not guarantee future performance.",
            "A best-scoring candidate is never published automatically "
            "and must not be read as proof of profitability.",
        ]
        if cfg.mode == "binary_options":
            disclaimers.append(
                "Candle-based binary results are historical simulations "
                "with the configured payout/expiry assumptions — not "
                "guaranteed broker execution results."
            )

        oos_block: dict[str, Any] | None = None
        if mode == "holdout":
            selected_rows = [r for r in self.candidate_rows if r.out_of_sample]
            oos_block = (
                {
                    "candidate_index": selected_rows[0].candidate_index,
                    "label": selected_rows[0].params.get("label"),
                    "digest": selected_rows[0].out_of_sample,
                }
                if selected_rows
                else None
            )
        elif mode == "walk_forward":
            windows = list(
                (
                    await self.session.scalars(
                        select(ResearchWindow)
                        .where(ResearchWindow.run_id == self.run.id)
                        .order_by(ResearchWindow.window_index.asc())
                    )
                ).all()
            )
            digests = [
                w.test_metrics
                for w in windows
                if w.status == "completed" and w.selected_candidate_index is not None
            ]
            oos_block = aggregate_oos(digests)
            skipped = [w for w in windows if w.status != "completed"]
            if skipped:
                oos_block["skipped_windows"] = [
                    {
                        "window_index": w.window_index,
                        "reason": (w.error or {}).get("code"),
                    }
                    for w in skipped
                ]
                disclaimers.append(
                    "One or more walk-forward windows were skipped or "
                    "failed (insufficient window data) — out-of-sample "
                    "aggregates cover the completed windows only."
                )
        else:
            disclaimers.append(
                "No out-of-sample evaluation was performed (validation "
                "mode 'in_sample_only'); every number here is an "
                "in-sample fit."
            )

        stability = robustness_mod.stability_views(cfg, self, mode)
        return {
            "objective": {
                "metric": cfg.objective["metric"],
                "direction": cfg.objective_direction,
                "note": cfg.objective_note,
                "constraints": cfg.objective_constraints,
            },
            "best_candidate": (
                {
                    "candidate_index": best.candidate_index,
                    "label": best.params.get("label"),
                    "rank": best.rank,
                    "score": best.score,
                    "times_selected": best.times_selected,
                }
                if best
                else None
            ),
            "out_of_sample": oos_block,
            "stability_views": stability,
            "warnings": self.warnings,
            "disclaimers": disclaimers,
        }


_runner: ResearchRunner | None = None
_runner_loop: asyncio.AbstractEventLoop | None = None


def get_runner() -> ResearchRunner:
    """Process-wide runner, scoped to the RUNNING event loop.

    asyncio primitives bind to the loop on first contended use; a module
    singleton reused across loops (tests create one loop per test) would
    crash with "bound to a different event loop" and silently strand
    queued runs. Recreating the runner per loop is cheap and safe: cancel
    events for finished runs are irrelevant, and startup recovery already
    handles rows stranded by restarts.
    """

    global _runner, _runner_loop
    loop = asyncio.get_running_loop()
    if _runner is None or _runner_loop is not loop:
        from app.core.config import get_settings

        _runner = ResearchRunner(
            max_concurrent=get_settings().research_max_concurrent
        )
        _runner_loop = loop
    return _runner


def reset_runner() -> None:
    """Test hook: drop the singleton (settings may have changed)."""

    global _runner, _runner_loop
    _runner = None
    _runner_loop = None


__all__ = [
    "ResearchRunner",
    "clip_series",
    "get_runner",
    "reset_runner",
]

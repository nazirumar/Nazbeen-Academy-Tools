"""Unit tests: Phase 8 AI integration — the research-run context.

The AI layer explains stored research evidence; it never recomputes
metrics. These tests pin the minimized context builder and the
fail-closed evidence-reference rules for research contexts.
"""

from __future__ import annotations

import pytest

from app.domains.ai.context import build_research_context
from app.domains.ai.validation import _context_refs


def research_summary(**overrides) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "owner_id": "local",
        "mode": "forex",
        "symbol": "EURUSD",
        "timeframe": "M15",
        "validation_mode": "holdout",
        "strategy_id": "22222222-2222-2222-2222-222222222222",
        "strategy_name": "London breakout",
        "strategy_version": 2,
        "status": "completed",
        "start_time": "2025-01-01T00:00:00Z",
        "end_time": "2025-03-01T00:00:00Z",
        "simulation_mode": "trade_simulation",
        "objective": {"metric": "net_pnl", "note": "n"},
        "combination_count": 3,
        "walk_budget": 1500,
        "walks_executed": 8,
        "progress": {"phase": "completed", "candidates_total": 3},
        "config": {"direction": "bullish"},
        "aggregate": {
            "best_candidate": {"candidate_index": 1, "label": "px.params.value=1.15", "rank": 1, "score": 12.5},
            "out_of_sample": {"trades": {"decided_trades": 5, "net_pnl": 4.2, "win_rate": 0.6}},
            "stability_views": {"time_periods": {"note": "n"}},
            "warnings": [{"code": "W", "message": "m"}],
            "disclaimers": ["Historical simulation; no guarantee."],
        },
    }
    base.update(overrides)
    return base


class TestBuildResearchContext:
    def test_context_carries_stored_evidence_verbatim(self):
        ctx = build_research_context(research_summary())
        assert ctx.context_kind == "research"
        assert ctx.run_id == "11111111-1111-1111-1111-111111111111"
        assert ctx.validation_mode == "holdout"
        assert ctx.strategy is not None and ctx.strategy.version == 2
        assert ctx.best_candidate["score"] == 12.5  # copied, never recomputed
        assert ctx.out_of_sample["trades"]["net_pnl"] == 4.2
        assert any("not a prediction" in l for l in ctx.limitations)
        assert ctx.disclaimers == ["Historical simulation; no guarantee."]

    def test_incomplete_run_marks_unavailable(self):
        ctx = build_research_context(research_summary(status="running", aggregate={}))
        assert ctx.status == "running"
        assert any("not completed" in u for u in ctx.unavailable)
        assert any("No aggregate" in u for u in ctx.unavailable)

    def test_failed_run_reports_error_in_unavailable(self):
        ctx = build_research_context(
            research_summary(status="failed", error={"code": "RESEARCH_FAILED", "message": "x"})
        )
        assert any("RESEARCH_FAILED" in u for u in ctx.unavailable)

    def test_no_strategy_name_leaves_ref_empty(self):
        ctx = build_research_context(research_summary(strategy_name=None))
        assert ctx.strategy is None


class TestResearchEvidenceRefs:
    def test_pseudo_refs_cover_stored_sections(self):
        ctx = build_research_context(research_summary())
        refs = _context_refs(ctx)
        assert "run:11111111-1111-1111-1111-111111111111" in refs
        for key in ("research:objective", "research:best_candidate", "research:out_of_sample"):
            assert key in refs

    def test_citation_outside_stored_sections_fails_closed(self):
        from app.domains.ai.validation import validate_chat_answer

        ctx = build_research_context(research_summary())
        # A fabricated condition-level ref (analysis-style) must be rejected.
        bad = {
            "answer": "The condition px was met.",
            "evidence_cited": [{"text": "x", "refs": ["core:px"]}],
            "uncertainty": [],
            "unavailable_information": [],
            "suggested_follow_ups": [],
        }
        with pytest.raises(Exception):
            validate_chat_answer(bad, ctx)

    def test_citation_of_stored_sections_passes(self):
        from app.domains.ai.validation import validate_chat_answer

        ctx = build_research_context(research_summary())
        ok = {
            "answer": "The out-of-sample net P&L was 4.2 account currency units.",
            "evidence_cited": [{"text": "OOS aggregate.", "refs": ["research:out_of_sample"]}],
            "uncertainty": ["Historical simulation only."],
            "unavailable_information": [],
            "suggested_follow_ups": [],
        }
        output, validation = validate_chat_answer(ok, ctx)
        assert validation.evidence_refs_ok is True
        assert validation.schema_ok is True

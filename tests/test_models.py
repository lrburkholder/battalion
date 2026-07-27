"""Tests for battalion.state.models — the versioned state contract (BTN-1)."""
import pytest
from pydantic import ValidationError

from battalion.state.models import RunState, RunStatus, RejectionRecord, InterruptLogEntry


def make_valid_state(**overrides):
    defaults = dict(
        schema_version="1.0",
        run_id="run-001",
        ticket_id="BTN-1",
        status=RunStatus.NOT_STARTED,
        phase="architect",
        write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
        reviewer_rejection_history=[],
        retry_bound=2,
        budget={"limit": 100, "used": 0},
        interrupt_log=[],
    )
    defaults.update(overrides)
    return RunState(**defaults)


def test_valid_state_constructs():
    state = make_valid_state()
    assert state.run_id == "run-001"
    assert state.status == RunStatus.NOT_STARTED


def test_status_must_be_valid_enum_value():
    with pytest.raises(ValidationError):
        make_valid_state(status="not-a-real-status")


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        RunState(schema_version="1.0", run_id="run-001")


def test_rejection_history_tracks_cause_and_cycle():
    record = RejectionRecord(cause="missing null check", cycle_number=1)
    state = make_valid_state(reviewer_rejection_history=[record])
    assert state.reviewer_rejection_history[0].cause == "missing null check"
    assert state.reviewer_rejection_history[0].cycle_number == 1


def test_interrupt_log_entry_requires_trigger():
    with pytest.raises(ValidationError):
        InterruptLogEntry(timestamp="2026-07-27T00:00:00Z")


def test_budget_tracked_per_run_not_per_node():
    # Budget is a single dict on RunState, not nested per-node — enforces
    # ADR/spec decision that budget is per-graph-run.
    state = make_valid_state(budget={"limit": 50, "used": 10})
    assert state.budget.used == 10
    assert state.budget.limit == 50


def test_write_scope_is_per_node_mapping():
    state = make_valid_state()
    assert state.write_scope["driver"] == ["src/"]
    assert state.write_scope["reviewer"] == []


def test_budget_exceeded_true_when_used_reaches_limit():
    from battalion.state.models import Budget

    assert Budget(limit=10, used=10).exceeded() is True


def test_budget_exceeded_false_when_under_limit():
    from battalion.state.models import Budget

    assert Budget(limit=10, used=5).exceeded() is False

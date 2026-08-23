"""Tests for battalion.interrupts — 6 v1 interrupt triggers (BTN-7, spec.md).

Trigger taxonomy:
1. Reviewer rejects same root cause twice (per-checkpoint-type)
2. Out-of-scope write attempt (defense-in-depth)
3. Budget exceeded (per-graph-run)
4. Role-definition edit
5. Infra failure (LLM call fails after retries)
6. Manual checkpoint (user-declared pause point)
"""
import pytest

from datetime import datetime

from battalion.interrupts.budget import (
    BudgetExceededError,
    increment_budget,
)
from battalion.interrupts.budget import check_budget_exceeded as check_budget_exceeded_bool
from battalion.interrupts.triggers import (
    TRIGGER_SAME_ROOT_CAUSE,
    TRIGGER_SCOPE_VIOLATION,
    TRIGGER_BUDGET_EXCEEDED,
    TRIGGER_ROLE_EDIT,
    TRIGGER_INFRA_FAILURE,
    TRIGGER_MANUAL_CHECKPOINT,
    check_reviewer_rejection,
    check_scope_violation,
    check_budget_exceeded_trigger,
    check_role_definition_edit,
    check_infra_failure,
    check_manual_checkpoint,
    check_any_trigger,
    get_trigger_name,
    log_interrupt,
)
from battalion.llm.litellm_client import InfraFailure
from battalion.scope.tool_binding import ScopeViolationError
from battalion.state.models import (
    Budget,
    CheckpointType,
    RejectionRecord,
    RunState,
    RunStatus,
)


# --- Fixtures ---

from conftest import make_run_state

def make_state(
    budget_used: int = 0,
    budget_limit: int = 100,
    write_scope: dict | None = None,
    rejection_history: list | None = None,
    manual_checkpoints: list | None = None,
    **overrides,
) -> RunState:
    fields = dict(
        ticket_id="BTN-7-test", run_id="run-001",
        status=RunStatus.IN_PROGRESS, phase="architect",
        write_scope=write_scope,
        budget_used=budget_used, budget_limit=budget_limit,
        rejection_history=rejection_history,
        manual_checkpoints=manual_checkpoints,
    )
    fields.update(overrides)
    return make_run_state(**fields)


# =============================================================================
# budget.py tests
# =============================================================================

class TestBudget:
    def test_increment_budget_increases_used(self):
        state = make_state(budget_used=0, budget_limit=100)
        new_state = increment_budget(state)
        assert new_state.budget.used == 1
        assert new_state.budget.limit == 100

    def test_increment_budget_with_custom_amount(self):
        state = make_state(budget_used=5, budget_limit=100)
        new_state = increment_budget(state, amount=3)
        assert new_state.budget.used == 8

    def test_increment_budget_returns_new_state(self):
        state = make_state(budget_used=0, budget_limit=100)
        new_state = increment_budget(state)
        assert state is not new_state
        assert state.budget.used == 0  # Original unchanged

    def test_check_budget_exceeded_true(self):
        state = make_state(budget_used=100, budget_limit=100)
        assert check_budget_exceeded_bool(state) is True

    def test_check_budget_exceeded_false(self):
        state = make_state(budget_used=50, budget_limit=100)
        assert check_budget_exceeded_bool(state) is False

    def test_check_budget_exceeded_exactly_at_limit(self):
        state = make_state(budget_used=100, budget_limit=100)
        assert check_budget_exceeded_bool(state) is True

    def test_budget_exceeded_error(self):
        err = BudgetExceededError(used=150, limit=100)
        assert "150 >= 100" in str(err)
        assert err.used == 150
        assert err.limit == 100


# =============================================================================
# triggers.py tests - Individual trigger checks
# =============================================================================

class TestTrigger1SameRootCause:
    def test_no_rejections_no_trigger(self):
        state = make_state(rejection_history=[])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is False
        assert trigger_id == TRIGGER_SAME_ROOT_CAUSE

    def test_one_rejection_no_trigger(self):
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            )
        ])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is False

    def test_two_different_causes_no_trigger(self):
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
            RejectionRecord(
                cause="ValueError in other.py",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
        ])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is False

    def test_two_same_causes_same_checkpoint_triggers(self):
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=2,
                checkpoint=CheckpointType.RED_CHECK,
            ),
        ])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is True
        assert trigger_id == TRIGGER_SAME_ROOT_CAUSE

    def test_same_cause_different_checkpoint_no_trigger(self):
        """ADR-009: counters are per-checkpoint-type, so same cause
        across different checkpoints shouldn't trigger."""
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=1,
                checkpoint=CheckpointType.GREEN_CHECK,
            ),
        ])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is False

    def test_case_insensitive_cause_matching(self):
        """Root cause matching should be case-insensitive."""
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
            RejectionRecord(
                cause="typeerror in module.py",
                cycle_number=2,
                checkpoint=CheckpointType.RED_CHECK,
            ),
        ])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is True

    def test_whitespace_normalized(self):
        """Root cause matching should normalize whitespace."""
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="  TypeError in module.py  ",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
            RejectionRecord(
                cause="TypeError in module.py",
                cycle_number=2,
                checkpoint=CheckpointType.RED_CHECK,
            ),
        ])
        fired, trigger_id = check_reviewer_rejection(state)
        assert fired is True


class TestTrigger2ScopeViolation:
    def test_scope_violation_error_triggers(self):
        error = ScopeViolationError("Attempted to write outside scope")
        fired, trigger_id = check_scope_violation(error)
        assert fired is True
        assert trigger_id == TRIGGER_SCOPE_VIOLATION

    def test_other_error_no_trigger(self):
        error = ValueError("Some other error")
        fired, trigger_id = check_scope_violation(error)
        assert fired is False

    def test_no_error_no_trigger(self):
        fired, trigger_id = check_scope_violation(None)
        assert fired is False


class TestTrigger3BudgetExceeded:
    def test_budget_exceeded_triggers(self):
        state = make_state(budget_used=100, budget_limit=100)
        fired, trigger_id = check_budget_exceeded_trigger(state)
        assert fired is True
        assert trigger_id == TRIGGER_BUDGET_EXCEEDED

    def test_budget_not_exceeded_no_trigger(self):
        state = make_state(budget_used=50, budget_limit=100)
        fired, trigger_id = check_budget_exceeded_trigger(state)
        assert fired is False


class TestTrigger4RoleDefinitionEdit:
    def test_write_scope_change_triggers(self):
        old_state = make_state(write_scope={"driver": ["src/"]})
        new_state = make_state(write_scope={"driver": ["src/", "tests/"]})
        fired, trigger_id = check_role_definition_edit(old_state, new_state)
        assert fired is True
        assert trigger_id == TRIGGER_ROLE_EDIT

    def test_no_change_no_trigger(self):
        old_state = make_state(write_scope={"driver": ["src/"]})
        new_state = make_state(write_scope={"driver": ["src/"]})
        fired, trigger_id = check_role_definition_edit(old_state, new_state)
        assert fired is False

    def test_no_old_state_no_trigger(self):
        new_state = make_state()
        fired, trigger_id = check_role_definition_edit(None, new_state)
        assert fired is False


class TestTrigger5InfraFailure:
    def test_infra_failure_triggers(self):
        error = InfraFailure("architect", "gpt-4", 3, RuntimeError("provider down"))
        fired, trigger_id = check_infra_failure(error)
        assert fired is True
        assert trigger_id == TRIGGER_INFRA_FAILURE

    def test_other_error_no_trigger(self):
        error = RuntimeError("Some other error")
        fired, trigger_id = check_infra_failure(error)
        assert fired is False

    def test_no_error_no_trigger(self):
        fired, trigger_id = check_infra_failure(None)
        assert fired is False


class TestTrigger6ManualCheckpoint:
    def test_phase_in_checkpoints_triggers(self):
        state = make_state(manual_checkpoints=["driver", "reviewer"])
        fired, trigger_id = check_manual_checkpoint(state, next_phase="driver")
        assert fired is True
        assert trigger_id == TRIGGER_MANUAL_CHECKPOINT

    def test_phase_not_in_checkpoints_no_trigger(self):
        state = make_state(manual_checkpoints=["driver"])
        fired, trigger_id = check_manual_checkpoint(state, next_phase="architect")
        assert fired is False


# =============================================================================
# check_any_trigger tests
# =============================================================================

class TestCheckAnyTrigger:
    def test_manual_checkpoint_highest_priority(self):
        """Manual checkpoint (trigger #6) should take precedence over all others."""
        state = make_state(
            budget_used=100,
            budget_limit=100,
            manual_checkpoints=["reviewer"],
        )
        fired, trigger_id, context = check_any_trigger(
            state, next_phase="reviewer"
        )
        assert fired is True
        assert trigger_id == TRIGGER_MANUAL_CHECKPOINT

    def test_infra_failure_priority_over_budget(self):
        """Infra failure (trigger #5) should take precedence over budget."""
        state = make_state(budget_used=100, budget_limit=100)
        error = InfraFailure("driver", "gpt-4", 3, RuntimeError("down"))
        fired, trigger_id, context = check_any_trigger(
            state, error=error
        )
        assert fired is True
        assert trigger_id == TRIGGER_INFRA_FAILURE

    def test_budget_exceeded_fires(self):
        state = make_state(budget_used=100, budget_limit=100)
        fired, trigger_id, context = check_any_trigger(state)
        assert fired is True
        assert trigger_id == TRIGGER_BUDGET_EXCEEDED
        assert context["used"] == 100
        assert context["limit"] == 100

    def test_same_root_cause_fires(self):
        state = make_state(rejection_history=[
            RejectionRecord(
                cause="TypeError",
                cycle_number=1,
                checkpoint=CheckpointType.RED_CHECK,
            ),
            RejectionRecord(
                cause="TypeError",
                cycle_number=2,
                checkpoint=CheckpointType.RED_CHECK,
            ),
        ])
        fired, trigger_id, context = check_any_trigger(state)
        assert fired is True
        assert trigger_id == TRIGGER_SAME_ROOT_CAUSE
        assert context["cause"] == "TypeError"

    def test_no_triggers_fires(self):
        state = make_state()
        fired, trigger_id, context = check_any_trigger(state)
        assert fired is False
        assert trigger_id == ""
        assert context == {}


# =============================================================================
# log_interrupt tests
# =============================================================================

class TestLogInterrupt:
    def test_sets_status_to_awaiting_human(self):
        state = make_state(status=RunStatus.IN_PROGRESS)
        new_state = log_interrupt(state, TRIGGER_BUDGET_EXCEEDED)
        assert new_state.status == RunStatus.AWAITING_HUMAN

    def test_adds_to_interrupt_log(self):
        state = make_state()
        new_state = log_interrupt(state, TRIGGER_BUDGET_EXCEEDED)
        assert len(new_state.interrupt_log) == 1
        assert new_state.interrupt_log[0].trigger == TRIGGER_BUDGET_EXCEEDED
        assert new_state.interrupt_log[0].resolution is None
        assert isinstance(new_state.interrupt_log[0].timestamp, datetime)

    def test_returns_new_state(self):
        state = make_state()
        new_state = log_interrupt(state, TRIGGER_BUDGET_EXCEEDED)
        assert state is not new_state
        assert len(state.interrupt_log) == 0  # Original unchanged


# =============================================================================
# get_trigger_name tests
# =============================================================================

class TestGetTriggerName:
    def test_all_trigger_names(self):
        assert "#1" in get_trigger_name(TRIGGER_SAME_ROOT_CAUSE)
        assert "#2" in get_trigger_name(TRIGGER_SCOPE_VIOLATION)
        assert "#3" in get_trigger_name(TRIGGER_BUDGET_EXCEEDED)
        assert "#4" in get_trigger_name(TRIGGER_ROLE_EDIT)
        assert "#5" in get_trigger_name(TRIGGER_INFRA_FAILURE)
        assert "#6" in get_trigger_name(TRIGGER_MANUAL_CHECKPOINT)

    def test_unknown_trigger(self):
        result = get_trigger_name("unknown-trigger")
        assert "Unknown" in result

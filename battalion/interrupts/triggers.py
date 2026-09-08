"""Interrupt trigger checks (BTN-7, spec.md Interrupt Taxonomy v1).

The 6 v1 interrupt triggers:
1. Reviewer rejects same root cause twice (per-checkpoint-type)
2. Out-of-scope write attempt (defense-in-depth, structurally prevented)
3. Budget exceeded (per-graph-run)
4. Role-definition edit (any modification to Battalion role definitions)
5. Infra failure (node crash, malformed state, LiteLLM failure after retries)
6. Manual checkpoint (user-declared pause point)

Each trigger check returns True if the trigger should fire, False otherwise.
None of these check functions should raise exceptions — they return bool.
The exception to this is check_infra_failure which accepts an Exception
parameter to check if it's an InfraFailure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from battalion.llm.litellm_client import InfraFailure
from battalion.nodes.errors import RoleOutputError
from battalion.scope.tool_binding import ScopeViolationError
from battalion.state.models import CheckpointType, RunState, RunStatus


# Trigger identifier constants (match spec.md taxonomy)
TRIGGER_SAME_ROOT_CAUSE = "same-root-cause-twice"
TRIGGER_SCOPE_VIOLATION = "out-of-scope-write"
TRIGGER_BUDGET_EXCEEDED = "budget-exceeded"
TRIGGER_ROLE_EDIT = "role-definition-edit"
TRIGGER_INFRA_FAILURE = "infra-failure"
TRIGGER_MANUAL_CHECKPOINT = "manual-checkpoint"
# A valid Driver authority escalation.  This is intentionally not a seventh
# system-detected v1 trigger; it labels the existing human-resolution boundary.
TRIGGER_ROLE_ESCALATION = "role-escalation"


def get_trigger_name(trigger_id: str) -> str:
    """Get human-readable trigger name from identifier."""
    names = {
        TRIGGER_SAME_ROOT_CAUSE: "#1: Same root cause rejected twice",
        TRIGGER_SCOPE_VIOLATION: "#2: Out-of-scope write attempt",
        TRIGGER_BUDGET_EXCEEDED: "#3: Budget exceeded",
        TRIGGER_ROLE_EDIT: "#4: Role-definition edit",
        TRIGGER_INFRA_FAILURE: "#5: Infra failure",
        TRIGGER_MANUAL_CHECKPOINT: "#6: Manual checkpoint",
        TRIGGER_ROLE_ESCALATION: "Role authority escalation",
    }
    return names.get(trigger_id, f"Unknown trigger: {trigger_id}")


def check_reviewer_rejection(state: RunState) -> tuple[bool, str]:
    """Check trigger #1: Reviewer rejects same root cause twice.
    
    Trigger fires when the same root cause is rejected twice for the same
    checkpoint type (per-checkpoint-type counters, ADR-009 from BTN-12).
    
    This is checked AFTER a Reviewer rejection is recorded. The check looks
    at reviewer_rejection_history for the current checkpoint type and sees
    if any cause appears with cycle_number >= 2.
    
    Args:
        state: RunState with reviewer_rejection_history populated
    
    Returns:
        (should_trigger, trigger_id) tuple. Trigger fires if any cause
        has cycle_number >= 2 for the current checkpoint.
    """
    # Need at least 2 rejections for same checkpoint to trigger
    if len(state.reviewer_rejection_history) < 2:
        return False, TRIGGER_SAME_ROOT_CAUSE
    
    # Group by checkpoint and cause
    from collections import defaultdict
    cause_counts: dict[tuple[CheckpointType, str], int] = defaultdict(int)
    
    for record in state.reviewer_rejection_history:
        key = (record.checkpoint, record.cause.strip().lower())
        cause_counts[key] += 1
    
    # Check if any cause appears twice for the same checkpoint
    for (checkpoint, cause), count in cause_counts.items():
        if count >= 2:
            return True, TRIGGER_SAME_ROOT_CAUSE
    
    return False, TRIGGER_SAME_ROOT_CAUSE


def check_scope_violation(error: Exception | None = None) -> tuple[bool, str]:
    """Check trigger #2: Out-of-scope write attempt.
    
    This is defense-in-depth. The primary enforcement is structural
    (ADR-002: nodes don't have tools for out-of-scope paths), so this
    trigger catching an actual ScopeViolationError means the structural
    enforcement was bypassed somehow. Still worth surfacing.
    
    Args:
        error: Exception that was raised (if any)
    
    Returns:
        (should_trigger, trigger_id) tuple. Trigger fires if error
        is a ScopeViolationError.
    """
    if error is not None and isinstance(error, ScopeViolationError):
        return True, TRIGGER_SCOPE_VIOLATION
    return False, TRIGGER_SCOPE_VIOLATION


def check_budget_exceeded_trigger(state: RunState) -> tuple[bool, str]:
    """Check trigger #3: Budget exceeded.
    
    Delegates to state.budget.exceeded(). Budget is tracked per graph
    run, not per node.
    
    Args:
        state: RunState with budget field
    
    Returns:
        (should_trigger, trigger_id) tuple. Trigger fires if
        budget.used >= budget.limit.
    """
    if state.budget.exceeded():
        return True, TRIGGER_BUDGET_EXCEEDED
    return False, TRIGGER_BUDGET_EXCEEDED


def check_role_definition_edit(
    old_state: RunState | None,
    new_state: RunState
) -> tuple[bool, str]:
    """Check trigger #4: Role-definition edit.
    
    Fires if any modification to Battalion role/node definitions is detected.
    In v1, this means checking if write_scope has changed between states.
    
    Args:
        old_state: Previous RunState (None if this is the first state)
        new_state: Current RunState
    
    Returns:
        (should_trigger, trigger_id) tuple. Trigger fires if write_scope
        differs between old and new state.
    """
    if old_state is None:
        return False, TRIGGER_ROLE_EDIT
    
    if old_state.write_scope != new_state.write_scope:
        return True, TRIGGER_ROLE_EDIT
    
    return False, TRIGGER_ROLE_EDIT


def check_infra_failure(error: Exception | None = None) -> tuple[bool, str]:
    """Check trigger #5: Infra failure.
    
    Distinct handling path — not folded into triggers #1 or #3.
    Fires when a node crash, malformed state, or LiteLLM call fails after
    retries (InfraFailure exception), or a role returns malformed, empty, or
    contract-violating provider output (RoleOutputError).
    
    Args:
        error: Exception that was raised (if any)
    
    Returns:
        (should_trigger, trigger_id) tuple. Trigger fires if error
        is an InfraFailure or RoleOutputError.
    """
    if error is not None and isinstance(error, (InfraFailure, RoleOutputError)):
        return True, TRIGGER_INFRA_FAILURE
    return False, TRIGGER_INFRA_FAILURE


def check_manual_checkpoint(state: RunState, next_phase: str) -> tuple[bool, str]:
    """Check trigger #6: Manual checkpoint.
    
    User declares a checkpoint on the ticket/run config. Graph pauses
    unconditionally at the declared point, regardless of whether any
    other trigger fired.
    
    Args:
        state: RunState with manual_checkpoints list
        next_phase: The phase the graph is about to transition to
    
    Returns:
        (should_trigger, trigger_id) tuple. Trigger fires if next_phase
        is in state.manual_checkpoints.
    """
    if next_phase in state.manual_checkpoints:
        return True, TRIGGER_MANUAL_CHECKPOINT
    return False, TRIGGER_MANUAL_CHECKPOINT


def check_any_trigger(
    state: RunState,
    old_state: RunState | None = None,
    error: Exception | None = None,
    next_phase: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Check all 6 triggers and return the first one that fires.
    
    This is the main entry point for interrupt checking. It evaluates
    all triggers in priority order and returns the first matching one.
    
    Priority order (first match wins):
    1. Manual checkpoint (user-declared, always takes precedence)
    2. Infra failure (explicit error condition)
    3. Scope violation (should never happen, but defense-in-depth)
    4. Budget exceeded
    5. Same root cause twice
    6. Role-definition edit
    
    Args:
        state: Current RunState
        old_state: Previous RunState (for role-definition edit check)
        error: Exception that occurred (if any)
        next_phase: Phase graph is transitioning to (for manual checkpoint)
    
    Returns:
        (should_trigger, trigger_id, context) tuple.
        - should_trigger: True if any trigger fires
        - trigger_id: The identifier of the firing trigger
        - context: Additional context dict for the interrupt log
    """
    # Priority 1: Manual checkpoint (user always wins)
    if next_phase is not None:
        fired, trigger_id = check_manual_checkpoint(state, next_phase)
        if fired:
            return True, trigger_id, {"phase": next_phase}
    
    # Priority 2: Infra failure (explicit error)
    if error is not None:
        fired, trigger_id = check_infra_failure(error)
        if fired:
            return True, trigger_id, {"error": str(error)}
        
        fired, trigger_id = check_scope_violation(error)
        if fired:
            return True, trigger_id, {"error": str(error)}
    
    # Priority 3: Budget exceeded
    fired, trigger_id = check_budget_exceeded_trigger(state)
    if fired:
        return True, trigger_id, {
            "used": state.budget.used,
            "limit": state.budget.limit,
        }
    
    # Priority 4: Same root cause twice
    fired, trigger_id = check_reviewer_rejection(state)
    if fired:
        # Find the latest rejection for context
        last_rejection = state.reviewer_rejection_history[-1]
        return True, trigger_id, {
            "cause": last_rejection.cause,
            "checkpoint": last_rejection.checkpoint,
            "cycle_number": last_rejection.cycle_number,
        }
    
    # Priority 5: Role-definition edit
    if old_state is not None:
        fired, trigger_id = check_role_definition_edit(old_state, state)
        if fired:
            return True, trigger_id, {
                "old_write_scope": old_state.write_scope,
                "new_write_scope": state.write_scope,
            }
    
    return False, "", {}


def log_interrupt(
    state: RunState,
    trigger_id: str,
    context: dict[str, Any] | None = None,
) -> RunState:
    """Log an interrupt trigger firing to the state's interrupt_log.
    
    Also sets status to AWAITING_HUMAN, as the graph should pause.
    
    Args:
        state: Current RunState
        trigger_id: The trigger identifier (from TRIGGER_* constants)
        context: Additional context to store in the log entry
    
    Returns:
        New RunState with interrupt logged and status set to AWAITING_HUMAN
    """
    from battalion.state.models import InterruptLogEntry
    
    entry = InterruptLogEntry(
        trigger=trigger_id,
        timestamp=datetime.now(timezone.utc),
        resolution=None,
        context=context or {},
    )
    
    new_log = state.interrupt_log + [entry]
    
    return state.model_copy(update={
        "status": RunStatus.AWAITING_HUMAN,
        "interrupt_log": new_log,
    })

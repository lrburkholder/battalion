"""Interrupt trigger implementations for Battalion.

See spec.md Interrupt Taxonomy (v1) for the 6 trigger definitions.
"""

from battalion.interrupts.budget import BudgetExceededError, increment_budget
from battalion.interrupts.triggers import (
    check_manual_checkpoint,
    check_budget_exceeded_trigger,
    check_infra_failure,
    check_reviewer_rejection,
    check_scope_violation,
    check_role_definition_edit,
    check_any_trigger,
    get_trigger_name,
)

__all__ = [
    "BudgetExceededError",
    "increment_budget",
    "check_manual_checkpoint",
    "check_budget_exceeded_trigger",
    "check_infra_failure",
    "check_reviewer_rejection",
    "check_scope_violation",
    "check_role_definition_edit",
    "check_any_trigger",
    "get_trigger_name",
]

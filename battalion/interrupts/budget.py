"""Per-graph-run budget tracking (BTN-7, spec.md interrupt trigger #3).

Budget is tracked per graph run (whole ticket), not per node. When budget
is exceeded, interrupt trigger #3 fires, pausing the graph.
"""
from __future__ import annotations

from battalion.state.models import RunState


class BudgetExceededError(Exception):
    """Raised when the per-graph-run budget has been exceeded.
    
    This is distinct from other exceptions — it's a business rule violation
    that should trigger interrupt #3, not crash the graph.
    """
    def __init__(self, used: int, limit: int):
        self.used = used
        self.limit = limit
        super().__init__(
            f"Budget exceeded: {used} >= {limit}"
        )


def increment_budget(state: RunState, amount: int = 1) -> RunState:
    """Increment the budget used count by the given amount (default 1).
    
    Returns a new state with budget.used incremented. Does NOT check if
    budget is exceeded — that's the caller's responsibility (or use
    check_budget_exceeded).
    
    Args:
        state: The current run state
        amount: How much to increment (default 1, for one LLM call/turn)
    
    Returns:
        New RunState with budget.used += amount
    """
    new_budget = state.budget.model_copy(update={
        "used": state.budget.used + amount
    })
    return state.model_copy(update={"budget": new_budget})


def check_budget_exceeded(state: RunState) -> bool:
    """Check if the per-graph-run budget has been exceeded.
    
    Trigger #3: Budget exceeded — tracked per graph run (whole ticket),
    not per node. When exceeded, graph pauses and shows spend/turns so
    far, asking to continue/adjust/stop.
    
    Args:
        state: The current run state
    
    Returns:
        True if budget.used >= budget.limit, False otherwise
    """
    return state.budget.exceeded()

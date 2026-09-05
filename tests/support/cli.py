"""CLI paused-run scenario shared by command and status tests."""


from support.state import make_run_state
from battalion.state.models import RunState, RunStatus


def make_paused_state(run_id: str, phase: str = "driver_red") -> RunState:
    """Create a state that's paused at an interrupt."""
    return make_run_state(
        run_id=run_id,
        ticket_id='BTN-9-test',
        status=RunStatus.AWAITING_HUMAN,
        phase=phase,
        write_scope={'architect': ['plan.md'], 'driver': ['src/'], 'reviewer': []},
        budget_used=10,
    )

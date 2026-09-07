"""Hermetic graph scenarios at the role-runner integration boundary."""
from contextlib import ExitStack, contextmanager
from unittest.mock import patch
from typing import Any

from battalion.graph import build_graph, resume_ticket
from battalion.state.models import CheckpointType, RunState, RunStatus
from support.state import make_llm_configs

# --- Mocked role runners -----------------------------------------------------
#
# The default fakes keep an invocation hermetic: whatever the routing does,
# a test never falls through into a real node implementation or pytest run.


_NODE_RUNNERS = {
    "architect": "battalion.nodes.architect.run_architect",
    "driver": "battalion.nodes.driver.run_driver",
    "reviewer": "battalion.nodes.reviewer.run_reviewer",
    "refactorer": "battalion.nodes.refactorer.run_refactorer",
}

CHECKPOINT_ACCEPT_PHASES = {
    CheckpointType.RED_CHECK: "driver_green",
    CheckpointType.GREEN_CHECK: "refactorer",
    CheckpointType.REFACTOR_CHECK: "done",
}


def architect_advancing(record=None):
    def fake(state, spec_text, llm_config, base_dir, prompts_dir=None):
        if record is not None:
            record.append("architect")
        return state.model_copy(update={"phase": "driver"})
    return fake


def driver_advancing(record=None):
    def fake(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
        if record is not None:
            record.append(f"driver_{mode}")
        return state.model_copy(update={"phase": "reviewer"})
    return fake


def refactorer_advancing(record=None):
    def fake(state, refactor_text, llm_config, base_dir, prompts_dir=None):
        if record is not None:
            record.append("refactorer")
        return state.model_copy(update={"phase": "reviewer"})
    return fake


def reviewer_with_phases(phase_for_checkpoint, record=None):
    """Reviewer fake whose verdict per checkpoint maps to a next phase."""
    def fake(
        state, base_dir, llm_config, checkpoint, prompts_dir=None,
        test_timeout_seconds=300.0,
    ):
        if record is not None:
            record.append(f"reviewer_{checkpoint.value}")
        phase = phase_for_checkpoint[checkpoint]
        status = RunStatus.DONE if phase == "done" else RunStatus.IN_PROGRESS
        return state.model_copy(update={"phase": phase, "status": status})
    return fake


def reviewer_accepting(record=None):
    return reviewer_with_phases(dict(CHECKPOINT_ACCEPT_PHASES), record=record)


def reviewer_rejecting(checkpoint: CheckpointType, record=None):
    """Accept earlier checkpoints and repeatedly reject the named checkpoint.

    This is a routing fake; real rejection evidence is tested at the Reviewer
    and end-to-end boundaries. Repeated rejection deliberately exhausts the
    scenario's explicit graph recursion limit.
    """
    retry_phases = {
        CheckpointType.RED_CHECK: "driver_red",
        CheckpointType.GREEN_CHECK: "driver_green",
        CheckpointType.REFACTOR_CHECK: "refactorer",
    }
    phases = dict(CHECKPOINT_ACCEPT_PHASES)
    phases[checkpoint] = retry_phases[checkpoint]
    return reviewer_with_phases(phases, record=record)


_DEFAULT_FAKES = {
    "architect": architect_advancing,
    "driver": driver_advancing,
    "reviewer": reviewer_accepting,
    "refactorer": refactorer_advancing,
}


@contextmanager
def patched_nodes(*, record: list[str] | None = None, **fakes):
    """Patch role runners for one graph invocation.

    Unspecified roles receive hermetic advancing/accepting fakes so partial
    patches cannot leak into real node implementations.
    """
    unknown = fakes.keys() - _NODE_RUNNERS.keys()
    if unknown:
        raise ValueError(f"Unknown role runners: {sorted(unknown)}")
    with ExitStack() as stack:
        for name, target in _NODE_RUNNERS.items():
            supplied = fakes.pop(name, None)
            fake = supplied if supplied is not None else _DEFAULT_FAKES[name](record)
            stack.enter_context(patch(target, side_effect=fake))
        yield


def invoke_graph(
    initial_state: RunState,
    base_dir,
    *,
    configs: dict | None = None,
    recursion_limit: int = 10,
    on_state_checkpoint=None,
    on_node_event=None,
    record: list[str] | None = None,
    **fakes,
) -> dict[str, Any]:
    """Build, compile, and invoke the graph with mocked role runners."""
    with patched_nodes(record=record, **fakes):
        app = build_graph(
            make_llm_configs() if configs is None else configs,
            base_dir=base_dir,
            on_state_checkpoint=on_state_checkpoint,
            on_node_event=on_node_event,
        ).compile()
        return app.invoke(initial_state, {"recursion_limit": recursion_limit})


def resume_graph(
    paused_state: RunState,
    base_dir,
    *,
    configs: dict | None = None,
    max_turns: int = 5,
    **fakes,
) -> RunState:
    """Resume a paused state through the canonical resume path."""
    with patched_nodes(**fakes):
        return resume_ticket(
            paused_state, make_llm_configs() if configs is None else configs,
            base_dir=base_dir, max_turns=max_turns,
        )

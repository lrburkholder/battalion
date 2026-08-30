"""Shared helpers for Battalion's graph-pipeline test modules.

Plain importable functions rather than fixtures (pytest puts ``tests/`` on
``sys.path``), so existing keyword-style call sites keep working unchanged:

    from conftest import make_llm_configs, make_run_state, invoke_graph

The state builders here are the single source of ``RunState`` construction
defaults; per-module wrappers only supply their ticket identity.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from battalion.graph import build_graph, resume_ticket
from battalion.llm.litellm_client import NodeLLMConfig
from battalion.state.models import Budget, CheckpointType, RunState, RunStatus

DEFAULT_WRITE_SCOPE = {
    "architect": ["plan.md"],
    "driver": ["src/"],
    "reviewer": [],
}


def make_llm_configs(model: str = "test-model") -> dict:
    """Per-role configs for graph tests. Provider calls stay mocked."""
    return {
        role: NodeLLMConfig(model=model, max_retries=0)
        for role in ("default", "architect", "driver", "reviewer", "refactorer")
    }


def make_run_state(
    *,
    ticket_id: str = "BTN-test",
    run_id: str | None = None,
    status: RunStatus = RunStatus.NOT_STARTED,
    phase: str = "architect",
    spec: str | None = None,
    write_scope: dict | None = None,
    budget_used: int = 0,
    budget_limit: int = 100,
    rejection_history: list | None = None,
    manual_checkpoints: list | None = None,
    **overrides,
) -> RunState:
    """Canonical RunState builder with overridable identity and knobs."""
    fields = dict(
        schema_version=overrides.pop("schema_version", "1.0"),
        run_id=run_id or f"run-{ticket_id}",
        ticket_id=ticket_id,
        status=status,
        phase=phase,
        write_scope=dict(DEFAULT_WRITE_SCOPE) if write_scope is None else write_scope,
        retry_bound=2,
        budget=Budget(limit=budget_limit, used=budget_used),
        reviewer_rejection_history=list(rejection_history or []),
        interrupt_log=[],
        manual_checkpoints=list(manual_checkpoints or []),
    )
    if spec is not None:
        fields["spec"] = spec
    fields.update(overrides)
    return RunState(**fields)


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


_DEFAULT_FAKES = {
    "architect": architect_advancing,
    "driver": driver_advancing,
    "reviewer": reviewer_accepting,
    "refactorer": refactorer_advancing,
}


@contextmanager
def patched_nodes(**fakes):
    """Patch role runners for one graph invocation.

    Unspecified roles receive hermetic advancing/accepting fakes so partial
    patches cannot leak into real node implementations.
    """
    with ExitStack() as stack:
        for name, target in _NODE_RUNNERS.items():
            supplied = fakes.pop(name, None)
            fake = supplied if supplied is not None else _DEFAULT_FAKES[name]()
            stack.enter_context(patch(target, side_effect=fake))
        yield


def invoke_graph(
    initial_state: RunState,
    base_dir,
    *,
    configs: dict | None = None,
    recursion_limit: int = 10,
    on_state_checkpoint=None,
    **fakes,
) -> RunState:
    """Build, compile, and invoke the graph with mocked role runners."""
    with patched_nodes(**fakes):
        app = build_graph(
            configs or make_llm_configs(),
            base_dir=base_dir,
            on_state_checkpoint=on_state_checkpoint,
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
            paused_state, configs or make_llm_configs(),
            base_dir=base_dir, max_turns=max_turns,
        )

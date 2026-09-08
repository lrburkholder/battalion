"""Validated run/config builders, independent of pytest and graph execution."""
from copy import deepcopy
from pathlib import Path
from typing import Any

from battalion.llm.litellm_client import NodeLLMConfig
from battalion.state.models import Budget, RejectionRecord, RunState, RunStatus
from battalion.state.persistence import save_state


def make_llm_configs(
    model: str = "test-model", *, reviewer_model: str = "reviewer-test-model",
    **overrides: NodeLLMConfig,
) -> dict[str, NodeLLMConfig]:
    configs = {
        role: NodeLLMConfig(model=model, max_retries=0)
        for role in ("default", "architect", "driver", "refactorer")
    }
    configs["reviewer"] = NodeLLMConfig(model=reviewer_model, max_retries=0)
    unknown = overrides.keys() - configs.keys()
    if unknown:
        raise ValueError(f"Unknown model roles: {sorted(unknown)}")
    configs.update(deepcopy(overrides))
    return configs


def make_run_state(
    *, ticket_id: str = "BTN-test", run_id: str | None = None,
    status: RunStatus = RunStatus.NOT_STARTED, phase: str = "architect",
    spec: str | None = None, write_scope: dict[str, list[str]] | None = None,
    budget_used: int = 0, budget_limit: int = 100,
    rejection_history: list[RejectionRecord] | None = None,
    manual_checkpoints: list[str] | None = None, **overrides: Any,
) -> RunState:
    """Default legacy graph scaffolding; explicit overrides remain authoritative.

    Keep semantic handoff data at the call site rather than adding it here.
    Deep copying also isolates caller-owned nested Pydantic evidence models.
    """
    unknown = overrides.keys() - RunState.model_fields.keys()
    if unknown:
        raise TypeError(f"Unknown RunState overrides: {sorted(unknown)}")
    fields = dict(
        schema_version="1.0", run_id=f"run-{ticket_id}" if run_id is None else run_id,
        ticket_id=ticket_id, status=status, phase=phase,
        write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []}
        if write_scope is None else write_scope,
        retry_bound=2, budget=Budget(limit=budget_limit, used=budget_used),
        reviewer_rejection_history=[] if rejection_history is None else rejection_history,
        interrupt_log=[], manual_checkpoints=[] if manual_checkpoints is None else manual_checkpoints,
    )
    if spec is not None:
        fields["spec"] = spec
    fields.update(overrides)
    return RunState(**deepcopy(fields))


def persisted_checkpoint(path: Path, state: RunState) -> Path:
    """Write through real persistence; callers choose the state and destination."""
    save_state(state, path)
    return path

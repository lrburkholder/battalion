"""Transport-neutral application commands and queries.

This module is the supported boundary between Battalion presentation clients and
the graph/persistence authorities.  Clients may construct a complete ``RunState``
and request an operation, but they do not invoke LangGraph or mutate saved state.

Domain failures are raised as ``ApplicationError`` subclasses so a CLI, desktop
client, or other adapter can render them without depending on filesystem error
types.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from battalion.config import BattalionConfig
from battalion.execution import summarize_costs
from battalion.graph import resume_ticket, run_ticket
from battalion.state.models import RunState, RunStatus
from battalion.state.persistence import load_state, save_state


DEFAULT_STATE_DIR = Path(".battalion/state")
EventCallback = Callable[[dict[str, Any]], None]


class ApplicationError(Exception):
    """Base class for expected failures exposed to presentation clients."""


class InvalidRunId(ApplicationError):
    """Raised when a run ID cannot safely identify one state file."""


class RunNotFound(ApplicationError):
    """Raised when a requested run has no saved state."""

    def __init__(self, run_id: str, path: Path) -> None:
        self.run_id = run_id
        self.path = path
        super().__init__(f"No state file found at {path}")


class RunAlreadyExists(ApplicationError):
    """Raised when start would replace durable state without authorization."""

    def __init__(self, run_id: str, path: Path) -> None:
        self.run_id = run_id
        self.path = path
        super().__init__(
            f"State file already exists at {path}. Use explicit overwrite authorization."
        )


class StateReadFailed(ApplicationError):
    """Raised when saved state exists but cannot be loaded or validated."""

    def __init__(self, run_id: str, path: Path, cause: Exception) -> None:
        self.run_id = run_id
        self.path = path
        self.cause = cause
        super().__init__(f"Could not read state for {run_id} at {path}: {cause}")


@dataclass(frozen=True)
class StartRun:
    """Request execution and persistence of one caller-supplied initial state."""

    initial_state: RunState
    config: BattalionConfig
    overwrite: bool = False


@dataclass(frozen=True)
class ResumeRun:
    """Human-authorized request to continue one durable run."""

    run_id: str
    config: BattalionConfig


@dataclass(frozen=True)
class InspectRun:
    """Read-only request for current durable run evidence."""

    run_id: str


@dataclass(frozen=True)
class RunOperationResult:
    """Typed result returned by state-changing run operations."""

    run_id: str
    state_version: str
    state_path: Path
    state: RunState
    warning: str | None = None


@dataclass(frozen=True)
class RunInspection:
    """Typed read model derived entirely from authoritative saved state."""

    run_id: str
    state_version: str
    state_path: Path
    state: RunState
    costs: dict[str, object]


def state_path(run_id: str, state_dir: str | Path = DEFAULT_STATE_DIR) -> Path:
    """Return the state path for a single, non-path run identifier."""
    if not run_id or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
        raise InvalidRunId(f"Invalid run ID: {run_id!r}")
    return Path(state_dir) / f"{run_id}.json"


def start_run(
    command: StartRun,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    on_node_event: EventCallback | None = None,
    on_token: EventCallback | None = None,
    _execute: Callable[..., RunState | dict[str, Any]] | None = None,
) -> RunOperationResult:
    """Execute a new run through the graph and persist its resulting state."""
    initial_state = command.initial_state
    path = state_path(initial_state.run_id, state_dir)
    if path.exists() and not command.overwrite:
        raise RunAlreadyExists(initial_state.run_id, path)

    execute = _execute or run_ticket
    final_state = RunState.model_validate(
        execute(
            initial_state=initial_state,
            llm_configs=command.config.models,
            base_dir=command.config.base_dir,
            prompts_dir=command.config.prompts_dir,
            on_node_event=on_node_event,
            on_token=on_token,
        )
    )
    save_state(final_state, path)
    return RunOperationResult(
        run_id=final_state.run_id,
        state_version=final_state.schema_version,
        state_path=path,
        state=final_state,
    )


def resume_run(
    command: ResumeRun,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    on_node_event: EventCallback | None = None,
    on_token: EventCallback | None = None,
    _execute: Callable[..., RunState | dict[str, Any]] | None = None,
) -> RunOperationResult:
    """Load, resume through the canonical graph behavior, and save one run."""
    state = _load_run(command.run_id, state_dir)
    warning = None
    if state.status != RunStatus.AWAITING_HUMAN:
        warning = (
            f"Run status is '{state.status.value}', not 'awaiting-human'. Resuming anyway."
        )

    execute = _execute or resume_ticket
    final_state = RunState.model_validate(
        execute(
            state=state,
            llm_configs=command.config.models,
            base_dir=command.config.base_dir,
            prompts_dir=command.config.prompts_dir,
            on_node_event=on_node_event,
            on_token=on_token,
        )
    )
    path = state_path(command.run_id, state_dir)
    save_state(final_state, path)
    return RunOperationResult(
        run_id=final_state.run_id,
        state_version=final_state.schema_version,
        state_path=path,
        state=final_state,
        warning=warning,
    )


def inspect_run(
    query: InspectRun, *, state_dir: str | Path = DEFAULT_STATE_DIR
) -> RunInspection:
    """Load authoritative state and derive its current cost projection."""
    state = _load_run(query.run_id, state_dir)
    return RunInspection(
        run_id=state.run_id,
        state_version=state.schema_version,
        state_path=state_path(query.run_id, state_dir),
        state=state,
        costs=summarize_costs(state.execution_record),
    )


def _load_run(run_id: str, state_dir: str | Path) -> RunState:
    path = state_path(run_id, state_dir)
    if not path.exists():
        raise RunNotFound(run_id, path)
    try:
        return load_state(path)
    except (OSError, ValueError) as exc:
        raise StateReadFailed(run_id, path, exc) from exc

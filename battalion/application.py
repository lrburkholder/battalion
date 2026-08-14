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
from battalion.workers import (
    DEFAULT_WORKER_DIR,
    WorkerRecord,
    WorkerAlreadyActive as _WorkerAlreadyActive,
    WorkerLaunchFailed as _WorkerLaunchFailed,
    WorkerNotFound as _WorkerNotFound,
    cancel_worker as _cancel_worker,
    launch_worker,
    observe_worker as _observe_worker,
    reconnect_worker as _reconnect_worker,
)


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


class WorkerApplicationError(ApplicationError):
    """Base class for expected supervision failures at the application boundary."""


class WorkerNotFound(WorkerApplicationError):
    pass


class WorkerAlreadyActive(WorkerApplicationError):
    pass


class WorkerLaunchFailed(WorkerApplicationError):
    pass


class WorkerRecordReadFailed(WorkerApplicationError):
    def __init__(self, run_id: str, cause: Exception) -> None:
        self.run_id = run_id
        self.cause = cause
        super().__init__(f"Could not read worker record for {run_id}: {cause}")


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
class StartWorker:
    """Launch a supervised process for a start or resume command."""

    command: StartRun | ResumeRun


@dataclass(frozen=True)
class ObserveWorker:
    """Query one worker's durable lifecycle record."""

    run_id: str


@dataclass(frozen=True)
class CancelWorker:
    """Request cancellation of the worker associated with one run."""

    run_id: str


@dataclass(frozen=True)
class ReconnectWorker:
    """Reload a worker association after a presentation client restart."""

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
            on_state_checkpoint=lambda checkpoint: save_state(
                RunState.model_validate(checkpoint), path
            ),
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
    path = state_path(command.run_id, state_dir)
    final_state = RunState.model_validate(
        execute(
            state=state,
            llm_configs=command.config.models,
            base_dir=command.config.base_dir,
            prompts_dir=command.config.prompts_dir,
            on_node_event=on_node_event,
            on_token=on_token,
            on_state_checkpoint=lambda checkpoint: save_state(
                RunState.model_validate(checkpoint), path
            ),
        )
    )
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


def start_worker(
    command: StartWorker,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    worker_dir: str | Path = DEFAULT_WORKER_DIR,
) -> WorkerRecord:
    """Start one isolated process without exposing process handles to clients."""
    operation = command.command
    if isinstance(operation, StartRun):
        run_id = operation.initial_state.run_id
        path = state_path(run_id, state_dir)
        if path.exists() and not operation.overwrite:
            raise RunAlreadyExists(run_id, path)
        try:
            return launch_worker(
                operation="start",
                run_id=run_id,
                state=operation.initial_state,
                state_version=operation.initial_state.schema_version,
                config=operation.config,
                state_dir=state_dir,
                worker_dir=worker_dir,
            )
        except _WorkerAlreadyActive as exc:
            raise WorkerAlreadyActive(str(exc)) from exc
        except _WorkerLaunchFailed as exc:
            raise WorkerLaunchFailed(str(exc)) from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise WorkerRecordReadFailed(run_id, exc) from exc

    state_path(operation.run_id, state_dir)  # validate before using it as a filename
    state = _load_run(operation.run_id, state_dir)
    try:
        return launch_worker(
            operation="resume",
            run_id=operation.run_id,
            state=None,
            state_version=state.schema_version,
            config=operation.config,
            state_dir=state_dir,
            worker_dir=worker_dir,
        )
    except _WorkerAlreadyActive as exc:
        raise WorkerAlreadyActive(str(exc)) from exc
    except _WorkerLaunchFailed as exc:
        raise WorkerLaunchFailed(str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkerRecordReadFailed(operation.run_id, exc) from exc


def observe_worker(
    query: ObserveWorker, *, worker_dir: str | Path = DEFAULT_WORKER_DIR
) -> WorkerRecord:
    state_path(query.run_id)  # identifier validation is shared by all clients
    try:
        return _observe_worker(query.run_id, worker_dir=worker_dir)
    except _WorkerNotFound as exc:
        raise WorkerNotFound(str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkerRecordReadFailed(query.run_id, exc) from exc


def cancel_worker(
    command: CancelWorker, *, worker_dir: str | Path = DEFAULT_WORKER_DIR
) -> WorkerRecord:
    state_path(command.run_id)
    try:
        return _cancel_worker(command.run_id, worker_dir=worker_dir)
    except _WorkerNotFound as exc:
        raise WorkerNotFound(str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkerRecordReadFailed(command.run_id, exc) from exc


def reconnect_worker(
    command: ReconnectWorker, *, worker_dir: str | Path = DEFAULT_WORKER_DIR
) -> WorkerRecord:
    state_path(command.run_id)
    try:
        return _reconnect_worker(command.run_id, worker_dir=worker_dir)
    except _WorkerNotFound as exc:
        raise WorkerNotFound(str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkerRecordReadFailed(command.run_id, exc) from exc


def _load_run(run_id: str, state_dir: str | Path) -> RunState:
    path = state_path(run_id, state_dir)
    if not path.exists():
        raise RunNotFound(run_id, path)
    try:
        return load_state(path)
    except (OSError, ValueError) as exc:
        raise StateReadFailed(run_id, path, exc) from exc

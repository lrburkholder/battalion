"""Transport-neutral application commands and queries.

This module is the supported boundary between Battalion presentation clients and
the graph/persistence authorities.  Clients may construct a complete ``RunState``
and request an operation, but they do not invoke LangGraph or mutate saved state.

Domain failures are raised as ``ApplicationError`` subclasses so a CLI, desktop
client, or other adapter can render them without depending on filesystem error
types.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from battalion.actors import (
    Actor,
    ActorError,
    ActorKind,
    ActorRegistry,
    ActorStatus,
    ExternalIdentity,
    bootstrap_local_actor,
    get_actor,
    get_external_identity as _get_external_identity,
    get_local_actor,
    link_external_identity as _link_external_identity,
    load_actor_registry,
    resolve_external_actor as _resolve_external_actor,
    unlink_external_identity as _unlink_external_identity,
)
from battalion.config import BattalionConfig
from battalion.execution import summarize_costs
from battalion.identity import (
    IdentityError,
    ProjectIdentity,
    ProjectRunCatalog,
    RunCatalogEntry,
    generate_run_identity,
    is_canonical_run_id,
    load_project_identity,
    load_run_catalog,
    register_run,
)
from battalion.integrations.events import OutboundEventPublisher, events_for_state
from battalion.integrations.runtime import IntegrationError, IntegrationRuntime, WorkSourcePort
from battalion.intel.candidates import (
    CandidateInbox,
    CandidateInboxEntry,
    CandidateNotFoundError,
    CandidateRepository,
)
from battalion.work import WorkItem
from battalion.intel.models import AcceptedInstinct, CandidateInstinct
from battalion.intel.repository import IntelRepository
from battalion.intel.review import (
    DecisionAlreadyRecordedError,
    InstinctDecisionRepository,
    InstinctReviewDecision,
    InstinctReviewWorkflow,
    ReviewAction,
)
from battalion.observation import (
    ObservationCallback,
    ObservationCursor,
    ObservationSource,
    RunObservationPublisher,
)
from battalion.role_results import RoleResultKind
from battalion.role_results import RoleExecutionResult
from battalion.state.models import (
    Budget,
    HumanActionRecord,
    HumanIntervention,
    InterventionKind,
    InterventionTarget,
    RunState,
    RunStatus,
)
from battalion.state.persistence import load_state, save_state
from battalion.workflow_recipes import (
    DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    WorkflowRecipe,
    WorkflowRecipeRegistry,
)
from battalion.workflow_execution import (
    WorkflowCompletionEvidence,
    WorkflowExecutionState,
    WorkflowStageEvidence,
    WorkflowUpgradeTrigger,
    record_workflow_stage as _record_workflow_stage,
    start_workflow_execution as _start_workflow_execution,
    upgrade_workflow_execution as _upgrade_workflow_execution,
    upgrade_for_driver_result as _upgrade_for_driver_result,
    record_workflow_completion as _record_workflow_completion,
    workflow_is_complete as _workflow_is_complete,
)
from battalion.workflow_admission import (
    AdmissionEvidenceSource,
    DEFAULT_WORKFLOW_ADMISSION_POLICY,
    WorkflowAdmissionAssessment,
    WorkflowAdmissionEvidence,
    WorkflowAdmissionOutcome,
    WorkflowAdmissionPolicy,
    assess_workflow_admission as _assess_workflow_admission,
)
from battalion.workflow_admission_decisions import (
    WorkflowAdmissionDecision,
    WorkflowAdmissionDisposition,
)
from battalion.tactician import (
    TacticianAssessment,
    TacticianAssessmentInput,
    run_tactician as _run_tactician,
)
from battalion.workers import (
    DEFAULT_WORKER_DIR,
    WorkerRecord,
    WorkerAlreadyActive as _WorkerAlreadyActive,
    WorkerLaunchFailed as _WorkerLaunchFailed,
    WorkerNotFound as _WorkerNotFound,
    cancel_worker as _cancel_worker,
    inactive_worker_guard as _inactive_worker_guard,
    launch_worker,
    observe_worker as _observe_worker,
    reconnect_worker as _reconnect_worker,
)


DEFAULT_STATE_DIR = Path(".battalion/state")
EventCallback = Callable[[dict[str, Any]], None]


def run_ticket(*args, **kwargs):
    """Load the graph authority only when a state-changing operation requests it."""
    from battalion.graph import run_ticket as execute

    return execute(*args, **kwargs)


def resume_ticket(*args, **kwargs):
    """Load resume policy only when a state-changing operation requests it."""
    from battalion.graph import resume_ticket as execute

    return execute(*args, **kwargs)


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


class IdentityApplicationError(ApplicationError):
    """Raised when run/project identity evidence cannot be reconciled."""


class ProjectReadFailed(ApplicationError):
    """Raised when a project cannot be opened as authoritative local data."""

    def __init__(self, project_root: Path, cause: Exception) -> None:
        self.project_root = project_root
        self.cause = cause
        super().__init__(f"Could not read project at {project_root}: {cause}")


class IntelReadFailed(ApplicationError):
    """Raised when persisted Intel or Recon evidence cannot be validated."""

    def __init__(self, project_root: Path, cause: Exception) -> None:
        self.project_root = project_root
        self.cause = cause
        super().__init__(f"Could not read Intel for {project_root}: {cause}")


class ActorRegistryFailed(ApplicationError):
    """Raised when project Actor identity cannot be created or inspected."""

    def __init__(self, project_root: Path, cause: Exception) -> None:
        self.project_root = project_root
        self.cause = cause
        super().__init__(f"Could not access Actors for {project_root}: {cause}")


class HumanActionRejected(ApplicationError):
    """Raised when a requested human action violates durable authority policy."""


class WorkflowAdmissionRejected(ApplicationError):
    """Raised when a human workflow-admission choice is not currently valid."""


class StaleWorkflowAdmission(WorkflowAdmissionRejected):
    """Raised when an assessment no longer matches the supplied current evidence/policy."""


class CandidateReviewFailed(ApplicationError):
    """Raised when candidate review cannot complete through the canonical workflow."""


class RunIdentityChanged(IdentityApplicationError):
    """Raised when graph execution attempts to replace canonical identity."""


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
class StartWorkItemRun:
    """Start one Run from a configured WorkSource read operation.

    ``ticket_id`` remains Battalion-owned display context. The normalized
    ``WorkItem`` preserves external identity separately in durable state.
    """

    ticket_id: str
    integration_name: str
    external_id: str
    config: BattalionConfig
    overwrite: bool = False


@dataclass(frozen=True)
class ResumeRun:
    """Human-authorized request to continue one durable run."""

    run_id: str
    config: BattalionConfig
    actor_id: UUID | None = None
    resolution: str = "authorized resume"


@dataclass(frozen=True)
class QueueIntervention:
    """Queue bounded context for one exact target-node attempt."""

    run_id: str
    kind: InterventionKind
    target: InterventionTarget
    text: str
    project_root: str | Path = "."
    actor_id: UUID | None = None


@dataclass(frozen=True)
class ReviewCandidate:
    """Human-authorized Recon candidate decision."""

    project_root: str | Path
    candidate_id: str
    action: ReviewAction
    actor_id: UUID | None = None
    edits: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class InspectRun:
    """Read-only request for current durable run evidence."""

    run_id: str


@dataclass(frozen=True)
class InspectProject:
    """Read-only request for a project's saved-run catalog."""

    project_root: str | Path


@dataclass(frozen=True)
class InspectIntel:
    """Read-only request for accepted Intel and immutable Recon candidates."""

    project_root: str | Path


@dataclass(frozen=True)
class ListWorkflowRecipes:
    """Read-only request for Battalion-owned registered workflow recipes."""


@dataclass(frozen=True)
class InspectWorkflowRecipe:
    """Read one exact versioned recipe without graph or storage access."""

    recipe_id: str
    recipe_version: str


@dataclass(frozen=True)
class StartWorkflowExecution:
    """Initialize execution from one exact admitted WorkflowRecipe."""

    recipe_id: str
    recipe_version: str


@dataclass(frozen=True)
class RecordWorkflowStage:
    """Retain behavioral or review evidence for one selected-recipe stage."""

    execution: WorkflowExecutionState
    evidence: WorkflowStageEvidence


@dataclass(frozen=True)
class RecordWorkflowCompletion:
    """Retain required related-review or human-acceptance evidence."""

    execution: WorkflowExecutionState
    evidence: WorkflowCompletionEvidence


@dataclass(frozen=True)
class InspectWorkflowCompletion:
    """Determine whether a recipe has all its required execution evidence."""

    execution: WorkflowExecutionState


@dataclass(frozen=True)
class UpgradeWorkflowExecution:
    """Apply the irreversible compact-to-stronger-workflow policy."""

    execution: WorkflowExecutionState
    trigger: WorkflowUpgradeTrigger
    reason: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class UpgradeWorkflowForDriverResult:
    """Apply deterministic compact policy to one validated Driver outcome."""

    execution: WorkflowExecutionState
    result: RoleExecutionResult
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssessWorkflowAdmission:
    """Create a deterministic pre-admission assessment from bounded evidence."""

    evidence: WorkflowAdmissionEvidence


@dataclass(frozen=True)
class AssessTactician:
    """Request an advisory assessment for deterministic admission uncertainty."""

    assessment_input: TacticianAssessmentInput
    config: BattalionConfig


@dataclass(frozen=True)
class InspectWorkflowAdmissionPolicy:
    """Read the versioned deterministic admission policy without executing it."""


@dataclass(frozen=True)
class InspectWorkflowAdmission:
    """Inspect current admission evidence and the choices it can support."""

    assessment: WorkflowAdmissionAssessment
    evidence: WorkflowAdmissionEvidence
    tactician_assessment: TacticianAssessment | None = None


@dataclass(frozen=True)
class DecideWorkflowAdmission:
    """Record an authorized human choice from current admission evidence."""

    project_root: str | Path
    assessment: WorkflowAdmissionAssessment
    evidence: WorkflowAdmissionEvidence
    disposition: WorkflowAdmissionDisposition
    actor_id: UUID | None = None
    tactician_assessment: TacticianAssessment | None = None
    recipe_id: str | None = None
    recipe_version: str | None = None
    annotation: str | None = None


@dataclass(frozen=True)
class BootstrapLocalActor:
    """Establish the first explicit human Actor for a local project."""

    project_root: str | Path
    display_name: str


@dataclass(frozen=True)
class InspectActors:
    """Read-only request for a project's durable Actor identities."""

    project_root: str | Path


@dataclass(frozen=True)
class LinkExternalIdentity:
    """Link one provider subject to an existing durable Actor."""

    project_root: str | Path
    actor_id: UUID
    integration_id: str
    provider: str
    external_subject: str
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class UnlinkExternalIdentity:
    """Remove one integration-scoped external identity mapping."""

    project_root: str | Path
    integration_id: str
    external_subject: str


@dataclass(frozen=True)
class ResolveExternalIdentity:
    """Resolve an external caller to an Actor without authorizing an operation."""

    project_root: str | Path
    integration_id: str
    external_subject: str


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
class ReconnectObservation:
    """Request an authoritative snapshot before resuming one live stream."""

    run_id: str
    stream_id: UUID


@dataclass(frozen=True)
class ObservationSnapshot:
    """Durable baseline and live cursor returned in safe consumption order."""

    inspection: RunInspection
    cursor: ObservationCursor


@dataclass(frozen=True)
class RunOperationResult:
    """Typed result returned by state-changing run operations."""

    run_id: str
    run_alias: str | None
    state_version: str
    state_path: Path
    state: RunState
    warning: str | None = None


@dataclass(frozen=True)
class RunInspection:
    """Typed read model derived entirely from authoritative saved state."""

    run_id: str
    run_alias: str | None
    state_version: str
    state_path: Path
    state: RunState
    costs: dict[str, object]


@dataclass(frozen=True)
class ProjectRunInspection:
    """One catalog entry plus either validated state or an explicit limitation."""

    catalog_entry: RunCatalogEntry
    availability: Literal["available", "malformed", "inaccessible"]
    inspection: RunInspection | None = None
    limitation: str | None = None


@dataclass(frozen=True)
class ProjectInspection:
    """Read-only project and run projection used by presentation clients."""

    project_root: Path
    identity: ProjectIdentity
    runs: tuple[ProjectRunInspection, ...]


@dataclass(frozen=True)
class IntelInspection:
    """Validated knowledge evidence without any review or mutation operations."""

    accepted: tuple[AcceptedInstinct, ...]
    candidates: tuple[CandidateInstinct, ...]
    candidate_entries: tuple[CandidateInboxEntry, ...] = ()


@dataclass(frozen=True)
class ActorInspection:
    """Validated Actor projection exposed through the shared query boundary."""

    actors: tuple[Actor, ...]
    local_actor: Actor | None
    registry: ActorRegistry


@dataclass(frozen=True)
class ExternalIdentityResolution:
    """The durable mapping and Actor identified by an external caller."""

    identity: ExternalIdentity
    actor: Actor


@dataclass(frozen=True)
class WorkflowAdmissionInspection:
    """Current decision surface derived from pre-admission evidence only."""

    assessment: WorkflowAdmissionAssessment
    tactician_assessment: TacticianAssessment | None
    available_dispositions: tuple[WorkflowAdmissionDisposition, ...]
    compact_unavailable_reason: str | None = None


@dataclass(frozen=True)
class WorkflowAdmissionDecisionResult:
    """An authorized admission choice and any exact recipe it selected."""

    decision: WorkflowAdmissionDecision
    recipe: WorkflowRecipe | None


@dataclass(frozen=True)
class HumanActionResult:
    """Durable run state produced by an authorized human action."""

    action: HumanActionRecord
    state: RunState
    state_path: Path


@dataclass(frozen=True)
class CandidateReviewResult:
    """Durable candidate decision and its resulting disposition."""

    decision: InstinctReviewDecision
    disposition: Literal["promoted", "rejected"]


def state_path(run_id: str, state_dir: str | Path = DEFAULT_STATE_DIR) -> Path:
    """Return the state path for a single, non-path run identifier."""
    if not run_id or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
        raise InvalidRunId(f"Invalid run ID: {run_id!r}")
    return Path(state_dir) / f"{run_id}.json"


def _validate_work_item_source(
    work_source: WorkSourcePort, work_item: WorkItem, external_id: str
) -> None:
    """Reject malformed adapter results before durable RunState is created."""

    if work_source.integration_id != work_item.source_integration_id:
        raise ApplicationError(
            "WorkSource returned a work item for a different integration"
        )
    if work_item.external_id != external_id:
        raise ApplicationError("WorkSource returned a work item with a different external ID")


def create_initial_state(
    ticket_id: str,
    spec: str,
    config: BattalionConfig,
    *,
    work_item: WorkItem | None = None,
) -> RunState:
    """Create one canonical new-run state through the application boundary."""
    try:
        project = load_project_identity(config.base_dir, create=True)
        _resolve_human_actor(config.base_dir, None)
    except (IdentityError, OSError) as exc:
        raise IdentityApplicationError(str(exc)) from exc
    identity = generate_run_identity(ticket_id)
    return RunState(
        schema_version="1.0",
        run_id=identity.run_id,
        run_alias=identity.display_alias,
        project_id=str(project.project_id),
        ticket_id=ticket_id,
        work_item=work_item,
        spec=spec,
        status=RunStatus.NOT_STARTED,
        phase="architect",
        write_scope=config.write_scope,
        retry_bound=2,
        budget=Budget(limit=config.budget_limit, used=0),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=config.manual_checkpoints,
    )


def start_work_item_run(
    command: StartWorkItemRun,
    *,
    integration_runtime: IntegrationRuntime,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    on_node_event: EventCallback | None = None,
    on_token: EventCallback | None = None,
    on_observation: ObservationCallback | None = None,
    _execute: Callable[..., RunState | dict[str, Any]] | None = None,
) -> RunOperationResult:
    """Retrieve a WorkItem then start through the normal application path.

    Provider selection occurs once at the application boundary. The graph
    receives only the durable snapshot in ``RunState`` and never a provider
    port, raw client, credential, or transport.
    """

    work_source = integration_runtime.work_source(command.integration_name)
    work_item = work_source.get(command.external_id)
    _validate_work_item_source(work_source, work_item, command.external_id)
    initial_state = create_initial_state(
        command.ticket_id,
        work_item.description,
        command.config,
        work_item=work_item,
    )
    return start_run(
        StartRun(
            initial_state=initial_state,
            config=command.config,
            overwrite=command.overwrite,
        ),
        state_dir=state_dir,
        integration_runtime=integration_runtime,
        on_node_event=on_node_event,
        on_token=on_token,
        on_observation=on_observation,
        _execute=_execute,
    )


def start_run(
    command: StartRun,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    integration_runtime: IntegrationRuntime | None = None,
    on_node_event: EventCallback | None = None,
    on_token: EventCallback | None = None,
    on_observation: ObservationCallback | None = None,
    _execute: Callable[..., RunState | dict[str, Any]] | None = None,
) -> RunOperationResult:
    """Execute a new run through the graph and persist its resulting state."""
    initial_state = command.initial_state
    path = state_path(initial_state.run_id, state_dir)
    if path.exists() and not command.overwrite:
        raise RunAlreadyExists(initial_state.run_id, path)

    _register_canonical_run(initial_state, command.config, path)

    execute = _execute or run_ticket
    publisher = (
        RunObservationPublisher(initial_state.run_id, on_observation)
        if on_observation is not None
        else None
    )
    node_callback, token_callback = _live_callbacks(
        publisher, on_node_event, on_token
    )

    def checkpoint(state: RunState | dict[str, Any]) -> None:
        validated = RunState.model_validate(state)
        save_state(validated, path)
        if publisher is not None:
            publisher.handle_checkpoint(validated)

    final_state = RunState.model_validate(
        execute(
            initial_state=initial_state,
            llm_configs=command.config.models,
            base_dir=command.config.base_dir,
            prompts_dir=command.config.prompts_dir,
            reviewer_test_timeout_seconds=command.config.reviewer_test_timeout_seconds,
            on_node_event=node_callback,
            on_token=token_callback,
            on_state_checkpoint=checkpoint,
        )
    )
    if final_state.run_id != initial_state.run_id:
        raise RunIdentityChanged(
            f"Run identity changed from {initial_state.run_id} to {final_state.run_id}."
        )
    save_state(final_state, path)
    _publish_durable_outbound_events(final_state, integration_runtime, path)
    return RunOperationResult(
        run_id=final_state.run_id,
        run_alias=final_state.run_alias,
        state_version=final_state.schema_version,
        state_path=path,
        state=final_state,
    )


def resume_run(
    command: ResumeRun,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    integration_runtime: IntegrationRuntime | None = None,
    on_node_event: EventCallback | None = None,
    on_token: EventCallback | None = None,
    on_observation: ObservationCallback | None = None,
    _execute: Callable[..., RunState | dict[str, Any]] | None = None,
) -> RunOperationResult:
    """Load, resume through the canonical graph behavior, and save one run."""
    state = _load_run(command.run_id, state_dir)
    actor = _resolve_human_actor(command.config.base_dir, command.actor_id)
    warning = None
    if state.status == RunStatus.AWAITING_HUMAN:
        state = _resolve_latest_interrupt(
            state,
            actor=actor,
            resolution=command.resolution,
        )
        save_state(state, state_path(command.run_id, state_dir))
    elif state.status == RunStatus.BLOCKED and _latest_blocked_role_result(state) is not None:
        state = _resolve_blocked_role_result(
            state,
            actor=actor,
            resolution=command.resolution,
        )
        save_state(state, state_path(command.run_id, state_dir))
    else:
        warning = (
            f"Run status is '{state.status.value}', not 'awaiting-human'. Resuming anyway."
        )

    execute = _execute or resume_ticket
    path = state_path(command.run_id, state_dir)
    publisher = (
        RunObservationPublisher(state.run_id, on_observation)
        if on_observation is not None
        else None
    )
    node_callback, token_callback = _live_callbacks(
        publisher, on_node_event, on_token
    )

    def checkpoint(checkpoint_state: RunState | dict[str, Any]) -> None:
        validated = RunState.model_validate(checkpoint_state)
        save_state(validated, path)
        if publisher is not None:
            publisher.handle_checkpoint(validated)

    final_state = RunState.model_validate(
        execute(
            state=state,
            llm_configs=command.config.models,
            base_dir=command.config.base_dir,
            prompts_dir=command.config.prompts_dir,
            reviewer_test_timeout_seconds=command.config.reviewer_test_timeout_seconds,
            on_node_event=node_callback,
            on_token=token_callback,
            on_state_checkpoint=checkpoint,
        )
    )
    if final_state.run_id != state.run_id:
        raise RunIdentityChanged(
            f"Run identity changed from {state.run_id} to {final_state.run_id}."
        )
    save_state(final_state, path)
    _publish_durable_outbound_events(final_state, integration_runtime, path)
    return RunOperationResult(
        run_id=final_state.run_id,
        run_alias=final_state.run_alias,
        state_version=final_state.schema_version,
        state_path=path,
        state=final_state,
        warning=warning,
    )


def _publish_durable_outbound_events(
    state: RunState,
    integration_runtime: IntegrationRuntime | None,
    path: Path,
) -> None:
    """Publish registered machine events after their authoritative state is saved.

    Event delivery is optional and one-way: a configured sink's rejection,
    timeout, or reconciliation requirement is captured in BTN-70 evidence but
    never rewrites the completed/paused Run outcome or grants the sink command
    authority. Unexpected programmer failures still surface after write-ahead
    intent is durable instead of being misclassified as a provider outcome.
    """

    if integration_runtime is None:
        return
    events = events_for_state(state)
    if not events:
        return
    try:
        sinks = integration_runtime.outbound_event_sinks()
    except IntegrationError:
        return

    publisher = OutboundEventPublisher(state)
    for sink in sinks:
        try:
            publisher.publish(
                events,
                sinks=(sink,),
                persist=lambda: save_state(state, path),
            )
        except IntegrationError:
            # Individual provider outcomes are durable evidence, not a graph
            # failure. Continue fan-out to independent configured consumers.
            continue


def inspect_run(
    query: InspectRun, *, state_dir: str | Path = DEFAULT_STATE_DIR
) -> RunInspection:
    """Load authoritative state and derive its current cost projection."""
    state = _load_run(query.run_id, state_dir)
    return RunInspection(
        run_id=state.run_id,
        run_alias=state.run_alias,
        state_version=state.schema_version,
        state_path=state_path(query.run_id, state_dir),
        state=state,
        costs=summarize_costs(state.execution_record),
    )


def inspect_project(query: InspectProject) -> ProjectInspection:
    """Discover saved runs without giving the caller filesystem authority."""
    root = Path(query.project_root).resolve()
    try:
        identity = load_project_identity(root)
        catalog = load_run_catalog(root)
    except (OSError, ValueError) as exc:
        raise ProjectReadFailed(root, exc) from exc

    entries = _discover_desktop_runs(root, catalog)
    runs = tuple(
        _inspect_catalog_entry(root, entry, identity) for entry in entries
    )
    return ProjectInspection(project_root=root, identity=identity, runs=runs)


def establish_local_actor(command: BootstrapLocalActor) -> ActorInspection:
    """Perform the one-time local Actor bootstrap through the application boundary."""
    root = Path(command.project_root).resolve()
    try:
        registry = bootstrap_local_actor(root, command.display_name)
    except (ActorError, IdentityError, OSError, ValueError, TypeError) as exc:
        raise ActorRegistryFailed(root, exc) from exc
    return _actor_inspection(registry)


def inspect_actors(query: InspectActors) -> ActorInspection:
    """Load Actors without exposing their project-local persistence path."""
    root = Path(query.project_root).resolve()
    try:
        registry = load_actor_registry(root)
    except (ActorError, IdentityError, OSError, ValueError, TypeError) as exc:
        raise ActorRegistryFailed(root, exc) from exc
    return _actor_inspection(registry)


def link_external_identity(command: LinkExternalIdentity) -> ActorInspection:
    """Persist a credential-free mapping through the application boundary."""
    root = Path(command.project_root).resolve()
    try:
        registry = _link_external_identity(
            root,
            actor_id=command.actor_id,
            integration_id=command.integration_id,
            provider=command.provider,
            external_subject=command.external_subject,
            metadata=dict(command.metadata or {}),
        )
    except (ActorError, IdentityError, OSError, ValueError, TypeError) as exc:
        raise ActorRegistryFailed(root, exc) from exc
    return _actor_inspection(registry)


def unlink_external_identity(command: UnlinkExternalIdentity) -> ActorInspection:
    """Remove a mapping through the shared application command boundary."""
    root = Path(command.project_root).resolve()
    try:
        registry = _unlink_external_identity(
            root, command.integration_id, command.external_subject
        )
    except (ActorError, IdentityError, OSError, ValueError, TypeError) as exc:
        raise ActorRegistryFailed(root, exc) from exc
    return _actor_inspection(registry)


def resolve_external_identity(
    query: ResolveExternalIdentity,
) -> ExternalIdentityResolution:
    """Resolve external identity without treating it as authorization evidence."""
    root = Path(query.project_root).resolve()
    try:
        identity = _get_external_identity(
            root, query.integration_id, query.external_subject
        )
        actor = _resolve_external_actor(
            root, query.integration_id, query.external_subject
        )
    except (ActorError, IdentityError, OSError, ValueError, TypeError) as exc:
        raise ActorRegistryFailed(root, exc) from exc
    return ExternalIdentityResolution(identity=identity, actor=actor)


def inspect_intel(query: InspectIntel) -> IntelInspection:
    """Load local Intel and Recon evidence through a read-only application query."""
    root = Path(query.project_root).resolve()
    try:
        accepted = IntelRepository(root / ".battalion" / "intel").list_all()
        candidate_repository = CandidateRepository(
            root / ".battalion" / "recon" / "candidates"
        )
        decision_repository = InstinctDecisionRepository(
            root / ".battalion" / "recon" / "decisions"
        )
        entries = CandidateInbox(
            candidate_repository, decision_repository
        ).list_all()
    except (OSError, ValueError, TypeError) as exc:
        raise IntelReadFailed(root, exc) from exc
    return IntelInspection(
        accepted=tuple(accepted),
        candidates=tuple(entry.candidate for entry in entries),
        candidate_entries=tuple(entries),
    )


def list_workflow_recipes(
    query: ListWorkflowRecipes,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> tuple[WorkflowRecipe, ...]:
    """Enumerate finite policy artifacts through the application boundary."""
    del query
    return registry.list()


def inspect_workflow_recipe(
    query: InspectWorkflowRecipe,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> WorkflowRecipe:
    """Inspect exact registered recipe semantics without selecting a graph."""
    return registry.resolve(query.recipe_id, query.recipe_version)


def start_workflow_execution(
    command: StartWorkflowExecution,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> WorkflowExecutionState:
    """Initialize exact registered recipe semantics through application policy."""
    return _start_workflow_execution(registry.resolve(command.recipe_id, command.recipe_version))


def record_workflow_stage(
    command: RecordWorkflowStage,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> WorkflowExecutionState:
    """Record stage evidence without presentation or graph mutation."""
    return _record_workflow_stage(command.execution, command.evidence, registry=registry)


def record_workflow_completion(
    command: RecordWorkflowCompletion,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> WorkflowExecutionState:
    """Record a bounded completion requirement through application policy."""
    return _record_workflow_completion(command.execution, command.evidence, registry=registry)


def workflow_is_complete(
    query: InspectWorkflowCompletion,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> bool:
    """Inspect completion without graph dispatch or persistence access."""
    return _workflow_is_complete(query.execution, registry=registry)


def upgrade_workflow_execution(
    command: UpgradeWorkflowExecution,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> WorkflowExecutionState:
    """Apply deterministic upgrade policy through the shared application boundary."""
    return _upgrade_workflow_execution(
        command.execution,
        trigger=command.trigger,
        reason=command.reason,
        evidence_ids=command.evidence_ids,
        registry=registry,
    )


def upgrade_workflow_for_driver_result(
    command: UpgradeWorkflowForDriverResult,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> WorkflowExecutionState:
    """Consume a typed Driver outcome without letting it select workflow policy."""
    return _upgrade_for_driver_result(
        command.execution,
        command.result,
        evidence_ids=command.evidence_ids,
        registry=registry,
    )


def assess_workflow_admission(
    command: AssessWorkflowAdmission,
    *,
    policy: WorkflowAdmissionPolicy = DEFAULT_WORKFLOW_ADMISSION_POLICY,
) -> WorkflowAdmissionAssessment:
    """Assess admission evidence through the application boundary, without model IO."""
    return _assess_workflow_admission(command.evidence, policy=policy)


def assess_tactician(
    command: AssessTactician,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    call_llm_fn: Callable[..., Any] | None = None,
) -> TacticianAssessment:
    """Run Tactician through normal model resolution, never graph dispatch.

    The ``tactician`` configuration is optional and inherits ``default`` when
    absent.  The role cannot select a model itself; provider failure leaves no
    assessment capable of authorizing compact execution.
    """
    llm_config = (
        command.config.models.get("tactician")
        or command.config.models.get("default")
    )
    if llm_config is None:
        raise ApplicationError("No Tactician or default model is configured")
    kwargs: dict[str, Any] = {"registry": registry}
    if call_llm_fn is not None:
        kwargs["call_llm_fn"] = call_llm_fn
    return _run_tactician(command.assessment_input, llm_config, **kwargs)


def inspect_workflow_admission_policy(
    query: InspectWorkflowAdmissionPolicy,
    *,
    policy: WorkflowAdmissionPolicy = DEFAULT_WORKFLOW_ADMISSION_POLICY,
) -> WorkflowAdmissionPolicy:
    """Inspect admission policy without graph, persistence, provider, or model access."""
    del query
    return policy


def inspect_workflow_admission(
    query: InspectWorkflowAdmission,
    *,
    policy: WorkflowAdmissionPolicy = DEFAULT_WORKFLOW_ADMISSION_POLICY,
) -> WorkflowAdmissionInspection:
    """Inspect the current human decision surface without authorizing a choice."""
    assessment = _require_current_workflow_assessment(
        query.assessment, query.evidence, policy
    )
    _validate_tactician_assessment(assessment, query.tactician_assessment)
    compact_reason = _compact_unavailable_reason(assessment, query.tactician_assessment)
    choices = [
        WorkflowAdmissionDisposition.FULL,
        WorkflowAdmissionDisposition.CLARIFICATION,
        WorkflowAdmissionDisposition.CANCELLED,
    ]
    if compact_reason is None:
        choices.insert(1, WorkflowAdmissionDisposition.COMPACT)
    return WorkflowAdmissionInspection(
        assessment=assessment,
        tactician_assessment=query.tactician_assessment,
        available_dispositions=tuple(choices),
        compact_unavailable_reason=compact_reason,
    )


def decide_workflow_admission(
    command: DecideWorkflowAdmission,
    *,
    policy: WorkflowAdmissionPolicy = DEFAULT_WORKFLOW_ADMISSION_POLICY,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    _clock: Callable[[], datetime] | None = None,
    _decision_id: str | None = None,
) -> WorkflowAdmissionDecisionResult:
    """Authorize and record one human workflow choice without graph dispatch.

    The operation is deliberately pre-execution. BTN-143 will persist its
    immutable output alongside distinct assessment and Tactician evidence.
    """
    assessment = _require_current_workflow_assessment(
        command.assessment, command.evidence, policy
    )
    _validate_tactician_assessment(assessment, command.tactician_assessment)
    actor = _resolve_human_actor(command.project_root, command.actor_id)
    recipe = _recipe_for_admission_choice(command, assessment, policy, registry)
    risk_flags = set(assessment.hard_risk_flags)
    if command.tactician_assessment is not None:
        risk_flags.update(command.tactician_assessment.risk_flags)
    occurred_at = (_clock or (lambda: datetime.now(timezone.utc)))()
    try:
        decision = WorkflowAdmissionDecision(
            decision_id=_decision_id or f"admission-{uuid4()}",
            disposition=command.disposition,
            admission_assessment_id=assessment.assessment_id,
            tactician_assessment_id=(
                command.tactician_assessment.assessment_id
                if command.tactician_assessment is not None
                else None
            ),
            selected_recipe_id=recipe.recipe_id if recipe is not None else None,
            selected_recipe_version=recipe.recipe_version if recipe is not None else None,
            approving_actor_id=actor.actor_id,
            approving_actor_display_name=actor.display_name,
            occurred_at=occurred_at,
            work_item_revision=assessment.work_item_revision,
            specification_revision=assessment.specification_revision,
            policy_id=assessment.policy_id,
            policy_version=assessment.policy_version,
            admitted_risk_flags=tuple(risk_flags),
            annotation=command.annotation,
        )
    except ValueError as exc:
        raise WorkflowAdmissionRejected(str(exc)) from exc
    return WorkflowAdmissionDecisionResult(decision=decision, recipe=recipe)


def _require_current_workflow_assessment(
    supplied: WorkflowAdmissionAssessment,
    evidence: WorkflowAdmissionEvidence,
    policy: WorkflowAdmissionPolicy,
) -> WorkflowAdmissionAssessment:
    """Fail closed when current evidence or policy differs from the assessment."""
    current = _assess_workflow_admission(evidence, policy=policy)
    if supplied != current:
        raise StaleWorkflowAdmission(
            "Admission assessment is stale or does not match current evidence and policy; "
            "reassess before choosing a workflow"
        )
    return current


def _validate_tactician_assessment(
    assessment: WorkflowAdmissionAssessment,
    tactician_assessment: TacticianAssessment | None,
) -> None:
    """Ensure advisory evidence belongs to the current uncertain admission input."""
    if tactician_assessment is None:
        return
    if assessment.outcome is not WorkflowAdmissionOutcome.UNCERTAIN:
        raise WorkflowAdmissionRejected(
            "Tactician evidence is valid only for an uncertain deterministic admission"
        )
    references = {
        reference.evidence_id: reference for reference in assessment.evidence_references
    }
    for reference in tactician_assessment.input_evidence_references:
        current = references.get(reference.evidence_id)
        if current is None or (
            current.source is not reference.source
            or current.source_revision != reference.source_revision
        ):
            raise WorkflowAdmissionRejected(
                "Tactician evidence does not match the current admission assessment"
            )
    if not any(
        reference.source is AdmissionEvidenceSource.WORK_ITEM
        and reference.source_revision == assessment.work_item_revision
        for reference in tactician_assessment.input_evidence_references
    ):
        raise WorkflowAdmissionRejected(
            "Tactician evidence must include the assessed work-item revision"
        )
    if assessment.specification_revision is not None and not any(
        reference.source is AdmissionEvidenceSource.SPECIFICATION
        and reference.source_revision == assessment.specification_revision
        for reference in tactician_assessment.input_evidence_references
    ):
        raise WorkflowAdmissionRejected(
            "Tactician evidence must include the assessed specification revision"
        )


def _compact_unavailable_reason(
    assessment: WorkflowAdmissionAssessment,
    tactician_assessment: TacticianAssessment | None,
) -> str | None:
    if assessment.outcome is WorkflowAdmissionOutcome.FULL_REQUIRED:
        return "Deterministic admission requires the full workflow"
    if (
        assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
        and tactician_assessment is None
    ):
        return (
            "Uncertain admission requires a Tactician assessment before compact may be chosen"
        )
    return None


def _recipe_for_admission_choice(
    command: DecideWorkflowAdmission,
    assessment: WorkflowAdmissionAssessment,
    policy: WorkflowAdmissionPolicy,
    registry: WorkflowRecipeRegistry,
) -> WorkflowRecipe | None:
    """Resolve an exact registered recipe only for a valid execution choice."""
    if command.disposition in {
        WorkflowAdmissionDisposition.CLARIFICATION,
        WorkflowAdmissionDisposition.CANCELLED,
    }:
        if command.recipe_id is not None or command.recipe_version is not None:
            raise WorkflowAdmissionRejected(
                "Clarification and cancellation cannot select an execution recipe"
            )
        return None
    if command.disposition is WorkflowAdmissionDisposition.FULL:
        expected_recipe_id = policy.full_recipe_id
        if command.recipe_id is not None and command.recipe_id != expected_recipe_id:
            raise WorkflowAdmissionRejected(
                "Full admission must select the policy's full workflow recipe"
            )
        recipe_id = expected_recipe_id
    else:
        reason = _compact_unavailable_reason(assessment, command.tactician_assessment)
        if reason is not None:
            raise WorkflowAdmissionRejected(reason)
        if command.recipe_id is None:
            if len(policy.compact_recipe_ids) != 1:
                raise WorkflowAdmissionRejected(
                    "Compact admission requires an explicit recipe when policy offers multiple recipes"
                )
            recipe_id = policy.compact_recipe_ids[0]
        else:
            recipe_id = command.recipe_id
        if recipe_id not in policy.compact_recipe_ids:
            raise WorkflowAdmissionRejected(
                "Compact admission must select a policy-declared compact recipe"
            )
    try:
        return (
            registry.resolve(recipe_id, command.recipe_version)
            if command.recipe_version is not None
            else registry.resolve_unversioned(recipe_id)
        )
    except ValueError as exc:
        raise WorkflowAdmissionRejected(str(exc)) from exc


def _actor_inspection(registry: ActorRegistry) -> ActorInspection:
    local = next(
        (actor for actor in registry.actors if actor.actor_id == registry.local_actor_id),
        None,
    )
    return ActorInspection(
        actors=registry.actors,
        local_actor=local,
        registry=registry,
    )


def _resolve_human_actor(
    project_root: str | Path, actor_id: UUID | None
) -> Actor:
    """Resolve explicit identity or establish the offline local default once."""
    root = Path(project_root).resolve()
    try:
        load_project_identity(root, create=True)
        actor = get_actor(root, actor_id) if actor_id is not None else get_local_actor(root)
    except ActorError:
        if actor_id is not None:
            raise HumanActionRejected(f"Unknown Actor {actor_id}") from None
        try:
            registry = bootstrap_local_actor(root, "Local Operator")
            actor = registry.actors[0]
        except (ActorError, IdentityError, OSError, ValueError, TypeError) as exc:
            raise HumanActionRejected(f"No local human Actor is available: {exc}") from exc
    if actor.kind is not ActorKind.HUMAN or actor.status is not ActorStatus.ACTIVE:
        raise HumanActionRejected("Human actions require an active human Actor")
    return actor


def queue_intervention(
    command: QueueIntervention,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
    worker_dir: str | Path = DEFAULT_WORKER_DIR,
    _clock: Callable[[], datetime] | None = None,
    _action_id: str | None = None,
) -> HumanActionResult:
    """Durably queue exact-target context while no run worker is active."""
    state_path(command.run_id, state_dir)
    try:
        with _inactive_worker_guard(command.run_id, worker_dir=worker_dir):
            return _persist_intervention(
                command,
                state_dir=state_dir,
                clock=_clock,
                action_id=_action_id,
            )
    except _WorkerAlreadyActive as exc:
        raise HumanActionRejected(
            "Interventions cannot be queued while a model generation may be in flight"
        ) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WorkerRecordReadFailed(command.run_id, exc) from exc


def _persist_intervention(
    command: QueueIntervention,
    *,
    state_dir: str | Path,
    clock: Callable[[], datetime] | None,
    action_id: str | None,
) -> HumanActionResult:
    state = _load_run(command.run_id, state_dir)
    actor = _resolve_human_actor(command.project_root, command.actor_id)
    occurred_at = (clock or (lambda: datetime.now(timezone.utc)))()
    identifier = action_id or f"action-{uuid4()}"
    try:
        kind = InterventionKind(command.kind)
        target = InterventionTarget(command.target)
        intervention = HumanIntervention(
            action_id=identifier,
            kind=kind,
            target=target,
            text=command.text,
            actor=actor.display_name,
            actor_id=actor.actor_id,
            requested_at=occurred_at,
        )
    except (TypeError, ValueError) as exc:
        raise HumanActionRejected(str(exc)) from exc
    action = HumanActionRecord(
        action_id=identifier,
        kind=kind.value,
        actor=actor.display_name,
        actor_id=actor.actor_id,
        occurred_at=occurred_at,
        target=target.value,
        disposition="queued",
        detail=command.text,
        resulting_state_version=state.schema_version,
        resulting_status=state.status,
        resulting_phase=state.phase,
    )
    updated = state.model_copy(update={
        "interventions": [*state.interventions, intervention],
        "human_action_log": [*state.human_action_log, action],
    })
    path = state_path(command.run_id, state_dir)
    save_state(updated, path)
    return HumanActionResult(action=action, state=updated, state_path=path)


def review_candidate(command: ReviewCandidate) -> CandidateReviewResult:
    """Apply one candidate decision through the audited Intel workflow."""
    root = Path(command.project_root).resolve()
    actor = _resolve_human_actor(root, command.actor_id)
    candidates = CandidateRepository(root / ".battalion" / "recon" / "candidates")
    workflow = InstinctReviewWorkflow(
        IntelRepository(root / ".battalion" / "intel"),
        InstinctDecisionRepository(root / ".battalion" / "recon" / "decisions"),
    )
    try:
        candidate = candidates.get(command.candidate_id)
        if command.action is ReviewAction.ACCEPT:
            if command.edits:
                raise HumanActionRejected("accept does not permit candidate edits")
            decision = workflow.accept(candidate, decided_by=actor)
        elif command.action is ReviewAction.EDIT_AND_ACCEPT:
            decision = workflow.edit_then_accept(
                candidate,
                decided_by=actor,
                edits=command.edits or {},
            )
        else:
            if command.edits:
                raise HumanActionRejected("reject does not permit candidate edits")
            decision = workflow.reject(candidate, decided_by=actor)
    except HumanActionRejected:
        raise
    except (
        CandidateNotFoundError,
        DecisionAlreadyRecordedError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise CandidateReviewFailed(str(exc)) from exc
    disposition = (
        "rejected" if decision.action is ReviewAction.REJECT else "promoted"
    )
    return CandidateReviewResult(decision=decision, disposition=disposition)


def reconnect_observation(
    query: ReconnectObservation,
    source: ObservationSource,
    *,
    state_dir: str | Path = DEFAULT_STATE_DIR,
) -> ObservationSnapshot:
    """Reload durable truth before a client consumes post-barrier live events.

    Capturing the barrier first ensures events published while persistence is
    being read have a sequence greater than the returned cursor.  The caller
    must render ``inspection`` before requesting ``source.after(cursor)``.
    """
    state_path(query.run_id, state_dir)
    cursor = source.barrier(query.run_id, query.stream_id)
    inspection = inspect_run(InspectRun(query.run_id), state_dir=state_dir)
    return ObservationSnapshot(inspection=inspection, cursor=cursor)


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
        _register_canonical_run(operation.initial_state, operation.config, path)
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
    actor = _resolve_human_actor(operation.config.base_dir, operation.actor_id)
    if not operation.resolution.strip():
        raise HumanActionRejected("Interrupt resolution must not be empty")
    if state.status is RunStatus.AWAITING_HUMAN:
        # Validate the durable resolution target before starting a process;
        # the worker performs and persists the actual state transition.
        _resolve_latest_interrupt(
            state,
            actor=actor,
            resolution=operation.resolution,
        )
    try:
        return launch_worker(
            operation="resume",
            run_id=operation.run_id,
            state=None,
            state_version=state.schema_version,
            config=operation.config,
            state_dir=state_dir,
            worker_dir=worker_dir,
            resume_actor_id=actor.actor_id,
            resume_resolution=operation.resolution,
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


def _live_callbacks(
    publisher: RunObservationPublisher | None,
    on_node_event: EventCallback | None,
    on_token: EventCallback | None,
) -> tuple[EventCallback | None, EventCallback | None]:
    if publisher is None:
        return on_node_event, on_token

    def node_callback(event: dict[str, Any]) -> None:
        publisher.handle_node_event(event)
        if on_node_event is not None:
            on_node_event(event)

    def token_callback(event: dict[str, Any]) -> None:
        publisher.handle_token(event)
        if on_token is not None:
            on_token(event)

    return node_callback, token_callback


def _resolve_latest_interrupt(
    state: RunState,
    *,
    actor: Actor,
    resolution: str,
    occurred_at: datetime | None = None,
    action_id: str | None = None,
) -> RunState:
    """Resolve exactly the latest unresolved interrupt before canonical resume."""
    unresolved = [
        index for index, entry in enumerate(state.interrupt_log)
        if entry.resolution is None
    ]
    if not resolution.strip():
        raise HumanActionRejected("Interrupt resolution must not be empty")
    interrupts = list(state.interrupt_log)
    if unresolved:
        index = unresolved[-1]
        interrupts[index] = interrupts[index].model_copy(
            update={"resolution": resolution.strip()}
        )
        target = f"interrupt:{index}"
    elif not interrupts:
        # Legacy awaiting-human state predates durable interrupt evidence.
        target = "legacy-pause"
    else:
        raise HumanActionRejected(
            "The awaiting-human run has no unresolved interrupt"
        )
    timestamp = occurred_at or datetime.now(timezone.utc)
    identifier = action_id or f"action-{uuid4()}"
    try:
        action = HumanActionRecord(
            action_id=identifier,
            kind="interrupt-resolution",
            actor=actor.display_name,
            actor_id=actor.actor_id,
            occurred_at=timestamp,
            target=target,
            disposition="applied",
            detail=resolution.strip(),
            resulting_state_version=state.schema_version,
            resulting_status=state.status,
            resulting_phase=state.phase,
        )
    except (TypeError, ValueError) as exc:
        raise HumanActionRejected(str(exc)) from exc
    return state.model_copy(update={
        "interrupt_log": interrupts,
        "human_action_log": [*state.human_action_log, action],
    })


def _latest_blocked_role_result(state: RunState):
    """Return the latest typed blocked attempt, if this is a BTN-133 pause."""
    for execution in reversed(state.execution_record.node_executions):
        if (
            execution.role_result is not None
            and execution.role_result.kind is RoleResultKind.BLOCKED
        ):
            return execution
    return None


def _resolve_blocked_role_result(
    state: RunState,
    *,
    actor: Actor,
    resolution: str,
    occurred_at: datetime | None = None,
    action_id: str | None = None,
) -> RunState:
    """Record a human confirmation before retrying a typed blocked attempt."""
    blocked = _latest_blocked_role_result(state)
    if blocked is None:
        raise HumanActionRejected("The blocked run has no typed role result")
    if not resolution.strip():
        raise HumanActionRejected("Blocked-result resolution must not be empty")
    timestamp = occurred_at or datetime.now(timezone.utc)
    identifier = action_id or f"action-{uuid4()}"
    try:
        action = HumanActionRecord(
            action_id=identifier,
            kind="interrupt-resolution",
            actor=actor.display_name,
            actor_id=actor.actor_id,
            occurred_at=timestamp,
            target=f"role-result:{blocked.execution_id}",
            disposition="applied",
            detail=resolution.strip(),
            resulting_state_version=state.schema_version,
            resulting_status=state.status,
            resulting_phase=state.phase,
        )
    except (TypeError, ValueError) as exc:
        raise HumanActionRejected(str(exc)) from exc
    return state.model_copy(update={
        "human_action_log": [*state.human_action_log, action],
    })


def _load_run(run_id: str, state_dir: str | Path) -> RunState:
    path = state_path(run_id, state_dir)
    if not path.exists():
        raise RunNotFound(run_id, path)
    try:
        return load_state(path)
    except (OSError, ValueError) as exc:
        raise StateReadFailed(run_id, path, exc) from exc


def _inspect_catalog_entry(
    project_root: Path, entry: RunCatalogEntry, identity: ProjectIdentity
) -> ProjectRunInspection:
    path = Path(entry.state_path)
    if not path.is_absolute():
        path = project_root / path
    try:
        state = load_state(path)
    except OSError as exc:
        return ProjectRunInspection(
            catalog_entry=entry,
            availability="inaccessible",
            limitation=str(exc),
        )
    except ValueError as exc:
        return ProjectRunInspection(
            catalog_entry=entry,
            availability="malformed",
            limitation=str(exc),
        )
    if state.run_id != entry.run_id:
        return ProjectRunInspection(
            catalog_entry=entry,
            availability="malformed",
            limitation=(
                f"State run ID {state.run_id!r} does not match catalog entry "
                f"{entry.run_id!r}."
            ),
        )
    if state.project_id is not None and state.project_id != str(identity.project_id):
        return ProjectRunInspection(
            catalog_entry=entry,
            availability="malformed",
            limitation=(
                f"State belongs to project {state.project_id}, not "
                f"{identity.project_id}."
            ),
        )
    return ProjectRunInspection(
        catalog_entry=entry,
        availability="available",
        inspection=RunInspection(
            run_id=state.run_id,
            run_alias=state.run_alias,
            state_version=state.schema_version,
            state_path=path,
            state=state,
            costs=summarize_costs(state.execution_record),
        ),
    )


def _discover_desktop_runs(
    project_root: Path, catalog: ProjectRunCatalog
) -> tuple[RunCatalogEntry, ...]:
    """Project uncataloged states without letting one malformed file hide history."""
    entries = list(catalog.runs)
    known_run_ids = {entry.run_id for entry in entries}
    known_paths = {
        _catalog_state_path(project_root, entry).resolve() for entry in entries
    }
    state_dir = project_root / ".battalion" / "state"
    if not state_dir.exists():
        return tuple(entries)
    for path in sorted(state_dir.glob("*.json")):
        if path.resolve() in known_paths:
            continue
        try:
            state = load_state(path)
        except (OSError, ValueError):
            entries.append(RunCatalogEntry(
                run_id=path.stem,
                display_alias=path.stem,
                ticket_id="Unknown ticket",
                state_path=path.relative_to(project_root).as_posix(),
                legacy_id=not is_canonical_run_id(path.stem),
            ))
            continue
        if state.run_id in known_run_ids:
            continue
        entries.append(RunCatalogEntry(
            run_id=state.run_id,
            display_alias=state.run_alias or state.run_id,
            ticket_id=state.ticket_id,
            state_path=path.relative_to(project_root).as_posix(),
            legacy_id=not is_canonical_run_id(state.run_id),
        ))
        known_run_ids.add(state.run_id)
    return tuple(entries)


def _catalog_state_path(project_root: Path, entry: RunCatalogEntry) -> Path:
    path = Path(entry.state_path)
    return path if path.is_absolute() else project_root / path


def _register_canonical_run(
    state: RunState, config: BattalionConfig, path: Path
) -> None:
    """Catalog BTN-32 states while leaving pre-BTN-32 starts compatible."""
    if state.project_id is None:
        return
    try:
        register_run(state, config.base_dir, state_path=path)
    except (IdentityError, OSError) as exc:
        raise IdentityApplicationError(str(exc)) from exc

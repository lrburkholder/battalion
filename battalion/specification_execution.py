"""Durable Specification lifecycle built on shared execution mechanics (BTN-228)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from battalion.identity import load_project_identity
from battalion.specifications import (
    SpecificationRepository,
    SpecificationRevisionStatus,
)
from battalion.workflow_execution import (
    BlockResolutionKind,
    WorkflowBlock,
    WorkflowEvidence,
    WorkflowExecution,
    WorkflowExecutionStatus,
)
from battalion.workflow_recipes import (
    DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    WorkflowKind,
    WorkflowRecipeRegistry,
)


class SpecificationExecutionError(ValueError):
    """A Specification lifecycle transition is not valid."""


class SpecificationExecutionNotFound(KeyError, SpecificationExecutionError):
    """The requested execution is not canonically persisted."""


class SpecificationPhase(str, Enum):
    INSPECTING = "inspecting"
    SYNTHESIZING = "synthesizing"
    AWAITING_HUMAN = "awaiting-human"


class SpecificationOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_RESOLUTION = "needs-resolution"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class UnresolvedSpecificationDependency(_Contract):
    dependency_id: UUID
    description: str = Field(min_length=1, max_length=2000)
    required_authority: str = Field(min_length=1, max_length=200)
    evidence: tuple[WorkflowEvidence, ...] = Field(min_length=1, max_length=100)
    resolution_reference: str | None = Field(default=None, min_length=1, max_length=1000)


class SpecificationExecution(_Contract):
    """Specification-only phase, candidate linkage, and semantic outcome."""

    execution: WorkflowExecution
    specification_id: UUID
    phase: SpecificationPhase
    candidate_revision_id: UUID | None = None
    outcome: SpecificationOutcome | None = None
    unresolved_dependencies: tuple[UnresolvedSpecificationDependency, ...] = Field(default_factory=tuple, max_length=100)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "SpecificationExecution":
        status = self.execution.status
        if self.execution.workflow_kind is not WorkflowKind.SPECIFICATION:
            raise ValueError("SpecificationExecution requires specification workflow mechanics")
        if self.outcome is not None and status is not WorkflowExecutionStatus.COMPLETED:
            raise ValueError("Specification outcomes require a completed execution")
        if status is WorkflowExecutionStatus.COMPLETED and self.outcome is None:
            raise ValueError("completed Specification executions require a semantic outcome")
        if self.outcome in {SpecificationOutcome.ACCEPTED, SpecificationOutcome.REJECTED} and self.candidate_revision_id is None:
            raise ValueError("accepted or rejected outcomes require an exact candidate revision")
        if self.outcome is SpecificationOutcome.NEEDS_RESOLUTION and not self.unresolved_dependencies:
            raise ValueError("needs-resolution outcomes require unresolved dependencies")
        if self.unresolved_dependencies and self.outcome is not SpecificationOutcome.NEEDS_RESOLUTION:
            raise ValueError("unresolved dependencies belong only to needs-resolution outcomes")
        return self


def start_specification_execution(
    specification_id: UUID,
    *,
    project_root: str | Path,
    requesting_actor_id: UUID | None = None,
    execution_id: UUID | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> SpecificationExecution:
    recipe = registry.resolve("specification", "1.0")
    if recipe.workflow_kind is not WorkflowKind.SPECIFICATION:
        raise SpecificationExecutionError("the selected recipe is not a Specification recipe")
    specification = SpecificationRepository(project_root).get(specification_id)
    return SpecificationExecution(
        execution=WorkflowExecution(
            execution_id=execution_id or uuid4(), project_id=specification.project_id,
            workflow_kind=recipe.workflow_kind, recipe_id=recipe.recipe_id, recipe_version=recipe.recipe_version,
            requesting_actor_id=requesting_actor_id, status=WorkflowExecutionStatus.RUNNING,
            created_at=now(), started_at=now(),
        ),
        specification_id=specification_id, phase=SpecificationPhase.INSPECTING,
    )


def set_specification_phase(execution: SpecificationExecution, phase: SpecificationPhase) -> SpecificationExecution:
    _require_active(execution)
    return execution.model_copy(update={"phase": phase})


def record_execution_evidence(
    execution: SpecificationExecution,
    *,
    worker_evidence: tuple[WorkflowEvidence, ...] = (),
    cost_evidence: tuple[WorkflowEvidence, ...] = (),
) -> SpecificationExecution:
    """Append worker/model or cost evidence without changing semantic state."""
    _require_active(execution)
    if not worker_evidence and not cost_evidence:
        raise SpecificationExecutionError("execution evidence must not be empty")
    shared = execution.execution
    return execution.model_copy(
        update={
            "execution": shared.model_copy(
                update={
                    "worker_evidence": (*shared.worker_evidence, *worker_evidence),
                    "cost_evidence": (*shared.cost_evidence, *cost_evidence),
                }
            )
        }
    )


def await_human_review(execution: SpecificationExecution, candidate_revision_id: UUID) -> SpecificationExecution:
    _require_active(execution)
    return execution.model_copy(update={"phase": SpecificationPhase.AWAITING_HUMAN, "candidate_revision_id": candidate_revision_id})


def pause_specification_execution(execution: SpecificationExecution, evidence: tuple[WorkflowEvidence, ...]) -> SpecificationExecution:
    _require_active(execution)
    if not evidence:
        raise SpecificationExecutionError("pausing requires durable evidence")
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.PAUSED, "pause_evidence": evidence})})


def resume_specification_execution(
    execution: SpecificationExecution,
    *,
    registry: WorkflowRecipeRegistry = DEFAULT_WORKFLOW_RECIPE_REGISTRY,
) -> SpecificationExecution:
    if execution.execution.status is not WorkflowExecutionStatus.PAUSED:
        raise SpecificationExecutionError("only a paused execution may resume")
    try:
        recipe = registry.resolve(execution.execution.recipe_id, execution.execution.recipe_version)
    except Exception as exc:
        raise SpecificationExecutionError(
            "the persisted execution recipe is stale or unavailable; re-admission is required"
        ) from exc
    if recipe.workflow_kind is not WorkflowKind.SPECIFICATION:
        raise SpecificationExecutionError("the persisted recipe no longer represents Specification")
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.RUNNING})})


def block_for_prerequisite(execution: SpecificationExecution, blocker: WorkflowBlock) -> SpecificationExecution:
    _require_active(execution)
    if blocker.resolved_at is not None:
        raise SpecificationExecutionError("an already resolved blocker cannot block an execution")
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.BLOCKED, "blocker": blocker})})


def recheck_blocked_prerequisite(
    execution: SpecificationExecution,
    *,
    prerequisite_satisfied: bool,
    resolution_evidence: tuple[WorkflowEvidence, ...],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> SpecificationExecution:
    """Resume only after the owning prerequisite has been re-evaluated."""
    if execution.execution.status is not WorkflowExecutionStatus.BLOCKED:
        raise SpecificationExecutionError("only a blocked execution has a prerequisite to recheck")
    if not prerequisite_satisfied:
        return execution
    if not resolution_evidence:
        raise SpecificationExecutionError("a satisfied prerequisite requires resolution evidence")
    resolved_blocker = execution.execution.blocker.model_copy(
        update={"resolved_at": now(), "resolution_evidence": resolution_evidence}
    )
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.RUNNING, "blocker": None, "block_history": (*execution.execution.block_history, resolved_blocker)})})


def complete_specification_execution(
    execution: SpecificationExecution,
    *,
    outcome: SpecificationOutcome,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    unresolved_dependencies: tuple[UnresolvedSpecificationDependency, ...] = (),
) -> SpecificationExecution:
    _require_active(execution)
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.COMPLETED, "completed_at": now()}), "outcome": outcome, "unresolved_dependencies": unresolved_dependencies})


def cancel_specification_execution(execution: SpecificationExecution, evidence: tuple[WorkflowEvidence, ...], *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> SpecificationExecution:
    _require_active(execution)
    if not evidence:
        raise SpecificationExecutionError("cancellation requires durable evidence")
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.CANCELLED, "cancellation_evidence": evidence, "completed_at": now()})})


def fail_specification_execution(execution: SpecificationExecution, evidence: tuple[WorkflowEvidence, ...], *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> SpecificationExecution:
    _require_active(execution)
    if not evidence:
        raise SpecificationExecutionError("failure requires durable evidence")
    return execution.model_copy(update={"execution": execution.execution.model_copy(update={"status": WorkflowExecutionStatus.FAILED, "failure_evidence": evidence, "completed_at": now()})})


class SpecificationExecutionRepository:
    """Project-local canonical persistence for independent Specification attempts."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def save(self, execution: SpecificationExecution) -> None:
        identity = load_project_identity(self.project_root, create=True)
        if execution.execution.project_id != identity.project_id:
            raise SpecificationExecutionError("execution belongs to another project")
        specifications = SpecificationRepository(self.project_root)
        specification = specifications.get(execution.specification_id)
        if specification.project_id != execution.execution.project_id:
            raise SpecificationExecutionError("execution and Specification belong to different projects")
        if execution.candidate_revision_id is not None:
            candidate = specifications.get_revision(
                execution.specification_id, execution.candidate_revision_id
            )
            if candidate.status is not SpecificationRevisionStatus.CANDIDATE:
                raise SpecificationExecutionError(
                    "execution candidate linkage must name an exact candidate revision"
                )
        directory = self.project_root / ".battalion" / "specification-executions"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{execution.execution.execution_id}.json"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
            json.dump(execution.model_dump(mode="json"), handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(target)

    def get(self, execution_id: UUID | str) -> SpecificationExecution:
        path = self.project_root / ".battalion" / "specification-executions" / f"{execution_id}.json"
        if not path.is_file():
            raise SpecificationExecutionNotFound(
                f"execution {execution_id} is not canonically persisted"
            )
        execution = SpecificationExecution.model_validate_json(path.read_text(encoding="utf-8"))
        if execution.execution.project_id != load_project_identity(self.project_root, create=True).project_id:
            raise SpecificationExecutionError("execution belongs to another project")
        return execution

    def list_for_specification(
        self, specification_id: UUID | str
    ) -> tuple[SpecificationExecution, ...]:
        """Inspect every independent persisted attempt for one Specification."""
        expected = UUID(str(specification_id))
        directory = self.project_root / ".battalion" / "specification-executions"
        if not directory.is_dir():
            return ()
        executions = [
            SpecificationExecution.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
        project_id = load_project_identity(self.project_root, create=True).project_id
        if any(item.execution.project_id != project_id for item in executions):
            raise SpecificationExecutionError("persisted execution history belongs to another project")
        return tuple(item for item in executions if item.specification_id == expected)


def _require_active(execution: SpecificationExecution) -> None:
    if execution.execution.status is not WorkflowExecutionStatus.RUNNING:
        raise SpecificationExecutionError("the execution is not currently running")

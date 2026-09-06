"""Versioned state contract for Battalion runs.

Every node (Architect, Driver, Reviewer) reads and writes against this single
schema — see spec.md's "State Schema (v1, draft)" and plan.md ADR-001.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from battalion.role_results import RoleExecutionResult
from battalion.work import WorkItem

if TYPE_CHECKING:
    from battalion.workflow_admission_state import WorkflowAdmissionRunRecord


class RunStatus(str, Enum):
    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    AWAITING_HUMAN = "awaiting-human"
    DONE = "done"
    FAILED_INFRA = "failed-infra"


class CheckpointType(str, Enum):
    """Which stage of the RED -> Reviewer -> GREEN -> Reviewer ->
    Refactorer -> Reviewer loop a review is checking (plan.md ADR-006
    through ADR-009)."""

    RED_CHECK = "red-check"
    GREEN_CHECK = "green-check"
    REFACTOR_CHECK = "refactor-check"


class RejectionRecord(BaseModel):
    """One Reviewer rejection, used to detect interrupt trigger #1
    (same root cause rejected twice). checkpoint (BTN-12, ADR-009) scopes
    cycle_number to be per-checkpoint-type, not ticket-wide — a rejection
    during the RED check and one during the GREEN check aren't "the same
    failure happening twice" even if they share a cause string."""

    cause: str
    cycle_number: int
    checkpoint: CheckpointType


class InterruptLogEntry(BaseModel):
    """A record of an interrupt trigger firing during a run."""

    trigger: str
    timestamp: datetime
    resolution: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    node_execution_id: str | None = None


class InterventionKind(str, Enum):
    CORRECTION = "correction"
    DESIGN_DECISION = "design-decision"


class InterventionTarget(str, Enum):
    ARCHITECT = "architect"
    DRIVER_RED = "driver_red"
    DRIVER_GREEN = "driver_green"
    REFACTORER = "refactorer"


class InterventionDisposition(str, Enum):
    QUEUED = "queued"
    DELIVERED = "delivered"


class HumanIntervention(BaseModel):
    """Bounded human context delivered to exactly one target-node attempt."""

    action_id: str = Field(min_length=1, max_length=200)
    kind: InterventionKind
    target: InterventionTarget
    text: str = Field(min_length=1, max_length=4000)
    actor: str = Field(min_length=1, max_length=500)
    actor_id: UUID | None = None
    requested_at: datetime
    disposition: InterventionDisposition = InterventionDisposition.QUEUED
    delivered_to_execution_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_authority_and_delivery(self) -> Self:
        if self.kind is InterventionKind.DESIGN_DECISION:
            if self.target is not InterventionTarget.ARCHITECT:
                raise ValueError("design decisions target Architect only")
        elif self.target is InterventionTarget.ARCHITECT:
            raise ValueError("corrections target Driver RED, Driver GREEN, or Refactorer")
        if self.disposition is InterventionDisposition.DELIVERED:
            if self.delivered_to_execution_id is None:
                raise ValueError("delivered intervention requires a node execution ID")
        elif self.delivered_to_execution_id is not None:
            raise ValueError("queued intervention cannot identify a node execution")
        return self


class HumanActionRecord(BaseModel):
    """Durable audit result for one human-authorized run action."""

    action_id: str = Field(min_length=1, max_length=200)
    kind: Literal["interrupt-resolution", "correction", "design-decision"]
    actor: str = Field(min_length=1, max_length=500)
    actor_id: UUID | None = None
    occurred_at: datetime
    target: str = Field(min_length=1, max_length=200)
    disposition: Literal["applied", "queued", "delivered", "rejected"]
    detail: str = Field(min_length=1, max_length=4000)
    resulting_state_version: str = Field(min_length=1, max_length=100)
    resulting_status: RunStatus
    resulting_phase: str = Field(min_length=1, max_length=200)


class ProgressStage(str, Enum):
    BEFORE_ATTEMPT = "interrupted-before-attempt"
    ATTEMPT_CREATED = "attempt-created"
    ATTEMPT_STARTED = "attempt-started"
    ATTEMPT_COMPLETED = "attempt-completed"
    OUTCOME_CHECKPOINTED = "outcome-checkpointed"


class GraphProgress(BaseModel):
    """Durable cursor; never infer recovery from a phase label or error prose."""

    schema_version: Literal["1.0"] = "1.0"
    stage: ProgressStage
    next_node: Literal[
        "architect", "driver_red", "driver_green", "reviewer_red",
        "reviewer_green", "refactorer", "reviewer_refactor", "done",
        "awaiting_human", "blocked",
    ]
    execution_id: str | None = None
    correction_context: str | None = Field(default=None, max_length=8000)
    correction_attempt: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def require_attempt_identity(self) -> Self:
        if self.stage in {
            ProgressStage.ATTEMPT_CREATED, ProgressStage.ATTEMPT_STARTED,
            ProgressStage.ATTEMPT_COMPLETED,
        } and self.execution_id is None:
            raise ValueError("attempt progress requires an execution identity")
        return self


class ResumeIntent(BaseModel):
    """Links replay to the original immutable authorization evidence."""

    action_id: str = Field(min_length=1, max_length=200)
    completed: bool = False


class EvidenceReference(BaseModel):
    """A bounded pointer and digest for node input without copying contents."""

    kind: Literal["state", "artifact", "workspace"]
    reference: str = Field(min_length=1, max_length=500)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    hash_algorithm: Literal["sha256"] | None = None
    inclusion_reason: str | None = Field(default=None, max_length=500)
    truncated: bool = False
    observed_bytes: int | None = Field(default=None, ge=0)
    hashed_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_digest_metadata(self) -> "EvidenceReference":
        metadata = (
            self.sha256,
            self.hash_algorithm,
            self.inclusion_reason,
            self.observed_bytes,
            self.hashed_bytes,
        )
        if any(value is not None for value in metadata):
            if any(value is None for value in metadata):
                raise ValueError("context digest metadata must be complete")
            if self.hashed_bytes > self.observed_bytes:
                raise ValueError("hashed_bytes cannot exceed observed_bytes")
        elif self.truncated:
            raise ValueError("legacy references without digest metadata cannot be truncated")
        return self


BoundedSummaryText = Annotated[str, Field(min_length=1, max_length=2000)]


class OperatorSummary(BaseModel):
    """Bounded handoff for an operator; statements link to mechanical evidence."""

    what_i_did: BoundedSummaryText
    what_should_happen_next: BoundedSummaryText
    open_questions: list[BoundedSummaryText] = Field(default_factory=list, max_length=10)
    verification_performed: list[BoundedSummaryText] = Field(
        default_factory=list, max_length=20
    )
    artifact_paths: list[str] = Field(default_factory=list, max_length=100)
    last_role: Literal["architect", "driver", "reviewer", "refactorer"]
    last_node: str = Field(min_length=1, max_length=100)
    last_phase: str = Field(min_length=1, max_length=100)


class PromptProvenance(BaseModel):
    """Prompt identity without retaining the template or rendered prompt."""

    template_identity: str = Field(min_length=1, max_length=200)
    template_path: str = Field(min_length=1, max_length=1000)
    contract_version: str = Field(min_length=1, max_length=100)
    template_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash_algorithm: Literal["sha256"] = "sha256"
    battalion_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"
    )
    model_configuration_identity: str = Field(pattern=r"^[0-9a-f]{64}$")


class CodeProvenance(BaseModel):
    """Git identity and dirty-state limits without retaining a patch."""

    version_control: Literal["git"] = "git"
    repository_available: bool
    base_commit_object_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"
    )
    object_id_algorithm: Literal["sha1", "sha256"] | None = None
    branch: str | None = Field(default=None, max_length=500)
    detached: bool | None = None
    dirty_at_start: bool | None = None
    dirty_at_end: bool | None = None
    exact_workspace_reconstructable: bool | None = None
    reconstruction_limitation: Literal["dirty-workspace-patch-not-retained"] | None = None

    @model_validator(mode="after")
    def validate_repository_evidence(self) -> "CodeProvenance":
        repository_fields = (
            self.base_commit_object_id,
            self.object_id_algorithm,
            self.detached,
            self.dirty_at_start,
            self.dirty_at_end,
            self.exact_workspace_reconstructable,
        )
        if self.repository_available and any(value is None for value in repository_fields):
            raise ValueError("available Git provenance requires complete repository evidence")
        if not self.repository_available and any(
            value is not None for value in repository_fields + (self.branch,)
        ):
            raise ValueError("unavailable Git provenance cannot claim repository evidence")
        if self.exact_workspace_reconstructable is False:
            if self.reconstruction_limitation is None:
                raise ValueError("non-reconstructable workspaces require a limitation")
        elif self.reconstruction_limitation is not None:
            raise ValueError("reconstruction limitation requires a non-reconstructable workspace")
        if self.exact_workspace_reconstructable is True and (
            self.dirty_at_start or self.dirty_at_end
        ):
            raise ValueError("dirty workspaces cannot be exactly reconstructable")
        return self


class ArtifactProvenance(BaseModel):
    """Identity and digest for an artifact; contents remain on disk."""

    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    originating_run_id: str
    originating_node_execution_id: str


class ToolActivity(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=100)
    target: str | None = Field(default=None, max_length=1000)
    outcome: Literal["succeeded", "failed"]


class TestOutcome(BaseModel):
    checkpoint: CheckpointType
    passed: bool
    expected_to_pass: bool
    accepted: bool


class TestExecutionClassification(str, Enum):
    """Mechanical disposition of one bounded Reviewer pytest process."""

    PASSED = "pass"
    TEST_FAILED = "test-failure"
    NO_TESTS_COLLECTED = "no-tests-collected"
    PYTEST_ERROR = "collection-usage-internal-error"
    TIMED_OUT = "timeout"
    CANCELLED = "cancellation"
    PROCESS_LAUNCH_FAILED = "process-launch-failure"
    MALFORMED_OUTPUT = "malformed-output"
    INVALID_EXIT = "invalid-pytest-outcome"


class TestExecutionEvidence(BaseModel):
    """Bounded, inspectable evidence from Reviewer's independent test run."""

    command: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        min_length=1, max_length=20
    )
    working_directory: str = Field(min_length=1, max_length=500)
    classification: TestExecutionClassification
    returncode: int | None = None
    tests_collected: int | None = Field(default=None, ge=0)
    failures: int | None = Field(default=None, ge=0)
    errors: int | None = Field(default=None, ge=0)
    stdout: str = Field(default="", max_length=65536)
    stderr: str = Field(default="", max_length=65536)
    stdout_observed_bytes: int = Field(ge=0)
    stderr_observed_bytes: int = Field(ge=0)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0, le=3600)
    timed_out: bool = False
    cancelled: bool = False
    cleanup_attempted: bool = False
    cleanup_succeeded: bool | None = None
    detail: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.classification in {
            TestExecutionClassification.PASSED,
            TestExecutionClassification.TEST_FAILED,
        }:
            if not self.tests_collected or self.errors != 0:
                raise ValueError("valid checkpoint evidence requires collected tests and no harness errors")
            if self.classification is TestExecutionClassification.PASSED:
                if self.returncode != 0 or self.failures != 0:
                    raise ValueError("passing evidence requires exit 0 and no failures")
            elif self.returncode != 1 or not self.failures:
                raise ValueError("failing evidence requires exit 1 and a collected-test failure")
        if self.stdout_truncated != (self.stdout_observed_bytes > 65536):
            raise ValueError("stdout truncation metadata is inconsistent")
        if self.stderr_truncated != (self.stderr_observed_bytes > 65536):
            raise ValueError("stderr truncation metadata is inconsistent")
        if self.timed_out != (
            self.classification is TestExecutionClassification.TIMED_OUT
        ):
            raise ValueError("timeout disposition is inconsistent with classification")
        if self.cancelled != (
            self.classification is TestExecutionClassification.CANCELLED
        ):
            raise ValueError("cancellation disposition is inconsistent with classification")
        if self.cleanup_attempted and self.cleanup_succeeded is None:
            raise ValueError("attempted cleanup requires a cleanup result")
        if not self.cleanup_attempted and self.cleanup_succeeded is not None:
            raise ValueError("cleanup result requires an attempted cleanup")
        return self


class ReviewResult(BaseModel):
    checkpoint: CheckpointType
    verdict: Literal["accepted", "rejected", "unavailable"]
    cause: str | None = Field(default=None, max_length=2000)


class CostSource(str, Enum):
    PROVIDER_REPORTED = "provider-reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class LLMCallCost(BaseModel):
    """Token, cost, and non-secret inference-identity evidence for one LLM call.

    ``model`` is retained as the pre-BTN-54 display/compatibility field.  New
    records keep the requested target separate from a response or router's
    effective identity; absent provider metadata stays absent.
    """

    call_id: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=500)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_currency: str | None = Field(
        default=None, pattern=r"^[A-Z]{3}$"
    )
    cost_source: CostSource = CostSource.UNKNOWN
    requested_model: str | None = Field(default=None, min_length=1, max_length=500)
    response_model: str | None = Field(default=None, min_length=1, max_length=500)
    backend: str | None = Field(default=None, min_length=1, max_length=200)
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=2000)
    inference_location: Literal["local", "remote", "unknown"] | None = None
    routed_provider: str | None = Field(default=None, min_length=1, max_length=500)
    routed_model: str | None = Field(default=None, min_length=1, max_length=500)
    identity_contradiction: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_cost_usd(cls, value: Any) -> Any:
        """Read execution-record 1.1 observations without rewriting history."""
        if not isinstance(value, dict) or "cost_usd" not in value or "cost" in value:
            return value
        migrated = dict(value)
        legacy_cost = migrated.pop("cost_usd")
        migrated.update(
            cost=str(legacy_cost),
            cost_currency="USD",
            cost_source=CostSource.PROVIDER_REPORTED,
        )
        return migrated

    @model_validator(mode="after")
    def validate_cost_evidence(self) -> Self:
        if self.cost is None:
            if self.cost_currency is not None or self.cost_source != CostSource.UNKNOWN:
                raise ValueError(
                    "unknown cost requires null currency and source 'unknown'"
                )
        elif self.cost_currency is None or self.cost_source == CostSource.UNKNOWN:
            raise ValueError(
                "known cost requires a currency and a known cost source"
            )
        return self


class RoleContractViolationEvidence(BaseModel):
    """Inspectable evidence for a rejected pre-write role candidate."""

    reason_code: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=2000)
    offending_paths: list[str] = Field(default_factory=list, max_length=100)
    attempt_number: int = Field(ge=1, le=100)
    mutation_applied: Literal[False] = False
    resulting_disposition: Literal["retry", "escalation"]


class NodeExecution(BaseModel):
    """Durable evidence for one attempt to execute one graph role node."""

    execution_id: str
    role: Literal["architect", "driver", "reviewer", "refactorer"]
    phase: str
    model_identity: str = Field(min_length=1, max_length=500)
    input_references: list[EvidenceReference] = Field(default_factory=list, max_length=20)
    output_reference: str | None = Field(default=None, max_length=1000)
    verdict: str | None = Field(default=None, max_length=2000)
    started_at: datetime
    ended_at: datetime | None = None
    outcome: Literal["in-progress", "succeeded", "rejected", "interrupted"]
    attempt_disposition: Literal[
        "accepted", "corrected", "rejected", "infra-failure"
    ] | None = None
    role_contract_violation: RoleContractViolationEvidence | None = None
    role_result: RoleExecutionResult | None = None
    tool_activity: list[ToolActivity] = Field(default_factory=list, max_length=100)
    test_outcome: TestOutcome | None = None
    test_execution: TestExecutionEvidence | None = None
    review_result: ReviewResult | None = None
    artifact_provenance: list[ArtifactProvenance] = Field(default_factory=list, max_length=100)
    interrupt_ids: list[int] = Field(default_factory=list, max_length=20)
    llm_calls: list[LLMCallCost] = Field(default_factory=list, max_length=20)
    streamed_reasoning_characters: int = Field(default=0, ge=0)
    streamed_content_characters: int = Field(default=0, ge=0)
    operator_summary: OperatorSummary | None = None
    prompt_provenance: PromptProvenance | None = None
    code_provenance: CodeProvenance | None = None

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        if (self.outcome == "in-progress") != (self.ended_at is None):
            raise ValueError("Only unfinished attempts may omit the completion timestamp")
        return self


class ExecutionRecord(BaseModel):
    """Separately versioned history for all node attempts in a run."""

    schema_version: Literal[
        "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"
    ] = "1.8"
    node_executions: list[NodeExecution] = Field(default_factory=list)


class SideEffectOutcome(str, Enum):
    """Confirmed knowledge about one external delivery attempt.

    ``AMBIGUOUS`` means the attempt may have reached the provider; only
    reconciliation against provider idempotency or status evidence may
    resolve it (RFC-0006 "Failure and side-effect semantics").
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class SideEffectStatus(str, Enum):
    """Replay-safety state of one logical external operation."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class SideEffectAttempt(BaseModel):
    """Durable evidence for one delivery attempt of one logical operation.

    Attempts never retain request or response payloads; bounded detail text,
    digests, and provider references carry the audit weight.
    """

    attempt_number: int = Field(ge=1, le=10000)
    started_at: datetime
    ended_at: datetime
    outcome: SideEffectOutcome
    failure_category: str | None = Field(default=None, max_length=100)
    detail: str | None = Field(default=None, max_length=2000)
    provider_idempotency_used: bool = False
    provider_reference: str | None = Field(default=None, max_length=500)
    request_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attempt timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_attempt_evidence(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("attempt cannot end before it starts")
        if self.outcome is SideEffectOutcome.SUCCEEDED and self.failure_category is not None:
            raise ValueError("succeeded attempts cannot record a failure category")
        if self.outcome is not SideEffectOutcome.SUCCEEDED and self.failure_category is None:
            raise ValueError("failed or ambiguous attempts require a failure category")
        return self


class SideEffectOperation(BaseModel):
    """One logical externally visible operation with replay-safe identity.

    ``operation_id`` is minted by Battalion before first delivery and is
    stable across retries, resume, crashes, and duplicate processing.
    """

    operation_id: str = Field(
        pattern=r"^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    dedupe_key: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    actor_id: UUID | None = None
    capability: str = Field(min_length=1, max_length=100)
    integration_id: str = Field(min_length=1, max_length=200)
    integration_name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    transport: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=200)
    created_at: datetime
    status: SideEffectStatus = SideEffectStatus.PENDING
    attempts: list[SideEffectAttempt] = Field(default_factory=list, max_length=100)
    reconciled_at: datetime | None = None
    reconciliation_detail: str | None = Field(default=None, max_length=2000)

    @field_validator("created_at", "reconciled_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("operation timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_operation_state(self) -> Self:
        previous = 0
        for attempt in self.attempts:
            if attempt.attempt_number <= previous:
                raise ValueError("attempt numbers must strictly increase from one")
            previous = attempt.attempt_number
        if (self.reconciled_at is None) != (self.reconciliation_detail is None):
            raise ValueError("reconciliation requires both timestamp and detail")
        if self.status is SideEffectStatus.PENDING:
            if self.attempts or self.reconciled_at is not None:
                raise ValueError("pending operations cannot record attempts or resolution")
            return self
        reconciled = self.reconciled_at is not None
        expected = {
            SideEffectStatus.SUCCEEDED: SideEffectOutcome.SUCCEEDED,
            SideEffectStatus.FAILED: SideEffectOutcome.FAILED,
            SideEffectStatus.AMBIGUOUS: SideEffectOutcome.AMBIGUOUS,
        }[self.status]
        if self.attempts:
            if not reconciled and self.attempts[-1].outcome is not expected:
                raise ValueError(
                    f"{self.status.value} operations require a matching final attempt"
                )
        elif not reconciled:
            raise ValueError(
                f"{self.status.value} operations require an attempt or reconciliation"
            )
        return self


class SideEffectLedger(BaseModel):
    """Separately versioned durable evidence for externally visible effects.

    The ledger lives inside the single RunState contract rather than in a
    parallel event store (ADR-0014, ADR-0021, ADR-0023, ADR-0028).
    """

    schema_version: Literal["1.0"] = "1.0"
    operations: list[SideEffectOperation] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_unique_identity(self) -> Self:
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation IDs must be unique within a ledger")
        seen: set[str] = set()
        for operation in self.operations:
            if operation.dedupe_key in seen:
                raise ValueError("dedupe keys must be unique within a ledger")
            seen.add(operation.dedupe_key)
        return self


class Budget(BaseModel):
    """Tracked per graph run, not per node — see plan.md ADR notes and
    spec.md interrupt trigger #3."""

    limit: int
    used: int = 0

    def exceeded(self) -> bool:
        return self.used >= self.limit


class RunState(BaseModel):
    """The single versioned state contract shared by every node."""

    schema_version: str
    run_id: str
    run_alias: str | None = None
    project_id: str | None = None
    ticket_id: str
    work_item: WorkItem | None = None
    spec: str = ""
    status: RunStatus
    phase: str
    write_scope: dict[str, list[str]] = Field(default_factory=dict)
    reviewer_rejection_history: list[RejectionRecord] = Field(default_factory=list)
    retry_bound: int
    budget: Budget
    interrupt_log: list[InterruptLogEntry] = Field(default_factory=list)
    manual_checkpoints: list[str] = Field(default_factory=list)
    resume_target: str | None = None
    graph_progress: GraphProgress | None = None
    resume_intent: ResumeIntent | None = None
    execution_record: ExecutionRecord = Field(default_factory=ExecutionRecord)
    interventions: list[HumanIntervention] = Field(default_factory=list, max_length=100)
    human_action_log: list[HumanActionRecord] = Field(default_factory=list, max_length=500)
    side_effect_ledger: SideEffectLedger = Field(default_factory=SideEffectLedger)
    workflow_admission: WorkflowAdmissionRunRecord | None = None

    @model_validator(mode="after")
    def validate_recovery_evidence(self) -> Self:
        if self.workflow_admission is not None and self.schema_version != "1.1":
            raise ValueError(
                "workflow admission persistence requires RunState schema version 1.1"
            )
        progress = self.graph_progress
        if progress is not None and progress.execution_id is not None:
            matches = [e for e in self.execution_record.node_executions
                       if e.execution_id == progress.execution_id]
            if len(matches) != 1:
                raise ValueError("Recovery cursor requires exactly one matching execution")
            attempt = matches[0]
            unfinished = progress.stage in {
                ProgressStage.ATTEMPT_CREATED, ProgressStage.ATTEMPT_STARTED,
            }
            if unfinished and (attempt.phase != progress.next_node or attempt.outcome != "in-progress"):
                raise ValueError("Unfinished recovery cursor must identify its target attempt")
            if not unfinished and attempt.outcome == "in-progress":
                raise ValueError("Completed recovery cursor requires a completed attempt")
        if self.resume_intent is not None:
            matches = [a for a in self.human_action_log if a.action_id == self.resume_intent.action_id]
            if len(matches) != 1 or matches[0].kind != "interrupt-resolution":
                raise ValueError("Resume intent requires its original authorization record")
        return self

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(UUID(value))


# Resolve the durable admission type after the legacy state models are defined.
# Tactician usage evidence imports CostSource from this module, so importing the
# linkage model earlier would create a partial-module cycle.
from battalion.workflow_admission_state import WorkflowAdmissionRunRecord

RunState.model_rebuild()

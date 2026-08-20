"""Versioned state contract for Battalion runs.

Every node (Architect, Driver, Reviewer) reads and writes against this single
schema — see spec.md's "State Schema (v1, draft)" and plan.md ADR-001.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


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
    occurred_at: datetime
    target: str = Field(min_length=1, max_length=200)
    disposition: Literal["applied", "queued", "delivered", "rejected"]
    detail: str = Field(min_length=1, max_length=4000)
    resulting_state_version: str = Field(min_length=1, max_length=100)
    resulting_status: RunStatus
    resulting_phase: str = Field(min_length=1, max_length=200)


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


class ReviewResult(BaseModel):
    checkpoint: CheckpointType
    verdict: Literal["accepted", "rejected"]
    cause: str | None = Field(default=None, max_length=2000)


class CostSource(str, Enum):
    PROVIDER_REPORTED = "provider-reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class LLMCallCost(BaseModel):
    """Token usage and explicitly sourced monetary evidence for one LLM call."""

    call_id: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=500)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_currency: str | None = Field(
        default=None, pattern=r"^[A-Z]{3}$"
    )
    cost_source: CostSource = CostSource.UNKNOWN

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
    ended_at: datetime
    outcome: Literal["succeeded", "rejected", "interrupted"]
    tool_activity: list[ToolActivity] = Field(default_factory=list, max_length=100)
    test_outcome: TestOutcome | None = None
    review_result: ReviewResult | None = None
    artifact_provenance: list[ArtifactProvenance] = Field(default_factory=list, max_length=100)
    interrupt_ids: list[int] = Field(default_factory=list, max_length=20)
    llm_calls: list[LLMCallCost] = Field(default_factory=list, max_length=20)
    operator_summary: OperatorSummary | None = None
    prompt_provenance: PromptProvenance | None = None
    code_provenance: CodeProvenance | None = None


class ExecutionRecord(BaseModel):
    """Separately versioned history for all node attempts in a run."""

    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    node_executions: list[NodeExecution] = Field(default_factory=list)


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
    execution_record: ExecutionRecord = Field(default_factory=ExecutionRecord)
    interventions: list[HumanIntervention] = Field(default_factory=list, max_length=100)
    human_action_log: list[HumanActionRecord] = Field(default_factory=list, max_length=500)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(UUID(value))

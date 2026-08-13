"""Versioned state contract for Battalion runs.

Every node (Architect, Driver, Reviewer) reads and writes against this single
schema — see spec.md's "State Schema (v1, draft)" and plan.md ADR-001.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class EvidenceReference(BaseModel):
    """A bounded pointer to node input without copying its contents."""

    kind: Literal["state", "artifact", "workspace"]
    reference: str = Field(min_length=1, max_length=500)


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


class ExecutionRecord(BaseModel):
    """Separately versioned history for all node attempts in a run."""

    schema_version: Literal["1.0"] = "1.0"
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

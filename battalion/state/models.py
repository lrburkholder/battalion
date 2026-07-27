"""Versioned state contract for Battalion runs.

Every node (Architect, Driver, Reviewer) reads and writes against this single
schema — see spec.md's "State Schema (v1, draft)" and plan.md ADR-001.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    AWAITING_HUMAN = "awaiting-human"
    DONE = "done"
    FAILED_INFRA = "failed-infra"


class RejectionRecord(BaseModel):
    """One Reviewer rejection, used to detect interrupt trigger #1
    (same root cause rejected twice)."""

    cause: str
    cycle_number: int


class InterruptLogEntry(BaseModel):
    """A record of an interrupt trigger firing during a run."""

    trigger: str
    timestamp: datetime
    resolution: str | None = None


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
    status: RunStatus
    phase: str
    write_scope: dict[str, list[str]] = Field(default_factory=dict)
    reviewer_rejection_history: list[RejectionRecord] = Field(default_factory=list)
    retry_bound: int
    budget: Budget
    interrupt_log: list[InterruptLogEntry] = Field(default_factory=list)

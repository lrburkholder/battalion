"""Provider-neutral durable work-item contracts.

These models sit above provider adapters so ``RunState`` can persist a bounded
work snapshot without importing the integrations package and its runtime.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class WorkItemProvenance(BaseModel):
    """Bounded evidence describing one normalized work-item retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    retrieved_at: datetime
    operation: Literal["work.get", "work.list", "work.refresh"]
    evidence: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, evidence: dict[str, str]) -> dict[str, str]:
        for key, value in evidence.items():
            if not key or len(key) > 100 or len(value) > 1000:
                raise ValueError("work-item provenance evidence must be bounded")
        return evidence


class WorkItem(BaseModel):
    """A provider-neutral, durable snapshot of an external unit of work."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_integration_id: str = Field(min_length=1, max_length=63)
    external_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=50_000)
    status: str = Field(default="unknown", min_length=1, max_length=100)
    labels: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    assignment_references: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    reference_url: str | None = Field(default=None, max_length=2_000)
    source_revision: str | None = Field(default=None, max_length=1_000)
    provenance: WorkItemProvenance

    @field_validator("source_integration_id")
    @classmethod
    def validate_integration_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("source integration ID must be a stable lowercase identifier")
        return value


class WorkSourceReadPort(Protocol):
    """Read-only WorkSource operations admitted to application intake."""

    @property
    def integration_id(self) -> str:
        """Stable configured integration identity for retrieved work."""

    def get(self, external_id: str) -> WorkItem:
        """Return one current normalized work-item snapshot."""

    def refresh(self, item: WorkItem) -> WorkItem:
        """Return a new normalized snapshot for one prior work item."""


class WorkSourceMutationPort(Protocol):
    """Separately admitted mutating WorkSource operations.

    Intake deliberately does not accept this protocol. A later operation
    policy can admit individual mutations with BTN-70 side-effect evidence
    without turning ordinary work retrieval into mutation authority.
    """

    def comment(self, external_id: str, body: str) -> None:
        """Create an externally visible comment when separately authorized."""

    def transition(self, external_id: str, status: str) -> None:
        """Change external status when separately authorized."""

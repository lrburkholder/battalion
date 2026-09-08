"""Human workflow-admission decision contracts.

These records deliberately reference the deterministic assessment and optional
Tactician assessment rather than copying or mutating either one.  Durable Run
linkage and persistence belong to BTN-143; this module defines the
application-owned decision semantics that persistence will retain.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowAdmissionDisposition(str, Enum):
    """The bounded choices an authorized human may make before execution."""

    FULL = "full"
    COMPACT = "compact"
    CLARIFICATION = "clarification"
    CANCELLED = "cancelled"


class WorkflowAdmissionDecision(BaseModel):
    """Immutable evidence of one human admission choice.

    The assessment and Tactician records remain independently inspectable
    pre-admission evidence.  This record stores their stable identities plus
    the exact selected recipe, the approving Actor, and bounded human context.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    decision_version: str = Field(default="1.0", pattern=r"^1\.0$")
    decision_id: str = Field(min_length=1, max_length=200)
    disposition: WorkflowAdmissionDisposition
    admission_assessment_id: str = Field(min_length=1, max_length=200)
    tactician_assessment_id: str | None = Field(default=None, min_length=1, max_length=200)
    selected_recipe_id: str | None = Field(default=None, min_length=1, max_length=200)
    selected_recipe_version: str | None = Field(default=None, min_length=1, max_length=100)
    approving_actor_id: UUID
    approving_actor_display_name: str = Field(min_length=1, max_length=500)
    occurred_at: datetime
    work_item_revision: str = Field(min_length=1, max_length=1_000)
    specification_revision: str | None = Field(default=None, min_length=1, max_length=1_000)
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=100)
    admitted_risk_flags: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    annotation: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("admitted_risk_flags")
    @classmethod
    def validate_risk_flags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("admitted risk flags must be unique")
        if any(not value or len(value) > 200 for value in values):
            raise ValueError("admitted risk flags must be bounded non-empty identifiers")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def validate_recipe_for_disposition(self) -> "WorkflowAdmissionDecision":
        has_recipe = self.selected_recipe_id is not None
        has_version = self.selected_recipe_version is not None
        if has_recipe != has_version:
            raise ValueError("selected recipes require an exact recipe version")
        needs_recipe = self.disposition in {
            WorkflowAdmissionDisposition.FULL,
            WorkflowAdmissionDisposition.COMPACT,
        }
        if needs_recipe != has_recipe:
            raise ValueError("execution dispositions require an exact selected recipe")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("decision timestamps must include a timezone")
        return self

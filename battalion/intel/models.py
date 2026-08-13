"""Versioned data contract for candidate and human-accepted Instincts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


INSTINCT_ID_PATTERN = r"^INS-[A-Z0-9][A-Z0-9-]{2,63}$"
InstinctId = Annotated[str, Field(pattern=INSTINCT_ID_PATTERN)]


class _ContractModel(BaseModel):
    """Strict base so undeclared lifecycle data cannot bypass the contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InstinctLifecycle(str, Enum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"


class InstinctAudience(str, Enum):
    ARCHITECT = "architect"
    DRIVER = "driver"
    REVIEWER = "reviewer"
    REFACTORER = "refactorer"


class InstinctEvidenceReference(_ContractModel):
    """A bounded execution-record pointer with enough context to inspect it."""

    run_id: str = Field(min_length=1, max_length=200)
    node_execution_id: str = Field(min_length=1, max_length=200)
    reference: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=2000)


class InstinctApplicability(_ContractModel):
    """Human-readable scope plus deterministic retrieval hints."""

    description: str = Field(min_length=1, max_length=2000)
    include: list[str] = Field(default_factory=list, max_length=20)
    exclude: list[str] = Field(default_factory=list, max_length=20)


class InstinctCreationProvenance(_ContractModel):
    originating_run_id: str = Field(min_length=1, max_length=200)
    originating_node_execution_ids: list[str] = Field(min_length=1, max_length=20)
    created_at: datetime
    created_by: Literal["recon", "operator"]


class AcceptanceProvenance(_ContractModel):
    """Proof that a human, rather than Recon, promoted the knowledge."""

    accepted_at: datetime
    accepted_by: str = Field(min_length=1, max_length=500)


class InstinctBase(_ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    instinct_id: InstinctId
    recommendation: str = Field(min_length=1, max_length=5000)
    evidence: list[InstinctEvidenceReference] = Field(min_length=1, max_length=20)
    audience: list[InstinctAudience] = Field(min_length=1, max_length=4)
    applicability: InstinctApplicability
    tags: list[str] = Field(min_length=1, max_length=20)
    creation_provenance: InstinctCreationProvenance
    supersedes_id: InstinctId | None = None

    @model_validator(mode="after")
    def validate_identity_and_provenance(self) -> "InstinctBase":
        if self.supersedes_id == self.instinct_id:
            raise ValueError("an Instinct cannot supersede itself")

        origin = self.creation_provenance
        if any(item.run_id != origin.originating_run_id for item in self.evidence):
            raise ValueError("evidence must belong to the originating run")
        origin_nodes = set(origin.originating_node_execution_ids)
        if any(item.node_execution_id not in origin_nodes for item in self.evidence):
            raise ValueError("evidence nodes must be declared in creation provenance")
        if len(set(self.audience)) != len(self.audience):
            raise ValueError("audience values must be unique")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")
        if any(not tag or len(tag) > 100 for tag in self.tags):
            raise ValueError("tags must contain between 1 and 100 characters")
        return self


class CandidateInstinct(InstinctBase):
    """Recon output which has no authority as accepted knowledge."""

    lifecycle: Literal[InstinctLifecycle.CANDIDATE] = InstinctLifecycle.CANDIDATE


class AcceptedInstinct(InstinctBase):
    """An independently understandable Instinct promoted by a human."""

    lifecycle: Literal[InstinctLifecycle.ACCEPTED]
    acceptance_provenance: AcceptanceProvenance


Instinct = Annotated[
    CandidateInstinct | AcceptedInstinct,
    Field(discriminator="lifecycle"),
]

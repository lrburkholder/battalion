"""Immutable artifact-target history, separate from workflow admission.

These persistence values retain evidence; constructing a record does not admit
Driver or authorize a correction. Application policy owns those operations.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from battalion.artifact_targets import (
    ArtifactTargetContract, ArtifactTargetEvidenceReference, Digest, Revision,
)


class ArtifactTargetReasonCode(str, Enum):
    MISSING_TARGETS = "missing-targets"
    MISSING_EVIDENCE = "missing-evidence"
    AMBIGUOUS_TARGETS = "ambiguous-targets"
    DUPLICATE_TARGETS = "duplicate-targets"
    UNSAFE_PATH = "unsafe-path"
    STALE_EVIDENCE = "stale-evidence"
    OUT_OF_SCOPE = "out-of-scope"
    CONTRADICTORY_EVIDENCE = "contradictory-evidence"
    INCOMPATIBLE_RECIPE = "incompatible-recipe"


class _HistoryValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("handoff timestamps must include a timezone")
    return value


class ArtifactTargetReconciliation(_HistoryValue):
    """One assessment of an exact contract and its external policy snapshot."""

    reconciliation_id: Revision
    contract_id: Digest | None = None
    occurred_at: datetime
    outcome: Literal["ready", "clarification-required"]
    reason_codes: tuple[ArtifactTargetReasonCode, ...] = Field(default=(), max_length=20)
    recipe_id: Revision
    recipe_version: Revision
    project_source_revision: Revision | None = None
    write_scope_digest: Digest
    path_policy_digest: Digest
    evidence_references: tuple[ArtifactTargetEvidenceReference, ...] = Field(
        default=(), max_length=5100,
    )

    _timezone = field_validator("occurred_at")(_require_timezone)

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reconciliation reason codes must be unique")
        if self.outcome == "ready":
            if self.contract_id is None or self.reason_codes:
                raise ValueError("ready reconciliation requires a contract and no failure reasons")
            if self.project_source_revision is None:
                raise ValueError("ready reconciliation requires project-source evidence")
        elif not self.reason_codes:
            raise ValueError("clarification requires at least one reason code")
        return self


class ArtifactTargetCorrection(_HistoryValue):
    """Actor attribution for an application-authorized handoff action.

    Actor eligibility and action replay are checked by the application, not by
    deserializing historical evidence about an Actor who may now be inactive.
    """

    action_id: Revision
    actor_id: UUID
    occurred_at: datetime
    action: Literal["approve-correction", "return-to-architect", "cancel"]
    previous_contract_id: Digest | None = None
    corrected_contract_id: Digest | None = None
    reason: str = Field(min_length=1, max_length=4000, strict=True)

    _timezone = field_validator("occurred_at")(_require_timezone)

    @model_validator(mode="after")
    def validate_correction(self) -> Self:
        if not self.reason.strip():
            raise ValueError("correction reason cannot be blank")
        if (self.action == "approve-correction") != (self.corrected_contract_id is not None):
            raise ValueError("only approval references a corrected contract")
        if self.corrected_contract_id is not None and (
            self.corrected_contract_id == self.previous_contract_id
        ):
            raise ValueError("a correction must create a new contract identity")
        return self


class ArtifactTargetHandoffRecord(_HistoryValue):
    """Ordered history with one explicit active identity, never implicit latest.

    Supersession is a single chain. An absent active reference represents a
    handoff awaiting a new candidate or cancelled by application policy.
    """

    schema_version: Literal["1.0"] = "1.0"
    contracts: tuple[ArtifactTargetContract, ...] = Field(default=(), max_length=100)
    reconciliations: tuple[ArtifactTargetReconciliation, ...] = Field(default=(), max_length=500)
    corrections: tuple[ArtifactTargetCorrection, ...] = Field(default=(), max_length=100)
    active_contract_id: Digest | None = None

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        contracts: dict[str, ArtifactTargetContract] = {}
        previous_id = None
        for contract in self.contracts:
            if contract.contract_id in contracts:
                raise ValueError("handoff contract IDs must be unique")
            if contract.supersedes_contract_id != previous_id:
                raise ValueError("contracts must preserve the ordered supersession chain")
            contracts[contract.contract_id] = contract
            previous_id = contract.contract_id
        if self.active_contract_id is not None and self.active_contract_id != previous_id:
            raise ValueError("active contract must reference the unsuperseded retained contract")

        reconciliation_ids: set[str] = set()
        previous_time = None
        for result in self.reconciliations:
            if result.reconciliation_id in reconciliation_ids:
                raise ValueError("reconciliation IDs must be unique")
            reconciliation_ids.add(result.reconciliation_id)
            if previous_time is not None and result.occurred_at < previous_time:
                raise ValueError("reconciliation history must be chronological")
            previous_time = result.occurred_at
            if result.contract_id is not None and result.contract_id not in contracts:
                raise ValueError("reconciliation references an unknown contract")
            if result.outcome == "ready" and (
                result.project_source_revision
                != contracts[result.contract_id].project_source_revision
            ):
                raise ValueError("ready reconciliation requires matching source revision")

        action_ids: set[str] = set()
        approved_ids: set[str] = set()
        previous_time = None
        for correction in self.corrections:
            if correction.action_id in action_ids:
                raise ValueError("correction action IDs must be unique")
            action_ids.add(correction.action_id)
            if previous_time is not None and correction.occurred_at < previous_time:
                raise ValueError("correction history must be chronological")
            previous_time = correction.occurred_at
            if correction.previous_contract_id is not None and (
                correction.previous_contract_id not in contracts
            ):
                raise ValueError("correction references an unknown previous contract")
            if correction.corrected_contract_id is not None:
                corrected = contracts.get(correction.corrected_contract_id)
                if corrected is None:
                    raise ValueError("correction references an unknown corrected contract")
                if corrected.supersedes_contract_id != correction.previous_contract_id:
                    raise ValueError("correction must reference the contract it supersedes")
                if corrected.contract_id in approved_ids:
                    raise ValueError("a corrected contract can have only one approval")
                approved_ids.add(corrected.contract_id)
        return self

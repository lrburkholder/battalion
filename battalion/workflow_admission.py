"""Deterministic, evidence-first workflow-admission policy.

Admission classifies bounded evidence; it is deliberately not a complexity
estimator and it never invokes a model or chooses a graph.  Later tickets own
human approval, persistence, Tactician escalation, and compact execution.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowAdmissionOutcome(str, Enum):
    """The three deterministic outcomes defined by RFC-0012."""

    FULL_REQUIRED = "full-required"
    COMPACT_ADMISSIBLE = "compact-admissible"
    UNCERTAIN = "uncertain"


class AdmissionEvidenceSource(str, Enum):
    """Authoritative sources that may provide bounded admission evidence."""

    WORK_ITEM = "work-item"
    SPECIFICATION = "specification"
    ADR = "adr"
    REPOSITORY = "repository"
    CARTOGRAPHY = "cartography"
    CONTEXT = "context"
    POLICY = "policy"


class AdmissionEvidenceCondition(str, Enum):
    """Whether an evidence reference can presently support an assessment."""

    PRESENT = "present"
    MISSING = "missing"
    STALE = "stale"
    CONTRADICTORY = "contradictory"


class CompactAdmissionEvidence(str, Enum):
    """Positive facts required before compact execution may be offered."""

    EXPLICIT_DESIRED_BEHAVIOR = "explicit-desired-behavior"
    APPLICABLE_EXISTING_ARCHITECTURE = "applicable-existing-architecture"
    BOUNDED_EXPECTED_SCOPE = "bounded-expected-scope"
    EXECUTABLE_BEHAVIORAL_VERIFICATION = "executable-behavioral-verification"
    REQUIRED_DETERMINISTIC_GATES = "required-deterministic-gates"
    INDEPENDENT_REVIEW = "independent-review"


class HardRiskFlag(str, Enum):
    """Built-in full-workflow prohibitions; policy may add stable flag IDs."""

    MATERIAL_ARCHITECTURE_BOUNDARY = "material-architecture-boundary"
    PUBLIC_INTERFACE_OR_SCHEMA = "public-interface-or-schema"
    PERSISTENCE_OR_MIGRATION = "persistence-or-migration"
    AUTHORIZATION_SECRETS_PRIVACY_SECURITY = "authorization-secrets-privacy-security"
    HIGH_CONSEQUENCE_RELEASE_DEPLOYMENT = "high-consequence-release-deployment"
    CROSS_SYSTEM_POLICY = "cross-system-policy"


_DEFAULT_HARD_RISK_FLAGS = frozenset(flag.value for flag in HardRiskFlag)
_DEFAULT_COMPACT_EVIDENCE = frozenset(CompactAdmissionEvidence)


class AdmissionEvidenceReference(BaseModel):
    """A bounded identity and classification of one admission-evidence input.

    Evidence contents remain in their owner (work-item, specification, ADR,
    repository, Cartography, or bounded context contract).  This record says
    which identity was used and which deterministic facts it establishes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, max_length=500)
    source: AdmissionEvidenceSource
    source_revision: str = Field(min_length=1, max_length=1_000)
    condition: AdmissionEvidenceCondition
    authoritative: bool
    establishes: frozenset[CompactAdmissionEvidence] = Field(default_factory=frozenset)
    hard_risk_flags: frozenset[str] = Field(default_factory=frozenset, max_length=50)
    mechanical_signal: bool = False

    @field_validator("hard_risk_flags")
    @classmethod
    def validate_hard_risk_flags(cls, flags: frozenset[str]) -> frozenset[str]:
        for flag in flags:
            if not flag or len(flag) > 200:
                raise ValueError("hard-risk flags must be bounded non-empty identifiers")
        return flags

    @model_validator(mode="after")
    def validate_claims_match_condition(self) -> Self:
        if self.condition is not AdmissionEvidenceCondition.PRESENT and (
            self.establishes or self.hard_risk_flags
        ):
            raise ValueError("only present evidence may establish facts or hard-risk flags")
        if self.mechanical_signal and self.hard_risk_flags:
            raise ValueError("mechanical signals cannot establish hard-risk flags")
        return self


class WorkflowAdmissionEvidence(BaseModel):
    """The bounded, revision-pinned input to deterministic admission."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    work_item_revision: str = Field(min_length=1, max_length=1_000)
    specification_revision: str | None = Field(default=None, min_length=1, max_length=1_000)
    evidence_references: tuple[AdmissionEvidenceReference, ...] = Field(
        min_length=1, max_length=50
    )

    @field_validator("evidence_references")
    @classmethod
    def validate_and_order_references(
        cls, references: tuple[AdmissionEvidenceReference, ...]
    ) -> tuple[AdmissionEvidenceReference, ...]:
        ids = [reference.evidence_id for reference in references]
        if len(ids) != len(set(ids)):
            raise ValueError("admission evidence references require unique evidence IDs")
        return tuple(sorted(references, key=lambda reference: reference.evidence_id))

    @model_validator(mode="after")
    def validate_revision_evidence(self) -> Self:
        has_work_item = any(
            reference.source is AdmissionEvidenceSource.WORK_ITEM
            and reference.source_revision == self.work_item_revision
            and reference.authoritative
            and reference.condition is AdmissionEvidenceCondition.PRESENT
            for reference in self.evidence_references
        )
        if not has_work_item:
            raise ValueError("work-item revision requires present authoritative work-item evidence")
        if self.specification_revision is not None:
            has_specification = any(
                reference.source is AdmissionEvidenceSource.SPECIFICATION
                and reference.source_revision == self.specification_revision
                and reference.authoritative
                and reference.condition is AdmissionEvidenceCondition.PRESENT
                for reference in self.evidence_references
            )
            if not has_specification:
                raise ValueError(
                    "specification revision requires present authoritative specification evidence"
                )
        return self


class WorkflowAdmissionPolicy(BaseModel):
    """Versioned deterministic policy, independent of presentation and models."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    policy_id: str = Field(default="workflow-admission", min_length=1, max_length=200)
    policy_version: str = Field(default="1.0", min_length=1, max_length=100)
    full_recipe_id: str = Field(
        default="full-implementation-run", min_length=1, max_length=200
    )
    compact_recipe_ids: tuple[str, ...] = Field(
        default=("compact-implementation-run",), min_length=1, max_length=20
    )
    configured_hard_risk_flags: frozenset[str] = Field(
        default_factory=lambda: _DEFAULT_HARD_RISK_FLAGS, min_length=1, max_length=100
    )
    required_compact_evidence: frozenset[CompactAdmissionEvidence] = Field(
        default_factory=lambda: _DEFAULT_COMPACT_EVIDENCE, min_length=1
    )

    @model_validator(mode="after")
    def validate_recipe_identity(self) -> Self:
        if self.full_recipe_id in self.compact_recipe_ids:
            raise ValueError("full recipe identity cannot be declared as a compact recipe")
        if len(self.compact_recipe_ids) != len(set(self.compact_recipe_ids)):
            raise ValueError("compact recipe identities must be unique")
        if any(not flag or len(flag) > 200 for flag in self.configured_hard_risk_flags):
            raise ValueError("configured hard-risk flags must be bounded non-empty identifiers")
        return self


class WorkflowAdmissionAssessment(BaseModel):
    """Inspectable, deterministic pre-admission assessment (not a Run)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    assessment_version: Literal["1.0"] = "1.0"
    assessment_id: str = Field(min_length=1, max_length=100)
    work_item_revision: str = Field(min_length=1, max_length=1_000)
    specification_revision: str | None = Field(default=None, min_length=1, max_length=1_000)
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=100)
    evidence_references: tuple[AdmissionEvidenceReference, ...] = Field(
        min_length=1, max_length=50
    )
    outcome: WorkflowAdmissionOutcome
    reasons: tuple[str, ...] = Field(min_length=1, max_length=30)
    hard_risk_flags: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    missing_evidence: tuple[CompactAdmissionEvidence, ...] = Field(
        default_factory=tuple, max_length=20
    )
    admissible_recipe_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


DEFAULT_WORKFLOW_ADMISSION_POLICY = WorkflowAdmissionPolicy()


def assess_workflow_admission(
    evidence: WorkflowAdmissionEvidence,
    *,
    policy: WorkflowAdmissionPolicy = DEFAULT_WORKFLOW_ADMISSION_POLICY,
) -> WorkflowAdmissionAssessment:
    """Classify revision-pinned evidence without provider, graph, or IO access.

    A configured hard prohibition wins before evidence completeness.  Otherwise
    every compact requirement needs present, authoritative, non-mechanical
    positive evidence.  Stale, contradictory, unconfigured risk, or incomplete
    inputs stay explicitly uncertain rather than becoming compact by absence.
    """

    references = evidence.evidence_references
    observed_flags = tuple(
        sorted(
            {
                flag
                for reference in references
                if reference.authoritative
                and not reference.mechanical_signal
                and reference.condition is AdmissionEvidenceCondition.PRESENT
                for flag in reference.hard_risk_flags
            }
        )
    )
    configured_flags = tuple(
        flag for flag in observed_flags if flag in policy.configured_hard_risk_flags
    )
    unconfigured_flags = tuple(
        flag for flag in observed_flags if flag not in policy.configured_hard_risk_flags
    )

    missing_evidence = tuple(
        fact
        for fact in sorted(policy.required_compact_evidence, key=lambda fact: fact.value)
        if not any(
            reference.authoritative
            and not reference.mechanical_signal
            and reference.condition is AdmissionEvidenceCondition.PRESENT
            and fact in reference.establishes
            for reference in references
        )
    )
    stale_references = tuple(
        reference.evidence_id
        for reference in references
        if reference.condition is AdmissionEvidenceCondition.STALE
    )
    contradictory_references = tuple(
        reference.evidence_id
        for reference in references
        if reference.condition is AdmissionEvidenceCondition.CONTRADICTORY
    )

    if configured_flags:
        outcome = WorkflowAdmissionOutcome.FULL_REQUIRED
        reasons = (
            "configured hard-risk evidence requires the full workflow: "
            + ", ".join(configured_flags),
        )
        admissible_recipe_ids = (policy.full_recipe_id,)
    elif missing_evidence or stale_references or contradictory_references or unconfigured_flags:
        outcome = WorkflowAdmissionOutcome.UNCERTAIN
        reasons_list: list[str] = []
        if missing_evidence:
            reasons_list.append(
                "missing positive compact evidence: "
                + ", ".join(fact.value for fact in missing_evidence)
            )
        if stale_references:
            reasons_list.append("stale admission evidence: " + ", ".join(stale_references))
        if contradictory_references:
            reasons_list.append(
                "contradictory admission evidence: " + ", ".join(contradictory_references)
            )
        if unconfigured_flags:
            reasons_list.append(
                "unconfigured hard-risk evidence requires policy or human review: "
                + ", ".join(unconfigured_flags)
            )
        reasons = tuple(reasons_list)
        admissible_recipe_ids = (policy.full_recipe_id,)
    else:
        outcome = WorkflowAdmissionOutcome.COMPACT_ADMISSIBLE
        reasons = (
            "all required positive compact evidence is present and no configured "
            "full-workflow prohibition was found",
        )
        admissible_recipe_ids = (*policy.compact_recipe_ids, policy.full_recipe_id)

    assessment_id = _assessment_identity(evidence, policy)
    return WorkflowAdmissionAssessment(
        assessment_id=assessment_id,
        work_item_revision=evidence.work_item_revision,
        specification_revision=evidence.specification_revision,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        evidence_references=references,
        outcome=outcome,
        reasons=reasons,
        hard_risk_flags=observed_flags,
        missing_evidence=missing_evidence,
        admissible_recipe_ids=admissible_recipe_ids,
    )


def _assessment_identity(
    evidence: WorkflowAdmissionEvidence, policy: WorkflowAdmissionPolicy
) -> str:
    """Return a stable identity for identical evidence and policy inputs."""

    payload = {
        "assessment_version": "1.0",
        "evidence": evidence.model_dump(mode="python"),
        "policy": policy.model_dump(mode="python"),
    }
    canonical = json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"))
    return "workflow-admission:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    """Normalize Pydantic/enum/set values for a deterministic content digest."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (set, frozenset)):
        normalized = [_canonicalize(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value

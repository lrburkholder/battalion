"""Deterministic, evidence-first next-step admission (BTN-230).

This module answers only what Battalion should do *next*.  In particular, an
implementation result is deliberately not a compact/full recipe decision;
RFC-0012 continues to own that follow-on admission question.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
)


class NextStep(str, Enum):
    """The finite, authority-neutral outcomes of next-step admission."""

    SPECIFICATION = "specification"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    CLARIFICATION = "clarification"
    NO_WORK = "no-work"


class NextStepEvidenceFact(str, Enum):
    """Deterministically established facts relevant before recipe selection."""

    AUTHORITATIVE_INTENT = "authoritative-intent"
    INSUFFICIENT_INTENT = "insufficient-intent"
    WORK_REQUIRED = "work-required"
    NO_WORK_REQUIRED = "no-work-required"
    ARCHITECTURE_REQUIRED = "architecture-required"


class NextStepAdmissionUpgradeRejected(ValueError):
    """A re-admission attempt would weaken or reinterpret prior handling."""


class NextStepAdmissionEvidence(BaseModel):
    """Bounded, revision-pinned evidence for one next-step decision.

    Facts are carried by the same exact evidence references already used by
    workflow admission.  A missing or conflicting fact is intentionally not
    coerced into implementation eligibility.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    work_item_revision: str = Field(min_length=1, max_length=1_000)
    specification_revision: str | None = Field(default=None, min_length=1, max_length=1_000)
    evidence_references: tuple[AdmissionEvidenceReference, ...] = Field(
        min_length=1, max_length=50
    )
    established_facts: tuple[NextStepEvidenceFact, ...] = Field(
        default_factory=tuple, max_length=20
    )
    fact_evidence_ids: tuple[tuple[NextStepEvidenceFact, str], ...] = Field(
        default_factory=tuple, max_length=20
    )

    @field_validator("evidence_references")
    @classmethod
    def order_references(
        cls, references: tuple[AdmissionEvidenceReference, ...]
    ) -> tuple[AdmissionEvidenceReference, ...]:
        identifiers = [reference.evidence_id for reference in references]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("next-step evidence references require unique evidence IDs")
        return tuple(sorted(references, key=lambda reference: reference.evidence_id))

    @field_validator("established_facts")
    @classmethod
    def order_facts(
        cls, facts: tuple[NextStepEvidenceFact, ...]
    ) -> tuple[NextStepEvidenceFact, ...]:
        if len(facts) != len(set(facts)):
            raise ValueError("next-step evidence facts must be unique")
        return tuple(sorted(facts, key=lambda fact: fact.value))

    @field_validator("fact_evidence_ids")
    @classmethod
    def order_fact_evidence(
        cls, bindings: tuple[tuple[NextStepEvidenceFact, str], ...]
    ) -> tuple[tuple[NextStepEvidenceFact, str], ...]:
        facts = [fact for fact, _ in bindings]
        if len(facts) != len(set(facts)):
            raise ValueError("each next-step fact requires one evidence reference")
        if any(not evidence_id or len(evidence_id) > 500 for _, evidence_id in bindings):
            raise ValueError("next-step fact evidence IDs must be bounded and non-empty")
        return tuple(sorted(bindings, key=lambda binding: binding[0].value))

    @model_validator(mode="after")
    def validate_evidence(self) -> "NextStepAdmissionEvidence":
        references = {reference.evidence_id: reference for reference in self.evidence_references}
        work_item = next(
            (
                reference
                for reference in references.values()
                if reference.source is AdmissionEvidenceSource.WORK_ITEM
                and reference.source_revision == self.work_item_revision
                and reference.authoritative
                and reference.condition is AdmissionEvidenceCondition.PRESENT
            ),
            None,
        )
        if work_item is None:
            raise ValueError("work-item revision requires present authoritative work-item evidence")
        if self.specification_revision is not None and not any(
            reference.source is AdmissionEvidenceSource.SPECIFICATION
            and reference.source_revision == self.specification_revision
            and reference.authoritative
            and reference.condition is AdmissionEvidenceCondition.PRESENT
            for reference in references.values()
        ):
            raise ValueError("specification revision requires present authoritative specification evidence")
        bound_facts = {fact for fact, _ in self.fact_evidence_ids}
        if bound_facts != set(self.established_facts):
            raise ValueError("every established next-step fact requires exact evidence")
        for fact, evidence_id in self.fact_evidence_ids:
            reference = references.get(evidence_id)
            if reference is None:
                raise ValueError(f"next-step fact {fact.value!r} references unknown evidence")
            if not reference.authoritative or reference.mechanical_signal:
                raise ValueError("next-step facts require authoritative non-mechanical evidence")
            if reference.condition is not AdmissionEvidenceCondition.PRESENT:
                raise ValueError("next-step facts require present evidence")
        return self


class NextStepAdmissionPolicy(BaseModel):
    """Versioned deterministic policy for the question preceding recipe admission."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    policy_id: str = Field(default="next-step-admission", min_length=1, max_length=200)
    policy_version: str = Field(default="1.0", min_length=1, max_length=100)


class SpecificationBypass(BaseModel):
    """Evidence that an existing accepted specification made a new one unnecessary."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    reason: str = Field(default="authoritative-specification-already-available")
    specification_revision: str = Field(min_length=1, max_length=1_000)
    evidence_id: str = Field(min_length=1, max_length=500)


class NextStepAdmissionAssessment(BaseModel):
    """Inspectable deterministic result; it neither starts work nor selects recipes."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    assessment_version: str = Field(default="1.0", pattern=r"^1\.0$")
    assessment_id: str = Field(min_length=1, max_length=200)
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=100)
    work_item_revision: str = Field(min_length=1, max_length=1_000)
    specification_revision: str | None = Field(default=None, min_length=1, max_length=1_000)
    evidence_references: tuple[AdmissionEvidenceReference, ...] = Field(min_length=1, max_length=50)
    next_step: NextStep
    reasons: tuple[str, ...] = Field(min_length=1, max_length=20)
    requires_tactician_assessment: bool = False
    specification_bypass: SpecificationBypass | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "NextStepAdmissionAssessment":
        if self.requires_tactician_assessment and self.next_step is not NextStep.CLARIFICATION:
            raise ValueError("only clarification may require a Tactician assessment")
        if self.specification_bypass is not None:
            if self.next_step not in {NextStep.ARCHITECTURE, NextStep.IMPLEMENTATION}:
                raise ValueError("a specification bypass requires an admitted downstream step")
            if self.specification_bypass.specification_revision != self.specification_revision:
                raise ValueError("specification bypass must reference the assessed revision")
        return self


DEFAULT_NEXT_STEP_ADMISSION_POLICY = NextStepAdmissionPolicy()


def assess_next_step(
    evidence: NextStepAdmissionEvidence,
    *,
    policy: NextStepAdmissionPolicy = DEFAULT_NEXT_STEP_ADMISSION_POLICY,
) -> NextStepAdmissionAssessment:
    """Resolve only clear cases, leaving semantic uncertainty to Tactician/humans."""

    facts = set(evidence.established_facts)
    contradictory = (
        {NextStepEvidenceFact.AUTHORITATIVE_INTENT, NextStepEvidenceFact.INSUFFICIENT_INTENT}
        <= facts
        or {NextStepEvidenceFact.WORK_REQUIRED, NextStepEvidenceFact.NO_WORK_REQUIRED} <= facts
    )
    if contradictory:
        next_step = NextStep.CLARIFICATION
        reasons = ("contradictory authoritative next-step evidence requires clarification",)
        requires_tactician_assessment = True
    elif NextStepEvidenceFact.NO_WORK_REQUIRED in facts:
        next_step = NextStep.NO_WORK
        reasons = ("authoritative evidence establishes that no work is required",)
        requires_tactician_assessment = False
    elif NextStepEvidenceFact.INSUFFICIENT_INTENT in facts:
        next_step = NextStep.SPECIFICATION
        reasons = ("authoritative evidence establishes insufficient product intent",)
        requires_tactician_assessment = False
    elif (
        NextStepEvidenceFact.WORK_REQUIRED in facts
        and NextStepEvidenceFact.AUTHORITATIVE_INTENT in facts
    ):
        if NextStepEvidenceFact.ARCHITECTURE_REQUIRED in facts:
            next_step = NextStep.ARCHITECTURE
            reasons = ("authoritative evidence requires architecture before implementation",)
        else:
            next_step = NextStep.IMPLEMENTATION
            reasons = ("authoritative intent and required work admit implementation handling",)
        requires_tactician_assessment = False
    else:
        next_step = NextStep.CLARIFICATION
        reasons = ("authoritative evidence is insufficient to determine the next step",)
        requires_tactician_assessment = True

    specification_bypass = _specification_bypass(evidence, next_step)
    return NextStepAdmissionAssessment(
        assessment_id=_assessment_identity(evidence, policy),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        work_item_revision=evidence.work_item_revision,
        specification_revision=evidence.specification_revision,
        evidence_references=evidence.evidence_references,
        next_step=next_step,
        reasons=reasons,
        requires_tactician_assessment=requires_tactician_assessment,
        specification_bypass=specification_bypass,
    )


def upgrade_next_step_admission(
    previous: NextStepAdmissionAssessment,
    evidence: NextStepAdmissionEvidence,
    *,
    policy: NextStepAdmissionPolicy = DEFAULT_NEXT_STEP_ADMISSION_POLICY,
) -> NextStepAdmissionAssessment:
    """Re-admit only when new evidence requires stronger pre-execution handling.

    This is the RFC-0020 upgrade-only ratchet before an Implementation recipe
    can continue. It deliberately does not treat a changed source snapshot as
    permission to silently choose a lighter next step.
    """
    current = assess_next_step(evidence, policy=policy)
    if (
        previous.policy_id != policy.policy_id
        or previous.policy_version != policy.policy_version
    ):
        raise NextStepAdmissionUpgradeRejected(
            "next-step admission policy changed; fresh human admission is required"
        )
    if current == previous:
        return current
    allowed_redirects = {
        NextStep.IMPLEMENTATION: {NextStep.SPECIFICATION, NextStep.CLARIFICATION},
        NextStep.ARCHITECTURE: {NextStep.SPECIFICATION, NextStep.CLARIFICATION},
        NextStep.SPECIFICATION: {NextStep.CLARIFICATION},
    }
    if current.next_step not in allowed_redirects.get(previous.next_step, set()):
        raise NextStepAdmissionUpgradeRejected(
            "next-step re-admission may only redirect to stronger handling"
        )
    return current


def _specification_bypass(
    evidence: NextStepAdmissionEvidence, next_step: NextStep
) -> SpecificationBypass | None:
    if evidence.specification_revision is None or next_step not in {
        NextStep.ARCHITECTURE,
        NextStep.IMPLEMENTATION,
    }:
        return None
    for fact, evidence_id in evidence.fact_evidence_ids:
        reference = next(
            item for item in evidence.evidence_references if item.evidence_id == evidence_id
        )
        if fact is NextStepEvidenceFact.AUTHORITATIVE_INTENT and reference.source is AdmissionEvidenceSource.SPECIFICATION:
            return SpecificationBypass(
                specification_revision=evidence.specification_revision, evidence_id=evidence_id
            )
    return None


def _assessment_identity(
    evidence: NextStepAdmissionEvidence, policy: NextStepAdmissionPolicy
) -> str:
    payload = {"assessment_version": "1.0", "evidence": evidence.model_dump(mode="python"), "policy": policy.model_dump(mode="python")}
    canonical = json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"))
    return "next-step-admission:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonicalize(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value

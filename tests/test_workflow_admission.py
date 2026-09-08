"""Tests for deterministic, evidence-first workflow admission (BTN-139)."""

from __future__ import annotations

import pytest

from battalion.application import (
    AssessWorkflowAdmission,
    InspectWorkflowAdmissionPolicy,
    assess_workflow_admission as assess_workflow_admission_via_application,
    inspect_workflow_admission_policy,
)
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    CompactAdmissionEvidence,
    HardRiskFlag,
    WorkflowAdmissionEvidence,
    WorkflowAdmissionOutcome,
    WorkflowAdmissionPolicy,
    assess_workflow_admission,
)


def _reference(
    evidence_id: str,
    *,
    source: AdmissionEvidenceSource = AdmissionEvidenceSource.REPOSITORY,
    establishes: frozenset[CompactAdmissionEvidence] = frozenset(),
    condition: AdmissionEvidenceCondition = AdmissionEvidenceCondition.PRESENT,
    hard_risk_flags: frozenset[str] = frozenset(),
    mechanical_signal: bool = False,
) -> AdmissionEvidenceReference:
    return AdmissionEvidenceReference(
        evidence_id=evidence_id,
        source=source,
        source_revision="revision-1",
        condition=condition,
        authoritative=True,
        establishes=establishes,
        hard_risk_flags=hard_risk_flags,
        mechanical_signal=mechanical_signal,
    )


def _positive_evidence(
    *additional: AdmissionEvidenceReference,
    omit: frozenset[CompactAdmissionEvidence] = frozenset(),
) -> WorkflowAdmissionEvidence:
    references = [
        _reference("work-item", source=AdmissionEvidenceSource.WORK_ITEM),
        *(
            _reference(
                f"evidence-{fact.value}",
                establishes=frozenset((fact,)),
            )
            for fact in CompactAdmissionEvidence
            if fact not in omit
        ),
        *additional,
    ]
    return WorkflowAdmissionEvidence(
        work_item_revision="revision-1", evidence_references=tuple(references)
    )


@pytest.mark.parametrize(
    "scenario,evidence",
    [
        ("hello-world", _positive_evidence()),
        ("small-verified-bug", _positive_evidence()),
        (
            "docs-config",
            WorkflowAdmissionEvidence(
                work_item_revision="revision-1",
                specification_revision="docs-revision-4",
                evidence_references=(
                    *_positive_evidence().evidence_references,
                    AdmissionEvidenceReference(
                        evidence_id="accepted-docs-config-specification",
                        source=AdmissionEvidenceSource.SPECIFICATION,
                        source_revision="docs-revision-4",
                        condition=AdmissionEvidenceCondition.PRESENT,
                        authoritative=True,
                    ),
                ),
            ),
        ),
    ],
)
def test_known_bounded_work_is_compact_admissible(
    scenario: str, evidence: WorkflowAdmissionEvidence
) -> None:
    assessment = assess_workflow_admission(evidence)

    assert assessment.outcome is WorkflowAdmissionOutcome.COMPACT_ADMISSIBLE, scenario
    assert assessment.admissible_recipe_ids == (
        "compact-implementation-run",
        "full-implementation-run",
    )
    assert assessment.missing_evidence == ()


def test_assessment_identity_is_deterministic_and_records_evidence_identities() -> None:
    evidence = _positive_evidence(
        _reference(
            "file-count",
            establishes=frozenset((CompactAdmissionEvidence.BOUNDED_EXPECTED_SCOPE,)),
            mechanical_signal=True,
        )
    )

    first = assess_workflow_admission(evidence)
    second = assess_workflow_admission(evidence)

    assert first.assessment_id == second.assessment_id
    assert [reference.evidence_id for reference in first.evidence_references] == sorted(
        reference.evidence_id for reference in evidence.evidence_references
    )


def test_mechanical_size_signal_cannot_independently_admit_compact_work() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(
            _reference(
                "file-count",
                establishes=frozenset((CompactAdmissionEvidence.BOUNDED_EXPECTED_SCOPE,)),
                mechanical_signal=True,
            ),
            omit=frozenset((CompactAdmissionEvidence.BOUNDED_EXPECTED_SCOPE,)),
        )
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
    assert assessment.missing_evidence == (CompactAdmissionEvidence.BOUNDED_EXPECTED_SCOPE,)


@pytest.mark.parametrize(
    "flag",
    [
        HardRiskFlag.MATERIAL_ARCHITECTURE_BOUNDARY.value,
        HardRiskFlag.AUTHORIZATION_SECRETS_PRIVACY_SECURITY.value,
        HardRiskFlag.PUBLIC_INTERFACE_OR_SCHEMA.value,
        HardRiskFlag.PERSISTENCE_OR_MIGRATION.value,
        HardRiskFlag.HIGH_CONSEQUENCE_RELEASE_DEPLOYMENT.value,
        HardRiskFlag.CROSS_SYSTEM_POLICY.value,
    ],
    ids=[
        "material-architecture-boundary",
        "deceptive-auth-security-change",
        "schema-interface-change",
        "persistence-migration",
        "release-deployment",
        "cross-domain-few-lines",
    ],
)
def test_configured_hard_risks_require_full_even_with_compact_evidence(flag: str) -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(_reference("hard-risk", hard_risk_flags=frozenset((flag,))))
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.FULL_REQUIRED
    assert assessment.hard_risk_flags == (flag,)
    assert assessment.admissible_recipe_ids == ("full-implementation-run",)


def test_ambiguous_scope_is_uncertain() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(omit=frozenset((CompactAdmissionEvidence.BOUNDED_EXPECTED_SCOPE,)))
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
    assert assessment.missing_evidence == (CompactAdmissionEvidence.BOUNDED_EXPECTED_SCOPE,)


def test_contradictory_cartography_is_uncertain() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(
            _reference(
                "cartography-contradiction",
                source=AdmissionEvidenceSource.CARTOGRAPHY,
                condition=AdmissionEvidenceCondition.CONTRADICTORY,
            )
        )
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
    assert "contradictory admission evidence" in assessment.reasons[0]


def test_stale_cartography_is_uncertain() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(
            _reference(
                "stale-cartography",
                source=AdmissionEvidenceSource.CARTOGRAPHY,
                condition=AdmissionEvidenceCondition.STALE,
            )
        )
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
    assert "stale admission evidence" in assessment.reasons[0]


def test_missing_behavioral_verification_is_uncertain() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(
            omit=frozenset((CompactAdmissionEvidence.EXECUTABLE_BEHAVIORAL_VERIFICATION,))
        )
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
    assert assessment.missing_evidence == (
        CompactAdmissionEvidence.EXECUTABLE_BEHAVIORAL_VERIFICATION,
    )


def test_unconfigured_hard_risk_is_uncertain_not_a_policy_override() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(_reference("unknown-risk", hard_risk_flags=frozenset(("new-risk",))))
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.UNCERTAIN
    assert assessment.hard_risk_flags == ("new-risk",)


def test_project_configured_future_full_only_condition_requires_full() -> None:
    assessment = assess_workflow_admission(
        _positive_evidence(
            _reference("project-policy", hard_risk_flags=frozenset(("regulated-domain",)))
        ),
        policy=WorkflowAdmissionPolicy(
            configured_hard_risk_flags=frozenset(("regulated-domain",))
        ),
    )

    assert assessment.outcome is WorkflowAdmissionOutcome.FULL_REQUIRED


def test_application_exposes_policy_inspection_and_credential_free_assessment() -> None:
    policy = WorkflowAdmissionPolicy(policy_version="test-1")

    assert inspect_workflow_admission_policy(
        InspectWorkflowAdmissionPolicy(), policy=policy
    ) is policy
    assert assess_workflow_admission_via_application(
        AssessWorkflowAdmission(evidence=_positive_evidence()), policy=policy
    ).policy_version == "test-1"

"""Contract tests for BTN-230 next-step admission."""

import pytest

from battalion.next_step_admission import (
    NextStep,
    NextStepAdmissionEvidence,
    NextStepEvidenceFact,
    assess_next_step,
    upgrade_next_step_admission,
)
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    CompactAdmissionEvidence,
    HardRiskFlag,
    WorkflowAdmissionEvidence,
    WorkflowAdmissionOutcome,
    assess_workflow_admission,
)


def _reference(evidence_id: str, source: AdmissionEvidenceSource) -> AdmissionEvidenceReference:
    return AdmissionEvidenceReference(
        evidence_id=evidence_id,
        source=source,
        source_revision="revision-1",
        condition=AdmissionEvidenceCondition.PRESENT,
        authoritative=True,
    )


def _evidence(*facts: tuple[NextStepEvidenceFact, str], specification_revision: str | None = None) -> NextStepAdmissionEvidence:
    references = [_reference("work-item", AdmissionEvidenceSource.WORK_ITEM)]
    if specification_revision is not None:
        references.append(
            AdmissionEvidenceReference(
                evidence_id="accepted-specification",
                source=AdmissionEvidenceSource.SPECIFICATION,
                source_revision=specification_revision,
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
            )
        )
    return NextStepAdmissionEvidence(
        work_item_revision="revision-1",
        specification_revision=specification_revision,
        evidence_references=tuple(references),
        established_facts=tuple(fact for fact, _ in facts),
        fact_evidence_ids=facts,
    )


def test_deterministic_insufficient_intent_starts_specification() -> None:
    assessment = assess_next_step(
        _evidence((NextStepEvidenceFact.INSUFFICIENT_INTENT, "work-item"))
    )

    assert assessment.next_step is NextStep.SPECIFICATION
    assert assessment.requires_tactician_assessment is False


def test_authoritative_accepted_specification_bypasses_specification() -> None:
    assessment = assess_next_step(
        _evidence(
            (NextStepEvidenceFact.AUTHORITATIVE_INTENT, "accepted-specification"),
            (NextStepEvidenceFact.WORK_REQUIRED, "work-item"),
            specification_revision="accepted-revision-4",
        )
    )

    assert assessment.next_step is NextStep.IMPLEMENTATION
    assert assessment.specification_bypass is not None
    assert assessment.specification_bypass.evidence_id == "accepted-specification"


def test_architecture_is_selected_before_implementation_when_evidence_requires_it() -> None:
    assessment = assess_next_step(
        _evidence(
            (NextStepEvidenceFact.AUTHORITATIVE_INTENT, "work-item"),
            (NextStepEvidenceFact.WORK_REQUIRED, "work-item"),
            (NextStepEvidenceFact.ARCHITECTURE_REQUIRED, "work-item"),
        )
    )

    assert assessment.next_step is NextStep.ARCHITECTURE


def test_uncertain_evidence_requests_advisory_tactician_assessment() -> None:
    assessment = assess_next_step(
        _evidence((NextStepEvidenceFact.WORK_REQUIRED, "work-item"))
    )

    assert assessment.next_step is NextStep.CLARIFICATION
    assert assessment.requires_tactician_assessment is True


def test_deterministic_no_work_does_not_manufacture_an_execution() -> None:
    assessment = assess_next_step(
        _evidence((NextStepEvidenceFact.NO_WORK_REQUIRED, "work-item"))
    )

    assert assessment.next_step is NextStep.NO_WORK


def test_upgrade_only_redirects_implementation_to_specification_for_hidden_intent_gap() -> None:
    implementation = assess_next_step(
        _evidence(
            (NextStepEvidenceFact.AUTHORITATIVE_INTENT, "work-item"),
            (NextStepEvidenceFact.WORK_REQUIRED, "work-item"),
        )
    )
    redirected = upgrade_next_step_admission(
        implementation,
        _evidence((NextStepEvidenceFact.INSUFFICIENT_INTENT, "work-item")),
    )

    assert implementation.next_step is NextStep.IMPLEMENTATION
    assert redirected.next_step is NextStep.SPECIFICATION


@pytest.mark.parametrize(
    "hard_risk,expected",
    [
        (frozenset(), WorkflowAdmissionOutcome.COMPACT_ADMISSIBLE),
        (
            frozenset((HardRiskFlag.MATERIAL_ARCHITECTURE_BOUNDARY.value,)),
            WorkflowAdmissionOutcome.FULL_REQUIRED,
        ),
    ],
    ids=["compact-follow-on", "full-follow-on"],
)
def test_implementation_next_step_does_not_choose_its_compact_or_full_recipe(
    hard_risk: frozenset[str], expected: WorkflowAdmissionOutcome
) -> None:
    references = [
        _reference("work-item", AdmissionEvidenceSource.WORK_ITEM),
        *(
            AdmissionEvidenceReference(
                evidence_id=f"compact-{fact.value}",
                source=AdmissionEvidenceSource.REPOSITORY,
                source_revision="revision-1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
                establishes=frozenset((fact,)),
            )
            for fact in CompactAdmissionEvidence
        ),
    ]
    if hard_risk:
        references.append(
            AdmissionEvidenceReference(
                evidence_id="hard-risk",
                source=AdmissionEvidenceSource.ADR,
                source_revision="revision-1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
                hard_risk_flags=hard_risk,
            )
        )
    workflow_evidence = WorkflowAdmissionEvidence(
        work_item_revision="revision-1", evidence_references=tuple(references)
    )
    next_evidence = NextStepAdmissionEvidence(
        work_item_revision="revision-1",
        evidence_references=workflow_evidence.evidence_references,
        established_facts=(
            NextStepEvidenceFact.AUTHORITATIVE_INTENT,
            NextStepEvidenceFact.WORK_REQUIRED,
        ),
        fact_evidence_ids=(
            (NextStepEvidenceFact.AUTHORITATIVE_INTENT, "work-item"),
            (NextStepEvidenceFact.WORK_REQUIRED, "work-item"),
        ),
    )

    assert assess_next_step(next_evidence).next_step is NextStep.IMPLEMENTATION
    assert assess_workflow_admission(workflow_evidence).outcome is expected

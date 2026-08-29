"""Contract tests for BTN-141 human workflow-admission operations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from battalion.application import (
    DecideWorkflowAdmission,
    HumanActionRejected,
    InspectWorkflowAdmission,
    StaleWorkflowAdmission,
    WorkflowAdmissionRejected,
    decide_workflow_admission,
    inspect_workflow_admission,
)
from battalion.tactician import TacticianAssessment
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    CompactAdmissionEvidence,
    HardRiskFlag,
    WorkflowAdmissionEvidence,
    WorkflowAdmissionPolicy,
    assess_workflow_admission,
)
from battalion.workflow_admission_decisions import WorkflowAdmissionDisposition
from battalion.workflow_recipes import (
    FULL_IMPLEMENTATION_RECIPE,
    WorkflowRecipeRegistry,
)


def _evidence(
    revision: str = "work-item-v1",
    *,
    compact: bool = False,
    hard_risk: str | None = None,
) -> WorkflowAdmissionEvidence:
    references = [
        AdmissionEvidenceReference(
            evidence_id="work-item",
            source=AdmissionEvidenceSource.WORK_ITEM,
            source_revision=revision,
            condition=AdmissionEvidenceCondition.PRESENT,
            authoritative=True,
            hard_risk_flags=frozenset((hard_risk,)) if hard_risk else frozenset(),
        )
    ]
    if compact:
        references.extend(
            AdmissionEvidenceReference(
                evidence_id=f"compact-{fact.value}",
                source=AdmissionEvidenceSource.REPOSITORY,
                source_revision="repository-v1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
                establishes=frozenset((fact,)),
            )
            for fact in CompactAdmissionEvidence
        )
    return WorkflowAdmissionEvidence(
        work_item_revision=revision,
        evidence_references=tuple(references),
    )


def _registry() -> WorkflowRecipeRegistry:
    compact = FULL_IMPLEMENTATION_RECIPE.model_copy(
        update={"recipe_id": "compact-implementation-run"}
    )
    return WorkflowRecipeRegistry((FULL_IMPLEMENTATION_RECIPE, compact))


def _tactician(assessment, *, recommendation: str = "compact-implementation-run"):
    return TacticianAssessment.model_validate(
        {
            "assessment_id": "tactician-assessment-v1",
            "recommendation_kind": "recipe",
            "recommended_recipe_id": recommendation,
            "recommended_recipe_version": "1.0",
            "rationale": ["The human should review the uncertain boundary."],
            "risk_flags": ["semantic-uncertainty"],
            "missing_evidence": [],
            "input_evidence_references": [
                reference.model_dump() for reference in assessment.evidence_references
            ],
            "provenance": {
                "configured_model": "tactician-model",
                "provider_model": "tactician-model",
                "prompt_contract_version": "tactician/v1",
                "prompt_sha256": "0" * 64,
                "temperature": 0,
                "max_retries": 0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": None,
                    "cost_currency": None,
                    "cost_source": "unknown",
                },
            },
        }
    )


def _command(tmp_path, assessment, evidence, disposition, **kwargs):
    return DecideWorkflowAdmission(
        project_root=tmp_path,
        assessment=assessment,
        evidence=evidence,
        disposition=disposition,
        **kwargs,
    )


def test_compact_requires_and_records_explicit_human_approval(tmp_path) -> None:
    evidence = _evidence(compact=True)
    assessment = assess_workflow_admission(evidence)

    result = decide_workflow_admission(
        _command(tmp_path, assessment, evidence, WorkflowAdmissionDisposition.COMPACT),
        registry=_registry(),
        _decision_id="decision-compact",
        _clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
    )

    assert result.recipe is not None
    assert result.recipe.recipe_id == "compact-implementation-run"
    assert result.decision.disposition is WorkflowAdmissionDisposition.COMPACT
    assert result.decision.admission_assessment_id == assessment.assessment_id
    assert result.decision.selected_recipe_version == "1.0"
    assert result.decision.approving_actor_id
    assert result.decision.work_item_revision == "work-item-v1"


def test_human_can_choose_full_when_compact_is_admissible(tmp_path) -> None:
    evidence = _evidence(compact=True)
    assessment = assess_workflow_admission(evidence)

    result = decide_workflow_admission(
        _command(tmp_path, assessment, evidence, WorkflowAdmissionDisposition.FULL),
        registry=_registry(),
    )

    assert result.recipe is FULL_IMPLEMENTATION_RECIPE
    assert result.decision.disposition is WorkflowAdmissionDisposition.FULL


@pytest.mark.parametrize(
    "disposition",
    [WorkflowAdmissionDisposition.CLARIFICATION, WorkflowAdmissionDisposition.CANCELLED],
)
def test_human_can_clarify_or_cancel_without_selecting_execution(tmp_path, disposition) -> None:
    evidence = _evidence()
    assessment = assess_workflow_admission(evidence)

    result = decide_workflow_admission(
        _command(
            tmp_path,
            assessment,
            evidence,
            disposition,
            annotation="Need a clearer boundary before implementation.",
        ),
        registry=_registry(),
    )

    assert result.recipe is None
    assert result.decision.annotation == "Need a clearer boundary before implementation."


def test_unknown_actor_cannot_make_an_admission_choice(tmp_path) -> None:
    evidence = _evidence(compact=True)
    assessment = assess_workflow_admission(evidence)

    with pytest.raises(HumanActionRejected, match="Unknown Actor"):
        decide_workflow_admission(
            _command(
                tmp_path,
                assessment,
                evidence,
                WorkflowAdmissionDisposition.FULL,
                actor_id=uuid4(),
            )
        )


def test_full_required_assessment_cannot_select_compact(tmp_path) -> None:
    evidence = _evidence(
        compact=True,
        hard_risk=HardRiskFlag.AUTHORIZATION_SECRETS_PRIVACY_SECURITY.value,
    )
    assessment = assess_workflow_admission(evidence)

    with pytest.raises(WorkflowAdmissionRejected, match="requires the full workflow"):
        decide_workflow_admission(
            _command(tmp_path, assessment, evidence, WorkflowAdmissionDisposition.COMPACT),
            registry=_registry(),
        )


def test_human_can_override_tactician_compact_recommendation_with_full(tmp_path) -> None:
    evidence = _evidence()
    assessment = assess_workflow_admission(evidence)
    tactician = _tactician(assessment)

    result = decide_workflow_admission(
        _command(
            tmp_path,
            assessment,
            evidence,
            WorkflowAdmissionDisposition.FULL,
            tactician_assessment=tactician,
            annotation="The operational consequence warrants the full workflow.",
        ),
        registry=_registry(),
    )

    assert result.decision.tactician_assessment_id == tactician.assessment_id
    assert result.decision.annotation == "The operational consequence warrants the full workflow."
    assert result.decision.admitted_risk_flags == ("semantic-uncertainty",)
    assert tactician.recommended_recipe_id == "compact-implementation-run"


def test_human_can_override_tactician_full_recommendation_with_compact(tmp_path) -> None:
    evidence = _evidence()
    assessment = assess_workflow_admission(evidence)
    tactician = _tactician(assessment, recommendation="full-implementation-run")

    result = decide_workflow_admission(
        _command(
            tmp_path,
            assessment,
            evidence,
            WorkflowAdmissionDisposition.COMPACT,
            tactician_assessment=tactician,
            annotation="Existing architecture and local operational context bound the change.",
        ),
        registry=_registry(),
    )

    assert result.recipe is not None
    assert result.recipe.recipe_id == "compact-implementation-run"
    assert result.decision.tactician_assessment_id == tactician.assessment_id
    assert tactician.recommended_recipe_id == "full-implementation-run"


def test_changed_evidence_requires_reassessment_before_decision(tmp_path) -> None:
    prior_evidence = _evidence("work-item-v1", compact=True)
    assessment = assess_workflow_admission(prior_evidence)
    current_evidence = _evidence("work-item-v2", compact=True)

    with pytest.raises(StaleWorkflowAdmission, match="reassess before choosing"):
        decide_workflow_admission(
            _command(
                tmp_path,
                assessment,
                current_evidence,
                WorkflowAdmissionDisposition.FULL,
            ),
            registry=_registry(),
        )


def test_changed_policy_requires_reassessment_before_decision(tmp_path) -> None:
    evidence = _evidence(compact=True)
    assessment = assess_workflow_admission(evidence)

    with pytest.raises(StaleWorkflowAdmission, match="reassess before choosing"):
        decide_workflow_admission(
            _command(tmp_path, assessment, evidence, WorkflowAdmissionDisposition.FULL),
            policy=WorkflowAdmissionPolicy(policy_version="2.0"),
            registry=_registry(),
        )


def test_inspection_exposes_only_valid_choices_and_keeps_tactician_separate(tmp_path) -> None:
    del tmp_path
    evidence = _evidence()
    assessment = assess_workflow_admission(evidence)
    tactician = _tactician(assessment)

    inspection = inspect_workflow_admission(
        InspectWorkflowAdmission(
            assessment=assessment,
            evidence=evidence,
            tactician_assessment=tactician,
        )
    )

    assert inspection.tactician_assessment is tactician
    assert inspection.available_dispositions == (
        WorkflowAdmissionDisposition.FULL,
        WorkflowAdmissionDisposition.COMPACT,
        WorkflowAdmissionDisposition.CLARIFICATION,
        WorkflowAdmissionDisposition.CANCELLED,
    )

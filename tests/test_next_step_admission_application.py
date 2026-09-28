"""Application authority tests for BTN-230 next-step admission."""

import pytest

from battalion.application import (
    AdmitNextStep,
    AssessNextStepAdmission,
    NextStepAdmissionRejected,
    admit_next_step,
    assess_next_step_admission,
    bootstrap_local_actor,
)
from battalion.identity import load_project_identity
from battalion.next_step_admission import (
    NextStep,
    NextStepAdmissionEvidence,
    NextStepEvidenceFact,
)
from battalion.specification_execution import SpecificationExecutionRepository
from battalion.tactician import (
    TacticianAssessmentInput,
    TacticianEvidence,
    TacticianRecipeSummary,
    run_tactician,
)
from battalion.llm.litellm_client import NodeLLMConfig
from battalion.workflow_recipes import DEFAULT_WORKFLOW_RECIPE_REGISTRY
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
)


def _evidence() -> NextStepAdmissionEvidence:
    reference = AdmissionEvidenceReference(
        evidence_id="work-item",
        source=AdmissionEvidenceSource.WORK_ITEM,
        source_revision="work-r1",
        condition=AdmissionEvidenceCondition.PRESENT,
        authoritative=True,
    )
    return NextStepAdmissionEvidence(
        work_item_revision="work-r1",
        evidence_references=(reference,),
        established_facts=(NextStepEvidenceFact.INSUFFICIENT_INTENT,),
        fact_evidence_ids=((NextStepEvidenceFact.INSUFFICIENT_INTENT, "work-item"),),
    )


def _uncertain_evidence() -> NextStepAdmissionEvidence:
    reference = AdmissionEvidenceReference(
        evidence_id="work-item",
        source=AdmissionEvidenceSource.WORK_ITEM,
        source_revision="work-r1",
        condition=AdmissionEvidenceCondition.PRESENT,
        authoritative=True,
    )
    return NextStepAdmissionEvidence(
        work_item_revision="work-r1",
        evidence_references=(reference,),
        established_facts=(NextStepEvidenceFact.WORK_REQUIRED,),
        fact_evidence_ids=((NextStepEvidenceFact.WORK_REQUIRED, "work-item"),),
    )


def test_authorized_human_admits_specification_and_battalion_starts_it(tmp_path) -> None:
    load_project_identity(tmp_path, create=True)
    actor_id = bootstrap_local_actor(tmp_path, "Operator").local_actor_id
    assert actor_id is not None
    assessment = assess_next_step_admission(AssessNextStepAdmission(_evidence()))

    result = admit_next_step(
        AdmitNextStep(
            project_root=tmp_path,
            canonical_work_identity="github:example/project#230",
            assessment=assessment,
            evidence=_evidence(),
            selected_next_step=NextStep.SPECIFICATION,
            actor_id=actor_id,
        )
    )

    assert result.specification_execution is not None
    assert result.specification_execution.execution.requesting_actor_id == actor_id
    assert result.specification_execution.admission_assessment == assessment
    assert SpecificationExecutionRepository(tmp_path).get(
        result.specification_execution.execution.execution_id
    ) == result.specification_execution


def test_human_cannot_override_deterministic_next_step(tmp_path) -> None:
    load_project_identity(tmp_path, create=True)
    actor_id = bootstrap_local_actor(tmp_path, "Operator").local_actor_id
    assert actor_id is not None
    assessment = assess_next_step_admission(AssessNextStepAdmission(_evidence()))

    with pytest.raises(NextStepAdmissionRejected, match="must match"):
        admit_next_step(
            AdmitNextStep(
                project_root=tmp_path,
                canonical_work_identity="github:example/project#230",
                assessment=assessment,
                evidence=_evidence(),
                selected_next_step=NextStep.IMPLEMENTATION,
                actor_id=actor_id,
            )
        )


def test_human_may_choose_specification_despite_tactician_architecture_recommendation(tmp_path) -> None:
    load_project_identity(tmp_path, create=True)
    actor_id = bootstrap_local_actor(tmp_path, "Operator").local_actor_id
    assert actor_id is not None
    evidence = _uncertain_evidence()
    assessment = assess_next_step_admission(AssessNextStepAdmission(evidence))
    tactician = run_tactician(
        TacticianAssessmentInput(
            next_step_assessment=assessment,
            evidence=(
                TacticianEvidence(
                    evidence_id="work-item",
                    source=AdmissionEvidenceSource.WORK_ITEM,
                    source_revision="work-r1",
                    content="The requested behavior lacks authoritative product intent.",
                ),
            ),
            registered_recipe_summaries=(
                TacticianRecipeSummary(
                    recipe_id="specification",
                    recipe_version="1.0",
                    summary="Human-reviewed specification workflow.",
                ),
            ),
        ),
        NodeLLMConfig(model="tactician-model"),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=lambda *_: {
            "choices": [{"message": {"content": """{
                \"recommendation_kind\": \"next-step\",
                \"recommended_recipe_id\": null,
                \"recommended_recipe_version\": null,
                \"recommended_next_step\": \"architecture\",
                \"rationale\": [\"Product intent requires human-reviewed specification.\"],
                \"risk_flags\": [],
                \"missing_evidence\": [\"accepted product intent\"]
            }"""}}],
        },
    )

    result = admit_next_step(
        AdmitNextStep(
            project_root=tmp_path,
            canonical_work_identity="github:example/project#230",
            assessment=assessment,
            evidence=evidence,
            selected_next_step=NextStep.SPECIFICATION,
            actor_id=actor_id,
            tactician_assessment=tactician,
        )
    )

    assert result.specification_execution is not None
    assert result.specification_execution.tactician_assessment == tactician


def test_tactician_recommendation_cannot_start_specification_without_human_admission(
    tmp_path,
) -> None:
    load_project_identity(tmp_path, create=True)
    actor_id = bootstrap_local_actor(tmp_path, "Operator").local_actor_id
    assert actor_id is not None
    evidence = _uncertain_evidence()
    assessment = assess_next_step_admission(AssessNextStepAdmission(evidence))

    with pytest.raises(NextStepAdmissionRejected, match="requires a Tactician assessment"):
        admit_next_step(
            AdmitNextStep(
                project_root=tmp_path,
                canonical_work_identity="github:example/project#230",
                assessment=assessment,
                evidence=evidence,
                selected_next_step=NextStep.SPECIFICATION,
                actor_id=actor_id,
            )
        )

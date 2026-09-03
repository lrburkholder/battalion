"""Application persistence and resume tests for BTN-143."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from battalion.admission_presentation import render_workflow_admission_history
from battalion.application import (
    CreateAdmittedRun,
    InspectRunWorkflowAdmission,
    RecordAdmittedWorkflowCompletion,
    RecordAdmittedWorkflowStage,
    ResumeRun,
    StateReadFailed,
    StaleWorkflowAdmission,
    UpgradeAdmittedWorkflow,
    WorkflowAdmissionRejected,
    WorkflowAdmissionResumeRejected,
    create_admitted_run,
    inspect_run_workflow_admission,
    record_admitted_workflow_completion,
    record_admitted_workflow_stage,
    resume_run,
    upgrade_admitted_workflow,
)
from battalion.config import BattalionConfig
from battalion.identity import load_run_catalog
from battalion.state.models import Budget, RunState, RunStatus
from battalion.state.persistence import save_state
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
from battalion.workflow_admission_state import WorkflowAdmissionRunRecord
from battalion.workflow_execution import (
    WorkflowCompletionEvidence,
    WorkflowStageEvidence,
    WorkflowUpgradeAlreadyApplied,
    WorkflowUpgradeTarget,
    WorkflowUpgradeTrigger,
)
from battalion.workflow_recipes import (
    DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    FULL_IMPLEMENTATION_RECIPE,
    CompletionRequirementKind,
    WorkflowRecipeRegistry,
    WorkflowStage,
)


def _evidence(
    revision: str = "BTN-143@revision-1",
    *,
    compact: bool = False,
) -> WorkflowAdmissionEvidence:
    references = [
        AdmissionEvidenceReference(
            evidence_id="work-item:BTN-143",
            source=AdmissionEvidenceSource.WORK_ITEM,
            source_revision=revision,
            condition=AdmissionEvidenceCondition.PRESENT,
            authoritative=True,
            hard_risk_flags=(
                frozenset()
                if compact
                else frozenset((HardRiskFlag.PERSISTENCE_OR_MIGRATION.value,))
            ),
        )
    ]
    if compact:
        references.extend(
            AdmissionEvidenceReference(
                evidence_id=f"compact:{fact.value}",
                source=AdmissionEvidenceSource.REPOSITORY,
                source_revision="repository@revision-1",
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


def _create(tmp_path, *, compact: bool = False):
    evidence = _evidence(compact=compact)
    assessment = assess_workflow_admission(evidence)
    result = create_admitted_run(
        CreateAdmittedRun(
            ticket_id="BTN-143",
            spec="Persist admitted workflow state.",
            config=BattalionConfig(base_dir=str(tmp_path)),
            assessment=assessment,
            evidence=evidence,
            disposition=(
                WorkflowAdmissionDisposition.COMPACT
                if compact
                else WorkflowAdmissionDisposition.FULL
            ),
        ),
        state_dir=tmp_path / ".battalion" / "state",
        _decision_id="admission-decision-1",
    )
    return result, evidence


def _tactician(assessment) -> TacticianAssessment:
    return TacticianAssessment.model_validate(
        {
            "assessment_id": "tactician-assessment-1",
            "recommendation_kind": "recipe",
            "recommended_recipe_id": "full-implementation-run",
            "recommended_recipe_version": "1.0",
            "rationale": ["Unresolved semantic risk warrants full handling."],
            "risk_flags": ["semantic-uncertainty"],
            "missing_evidence": ["bounded ownership evidence"],
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
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cost": None,
                    "cost_currency": None,
                    "cost_source": "unknown",
                },
            },
        }
    )


def test_create_admitted_run_persists_exact_evidence_and_catalog_linkage(tmp_path) -> None:
    result, _ = _create(tmp_path)

    assert UUID(result.run_id).version == 4
    assert result.state_version == "1.1"
    assert result.state.status is RunStatus.NOT_STARTED
    assert result.state.phase == "architect"
    assert result.state.workflow_admission is not None
    assert result.state.workflow_admission.decision.decision_id == "admission-decision-1"
    assert result.state.workflow_admission.execution.recipe_id == "full-implementation-run"
    assert result.state_path.exists()
    catalog = load_run_catalog(tmp_path)
    assert [entry.run_id for entry in catalog.runs] == [result.run_id]

    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(result.run_id),
        state_dir=tmp_path / ".battalion" / "state",
    )
    assert inspection.availability == "available"
    assert inspection.record == result.state.workflow_admission


def test_process_restart_resumes_persisted_full_recipe_without_re_admission(tmp_path) -> None:
    result, evidence = _create(tmp_path)
    paused = result.state.model_copy(
        update={"status": RunStatus.AWAITING_HUMAN, "phase": "architect"}
    )
    save_state(paused, result.state_path)
    captured = {}

    resumed = resume_run(
        ResumeRun(
            run_id=result.run_id,
            config=BattalionConfig(base_dir=str(tmp_path)),
            current_admission_evidence=evidence,
        ),
        state_dir=result.state_path.parent,
        _execute=lambda **kwargs: captured.setdefault("state", kwargs["state"]).model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        ),
    )

    assert captured["state"].workflow_admission == result.state.workflow_admission
    assert resumed.state.workflow_admission == result.state.workflow_admission
    assert resumed.state.status is RunStatus.DONE


def test_optional_tactician_evidence_remains_distinct_from_human_decision(tmp_path) -> None:
    evidence = WorkflowAdmissionEvidence(
        work_item_revision="BTN-143@uncertain-1",
        evidence_references=(
            AdmissionEvidenceReference(
                evidence_id="work-item:BTN-143",
                source=AdmissionEvidenceSource.WORK_ITEM,
                source_revision="BTN-143@uncertain-1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
            ),
        ),
    )
    assessment = assess_workflow_admission(evidence)
    tactician = _tactician(assessment)

    result = create_admitted_run(
        CreateAdmittedRun(
            ticket_id="BTN-143",
            spec="Persist uncertain admission evidence.",
            config=BattalionConfig(base_dir=str(tmp_path)),
            assessment=assessment,
            evidence=evidence,
            tactician_assessment=tactician,
            disposition=WorkflowAdmissionDisposition.FULL,
        ),
        state_dir=tmp_path / ".battalion" / "state",
    )

    record = result.state.workflow_admission
    assert record is not None
    assert record.assessment == assessment
    assert record.tactician_assessment == tactician
    assert record.decision.tactician_assessment_id == tactician.assessment_id
    assert record.decision.admitted_risk_flags == ("semantic-uncertainty",)


def test_resume_fails_closed_when_current_evidence_is_stale(tmp_path) -> None:
    result, _ = _create(tmp_path)

    with pytest.raises(StaleWorkflowAdmission, match="re-admission or stronger"):
        resume_run(
            ResumeRun(
                run_id=result.run_id,
                config=BattalionConfig(base_dir=str(tmp_path)),
                current_admission_evidence=_evidence("BTN-143@revision-2"),
            ),
            state_dir=result.state_path.parent,
            _execute=lambda **kwargs: kwargs["state"],
        )


def test_resume_fails_closed_when_admission_policy_version_changed(tmp_path) -> None:
    result, _ = _create(tmp_path)

    with pytest.raises(StaleWorkflowAdmission, match="different policy version"):
        resume_run(
            ResumeRun(
                run_id=result.run_id,
                config=BattalionConfig(base_dir=str(tmp_path)),
            ),
            state_dir=result.state_path.parent,
            policy=WorkflowAdmissionPolicy(policy_version="2.0"),
            _execute=lambda **kwargs: kwargs["state"],
        )


def test_unknown_historical_recipe_is_inspectable_but_cannot_resume(tmp_path) -> None:
    result, _ = _create(tmp_path)
    record = result.state.workflow_admission
    assert record is not None
    unknown_decision = record.decision.model_copy(
        update={
            "selected_recipe_id": "retired-full-recipe",
            "selected_recipe_version": "7.0",
        }
    )
    unknown_execution = record.execution.model_copy(
        update={"recipe_id": "retired-full-recipe", "recipe_version": "7.0"}
    )
    unknown_record = WorkflowAdmissionRunRecord(
        assessment=record.assessment,
        decision=unknown_decision,
        execution=unknown_execution,
    )
    save_state(
        result.state.model_copy(update={"workflow_admission": unknown_record}),
        result.state_path,
    )

    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(result.run_id),
        state_dir=result.state_path.parent,
    )
    assert inspection.availability == "unknown-recipe"
    assert inspection.record == unknown_record
    with pytest.raises(WorkflowAdmissionResumeRejected, match="retired-full-recipe"):
        resume_run(
            ResumeRun(
                run_id=result.run_id,
                config=BattalionConfig(base_dir=str(tmp_path)),
            ),
            state_dir=result.state_path.parent,
            _execute=lambda **kwargs: kwargs["state"],
        )


def test_corrupt_cross_record_admission_is_a_typed_history_failure(tmp_path) -> None:
    result, _ = _create(tmp_path)
    raw = json.loads(result.state_path.read_text(encoding="utf-8"))
    raw["workflow_admission"]["decision"]["admission_assessment_id"] = (
        "workflow-admission:missing"
    )
    result.state_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(StateReadFailed, match="different assessment"):
        inspect_run_workflow_admission(
            InspectRunWorkflowAdmission(result.run_id),
            state_dir=result.state_path.parent,
        )


def test_known_recipe_with_wrong_disposition_semantics_cannot_resume(tmp_path) -> None:
    result, _ = _create(tmp_path)
    record = result.state.workflow_admission
    assert record is not None
    inconsistent = WorkflowAdmissionRunRecord(
        assessment=record.assessment,
        decision=record.decision.model_copy(
            update={
                "selected_recipe_id": "compact-implementation-run",
                "selected_recipe_version": "1.0",
            }
        ),
        execution=record.execution.model_copy(
            update={
                "recipe_id": "compact-implementation-run",
                "recipe_version": "1.0",
            }
        ),
    )
    save_state(
        result.state.model_copy(update={"workflow_admission": inconsistent}),
        result.state_path,
    )

    with pytest.raises(WorkflowAdmissionResumeRejected, match="full recipe"):
        resume_run(
            ResumeRun(
                run_id=result.run_id,
                config=BattalionConfig(base_dir=str(tmp_path)),
            ),
            state_dir=result.state_path.parent,
        )


def test_legacy_run_has_explicit_history_compatibility_projection(tmp_path) -> None:
    state = RunState(
        schema_version="1.0",
        run_id="legacy-run",
        ticket_id="BTN-legacy",
        status=RunStatus.DONE,
        phase="done",
        retry_bound=2,
        budget=Budget(limit=10),
    )
    path = tmp_path / "legacy-run.json"
    save_state(state, path)

    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(state.run_id), state_dir=tmp_path
    )

    assert inspection.availability == "legacy"
    assert inspection.record is None
    assert inspection.limitation == "Run predates durable workflow admission."


def test_compact_run_persists_driver_entry_without_using_full_resume_graph(tmp_path) -> None:
    result, _ = _create(tmp_path, compact=True)

    assert result.state.phase == "driver_red"
    assert result.state.workflow_admission is not None
    assert result.state.workflow_admission.execution.recipe_id == (
        "compact-implementation-run"
    )
    with pytest.raises(WorkflowAdmissionResumeRejected, match="full-workflow graph"):
        resume_run(
            ResumeRun(
                run_id=result.run_id,
                config=BattalionConfig(base_dir=str(tmp_path)),
            ),
            state_dir=result.state_path.parent,
        )


def test_stage_evidence_and_upgrade_history_are_saved_append_only(tmp_path) -> None:
    result, _ = _create(tmp_path, compact=True)
    staged = record_admitted_workflow_stage(
        RecordAdmittedWorkflowStage(
            run_id=result.run_id,
            evidence=WorkflowStageEvidence(
                stage=WorkflowStage.DRIVER_RED,
                evidence_ids=("node-attempt:driver-red-1",),
            ),
        ),
        state_dir=result.state_path.parent,
    )
    upgraded = upgrade_admitted_workflow(
        UpgradeAdmittedWorkflow(
            run_id=result.run_id,
            trigger=WorkflowUpgradeTrigger.ARCHITECTURE_DECISION,
            reason="The implementation exposed a new ownership boundary.",
            evidence_ids=("driver-result:escalated-1",),
        ),
        state_dir=result.state_path.parent,
    )

    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(result.run_id),
        state_dir=result.state_path.parent,
    )
    assert inspection.record is not None
    execution = inspection.record.execution
    assert execution.completed_stage_evidence == (
        staged.state.workflow_admission.execution.completed_stage_evidence  # type: ignore[union-attr]
    )
    assert execution.upgrade_target is WorkflowUpgradeTarget.FULL
    assert execution.continuation_recipe_id == "full-implementation-run"
    assert execution.upgrade_history[0].evidence_ids == (
        "driver-result:escalated-1",
    )
    rendered = render_workflow_admission_history(inspection)
    assert "ORIGINAL ADMISSION" in rendered
    assert "Selected recipe: compact-implementation-run 1.0" in rendered
    assert "LATER UPGRADES" in rendered
    assert "architecture-decision -> full" in rendered
    assert "The implementation exposed a new ownership boundary." in rendered
    assert "Continuation recipe: full-implementation-run 1.0" in rendered
    with pytest.raises(WorkflowAdmissionResumeRejected, match="stronger handling"):
        resume_run(
            ResumeRun(
                run_id=result.run_id,
                config=BattalionConfig(base_dir=str(tmp_path)),
            ),
            state_dir=upgraded.state_path.parent,
        )
    with pytest.raises(WorkflowUpgradeAlreadyApplied):
        upgrade_admitted_workflow(
            UpgradeAdmittedWorkflow(
                run_id=result.run_id,
                trigger=WorkflowUpgradeTrigger.CONFIGURED_FULL_ONLY_CONDITION,
                reason="A second trigger cannot rewrite the first upgrade.",
                evidence_ids=("policy:full-only",),
            ),
            state_dir=upgraded.state_path.parent,
        )


def test_compact_completion_evidence_survives_each_durable_transition(tmp_path) -> None:
    result, _ = _create(tmp_path, compact=True)
    for stage in (
        WorkflowStage.DRIVER_RED,
        WorkflowStage.DRIVER_GREEN,
        WorkflowStage.REVIEW_GREEN,
    ):
        result = record_admitted_workflow_stage(
            RecordAdmittedWorkflowStage(
                run_id=result.run_id,
                evidence=WorkflowStageEvidence(
                    stage=stage,
                    evidence_ids=(f"evidence:{stage.value}",),
                ),
            ),
            state_dir=result.state_path.parent,
        )
    for kind in (
        CompletionRequirementKind.SEMANTIC_REVIEW,
        CompletionRequirementKind.HUMAN_ACCEPTANCE,
    ):
        result = record_admitted_workflow_completion(
            RecordAdmittedWorkflowCompletion(
                run_id=result.run_id,
                evidence=WorkflowCompletionEvidence(
                    kind=kind,
                    evidence_ids=(f"evidence:{kind.value}",),
                ),
            ),
            state_dir=result.state_path.parent,
        )

    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(result.run_id),
        state_dir=result.state_path.parent,
    )
    assert inspection.record is not None
    assert [item.kind for item in inspection.record.execution.completion_evidence] == [
        CompletionRequirementKind.SEMANTIC_REVIEW,
        CompletionRequirementKind.HUMAN_ACCEPTANCE,
    ]


def test_completed_historical_admission_cannot_be_rewritten(tmp_path) -> None:
    result, _ = _create(tmp_path, compact=True)
    save_state(
        result.state.model_copy(update={"status": RunStatus.DONE, "phase": "done"}),
        result.state_path,
    )

    with pytest.raises(WorkflowAdmissionRejected, match="immutable"):
        record_admitted_workflow_stage(
            RecordAdmittedWorkflowStage(
                run_id=result.run_id,
                evidence=WorkflowStageEvidence(
                    stage=WorkflowStage.DRIVER_RED,
                    evidence_ids=("late-evidence",),
                ),
            ),
            state_dir=result.state_path.parent,
        )
    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(result.run_id),
        state_dir=result.state_path.parent,
    )
    assert inspection.availability == "available"
    assert inspection.record == result.state.workflow_admission


def test_completed_history_keeps_original_semantics_after_registry_changes(tmp_path) -> None:
    result, _ = _create(tmp_path)
    save_state(
        result.state.model_copy(update={"status": RunStatus.DONE, "phase": "done"}),
        result.state_path,
    )
    newer_full = FULL_IMPLEMENTATION_RECIPE.model_copy(
        update={"recipe_version": "2.0"}
    )
    changed_registry = WorkflowRecipeRegistry(
        (*DEFAULT_WORKFLOW_RECIPE_REGISTRY.list(), newer_full)
    )
    changed_policy = WorkflowAdmissionPolicy(policy_version="2.0")

    inspection = inspect_run_workflow_admission(
        InspectRunWorkflowAdmission(result.run_id),
        state_dir=result.state_path.parent,
        registry=changed_registry,
    )

    assert changed_policy.policy_version == "2.0"
    assert inspection.availability == "available"
    assert inspection.record is not None
    assert inspection.record.assessment.policy_version == "1.0"
    assert inspection.record.execution.recipe_version == "1.0"

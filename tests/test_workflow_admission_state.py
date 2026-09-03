"""Persistence contract tests for BTN-143 workflow-admission linkage."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from battalion.state.models import Budget, RunState, RunStatus
from battalion.state.persistence import load_state, save_state
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    HardRiskFlag,
    WorkflowAdmissionEvidence,
    assess_workflow_admission,
)
from battalion.workflow_admission_decisions import (
    WorkflowAdmissionDecision,
    WorkflowAdmissionDisposition,
)
from battalion.workflow_admission_state import WorkflowAdmissionRunRecord
from battalion.workflow_execution import start_workflow_execution
from battalion.workflow_recipes import FULL_IMPLEMENTATION_RECIPE


def _admission_record() -> WorkflowAdmissionRunRecord:
    evidence = WorkflowAdmissionEvidence(
        work_item_revision="BTN-143@revision-1",
        specification_revision="spec@revision-1",
        evidence_references=(
            AdmissionEvidenceReference(
                evidence_id="work-item:BTN-143",
                source=AdmissionEvidenceSource.WORK_ITEM,
                source_revision="BTN-143@revision-1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
                hard_risk_flags=frozenset((HardRiskFlag.PERSISTENCE_OR_MIGRATION.value,)),
            ),
            AdmissionEvidenceReference(
                evidence_id="spec:workflow-admission",
                source=AdmissionEvidenceSource.SPECIFICATION,
                source_revision="spec@revision-1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
            ),
        ),
    )
    assessment = assess_workflow_admission(evidence)
    decision = WorkflowAdmissionDecision(
        decision_id="admission-decision-1",
        disposition=WorkflowAdmissionDisposition.FULL,
        admission_assessment_id=assessment.assessment_id,
        selected_recipe_id=FULL_IMPLEMENTATION_RECIPE.recipe_id,
        selected_recipe_version=FULL_IMPLEMENTATION_RECIPE.recipe_version,
        approving_actor_id=UUID("8fd5f40b-37dd-4ab3-8f7d-938a30fe3d46"),
        approving_actor_display_name="Test Operator",
        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        work_item_revision=assessment.work_item_revision,
        specification_revision=assessment.specification_revision,
        policy_id=assessment.policy_id,
        policy_version=assessment.policy_version,
        admitted_risk_flags=assessment.hard_risk_flags,
    )
    return WorkflowAdmissionRunRecord(
        assessment=assessment,
        decision=decision,
        execution=start_workflow_execution(FULL_IMPLEMENTATION_RECIPE),
    )


def _run_state(*, admission: WorkflowAdmissionRunRecord | None) -> RunState:
    return RunState(
        schema_version="1.1" if admission is not None else "1.0",
        run_id="1e8b9ef0-5bb4-4b6e-853c-5ca6adf7fdb8",
        run_alias="BTN-143",
        ticket_id="BTN-143",
        status=RunStatus.IN_PROGRESS,
        phase="driver_red",
        retry_bound=2,
        budget=Budget(limit=100),
        workflow_admission=admission,
    )


def test_admission_assessment_decision_and_execution_round_trip_together(tmp_path) -> None:
    path = tmp_path / "run.json"
    original = _run_state(admission=_admission_record())

    save_state(original, path)
    loaded = load_state(path)

    assert loaded == original
    assert loaded.workflow_admission is not None
    assert loaded.workflow_admission.assessment.assessment_id.startswith(
        "workflow-admission:"
    )
    assert loaded.workflow_admission.execution.recipe_id == "full-implementation-run"


def test_legacy_run_without_admission_record_remains_readable(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    save_state(_run_state(admission=None), path)

    loaded = load_state(path)

    assert loaded.workflow_admission is None


def test_legacy_state_version_cannot_claim_new_admission_storage() -> None:
    record = _admission_record()

    with pytest.raises(ValidationError, match="RunState schema version 1.1"):
        RunState.model_validate(
            {
                **_run_state(admission=None).model_dump(),
                "workflow_admission": record.model_dump(),
            }
        )


def test_cross_record_recipe_rewrite_fails_closed() -> None:
    record = _admission_record()
    rewritten = record.execution.model_copy(
        update={"recipe_id": "compact-implementation-run"}
    )

    with pytest.raises(ValidationError, match="admitted exact recipe"):
        WorkflowAdmissionRunRecord.model_validate(
            {**record.model_dump(), "execution": rewritten.model_dump()}
        )


def test_unknown_admission_record_version_fails_closed() -> None:
    record = _admission_record()

    with pytest.raises(ValidationError, match="schema_version"):
        WorkflowAdmissionRunRecord.model_validate(
            {**record.model_dump(), "schema_version": "9.0"}
        )


def test_unknown_deterministic_assessment_version_fails_closed() -> None:
    record = _admission_record()
    assessment = {**record.assessment.model_dump(), "assessment_version": "9.0"}

    with pytest.raises(ValidationError, match="assessment_version"):
        WorkflowAdmissionRunRecord.model_validate(
            {**record.model_dump(), "assessment": assessment}
        )


def test_missing_referenced_assessment_fails_closed() -> None:
    record = _admission_record()
    rewritten = record.decision.model_copy(
        update={"admission_assessment_id": "workflow-admission:missing"}
    )

    with pytest.raises(ValidationError, match="different assessment"):
        WorkflowAdmissionRunRecord.model_validate(
            {**record.model_dump(), "decision": rewritten.model_dump()}
        )

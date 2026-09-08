"""Persistence contract tests for BTN-143 workflow-admission linkage."""

from __future__ import annotations

import json

from support.state import make_run_state
from support.execution import make_node_execution

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from battalion.artifact_targets import ArtifactTargetContract
from battalion.artifact_target_state import ArtifactTargetHandoffRecord
from battalion.state.models import RunState, RunStatus
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
    return make_run_state(
        schema_version='1.1' if admission is not None else '1.0',
        run_id='1e8b9ef0-5bb4-4b6e-853c-5ca6adf7fdb8',
        run_alias='BTN-143',
        ticket_id='BTN-143',
        status=RunStatus.IN_PROGRESS,
        phase='driver_red',
        workflow_admission=admission,
        write_scope={},
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


def _run_with_target_history() -> RunState:
    admission = _admission_record()
    contract = ArtifactTargetContract(
        project_id="bd4b6e64-25fd-408a-a747-9633a803f036",
        work_item_revision=admission.decision.work_item_revision,
        specification_revision=admission.decision.specification_revision,
        project_source_revision="source-r1",
        workflow_admission_decision_id=admission.decision.decision_id,
        architect_execution_id="architect:1", plan_artifact_digest="a" * 64,
        evidence_references=[{
            "evidence_id": "work-item:BTN-143", "source": "work-item",
            "source_revision": admission.decision.work_item_revision,
        }],
        targets=[{
            "target_id": "greeting-test", "project_relative_path": "src/test_greeting.py",
            "assignments": [{
                "owner_role": "driver", "workflow_phase": "driver-red",
                "intended_operation": "create",
            }],
        }],
    )
    return make_run_state(
        schema_version="1.2", run_id="run-targets", project_id=str(contract.project_id),
        workflow_admission=admission,
        artifact_target_handoff=ArtifactTargetHandoffRecord(
            contracts=[contract], active_contract_id=contract.contract_id,
            reconciliations=[{
                "reconciliation_id": "reconciliation:1", "contract_id": contract.contract_id,
                "occurred_at": "2026-09-06T00:00:00Z", "outcome": "ready",
                "recipe_id": admission.execution.recipe_id,
                "recipe_version": admission.execution.recipe_version,
                "project_source_revision": "source-r1", "write_scope_digest": "b" * 64,
                "path_policy_digest": "c" * 64,
            }],
        ),
        execution_record={"node_executions": [make_node_execution(
            role="architect", phase="architect", execution_id="architect:1",
            artifact_provenance=[{
                "path": "plan.md", "sha256": "a" * 64, "originating_run_id": "run-targets",
                "originating_node_execution_id": "architect:1",
            }],
        )]},
    )


def test_target_handoff_round_trips_without_rewriting_admission(tmp_path):
    state = _run_with_target_history()
    admission_json = state.workflow_admission.model_dump_json()
    path = tmp_path / "run.json"
    save_state(state, path)
    loaded = load_state(path)
    assert loaded == state
    assert loaded.schema_version == "1.2"
    assert loaded.workflow_admission.model_dump_json() == admission_json
    assert loaded.artifact_target_handoff.active_contract_id == (
        loaded.artifact_target_handoff.contracts[0].contract_id
    )


@pytest.mark.parametrize("schema_version", ["1.0", "1.1"])
def test_pre_target_schema_loads_without_fabricating_handoff(tmp_path, schema_version):
    state = _run_state(admission=_admission_record() if schema_version == "1.1" else None)
    raw = state.model_dump(mode="json", exclude={"artifact_target_handoff"})
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_state(path)
    assert loaded.schema_version == schema_version
    assert loaded.artifact_target_handoff is None


@pytest.mark.parametrize("mutation,match", [
    pytest.param("legacy-schema", "schema version 1.2", id="legacy-cannot-claim-targets"),
    pytest.param("missing-admission", "requires project identity and workflow admission", id="missing-admission"),
    pytest.param("different-project", "different project", id="project-mismatch"),
    pytest.param("work-revision", "retained admission identity", id="work-revision-mismatch"),
    pytest.param("spec-revision", "retained admission identity", id="spec-revision-mismatch"),
    pytest.param("decision", "retained admission identity", id="decision-mismatch"),
    pytest.param("missing-architect", "successful Architect", id="missing-architect"),
    pytest.param("wrong-role", "successful Architect", id="wrong-provenance-role"),
    pytest.param("failed-architect", "successful Architect", id="unsuccessful-architect"),
    pytest.param("plan-digest", "plan digest", id="plan-digest-mismatch"),
    pytest.param("plan-run", "plan digest", id="plan-run-mismatch"),
    pytest.param("recipe", "unadmitted recipe", id="recipe-mismatch"),
])
def test_persisted_target_cross_record_corruption_is_rejected(tmp_path, mutation, match):
    raw = _run_with_target_history().model_dump(mode="json")
    if mutation == "legacy-schema":
        raw["schema_version"] = "1.1"
    elif mutation == "missing-admission":
        raw["workflow_admission"] = None
    elif mutation == "different-project":
        raw["project_id"] = "8fd5f40b-37dd-4ab3-8f7d-938a30fe3d46"
    elif mutation in {"work-revision", "spec-revision", "decision"}:
        field = {"work-revision": "work_item_revision", "spec-revision": "specification_revision",
                 "decision": "workflow_admission_decision_id"}[mutation]
        handoff = raw["artifact_target_handoff"]
        contract = handoff["contracts"][0]
        contract[field] = "different-revision"
        contract.pop("contract_id")
        replacement = ArtifactTargetContract.model_validate(contract)
        handoff["contracts"] = [replacement.model_dump(mode="json")]
        handoff["active_contract_id"] = replacement.contract_id
        handoff["reconciliations"][0]["contract_id"] = replacement.contract_id
    elif mutation == "missing-architect":
        raw["execution_record"]["node_executions"] = []
    elif mutation == "wrong-role":
        raw["execution_record"]["node_executions"][0]["role"] = "driver"
    elif mutation == "failed-architect":
        raw["execution_record"]["node_executions"][0]["outcome"] = "rejected"
    elif mutation == "plan-digest":
        raw["execution_record"]["node_executions"][0]["artifact_provenance"][0]["sha256"] = "d" * 64
    elif mutation == "plan-run":
        raw["execution_record"]["node_executions"][0]["artifact_provenance"][0]["originating_run_id"] = "other-run"
    elif mutation == "recipe":
        raw["artifact_target_handoff"]["reconciliations"][0]["recipe_version"] = "9.0"
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError, match=match):
        load_state(path)

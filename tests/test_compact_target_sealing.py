"""Exact compact target authority, independent of compact recipe dispatch."""

from dataclasses import replace
import subprocess

import pytest

from battalion.application import (
    ArtifactTargetHandoffRejected, CreateAdmittedRun, ResumeRun, SealCompactTargetHandoff, StartRun,
    WorkflowAdmissionResumeRejected, create_admitted_run, resume_run, seal_compact_target_handoff, start_run,
)
from battalion.artifact_target_reconciliation import ArtifactTargetCurrentEvidence
from battalion.config import BattalionConfig
from battalion.graph import _make_driver_node
from battalion.state.models import RunState
from battalion.state.persistence import load_state, save_state
from battalion.workflow_admission import CompactAdmissionEvidence, WorkflowAdmissionEvidence, assess_workflow_admission
from battalion.workflow_admission_decisions import WorkflowAdmissionDisposition
from support.graph import patched_nodes
from support.state import make_llm_configs


@pytest.fixture
def compact_project(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    evidence = WorkflowAdmissionEvidence(
        work_item_revision="work-r1", specification_revision="spec-r1",
        evidence_references=[
            {"evidence_id": "work:compact", "source": "work-item", "source_revision": "work-r1",
             "condition": "present", "authoritative": True, "establishes": list(CompactAdmissionEvidence)},
            {"evidence_id": "spec:compact", "source": "specification", "source_revision": "spec-r1",
             "condition": "present", "authoritative": True},
        ],
    )
    config = BattalionConfig(base_dir=str(tmp_path), models=make_llm_configs(),
                             write_scope={"driver_red": ["tests/"], "driver_green": ["src/"]})
    created = create_admitted_run(CreateAdmittedRun(
        ticket_id="BTN-195", spec="Correct the greeting", config=config,
        assessment=assess_workflow_admission(evidence), evidence=evidence,
        disposition=WorkflowAdmissionDisposition.COMPACT,
    ), state_dir=tmp_path / ".battalion/state")
    current = ArtifactTargetCurrentEvidence(
        project_id=created.state.project_id, work_item_revision="work-r1", specification_revision="spec-r1",
        workflow_admission_decision_id=created.state.workflow_admission.decision.decision_id,
        recipe_id="compact-implementation-run", recipe_version="1.0", references=evidence.evidence_references,
        exact_targets=[{
            "reference": {"evidence_id": "work:compact", "source": "work-item", "source_revision": "work-r1"},
            "targets": [
                {"target_id": "test", "project_relative_path": "tests/test_greeting.py", "assignments": [
                    {"owner_role": "driver", "workflow_phase": "driver-red", "intended_operation": "create"}]},
                {"target_id": "implementation", "project_relative_path": "src/greeting.py", "assignments": [
                    {"owner_role": "driver", "workflow_phase": "driver-green", "intended_operation": "modify"}]},
            ],
        }],
    )
    return SealCompactTargetHandoff(created.run_id, tmp_path, current), created.state_path, config


def seal(command, path):
    return seal_compact_target_handoff(command, state_dir=path.parent,
                                      worker_dir=path.parent.parent / "workers")


def test_compact_sealing_has_source_provenance_without_fabricated_architect(compact_project):
    command, path, config = compact_project
    before = load_state(path)
    state = seal(command, path).state
    contract = state.artifact_target_handoff.contracts[-1]
    assert contract.architect_execution_id is None and contract.plan_artifact_digest is None
    assert {item.target_id: item for item in contract.targets} == {
        item.target_id: item for item in command.current_evidence.exact_targets[0].targets
    }
    assert contract.project_source_revision == before.project_source_snapshot.revision
    assert state.artifact_target_handoff.reconciliations[-1].outcome == "ready"
    assert state.workflow_admission == before.workflow_admission
    assert state.execution_record == before.execution_record
    assert not (command.project_root / "plan.md").exists()
    saved = path.read_bytes()
    assert seal(command, path).state == state
    assert path.read_bytes() == saved
    # Contract readiness does not authorize the legacy full-workflow dispatcher.
    with pytest.raises(WorkflowAdmissionResumeRejected, match="compact recipe"):
        resume_run(ResumeRun(command.run_id, config, current_artifact_evidence=command.current_evidence), state_dir=path.parent)
    with pytest.raises(WorkflowAdmissionResumeRejected, match="compact recipe"):
        start_run(StartRun(state, config, overwrite=True, current_artifact_evidence=command.current_evidence), state_dir=path.parent)
    assert path.read_bytes() == saved


@pytest.mark.parametrize("change,reason", [
    ("no-exact-targets", "missing-targets"), ("advisory-source", "missing-targets"),
    ("not-authoritative", "missing-targets"), ("stale-source", "stale-evidence"),
    ("duplicate-path", "duplicate-targets"), ("unknown-recipe", "incompatible-recipe"),
])
def test_compact_sealing_cannot_invent_or_trust_invalid_targets(compact_project, change, reason):
    command, path, _ = compact_project
    raw = command.current_evidence.model_dump()
    if change == "no-exact-targets":
        raw["exact_targets"] = []
    elif change == "advisory-source":
        raw["exact_targets"][0]["reference"]["source"] = "repository"
    elif change == "not-authoritative":
        raw["references"] = [{**ref, "authoritative": False} for ref in raw["references"]]
    elif change == "stale-source":
        raw["exact_targets"][0]["reference"]["source_revision"] = "old-work"
    elif change == "unknown-recipe":
        raw["recipe_version"] = "missing-version"
    else:
        raw["exact_targets"][0]["targets"][0]["project_relative_path"] = "src/greeting.py"
    saved = path.read_bytes()
    with pytest.raises(ArtifactTargetHandoffRejected) as rejected:
        seal(replace(command, current_evidence=ArtifactTargetCurrentEvidence.model_validate(raw)), path)
    assert rejected.value.reason_code == reason
    assert path.read_bytes() == saved


def test_conflicting_authoritative_targets_persist_clarification(compact_project):
    command, path, _ = compact_project
    raw = command.current_evidence.model_dump()
    targets = [dict(target) for target in raw["exact_targets"][0]["targets"]]
    targets[0]["project_relative_path"] = "tests/test_different.py"
    raw["exact_targets"] = [*raw["exact_targets"], {
        "reference": {"evidence_id": "spec:compact", "source": "specification", "source_revision": "spec-r1"},
        "targets": targets,
    }]
    state = seal(replace(command, current_evidence=ArtifactTargetCurrentEvidence.model_validate(raw)), path).state
    assert state.artifact_target_handoff.reconciliations[-1].outcome == "clarification-required"
    assert "contradictory-evidence" in state.artifact_target_handoff.reconciliations[-1].reason_codes
    assert state.execution_record.node_executions == []


def test_driver_gate_can_seal_compact_targets_before_its_first_attempt(compact_project):
    command, path, config = compact_project
    seen = []
    with patched_nodes(record=seen):
        result = _make_driver_node("red", config.models, str(command.project_root),
                                   current_artifact_evidence=command.current_evidence,
                                   on_state_checkpoint=lambda state: save_state(state, path))(load_state(path))
    result = RunState.model_validate(result)
    assert seen == ["driver_red"]
    assert result.execution_record.node_executions[-1].artifact_target_contract_id == result.artifact_target_handoff.active_contract_id
    assert not any(item.role == "architect" for item in result.execution_record.node_executions)

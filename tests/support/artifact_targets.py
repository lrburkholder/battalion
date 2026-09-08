"""A real admitted Git Run stopped after its Architect handoff."""

from functools import partial
import subprocess

import pytest

from battalion.application import (
    CreateAdmittedRun, SealArchitectTargetHandoff, create_admitted_run,
)
from battalion.artifact_target_reconciliation import ArtifactTargetCurrentEvidence
from battalion.config import BattalionConfig
from battalion.graph import _make_architect_node
from battalion.nodes.architect import run_architect
from battalion.state.models import RunState
from battalion.state.persistence import save_state
from battalion.workflow_admission import WorkflowAdmissionEvidence, assess_workflow_admission
from battalion.workflow_admission_decisions import WorkflowAdmissionDisposition
from support.graph import patched_nodes
from support.responses import json_response
from support.state import make_llm_configs


def prepare_handoff_project(tmp_path, *, manual_checkpoint: bool):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    evidence = WorkflowAdmissionEvidence(
        work_item_revision="work-r1", specification_revision="spec-r1",
        evidence_references=[
            {"evidence_id": "work:1", "source": "work-item", "source_revision": "work-r1",
             "condition": "present", "authoritative": True},
            {"evidence_id": "spec:1", "source": "specification", "source_revision": "spec-r1",
             "condition": "present", "authoritative": True},
        ],
    )
    config = BattalionConfig(
        base_dir=str(tmp_path), models=make_llm_configs(),
        manual_checkpoints=["driver"] if manual_checkpoint else [],
        write_scope={"architect": ["plan.md"], "driver_red": ["tests/"], "driver_green": ["src/"]},
    )
    initial = create_admitted_run(CreateAdmittedRun(
        ticket_id="BTN-195", spec="Implement greeting behavior.", config=config,
        assessment=assess_workflow_admission(evidence), evidence=evidence,
        disposition=WorkflowAdmissionDisposition.FULL,
    ), state_dir=tmp_path / ".battalion/state")
    candidate = {
        "plan_markdown": "# Greeting\nImplement greeting behavior.",
        "targets": [
            {"target_id": "greeting-test", "project_relative_path": "tests/test_greeting.py",
             "assignments": [{"owner_role": "driver", "workflow_phase": "driver-red", "intended_operation": "create"}]},
            {"target_id": "greeting-source", "project_relative_path": "src/greeting.py",
             "assignments": [{"owner_role": "driver", "workflow_phase": "driver-green", "intended_operation": "create"}]},
        ],
        "implementation_steps": [{"description": "Test and implement greeting behavior.",
                                  "target_ids": ["greeting-test", "greeting-source"]}],
    }

    def unexpected_role(*args, **kwargs):
        pytest.fail("No role after Architect may execute in the handoff fixture")

    with patched_nodes(
        architect=partial(run_architect, call_llm_fn=lambda *args, **kwargs: json_response(candidate)),
        driver=unexpected_role, reviewer=unexpected_role, refactorer=unexpected_role,
    ):
        raw = _make_architect_node(
            config.models, base_dir=tmp_path,
            on_state_checkpoint=lambda state: save_state(state, initial.state_path),
        )(initial.state)
    state = RunState.model_validate(raw)
    save_state(state, initial.state_path)
    # Construct current external evidence explicitly; the sealing operation
    # collects execution and on-disk plan provenance itself.
    current = ArtifactTargetCurrentEvidence(
        project_id=state.project_id, work_item_revision="work-r1", specification_revision="spec-r1",
        project_source_revision=state.project_source_snapshot.revision,
        workflow_admission_decision_id=state.workflow_admission.decision.decision_id,
        recipe_id="full-implementation-run", recipe_version="1.0",
        references=evidence.evidence_references,
    )
    command = SealArchitectTargetHandoff(
        run_id=state.run_id, architect_execution_id=state.execution_record.node_executions[-1].execution_id,
        project_root=tmp_path, current_evidence=current, case_sensitive_paths=True,
    )
    return command, initial.state_path


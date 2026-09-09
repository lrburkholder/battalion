"""Credential-free acceptance coverage for the ADR-0038 enforcement path."""

import json
import subprocess
from dataclasses import replace
from functools import partial

from battalion.application import (
    ChangeArtifactTargetHandoff,
    CreateAdmittedRun,
    InspectRun,
    ResumeRun,
    change_artifact_target_handoff,
    StartRun,
    create_admitted_run,
    inspect_run,
    resume_run,
    seal_architect_target_handoff,
    start_run,
)
from battalion.cli import app
from battalion.config import BattalionConfig
from battalion.desktop.presentation import render_execution
from battalion.nodes.architect import run_architect
from battalion.nodes.driver import run_driver
from battalion.artifact_targets import ArtifactTargetContract
from battalion.state.models import RunStatus
from battalion.state.persistence import load_state, save_state
from battalion.workflow_admission import WorkflowAdmissionEvidence, assess_workflow_admission
from battalion.workflow_admission_decisions import WorkflowAdmissionDisposition
from support.graph import patched_nodes
from support.responses import json_response
from support.state import make_llm_configs
from support.artifact_targets import prepare_handoff_project
from typer.testing import CliRunner


def _conflicting_greeting_candidate():
    return {
        "handoff_version": "1.0",
        "plan_markdown": "# Greeting\n\nAdd a greeting test.",
        "targets": [
            {
                "target_id": "greeting-test",
                "project_relative_path": "src/test_greeting.py",
                "assignments": [{
                    "owner_role": "driver", "workflow_phase": "driver-red",
                    "intended_operation": "create",
                }],
            },
            {
                "target_id": "greeting-test",
                "project_relative_path": "test_greeting.py",
                "assignments": [{
                    "owner_role": "driver", "workflow_phase": "driver-red",
                    "intended_operation": "create",
                }],
            },
        ],
        "implementation_steps": [{
            "description": "Add the failing greeting test.",
            "target_ids": ["greeting-test"],
        }],
    }


def test_conflicting_greeting_targets_pause_before_plan_or_driver_and_project_durable_evidence(
    tmp_path, monkeypatch,
):
    """Exercise Architect -> application -> graph -> persisted CLI/desktop evidence."""
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
        write_scope={"architect": ["plan.md"], "driver_red": ["src/"], "driver_green": ["src/"]},
    )
    admitted = create_admitted_run(
        CreateAdmittedRun(
            ticket_id="BTN-197", spec="Implement the greeting behavior.", config=config,
            assessment=assess_workflow_admission(evidence), evidence=evidence,
            disposition=WorkflowAdmissionDisposition.FULL,
        ),
        state_dir=tmp_path / ".battalion" / "state",
    )
    candidate = _conflicting_greeting_candidate()

    def architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
        return run_architect(
            state, spec_text, llm_config, base_dir=base_dir, prompts_dir=prompts_dir,
            call_llm_fn=lambda *args, **kwargs: json_response(candidate),
        )

    def driver_must_not_run(*args, **kwargs):
        raise AssertionError("Driver must not run after a conflicting Architect target")

    with patched_nodes(architect=architect, driver=driver_must_not_run):
        result = start_run(
            StartRun(admitted.state, config, overwrite=True), state_dir=tmp_path / ".battalion" / "state",
        )

    assert result.state.status is RunStatus.AWAITING_HUMAN
    assert not (tmp_path / "plan.md").exists()
    assert not any(item.role == "driver" for item in result.state.execution_record.node_executions)
    assert not any(item.tool_activity for item in result.state.execution_record.node_executions)

    inspection = inspect_run(InspectRun(result.run_id), state_dir=tmp_path / ".battalion" / "state")
    assert inspection.state == result.state
    attempts = inspection.state.execution_record.node_executions
    assert [item.role_contract_violation.resulting_disposition for item in attempts] == ["retry", "escalation"]
    assert all(item.role_contract_violation.reason_code == "architect-handoff-invalid" for item in attempts)
    assert all(item.role_contract_violation.offending_paths == ["src/test_greeting.py", "test_greeting.py"] for item in attempts)

    monkeypatch.chdir(tmp_path)
    human = CliRunner().invoke(app, ["status", result.run_id, "--human"])
    machine = CliRunner().invoke(app, ["status", result.run_id])
    assert human.exit_code == 0, human.output
    assert "Role-contract corrections:" in human.output
    assert "src/test_greeting.py, test_greeting.py" in human.output
    assert "clarification-required (escalation)" in human.output
    assert machine.exit_code == 0, machine.output
    machine_state = json.loads(machine.output)
    assert machine_state["execution_record"]["node_executions"][0]["role_contract_violation"]["offending_paths"] == [
        "src/test_greeting.py", "test_greeting.py",
    ]
    desktop = render_execution(attempts[-1])
    assert "ROLE-CONTRACT CORRECTION" in desktop
    assert "src/test_greeting.py, test_greeting.py" in desktop


def test_actor_correction_supersedes_clarified_contract_and_binds_reloaded_driver(
    tmp_path,
):
    """Keep both identities durable and admit Driver only against the correction."""
    sealing, state_path = prepare_handoff_project(tmp_path, manual_checkpoint=True)
    unsealed = load_state(state_path)
    unsealed.write_scope["driver_red"] = ["test/"]
    save_state(unsealed, state_path)
    clarified = seal_architect_target_handoff(
        replace(sealing, case_sensitive_paths=False), state_dir=state_path.parent,
        worker_dir=tmp_path / ".battalion" / "workers",
    ).state
    original = clarified.artifact_target_handoff.contracts[-1]
    assert clarified.artifact_target_handoff.reconciliations[-1].outcome == "clarification-required"
    assert "out-of-scope" in clarified.artifact_target_handoff.reconciliations[-1].reason_codes

    corrected = ArtifactTargetContract.model_validate({
        **original.model_dump(exclude={"contract_id"}),
        "supersedes_contract_id": original.contract_id,
        "targets": [
            {**target.model_dump(), "project_relative_path": "test/test_greeting.py"}
            if target.target_id == "greeting-test" else target.model_dump()
            for target in original.targets
        ],
    })
    approved = change_artifact_target_handoff(
        ChangeArtifactTargetHandoff(
            run_id=clarified.run_id, project_root=tmp_path, action_id="btn-197-correct-greeting",
            action="approve-correction", expected_contract_id=original.contract_id,
            reason="Use the exact Driver RED target.", corrected_contract=corrected,
            current_evidence=sealing.current_evidence,
        ),
        state_dir=state_path.parent, worker_dir=tmp_path / ".battalion" / "workers",
    ).state
    assert [item.contract_id for item in approved.artifact_target_handoff.contracts] == [
        original.contract_id, corrected.contract_id,
    ]
    assert approved.artifact_target_handoff.active_contract_id == corrected.contract_id
    assert [item.outcome for item in approved.artifact_target_handoff.reconciliations] == [
        "clarification-required", "ready",
    ]
    assert approved.artifact_target_handoff.corrections[-1].previous_contract_id == original.contract_id
    assert approved.artifact_target_handoff.corrections[-1].corrected_contract_id == corrected.contract_id

    reloaded = load_state(state_path)
    seen_contract_ids = []

    def provider(role, config, messages):
        handoff = json.loads(messages[-1]["content"].splitlines()[2])
        seen_contract_ids.append(handoff["contract_id"])
        return json_response({"files": {
            target["project_relative_path"]: "# greeting\n" for target in handoff["targets"]
        }})

    with patched_nodes(driver=partial(run_driver, call_llm_fn=provider)):
        resumed = resume_run(
            ResumeRun(
                run_id=reloaded.run_id,
                config=BattalionConfig(
                    base_dir=str(tmp_path), models=make_llm_configs(),
                    write_scope=reloaded.write_scope,
                ),
                resolution="Approve the corrected greeting targets.",
                action_id="btn-197-resume-corrected",
                current_artifact_evidence=sealing.current_evidence,
                artifact_target_contract_id=corrected.contract_id,
            ),
            state_dir=state_path.parent,
        ).state
    assert resumed.status is RunStatus.DONE
    assert seen_contract_ids == [corrected.contract_id, corrected.contract_id]
    assert (tmp_path / "test/test_greeting.py").read_text() == "# greeting\n"
    assert (tmp_path / "src/greeting.py").read_text() == "# greeting\n"
    assert all(
        item.artifact_target_contract_id == corrected.contract_id
        for item in resumed.execution_record.node_executions if item.role == "driver"
    )


def test_ready_contract_reaches_driver_without_tactician_or_human_pause(tmp_path):
    """A fully admitted, exact target contract must not add an extra checkpoint."""
    sealing, state_path = prepare_handoff_project(tmp_path, manual_checkpoint=False)
    ready = seal_architect_target_handoff(
        replace(sealing, case_sensitive_paths=False), state_dir=state_path.parent,
        worker_dir=tmp_path / ".battalion" / "workers",
    ).state
    assert ready.artifact_target_handoff.reconciliations[-1].outcome == "ready"
    assert not ready.interrupt_log

    seen = []
    with patched_nodes(record=seen):
        completed = resume_run(
            ResumeRun(
                run_id=ready.run_id,
                config=BattalionConfig(
                    base_dir=str(tmp_path), models=make_llm_configs(), write_scope=ready.write_scope,
                ),
                current_artifact_evidence=sealing.current_evidence,
            ),
            state_dir=state_path.parent,
        ).state
    assert completed.status is RunStatus.DONE
    assert seen == [
        "driver_red", "reviewer_red-check", "driver_green", "reviewer_green-check",
        "refactorer", "reviewer_refactor-check",
    ]
    assert not completed.interrupt_log
    assert not completed.human_action_log

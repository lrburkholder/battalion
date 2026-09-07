"""Human handoff authority through real persistence and source reconciliation."""

from dataclasses import replace

import pytest

from battalion.application import (
    ChangeArtifactTargetHandoff, HumanActionRejected, ArtifactTargetHandoffRejected,
    change_artifact_target_handoff, seal_architect_target_handoff,
)
from battalion.artifact_targets import ArtifactTargetContract
from battalion.graph import run_ticket
from battalion.state.models import RunState, RunStatus
from battalion.state.persistence import load_state
from support.artifact_targets import prepare_handoff_project
from support.graph import patched_nodes
from support.state import make_llm_configs


@pytest.fixture
def action_project(tmp_path):
    command, path = prepare_handoff_project(tmp_path, manual_checkpoint=True)
    state = seal_architect_target_handoff(
        replace(command, case_sensitive_paths=False), state_dir=path.parent,
        worker_dir=tmp_path / ".battalion/workers",
    ).state
    old = state.artifact_target_handoff.contracts[-1]
    corrected = ArtifactTargetContract.model_validate({
        **old.model_dump(exclude={"contract_id"}), "supersedes_contract_id": old.contract_id,
        "targets": [
            {**target.model_dump(), "project_relative_path": target.project_relative_path.replace("greeting", "salutation")}
            for target in old.targets
        ],
    })
    return ChangeArtifactTargetHandoff(
        run_id=state.run_id, project_root=tmp_path, action_id="correct-targets-1",
        action="approve-correction", expected_contract_id=old.contract_id,
        reason="Use the salutation module and matching test", corrected_contract=corrected,
        current_evidence=command.current_evidence,
    ), path


def apply_action(command, path):
    return change_artifact_target_handoff(command, state_dir=path.parent,
                                         worker_dir=path.parent.parent / "workers")


def test_exact_correction_is_append_only_and_replay_is_not_a_new_approval(action_project):
    command, path = action_project
    before = load_state(path)
    result = apply_action(command, path).state
    history = result.artifact_target_handoff
    assert history.contracts[:-1] == before.artifact_target_handoff.contracts
    assert history.active_contract_id == command.corrected_contract.contract_id
    assert history.reconciliations[-1].outcome == "ready"
    assert history.corrections[-1].actor_id == before.workflow_admission.decision.approving_actor_id
    assert result.workflow_admission == before.workflow_admission
    assert result.execution_record == before.execution_record
    assert result.interrupt_log == before.interrupt_log
    saved = path.read_bytes()
    assert apply_action(command, path).state == result
    assert path.read_bytes() == saved
    with pytest.raises(HumanActionRejected, match="conflicts"):
        apply_action(replace(command, reason="Different request"), path)
    assert path.read_bytes() == saved


@pytest.mark.parametrize("change", ["stale-tip", "out-of-scope", "source-edit", "active-worker", "unknown-actor"])
def test_invalid_correction_cannot_save_partial_approval(action_project, change):
    from uuid import uuid4
    command, path = action_project
    if change == "stale-tip":
        command = replace(command, expected_contract_id="0" * 64)
    elif change == "out-of-scope":
        raw = command.corrected_contract.model_dump(exclude={"contract_id"})
        raw["targets"][0]["project_relative_path"] = "elsewhere.py"
        command = replace(command, corrected_contract=ArtifactTargetContract.model_validate(raw))
    elif change == "source-edit":
        (command.project_root / "unexpected.py").write_text("changed", encoding="utf-8")
    elif change == "unknown-actor":
        command = replace(command, actor_id=uuid4())
    else:
        workers = path.parent.parent / "workers"
        workers.mkdir(exist_ok=True)
        (workers / f"{command.run_id}.json.launch.lock").touch()
    saved = path.read_bytes()
    with pytest.raises((HumanActionRejected, ArtifactTargetHandoffRejected)):
        apply_action(command, path)
    assert path.read_bytes() == saved


@pytest.mark.parametrize("action", ["return-to-architect", "cancel"])
def test_return_and_cancel_preserve_history_without_dispatch(action_project, action):
    command, path = action_project
    command = replace(command, action=action, corrected_contract=None, current_evidence=None)
    before = load_state(path)
    result = apply_action(command, path).state
    assert result.artifact_target_handoff.active_contract_id is None
    assert result.artifact_target_handoff.contracts == before.artifact_target_handoff.contracts
    assert result.execution_record == before.execution_record
    assert result.interrupt_log == before.interrupt_log
    assert result.workflow_admission == before.workflow_admission
    assert result.resume_target == ("architect" if action == "return-to-architect" else "blocked")
    saved = path.read_bytes()
    apply_action(command, path)
    assert path.read_bytes() == saved
    if action == "cancel":
        seen = []
        with patched_nodes(record=seen):
            final = RunState.model_validate(run_ticket(result, make_llm_configs(), base_dir=command.project_root))
        assert final.status == RunStatus.BLOCKED
        assert seen == []
        with pytest.raises(HumanActionRejected, match="cancelled"):
            apply_action(replace(command, action_id="another-action"), path)


def test_return_to_architect_requires_new_candidate_then_allows_explicit_approval(action_project):
    from functools import partial
    from battalion.graph import resume_ticket
    from battalion.nodes.architect import run_architect
    from battalion.state.persistence import save_state
    from support.responses import json_response
    command, path = action_project
    returned = apply_action(replace(command, action="return-to-architect", corrected_contract=None,
                                    current_evidence=None, action_id="return-1"), path).state
    with pytest.raises(HumanActionRejected, match="new completed Architect"):
        apply_action(command, path)
    candidate = returned.execution_record.node_executions[-1].architect_handoff_candidate.model_dump(mode="json")
    candidate["plan_markdown"] = "# Revised plan\nUse the salutation module."
    candidate["targets"] = [item.model_dump(mode="json") for item in command.corrected_contract.targets]
    seen = []
    with patched_nodes(record=seen, architect=partial(run_architect, call_llm_fn=lambda *args, **kwargs: json_response(candidate))):
        revised = RunState.model_validate(resume_ticket(
            returned, make_llm_configs(), base_dir=command.project_root,
            on_state_checkpoint=lambda state: save_state(state, path),
        ))
    assert seen == []  # No downstream fake role was dispatched.
    architect = revised.execution_record.node_executions[-1]
    assert architect.execution_id != returned.execution_record.node_executions[-1].execution_id
    replacement = ArtifactTargetContract.model_validate({
        **command.corrected_contract.model_dump(exclude={"contract_id"}),
        "architect_execution_id": architect.execution_id,
        "plan_artifact_digest": next(item.sha256 for item in architect.artifact_provenance if item.path == "plan.md"),
    })
    approved = apply_action(replace(command, corrected_contract=replacement), path).state
    assert approved.artifact_target_handoff.active_contract_id == replacement.contract_id
    assert approved.artifact_target_handoff.reconciliations[-1].outcome == "ready"
    assert approved.execution_record == revised.execution_record


def test_correction_renews_an_already_resolved_driver_checkpoint(action_project):
    from battalion.application import ResumeRun, RunRecoverable, resume_run
    from battalion.config import BattalionConfig
    command, path = action_project
    config = BattalionConfig(base_dir=str(command.project_root), models=make_llm_configs())
    resume = ResumeRun(command.run_id, config, action_id="old-checkpoint", resolution="Approve original",
                       current_artifact_evidence=command.current_evidence,
                       artifact_target_contract_id=command.expected_contract_id)
    def crash(**kwargs):
        raise RuntimeError("crash before graph entry")
    with pytest.raises(RunRecoverable):
        resume_run(resume, state_dir=path.parent, _execute=crash)
    corrected = apply_action(command, path).state
    assert corrected.interrupt_log[-2].resolution == "Approve original"
    assert corrected.interrupt_log[-1].resolution is None
    assert corrected.interrupt_log[-1].context["artifact_target_contract_id"] == command.corrected_contract.contract_id
    assert corrected.human_action_log[-1].artifact_target_contract_id == command.expected_contract_id
    with patched_nodes():
        result = resume_run(replace(resume, action_id="new-checkpoint", resolution="Approve replacement",
                                    artifact_target_contract_id=command.corrected_contract.contract_id), state_dir=path.parent)
    assert result.state.status == RunStatus.DONE
    assert all(item.artifact_target_contract_id == command.corrected_contract.contract_id
               for item in result.state.execution_record.node_executions if item.role == "driver")

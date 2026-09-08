"""Real Architect capture, restart, and application-owned initial sealing."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from battalion.application import (
    ArtifactTargetHandoffRejected, seal_architect_target_handoff,
)
from battalion.artifact_target_reconciliation import ArtifactTargetCurrentEvidence
from battalion.artifact_target_state import ArtifactTargetHandoffRecord
from battalion.config import BattalionConfig
from battalion.graph import run_ticket, resume_ticket
from battalion.artifact_target_runtime import ArtifactTargetGateRejected
from battalion.state.models import RunState, RunStatus
from battalion.state.persistence import load_state, save_state
from support.execution import make_node_execution
from support.graph import patched_nodes
from support.state import make_llm_configs


@pytest.fixture
def handoff_project(tmp_path, request):
    from support.artifact_targets import prepare_handoff_project
    return prepare_handoff_project(tmp_path, manual_checkpoint=getattr(request, "param", True))


def _seal(command, path):
    return seal_architect_target_handoff(
        command, state_dir=path.parent, worker_dir=path.parent.parent / "workers",
    )


def test_real_architect_candidate_survives_restart_and_seals_at_manual_pause(handoff_project):
    command, path = handoff_project
    command = replace(command, current_evidence=command.current_evidence.model_copy(
        update={"project_source_revision": None},
    ))
    before = load_state(path)
    attempt = before.execution_record.node_executions[-1]
    assert attempt.architect_handoff_candidate is not None
    assert attempt.outcome == "interrupted"
    assert before.execution_record.schema_version == "1.9"
    assert before.interrupt_log[-1].trigger == "manual-checkpoint"
    original_admission = before.workflow_admission.model_dump_json()
    result = _seal(command, path)
    loaded = load_state(path)
    assert loaded == result.state
    assert loaded.schema_version == "1.2"
    assert loaded.artifact_target_handoff.reconciliations[-1].outcome == "ready"
    contract = loaded.artifact_target_handoff.contracts[0]
    assert contract.targets == attempt.architect_handoff_candidate.targets
    assert contract.project_source_revision == before.project_source_snapshot.revision
    assert contract.architect_execution_id == attempt.execution_id
    assert contract.plan_artifact_digest == attempt.artifact_provenance[0].sha256
    assert loaded.workflow_admission.model_dump_json() == original_admission
    assert loaded.execution_record == before.execution_record
    assert loaded.interrupt_log == before.interrupt_log
    assert loaded.status == RunStatus.AWAITING_HUMAN
    assert not any(e.role == "driver" for e in loaded.execution_record.node_executions)


def test_unrelated_source_edit_invalidates_sealing(handoff_project):
    command, path = handoff_project
    (command.project_root / "unrelated.py").write_text("changed = True", encoding="utf-8")
    result = _seal(command, path)
    history = result.state.artifact_target_handoff
    assert history.reconciliations[-1].outcome == "clarification-required"
    assert "stale-evidence" in history.reconciliations[-1].reason_codes
    assert history.contracts[0].project_source_revision == result.state.project_source_snapshot.revision


def test_legacy_run_cannot_capture_baseline_after_architect(handoff_project):
    command, path = handoff_project
    state = load_state(path)
    state.project_source_snapshot = None
    save_state(state, path)
    saved = path.read_bytes()
    with pytest.raises(ArtifactTargetHandoffRejected, match="no pre-execution source snapshot"):
        _seal(command, path)
    assert path.read_bytes() == saved


def test_identical_sealing_replay_does_not_rewrite_state(handoff_project):
    command, path = handoff_project
    first = _seal(command, path)
    original_bytes = path.read_bytes()
    second = _seal(command, path)
    assert second == first
    assert path.read_bytes() == original_bytes
    assert len(second.state.artifact_target_handoff.reconciliations) == 1


def test_older_execution_schema_cannot_claim_candidate_evidence(handoff_project):
    _, path = handoff_project
    raw = load_state(path).model_dump()
    raw["execution_record"]["schema_version"] = "1.8"
    with pytest.raises(ValidationError, match="execution-record schema 1.9"):
        RunState.model_validate(raw)


def test_edited_plan_fails_closed_and_restoring_it_is_not_human_approval(handoff_project):
    command, path = handoff_project
    plan = command.project_root / "plan.md"
    original = plan.read_bytes()
    plan.write_text("A different plan", encoding="utf-8")
    result = _seal(command, path)
    assert result.state.artifact_target_handoff.reconciliations[-1].outcome == "clarification-required"
    assert "stale-evidence" in result.state.artifact_target_handoff.reconciliations[-1].reason_codes
    plan.write_bytes(original)
    saved = path.read_bytes()
    with pytest.raises(ArtifactTargetHandoffRejected, match="human correction"):
        _seal(command, path)
    assert path.read_bytes() == saved


@pytest.mark.parametrize("mutation,reason", [
    pytest.param("missing-candidate", "missing-targets", id="legacy-prose-not-parsed"),
    pytest.param("prior-driver", "stale-evidence", id="cannot-authorize-history"),
    pytest.param("newer-architect", "stale-evidence", id="cannot-select-old-success"),
    pytest.param("infra-interrupt", "missing-evidence", id="failure-not-a-completed-plan"),
    pytest.param("terminal", "stale-evidence", id="terminal-history"),
    pytest.param("cancelled", "stale-evidence", id="cannot-reactivate-cancelled-empty-history"),
])
def test_ineligible_execution_cannot_be_sealed(handoff_project, mutation, reason):
    command, path = handoff_project
    state = load_state(path)
    if mutation == "missing-candidate":
        state.execution_record.node_executions[-1].architect_handoff_candidate = None
        # A legacy successful execution still cannot supply paths from prose.
        state.execution_record.node_executions[-1].outcome = "succeeded"
    elif mutation == "prior-driver":
        state.execution_record.node_executions.append(make_node_execution(role="driver", phase="driver_red"))
    elif mutation == "newer-architect":
        state.execution_record.node_executions.append(make_node_execution(role="architect", phase="architect", outcome="rejected"))
    elif mutation == "infra-interrupt":
        state.interrupt_log[-1].trigger = "infra-failure"
    elif mutation == "terminal":
        state.status = RunStatus.DONE
    elif mutation == "cancelled":
        state.schema_version = "1.2"
        state.artifact_target_handoff = ArtifactTargetHandoffRecord(corrections=[{
            "action_id": "cancel:1", "action": "cancel", "reason": "Stop this handoff.",
            "actor_id": state.workflow_admission.decision.approving_actor_id,
            "occurred_at": state.workflow_admission.decision.occurred_at,
        }])
    save_state(state, path)
    saved = path.read_bytes()
    with pytest.raises(ArtifactTargetHandoffRejected) as rejected:
        _seal(command, path)
    assert rejected.value.reason_code == reason
    assert path.read_bytes() == saved


def test_initial_sealing_cannot_replace_a_contract(handoff_project):
    command, path = handoff_project
    _seal(command, path)
    changed = replace(command, current_evidence=ArtifactTargetCurrentEvidence.model_validate({
        **command.current_evidence.model_dump(), "project_source_revision": "source-r2",
    }))
    saved = path.read_bytes()
    with pytest.raises(ArtifactTargetHandoffRejected, match="cannot replace"):
        _seal(changed, path)
    assert path.read_bytes() == saved


def test_worker_action_lock_prevents_sealing(handoff_project):
    command, path = handoff_project
    workers = path.parent.parent / "workers"
    workers.mkdir(exist_ok=True)
    (workers / f"{command.run_id}.json.launch.lock").touch()
    saved = path.read_bytes()
    with pytest.raises(ArtifactTargetHandoffRejected, match="worker or another action"):
        _seal(command, path)
    assert path.read_bytes() == saved


def test_sealing_uses_persisted_scope_and_cannot_override_it(handoff_project):
    command, path = handoff_project
    state = load_state(path)
    state.write_scope["driver_red"] = []
    save_state(state, path)
    result = _seal(command, path)
    assert result.state.artifact_target_handoff.reconciliations[-1].outcome == "clarification-required"
    assert "out-of-scope" in result.state.artifact_target_handoff.reconciliations[-1].reason_codes


@pytest.mark.parametrize("handoff_project", [False], indirect=True)
@pytest.mark.parametrize("restart", [False, True], ids=["continuous", "unstarted-attempt-restart"])
def test_live_driver_gate_seals_before_attempt_and_revalidates_green(handoff_project, restart):
    from battalion.scope.tool_binding import build_write_tools
    command, path = handoff_project
    state = load_state(path)
    seen = []

    def driver(state, ticket_text, llm_config, base_dir, mode, prompts_dir=None):
        attempt = state.execution_record.node_executions[-1]
        assert attempt.artifact_target_contract_id == state.artifact_target_handoff.active_contract_id
        durable = load_state(path)
        assert durable.execution_record.node_executions[-1].artifact_target_contract_id == attempt.artifact_target_contract_id
        seen.append(mode)
        scope = "tests/" if mode == "red" else "src/"
        name = "test_greeting.py" if mode == "red" else "greeting.py"
        build_write_tools(f"driver_{mode}", state.write_scope, base_dir)[scope].write(name, "# scoped output\n")
        return state.model_copy(update={"phase": "reviewer"})

    if restart:
        class SimulatedCrash(RuntimeError):
            pass

        def crash_after_creation(updated):
            save_state(updated, path)
            if updated.graph_progress.stage.value == "attempt-created":
                raise SimulatedCrash("crash after durable attempt creation")

        with patched_nodes(driver=driver), pytest.raises(SimulatedCrash):
            resume_ticket(state, make_llm_configs(), base_dir=command.project_root,
                          current_artifact_evidence=command.current_evidence,
                          on_state_checkpoint=crash_after_creation)
        state = load_state(path)
        original_attempt_id = state.execution_record.node_executions[-1].execution_id
        assert seen == []

    with patched_nodes(driver=driver):
        result = RunState.model_validate(resume_ticket(
            state, make_llm_configs(), base_dir=command.project_root,
            current_artifact_evidence=command.current_evidence,
            on_state_checkpoint=lambda updated: save_state(updated, path),
        ))
    assert result.status == RunStatus.DONE
    assert seen == ["red", "green"]
    if restart:
        assert next(item for item in result.execution_record.node_executions
                    if item.phase == "driver_red").execution_id == original_attempt_id
    contract_id = result.artifact_target_handoff.active_contract_id
    assert all(item.artifact_target_contract_id == contract_id
               for item in result.execution_record.node_executions if item.role == "driver")
    raw = result.model_dump()
    next(item for item in raw["execution_record"]["node_executions"]
         if item["role"] == "driver")["artifact_target_contract_id"] = "0" * 64
    with pytest.raises(ValidationError, match="unknown artifact target contract"):
        RunState.model_validate(raw)


@pytest.mark.parametrize("handoff_project", [False], indirect=True)
@pytest.mark.parametrize("mutation,reason", [
    ("no-current-evidence", "missing-evidence"),
    ("legacy", "missing-evidence"),
    ("source-edit", "stale-evidence"),
    ("scope-change", "out-of-scope"),
])
def test_live_driver_gate_rejects_before_attempt_budget_and_tools(handoff_project, mutation, reason):
    command, path = handoff_project
    state = load_state(path)
    current = command.current_evidence
    if mutation == "no-current-evidence":
        current = None
    elif mutation == "legacy":
        state.project_source_snapshot = None
    elif mutation == "source-edit":
        (command.project_root / "unrelated.py").write_text("changed", encoding="utf-8")
    else:
        state.write_scope["driver_red"] = []
    before_budget = state.budget.used
    before_attempts = len(state.execution_record.node_executions)
    seen = []
    with patched_nodes(record=seen), pytest.raises(ArtifactTargetGateRejected) as rejected:
        resume_ticket(
            state, make_llm_configs(), base_dir=command.project_root,
            current_artifact_evidence=current,
            on_state_checkpoint=lambda updated: save_state(updated, path),
        )
    assert rejected.value.reason_code == reason
    assert seen == []
    saved = load_state(path)
    assert saved.budget.used == before_budget
    assert len(saved.execution_record.node_executions) == before_attempts
    assert saved.interrupt_log == state.interrupt_log


def test_generic_driver_checkpoint_resolution_cannot_authorize_contract(handoff_project):
    command, path = handoff_project
    state = load_state(path)
    state.interrupt_log[-1].resolution = "go ahead"
    state.status = RunStatus.IN_PROGRESS
    state.phase = "driver_red"
    state.resume_target = "driver_red"
    state.graph_progress = None
    with patched_nodes(), pytest.raises(ArtifactTargetGateRejected, match="contract-bound"):
        run_ticket(state, make_llm_configs(), base_dir=command.project_root,
                   current_artifact_evidence=command.current_evidence)


@pytest.mark.parametrize("handoff_project", [False], indirect=True)
def test_application_translates_gate_failure_without_generic_recovery_error(handoff_project):
    from battalion.application import StartRun, start_run
    command, path = handoff_project
    state = load_state(path).model_copy(update={"resume_target": "driver_red"})
    config = BattalionConfig(base_dir=str(command.project_root), models=make_llm_configs(),
                             write_scope=state.write_scope)
    seen = []
    with patched_nodes(record=seen), pytest.raises(ArtifactTargetHandoffRejected) as rejected:
        start_run(StartRun(state, config, overwrite=True), state_dir=path.parent)
    assert rejected.value.reason_code == "missing-evidence"
    assert seen == []
    assert not any(item.role == "driver" for item in load_state(path).execution_record.node_executions)


@pytest.mark.parametrize("handoff_project", [False], indirect=True)
def test_ready_handoff_is_revalidated_before_green_after_restart(handoff_project):
    command, path = handoff_project
    state = load_state(path)

    class SimulatedCrash(RuntimeError):
        pass

    def stop_before_green(updated):
        save_state(updated, path)
        if (updated.graph_progress.stage.value == "attempt-completed"
                and updated.graph_progress.next_node == "driver_green"):
            raise SimulatedCrash("saved RED review")

    with patched_nodes(), pytest.raises(SimulatedCrash):
        resume_ticket(state, make_llm_configs(), base_dir=command.project_root,
                      current_artifact_evidence=command.current_evidence,
                      on_state_checkpoint=stop_before_green)
    before = load_state(path)
    assert before.artifact_target_handoff.reconciliations[-1].outcome == "ready"
    (command.project_root / "unrelated.py").write_text("unexpected", encoding="utf-8")
    seen = []
    with patched_nodes(record=seen), pytest.raises(ArtifactTargetGateRejected):
        resume_ticket(before, make_llm_configs(), base_dir=command.project_root,
                      current_artifact_evidence=command.current_evidence,
                      on_state_checkpoint=lambda updated: save_state(updated, path))
    after = load_state(path)
    assert seen == []
    assert after.execution_record == before.execution_record
    assert after.budget == before.budget
    assert after.artifact_target_handoff.active_contract_id == before.artifact_target_handoff.active_contract_id
    assert after.artifact_target_handoff.reconciliations[-1].outcome == "clarification-required"
    assert "stale-evidence" in after.artifact_target_handoff.reconciliations[-1].reason_codes


def _checkpoint_command(handoff_project):
    from battalion.application import ResumeRun
    command, path = handoff_project
    sealed = _seal(replace(command, case_sensitive_paths=False), path).state
    return ResumeRun(
        run_id=command.run_id,
        config=BattalionConfig(base_dir=str(command.project_root), models=make_llm_configs(),
                               write_scope=sealed.write_scope),
        resolution="Approve these exact targets", action_id="checkpoint-approval-1",
        current_artifact_evidence=command.current_evidence,
        artifact_target_contract_id=sealed.artifact_target_handoff.active_contract_id,
    ), path


@pytest.mark.parametrize("restart", [False, True], ids=["continuous", "after-authorization-restart"])
def test_contract_bound_checkpoint_authorizes_driver_and_replays_without_mutation(handoff_project, restart):
    from battalion.application import HumanActionRejected, RunRecoverable, resume_run
    command, path = _checkpoint_command(handoff_project)
    before = load_state(path)
    if restart:
        def crash_before_graph(**kwargs):
            raise RuntimeError("simulated process failure before graph entry")
        with pytest.raises(RunRecoverable):
            resume_run(command, state_dir=path.parent, _execute=crash_before_graph)
        authorized = load_state(path)
        assert authorized.resume_intent.action_id == command.action_id
        assert not authorized.resume_intent.completed
        assert len(authorized.human_action_log) == 1
    seen = []
    with patched_nodes(record=seen):
        result = resume_run(command, state_dir=path.parent)
    assert result.state.status == RunStatus.DONE
    assert "driver_red" in seen and "driver_green" in seen
    action = result.state.human_action_log[-1]
    assert len(result.state.human_action_log) == 1
    assert action.artifact_target_contract_id == command.artifact_target_contract_id
    assert action.actor_id == before.workflow_admission.decision.approving_actor_id
    assert result.state.workflow_admission == before.workflow_admission
    assert result.state.interrupt_log[-1].resolution == command.resolution
    saved = path.read_bytes()
    replay = resume_run(command, state_dir=path.parent)
    assert replay.state == result.state
    assert path.read_bytes() == saved
    with pytest.raises(HumanActionRejected, match="conflicts"):
        resume_run(replace(command, artifact_target_contract_id="0" * 64), state_dir=path.parent)
    assert path.read_bytes() == saved
    raw = result.state.model_dump()
    raw["human_action_log"][-1]["artifact_target_contract_id"] = "0" * 64
    with pytest.raises(ValidationError, match="unknown contract"):
        RunState.model_validate(raw)
    # A later, structurally valid contract must not inherit this approval.
    from battalion.artifact_targets import ArtifactTargetContract
    from battalion.artifact_target_runtime import admit_driver_attempt
    from battalion.artifact_target_state import ArtifactTargetReconciliation
    from datetime import datetime, timezone
    history = result.state.artifact_target_handoff
    old = history.contracts[-1]
    newer = ArtifactTargetContract.model_validate({
        **old.model_dump(exclude={"contract_id"}), "supersedes_contract_id": old.contract_id,
    })
    new_ready = ArtifactTargetReconciliation.model_validate({
        **history.reconciliations[-1].model_dump(), "contract_id": newer.contract_id,
        "reconciliation_id": "later-contract-ready", "occurred_at": datetime.now(timezone.utc),
    })
    later = RunState.model_validate({
        **result.state.model_dump(), "status": "in-progress",
        "artifact_target_handoff": ArtifactTargetHandoffRecord(
            contracts=(*history.contracts, newer),
            reconciliations=(*history.reconciliations, new_ready),
            active_contract_id=newer.contract_id,
        ),
    })
    with pytest.raises(ArtifactTargetGateRejected, match="contract-bound"):
        admit_driver_attempt(later, project_root=command.config.base_dir,
                             current=command.current_artifact_evidence, node_name="driver_red")
    assert later.human_action_log[-1].artifact_target_contract_id == old.contract_id


@pytest.mark.parametrize("change", ["missing-id", "different-id", "stale-source", "missing-current"])
def test_invalid_checkpoint_authorization_does_not_consume_action_or_resolution(handoff_project, change):
    from battalion.application import HumanActionRejected, resume_run
    command, path = _checkpoint_command(handoff_project)
    if change == "missing-id":
        command = replace(command, artifact_target_contract_id=None)
    elif change == "different-id":
        command = replace(command, artifact_target_contract_id="0" * 64)
    elif change == "missing-current":
        command = replace(command, current_artifact_evidence=None)
    else:
        (Path(command.config.base_dir) / "unexpected.py").write_text("changed", encoding="utf-8")
    saved = path.read_bytes()
    seen = []
    with patched_nodes(record=seen), pytest.raises((HumanActionRejected, ArtifactTargetHandoffRejected)):
        resume_run(command, state_dir=path.parent)
    assert path.read_bytes() == saved
    assert seen == []

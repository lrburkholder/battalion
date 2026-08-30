"""BTN-165 fault injection against real graph routing and atomic JSON state."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from battalion import application, graph
from battalion.application import (
    HumanActionRejected, InspectRun, QueueIntervention, ResumeRun,
    RunRecoverable, RunRecoveryUnsafe, StartRun, StartWorker,
    inspect_run, queue_intervention, resume_run, start_run, start_worker,
)
from battalion.config import BattalionConfig
from battalion.nodes.errors import RoleContractViolation
from battalion.recovery import assess_recovery
from battalion.state.models import (
    InterventionDisposition, InterventionKind, InterventionTarget,
    InterruptLogEntry, ProgressStage, RunState, RunStatus,
)
from battalion.state.persistence import load_state, save_state
from conftest import (
    make_run_state, make_llm_configs, patched_nodes, driver_advancing,
    architect_advancing, reviewer_accepting, refactorer_advancing,
)


class ProcessCrash(BaseException):
    """A hard stop that deliberately bypasses application exception handling."""


@pytest.fixture
def paused(tmp_path):
    config = BattalionConfig(base_dir=str(tmp_path), models=make_llm_configs())
    # Use separate identities even with hermetic runner fakes.
    config.models["reviewer"] = replace(config.models["reviewer"], model="review-model")
    state = make_run_state(
        ticket_id="BTN-165", status=RunStatus.AWAITING_HUMAN, phase="awaiting_human",
        interrupt_log=[InterruptLogEntry(
            trigger="manual-checkpoint", timestamp=datetime.now(timezone.utc),
            context={"next_phase": "driver_green"},
        )],
    )
    path = tmp_path / f"{state.run_id}.json"
    save_state(state, path)
    return ResumeRun(state.run_id, config, resolution="Approved GREEN correction"), path


def crash_at_save(monkeypatch, predicate, *, after=True, exception=ProcessCrash):
    def save(state, path):
        if predicate(state):
            if after:
                save_state(state, path)
            raise exception("injected crash")
        save_state(state, path)
    monkeypatch.setattr(application, "save_state", save)


def at_stage(stage):
    return lambda s: s.graph_progress is not None and s.graph_progress.stage is stage


def test_crash_after_resolution_replays_original_authorization(paused, monkeypatch):
    command, path = paused
    crash_at_save(monkeypatch, lambda s: s.resume_intent is not None)
    with pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    original = saved.human_action_log[0]
    assert saved.interrupt_log[0].resolution == command.resolution
    assert assess_recovery(saved).stage is ProgressStage.BEFORE_ATTEMPT
    monkeypatch.setattr(application, "save_state", save_state)
    with patched_nodes():
        result = resume_run(command, state_dir=path.parent)
    assert result.state.status is RunStatus.DONE
    assert result.state.human_action_log == [original]
    # An explicit replay after completion must not execute again or mutate evidence.
    with patched_nodes(driver=lambda **_: pytest.fail("completed decision replayed")):
        replay = resume_run(replace(command, action_id=original.action_id), state_dir=path.parent)
    assert replay.state == result.state


@pytest.mark.parametrize("after", [False, True])
def test_creation_and_delivery_are_atomic_and_replay_same_attempt(paused, monkeypatch, after):
    command, path = paused
    queued = queue_intervention(QueueIntervention(
        command.run_id, InterventionKind.CORRECTION, InterventionTarget.DRIVER_GREEN,
        "Preserve the approved write boundary", project_root=path.parent,
    ), state_dir=path.parent, worker_dir=path.parent / "workers")
    crash_at_save(monkeypatch, at_stage(ProgressStage.ATTEMPT_CREATED), after=after)
    with patched_nodes(), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    if after:
        attempt = saved.execution_record.node_executions[-1]
        assert attempt.outcome == "in-progress" and attempt.ended_at is None
        assert saved.interventions[0].delivered_to_execution_id == attempt.execution_id
    else:
        assert not saved.execution_record.node_executions
        assert saved.interventions[0].disposition is InterventionDisposition.QUEUED
    monkeypatch.setattr(application, "save_state", save_state)
    texts = []
    def driver(**kwargs):
        texts.append(kwargs["ticket_text"])
        return kwargs["state"].model_copy(update={"phase": "reviewer"})
    with patched_nodes(driver=driver):
        result = resume_run(command, state_dir=path.parent)
    assert len(texts) == 1 and queued.action.detail in texts[0]
    received = result.state.execution_record.node_executions[0]
    assert result.state.interventions[0].delivered_to_execution_id == received.execution_id
    if after:
        assert received.execution_id == attempt.execution_id
    assert result.state.human_action_log[0].actor_id == queued.action.actor_id
    assert result.state.budget.used == 4


def test_unknown_started_attempt_is_terminal_and_never_replayed(paused, monkeypatch):
    command, path = paused
    crash_at_save(monkeypatch, at_stage(ProgressStage.ATTEMPT_STARTED))
    with patched_nodes(), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    saved_bytes = path.read_bytes()
    inspection = inspect_run(InspectRun(command.run_id), state_dir=path.parent)
    assert inspection.recovery.disposition == "terminal"
    assert inspection.recovery.stage is ProgressStage.ATTEMPT_STARTED
    monkeypatch.setattr(application, "save_state", save_state)
    with pytest.raises(RunRecoveryUnsafe, match="Inspect the execution record and workspace"):
        resume_run(command, state_dir=path.parent, _execute=lambda **_: pytest.fail("unsafe replay"))
    with pytest.raises(RunRecoveryUnsafe):
        start_worker(StartWorker(command), state_dir=path.parent, worker_dir=path.parent / "workers")
    assert path.read_bytes() == saved_bytes


@pytest.mark.parametrize("stage", [ProgressStage.ATTEMPT_COMPLETED, ProgressStage.OUTCOME_CHECKPOINTED])
def test_completed_step_recovery_uses_exact_successor(paused, monkeypatch, stage):
    command, path = paused
    crash_at_save(monkeypatch, at_stage(stage))
    with patched_nodes(), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    assert saved.graph_progress.next_node == "reviewer_green"
    assert saved.execution_record.node_executions[-1].ended_at is not None
    monkeypatch.setattr(application, "save_state", save_state)
    calls = []
    with patched_nodes(driver=driver_advancing(calls), reviewer=reviewer_accepting(calls),
                       refactorer=refactorer_advancing(calls)):
        result = resume_run(command, state_dir=path.parent)
    assert calls == ["reviewer_green-check", "refactorer", "reviewer_refactor-check"]
    assert len(result.state.human_action_log) == 1
    assert result.state.execution_record.node_executions[0] == saved.execution_record.node_executions[0]


def test_node_end_exception_retains_output_and_reports_recoverable(paused):
    command, path = paused
    def fail_event(event):
        if event["type"] == "node_end":
            raise RuntimeError("broken observer")
    with patched_nodes(), pytest.raises(RunRecoverable):
        resume_run(command, state_dir=path.parent, on_node_event=fail_event)
    saved = load_state(path)
    assert saved.graph_progress.stage is ProgressStage.ATTEMPT_COMPLETED
    assert saved.graph_progress.next_node == "reviewer_green"
    assert saved.budget.used == 1
    with patched_nodes():
        result = resume_run(command, state_dir=path.parent)
    assert result.state.budget.used == 4


@pytest.mark.parametrize("resuming", [False, True])
def test_recursion_limit_preserves_latest_checkpoint_and_can_continue(paused, resuming):
    command, path = paused
    with patched_nodes():
        if resuming:
            result = resume_run(command, state_dir=path.parent,
                                _execute=lambda **kw: graph.resume_ticket(max_turns=1, **kw))
            expected = "reviewer_green"
        else:
            path.unlink()
            initial = make_run_state(ticket_id="BTN-165")
            result = start_run(StartRun(initial, command.config), state_dir=path.parent,
                               _execute=lambda **kw: graph.run_ticket(max_turns=1, **kw))
            expected = "driver_red"
    assert result.state.status is RunStatus.BLOCKED
    assert result.state.graph_progress.next_node == expected
    assert result.state.budget.used == 1
    assert len(result.state.execution_record.node_executions) == 1
    assert load_state(path) == result.state
    with patched_nodes():
        completed = resume_run(command, state_dir=path.parent)
    assert completed.state.status is RunStatus.DONE
    assert completed.state.execution_record.node_executions[0] == result.state.execution_record.node_executions[0]


def test_intervention_replay_is_idempotent_and_conflicts_are_rejected(paused):
    command, path = paused
    request = QueueIntervention(command.run_id, InterventionKind.CORRECTION,
                                InterventionTarget.DRIVER_GREEN, "Use exact scope", project_root=path.parent)
    first = queue_intervention(request, state_dir=path.parent, worker_dir=path.parent / "workers")
    again = queue_intervention(request, state_dir=path.parent, worker_dir=path.parent / "workers")
    assert first == again
    with pytest.raises(HumanActionRejected, match="conflicts"):
        queue_intervention(replace(request, text="Changed decision"), state_dir=path.parent,
                           worker_dir=path.parent / "workers")
    assert load_state(path).human_action_log == [first.action]


def test_resume_replay_rejects_changed_decision(paused, monkeypatch):
    command, path = paused
    crash_at_save(monkeypatch, lambda s: s.resume_intent is not None)
    with pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    before = path.read_bytes()
    with pytest.raises(HumanActionRejected, match="conflicts"):
        resume_run(replace(command, resolution="A different decision"), state_dir=path.parent)
    assert path.read_bytes() == before


def test_failed_initial_graph_build_retains_recoverable_baseline(tmp_path):
    initial = make_run_state()
    def fail(**kwargs):
        raise RuntimeError("could not construct graph")
    with pytest.raises(RunRecoverable):
        start_run(StartRun(initial, BattalionConfig()), state_dir=tmp_path, _execute=fail)
    saved = load_state(tmp_path / f"{initial.run_id}.json")
    assert saved.graph_progress.stage is ProgressStage.BEFORE_ATTEMPT


def test_worker_restart_accepts_pending_resolution_without_new_authorization(paused, monkeypatch):
    command, path = paused
    crash_at_save(monkeypatch, lambda s: s.resume_intent is not None)
    with pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    captured = {}
    monkeypatch.setattr(application, "launch_worker", lambda **kw: captured.update(kw))
    start_worker(StartWorker(command), state_dir=path.parent, worker_dir=path.parent / "workers")
    assert captured["resume_actor_id"] == load_state(path).human_action_log[0].actor_id
    assert captured["resume_resolution"] == command.resolution
    assert captured["resume_action_id"] == load_state(path).resume_intent.action_id


def test_worker_continuation_does_not_replay_an_already_completed_action(paused, monkeypatch):
    command, path = paused
    crash_at_save(monkeypatch, at_stage(ProgressStage.ATTEMPT_COMPLETED))
    with patched_nodes(), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    captured = {}
    monkeypatch.setattr(application, "launch_worker", lambda **kw: captured.update(kw))
    start_worker(StartWorker(command), state_dir=path.parent, worker_dir=path.parent / "workers")
    assert captured["resume_action_id"] is None
    monkeypatch.setattr(application, "save_state", save_state)
    with patched_nodes():
        result = resume_run(replace(command, action_id=captured["resume_action_id"]), state_dir=path.parent)
    assert result.state.status is RunStatus.DONE


def test_typed_blocked_resume_ignores_an_older_interrupt_target(paused):
    from battalion.execution import record_role_result
    from battalion.role_results import DriverReasonCode, RoleExecutionResult, RoleResultKind
    command, path = paused
    def blocked(**kwargs):
        record_role_result(RoleExecutionResult(
            kind=RoleResultKind.BLOCKED, reason_code=DriverReasonCode.MISSING_CONTEXT,
            summary="Need a fixture",
        ))
        return kwargs["state"].model_copy(update={
            "status": RunStatus.BLOCKED, "phase": "driver_green", "resume_target": "driver_green",
        })
    saved = load_state(path)
    saved.interrupt_log[0].context["next_phase"] = "driver_red"
    # Reach GREEN via a normal RED review, then block there.
    save_state(saved, path)
    def driver(**kwargs):
        if kwargs["mode"] == "green":
            return blocked(**kwargs)
        return kwargs["state"].model_copy(update={"phase": "reviewer"})
    with patched_nodes(driver=driver):
        result = resume_run(command, state_dir=path.parent)
    assert result.state.status is RunStatus.BLOCKED
    calls = []
    with patched_nodes(driver=driver_advancing(calls)):
        completed = resume_run(command, state_dir=path.parent)
    assert calls == ["driver_green"]
    assert completed.state.status is RunStatus.DONE


def test_cli_reports_recovery_from_saved_checkpoint(paused, monkeypatch):
    from typer.testing import CliRunner
    from battalion import cli
    command, path = paused
    crash_at_save(monkeypatch, lambda s: s.resume_intent is not None)
    with pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    monkeypatch.setattr(cli, "STATE_DIR", path.parent)
    response = CliRunner().invoke(cli.app, ["status", command.run_id, "--human"])
    assert response.exit_code == 0
    assert "recoverable" in response.output and "saved authorization" in response.output


def test_delivery_without_durable_attempt_is_rejected(paused):
    command, path = paused
    queued = queue_intervention(QueueIntervention(
        command.run_id, InterventionKind.CORRECTION, InterventionTarget.DRIVER_GREEN,
        "Use approved scope", project_root=path.parent,
    ), state_dir=path.parent, worker_dir=path.parent / "workers")
    with pytest.raises(ValueError, match="existing unfinished target attempt"):
        graph._deliver_interventions(queued.state, "driver_green", "nonexistent", None)


def test_correction_retry_keeps_context_and_retry_bound_after_crash(paused, monkeypatch):
    command, path = paused
    def invalid(**kwargs):
        raise RoleContractViolation("No test edits in GREEN", reason_code="wrong-phase")
    crash_at_save(monkeypatch, lambda s: (
        s.graph_progress is not None and s.graph_progress.correction_attempt == 1
        and s.graph_progress.stage is ProgressStage.ATTEMPT_COMPLETED
    ))
    with patched_nodes(driver=invalid), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    assert saved.graph_progress.next_node == "driver_green"
    assert "wrong-phase" in saved.graph_progress.correction_context
    monkeypatch.setattr(application, "save_state", save_state)
    attempts = []
    def still_invalid(**kwargs):
        attempts.append(kwargs["ticket_text"])
        invalid(**kwargs)
    with patched_nodes(driver=still_invalid):
        result = resume_run(command, state_dir=path.parent)
    assert len(attempts) == 1 and "wrong-phase" in attempts[0]
    assert result.state.status is RunStatus.AWAITING_HUMAN
    executions = result.state.execution_record.node_executions
    assert [e.role_contract_violation.attempt_number for e in executions] == [1, 2]
    assert result.state.budget.used == 2


def test_unexpected_runner_failure_cannot_replace_newer_checkpoint(paused):
    command, path = paused
    def fail_review(**kwargs):
        raise RuntimeError("unexpected reviewer failure")
    with patched_nodes(reviewer=fail_review), pytest.raises(RunRecoveryUnsafe):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    assert saved.graph_progress.next_node == "reviewer_green"
    assert saved.graph_progress.stage is ProgressStage.ATTEMPT_STARTED
    assert saved.budget.used == 2
    assert [e.outcome for e in saved.execution_record.node_executions] == ["succeeded", "in-progress"]
    assert saved.resume_intent.completed


def test_failure_before_outcome_commit_is_not_claimed_replay_safe(paused, monkeypatch):
    command, path = paused
    crash_at_save(monkeypatch, at_stage(ProgressStage.ATTEMPT_COMPLETED), after=False,
                  exception=RuntimeError)
    with patched_nodes(), pytest.raises(RunRecoveryUnsafe):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    assert saved.graph_progress.stage is ProgressStage.ATTEMPT_STARTED
    assert saved.execution_record.node_executions[-1].outcome == "in-progress"


def test_malformed_recovery_identity_is_a_typed_read_failure(paused, monkeypatch):
    from battalion.application import StateReadFailed
    command, path = paused
    crash_at_save(monkeypatch, at_stage(ProgressStage.ATTEMPT_CREATED))
    with patched_nodes(), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    saved = load_state(path)
    corrupt = saved.model_copy(update={
        "graph_progress": saved.graph_progress.model_copy(update={"execution_id": "missing"}),
    })
    save_state(corrupt, path)
    with pytest.raises(StateReadFailed, match="matching execution"):
        inspect_run(InspectRun(command.run_id), state_dir=path.parent)


@pytest.mark.parametrize("stage,disposition", [
    (ProgressStage.ATTEMPT_CREATED, "recoverable"),
    (ProgressStage.ATTEMPT_STARTED, "terminal"),
])
def test_desktop_projects_typed_recovery_and_unfinished_attempt(paused, monkeypatch, stage, disposition):
    from battalion.application import ProjectRunInspection
    from battalion.identity import RunCatalogEntry
    from battalion.desktop.presentation import render_run, render_execution
    command, path = paused
    crash_at_save(monkeypatch, at_stage(stage))
    with patched_nodes(), pytest.raises(ProcessCrash):
        resume_run(command, state_dir=path.parent)
    inspection = inspect_run(InspectRun(command.run_id), state_dir=path.parent)
    entry = RunCatalogEntry(
        run_id=command.run_id, ticket_id="BTN-165", display_alias="Crash recovery",
        state_path=str(path), legacy_id=True,
    )
    text = render_run(ProjectRunInspection(entry, "available", inspection))
    assert f"Recovery: {disposition}" in text and stage.value in text
    assert "Not completed" in render_execution(inspection.state.execution_record.node_executions[-1])

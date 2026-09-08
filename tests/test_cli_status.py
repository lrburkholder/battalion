"""CLI durable status, recovery guidance, and human inspection."""


import json
from datetime import datetime, timezone
import pytest
from typer.testing import CliRunner
from battalion.cli import app, _state_path
from battalion.role_results import DriverReasonCode, RoleExecutionResult, RoleResultKind
from battalion.state.models import Budget, ExecutionRecord, NodeExecution, RunState, RunStatus
from battalion.state.persistence import save_state
from support.cli import (
    make_paused_state,
)


runner = CliRunner()


@pytest.mark.parametrize("trigger,anchor", [
    ("infra-failure", "infra-failure"),
    ("out-of-scope-write", "authority-stop"),
    ("role-definition-edit", "authority-stop"),
    ("manual-checkpoint", "human-checkpoints"),
    ("budget-exceeded", "human-checkpoints"),
    ("same-root-cause-twice", "reviewer-tests"),
    ("role-escalation", "role-output"),
    ("future-trigger", "run-stopped"),
])
def test_status_maps_stops_to_guide_without_changing_json(tmp_path, monkeypatch, trigger, anchor):
    from battalion.cli import TROUBLESHOOTING_URL
    from battalion.state.models import InterruptLogEntry

    monkeypatch.chdir(tmp_path)
    state = make_paused_state("00000000-0000-4000-8000-000000000001")
    state.interrupt_log = [InterruptLogEntry(
        trigger=trigger, timestamp=datetime.now(timezone.utc), context={},
    )]
    path = _state_path(state.run_id)
    save_state(state, path)
    original = path.read_bytes()
    human = runner.invoke(app, ["status", state.run_id, "--human"])
    assert human.exit_code == 0, human.output
    assert f"{TROUBLESHOOTING_URL}#{anchor}" in human.output
    structured = runner.invoke(app, ["status", state.run_id])
    assert structured.exit_code == 0, structured.output
    assert json.loads(structured.output) == state.model_dump(mode="json")
    assert path.read_bytes() == original


def test_pause_does_not_mislabel_reviewer_harness_failure_as_provider_failure(capsys):
    from battalion.cli import _print_pause_reason
    from battalion.state.models import InterruptLogEntry

    state = make_paused_state("guide-harness-failure")
    state.interrupt_log = [InterruptLogEntry(
        trigger="infra-failure", timestamp=datetime.now(timezone.utc),
        context={"error": "Reviewer test execution: collection-usage-internal-error"},
    )]
    _print_pause_reason(state, state.run_id)
    output = capsys.readouterr().out
    assert "collection-usage-internal-error" in output
    assert "troubleshooting.html#infra-failure" in output
    assert "LLM call failed" not in output
    assert "Provider error" not in output


@pytest.mark.parametrize("stage,disposition", [
    ("interrupted-before-attempt", "recoverable"),
    ("attempt-started", "terminal"),
])
def test_recovery_status_links_to_replay_safety_guidance(tmp_path, monkeypatch, stage, disposition):
    from battalion.state.models import GraphProgress

    monkeypatch.chdir(tmp_path)
    state = make_paused_state("guide-recovery")
    state.status = RunStatus.IN_PROGRESS
    state.graph_progress = GraphProgress(
        stage=stage, next_node="driver_red",
        execution_id="attempt-guide" if stage == "attempt-started" else None,
    )
    if stage == "attempt-started":
        state.execution_record.node_executions = [NodeExecution(
            execution_id="attempt-guide", role="driver", phase="driver_red",
            model_identity="offline-guide-model", started_at=datetime.now(timezone.utc),
            outcome="in-progress",
        )]
    save_state(state, _state_path(state.run_id))
    result = runner.invoke(app, ["status", state.run_id, "--human"])
    assert result.exit_code == 0, result.output
    assert f"Recovery:    {disposition}" in result.output
    assert "troubleshooting.html#resume-recovery" in result.output


def test_status_json_output(tmp_path, monkeypatch):
    """Test that `battalion status` outputs JSON by default."""
    # Create a state file
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    state = RunState(
        schema_version="1.0",
        run_id="run-BTN-9-test",
        ticket_id="BTN-9-test",
        status=RunStatus.IN_PROGRESS,
        phase="driver_red",
        write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
        retry_bound=2,
        budget=Budget(limit=100, used=25),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=["reviewer"],
    )
    save_state(state, state_dir / "run-BTN-9-test.json")

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["status", "run-BTN-9-test"])

    assert result.exit_code == 0

    # Parse as JSON
    output = json.loads(result.output)
    assert output["run_id"] == "run-BTN-9-test"
    assert output["status"] == "in-progress"
    assert output["phase"] == "driver_red"
    assert output["budget"]["used"] == 25
    assert output["budget"]["limit"] == 100


def test_status_human_flag(tmp_path, monkeypatch):
    """Test that `battalion status --human` outputs human-readable text."""
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    state = RunState(
        schema_version="1.0",
        run_id="run-BTN-9-test",
        ticket_id="BTN-9-test",
        status=RunStatus.AWAITING_HUMAN,
        phase="awaiting_human",
        write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
        retry_bound=2,
        budget=Budget(limit=100, used=50),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=["driver", "reviewer"],
    )
    save_state(state, state_dir / "run-BTN-9-test.json")

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["status", "run-BTN-9-test", "--human"])

    assert result.exit_code == 0
    assert "Run ID:      run-BTN-9-test" in result.output
    assert "Ticket:      BTN-9-test" in result.output
    assert "Status:      awaiting-human" in result.output
    assert "Phase:       awaiting_human" in result.output
    assert "Budget:      50 / 100" in result.output
    assert "Checkpoints: driver, reviewer" in result.output


def test_status_human_flag_displays_normalized_role_result(tmp_path, monkeypatch):
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    state = RunState(
        schema_version="1.0",
        run_id="run-role-result",
        ticket_id="BTN-133",
        status=RunStatus.BLOCKED,
        phase="driver_red",
        write_scope={"driver_red": ["tests/"]},
        retry_bound=2,
        budget=Budget(limit=100, used=1),
        execution_record=ExecutionRecord(node_executions=[NodeExecution(
            execution_id="node-role-result",
            role="driver",
            phase="driver_red",
            model_identity="test-model",
            started_at=now,
            ended_at=now,
            outcome="succeeded",
            role_result=RoleExecutionResult(
                kind=RoleResultKind.BLOCKED,
                reason_code=DriverReasonCode.MISSING_CONTEXT,
                summary="The public API contract is not supplied.",
            ),
        )]),
    )
    save_state(state, state_dir / "run-role-result.json")

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["status", "run-role-result", "--human"])

    assert result.exit_code == 0
    assert "Role results:" in result.output
    assert "driver_red: blocked (missing-context; The public API contract is not supplied.)" in result.output


def test_status_missing_state_file(tmp_path, monkeypatch):
    """Test that status fails with missing state file."""
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["status", "run-nonexistent"])

    assert result.exit_code != 0
    assert "No state file found" in result.output

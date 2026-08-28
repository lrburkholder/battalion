"""CLI integration tests for battalion.cli (BTN-9)."""

import json
import tempfile
import tomllib
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from battalion.cli import app, _state_path
from battalion.state.models import RunState, RunStatus, Budget
from battalion.state.persistence import save_state


runner = CliRunner()


def test_project_installs_the_battalion_console_script() -> None:
    """Pause guidance must name an entry point that package installs expose."""
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["battalion"] == "battalion.cli:main"


def make_paused_state(run_id: str, phase: str = "driver_red") -> RunState:
    """Create a state that's paused at an interrupt."""
    return RunState(
        schema_version="1.0",
        run_id=run_id,
        ticket_id="BTN-9-test",
        status=RunStatus.AWAITING_HUMAN,
        phase=phase,
        write_scope={
            "architect": ["plan.md"],
            "driver": ["src/"],
            "reviewer": [],
        },
        retry_bound=2,
        budget=Budget(limit=100, used=10),
        reviewer_rejection_history=[],
        interrupt_log=[],
        manual_checkpoints=[],
    )


def test_cli_help_uses_console_safe_separator() -> None:
    """Public help must remain legible on legacy Windows code pages."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Battalion SDLC Orchestrator - run, resume" in result.output
    assert "--trace-output" in runner.invoke(app, ["run", "--help"]).output
    assert "--trace-output" in runner.invoke(app, ["resume", "--help"]).output


def test_run_creates_state_file(tmp_path, monkeypatch):
    """Test that `battalion run` creates a state file."""
    # Mock the graph execution to avoid actual LLM calls
    import battalion.application as application_module
    
    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        return initial_state.model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        )
    
    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)
    
    # Create a spec file
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec\n\nImplement a hello world function.")
    
    # Run the CLI
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["run", "BTN-9-test", "--spec", str(spec_file)])
    
    assert result.exit_code == 0
    assert "Run complete" in result.output
    assert " -> done" in result.output
    
    # Check state file was created
    state_files = list((tmp_path / ".battalion" / "state").glob("*.json"))
    assert len(state_files) == 1
    
    # Verify state content
    loaded = RunState.model_validate_json(state_files[0].read_text())
    assert UUID(loaded.run_id).version == 4
    assert loaded.run_alias.startswith("BTN-9-test-")
    assert loaded.project_id is not None
    assert loaded.status == RunStatus.DONE


def test_run_appends_node_associated_trace_output(tmp_path, monkeypatch):
    import battalion.application as application_module

    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        kwargs["on_node_event"]({"type": "node_start", "node": "architect"})
        kwargs["on_token"]({"type": "reasoning", "content": "plan carefully"})
        kwargs["on_token"]({"type": "token", "content": "# Plan"})
        kwargs["on_node_event"]({"type": "node_end", "node": "architect", "phase": "done"})
        return initial_state.model_copy(update={"status": RunStatus.DONE, "phase": "done"})

    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec", encoding="utf-8")
    trace_path = tmp_path / "traces" / "run.jsonl"

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["run", "BTN-9-test", "--spec", str(spec_file), "--trace-output", str(trace_path)],
        )

    assert result.exit_code == 0
    assert f"Trace output: {trace_path.resolve()}" in result.output
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [(event["node"], event["kind"], event["content"]) for event in events] == [
        ("architect", "reasoning", "plan carefully"),
        ("architect", "token", "# Plan"),
    ]


def test_repeated_ticket_runs_get_distinct_canonical_ids(tmp_path, monkeypatch):
    """Aliases may repeat in meaning without overwriting prior ticket runs."""
    import battalion.application as application_module
    
    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        return initial_state.model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        )
    
    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)
    
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec")
    
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        # First run
        runner.invoke(app, ["run", "BTN-9-test", "--spec", str(spec_file)])
        # A second run is a different canonical run, even for the same ticket.
        result = runner.invoke(app, ["run", "BTN-9-test", "--spec", str(spec_file)])
        assert result.exit_code == 0
        state_files = list((tmp_path / ".battalion" / "state").glob("*.json"))
        assert len(state_files) == 2
        assert state_files[0].stem != state_files[1].stem


def test_resume_loads_and_continues(tmp_path, monkeypatch):
    """Test that `battalion resume` loads state and continues."""
    import battalion.application as application_module
    from battalion.state.persistence import load_state
    
    def mock_resume_ticket(state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        return state.model_copy(update={
            "status": RunStatus.DONE,
            "phase": "done",
            "budget": Budget(limit=100, used=15),
        })
    
    monkeypatch.setattr(application_module, "resume_ticket", mock_resume_ticket)
    
    # Create a paused state file
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    state = make_paused_state("run-BTN-9-test")
    save_state(state, state_dir / "run-BTN-9-test.json")
    
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["resume", "run-BTN-9-test"])
    
    assert result.exit_code == 0
    assert "Resumed" in result.output
    assert " -> done" in result.output
    assert "done" in result.output
    
    # Verify state was updated
    loaded = load_state(state_dir / "run-BTN-9-test.json")
    assert loaded.status == RunStatus.DONE
    assert loaded.budget.used == 15


def test_resume_appends_node_associated_trace_output(tmp_path, monkeypatch):
    import battalion.application as application_module

    def mock_resume_ticket(state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        kwargs["on_node_event"]({"type": "node_start", "node": "driver_green"})
        kwargs["on_token"]({"type": "reasoning", "content": "implement now"})
        kwargs["on_node_event"]({"type": "node_end", "node": "driver_green", "phase": "done"})
        return state.model_copy(update={"status": RunStatus.DONE, "phase": "done"})

    monkeypatch.setattr(application_module, "resume_ticket", mock_resume_ticket)
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    save_state(make_paused_state("run-BTN-9-test"), state_dir / "run-BTN-9-test.json")
    trace_path = tmp_path / "traces" / "resume.jsonl"

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["resume", "run-BTN-9-test", "--trace-output", str(trace_path)],
        )

    assert result.exit_code == 0
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [(event["node"], event["kind"], event["content"]) for event in events] == [
        ("driver_green", "reasoning", "implement now"),
    ]


def test_resume_missing_state_file(tmp_path, monkeypatch):
    """Test that resume fails with missing state file."""
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["resume", "run-nonexistent"])
    
    assert result.exit_code != 0
    assert "No state file found" in result.output


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


def test_status_missing_state_file(tmp_path, monkeypatch):
    """Test that status fails with missing state file."""
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["status", "run-nonexistent"])
    
    assert result.exit_code != 0
    assert "No state file found" in result.output


def test_run_reports_why_run_paused(tmp_path, monkeypatch):
    """When a run pauses on an interrupt, the CLI must tell the human WHY —
    e.g. the provider error from an infra failure — not just say
    'awaiting-human'."""
    import battalion.application as application_module
    from battalion.state.models import InterruptLogEntry
    from datetime import datetime, timezone
    from battalion.interrupts.triggers import TRIGGER_INFRA_FAILURE

    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        return initial_state.model_copy(
            update={
                "status": RunStatus.AWAITING_HUMAN,
                "phase": "awaiting_human",
                "budget": Budget(limit=100, used=1),
                "interrupt_log": [
                InterruptLogEntry(
                    trigger=TRIGGER_INFRA_FAILURE,
                    timestamp=datetime.now(timezone.utc),
                    resolution=None,
                    context={"error": "LLM call for node 'architect' failed after 3 attempts: AuthenticationError: Invalid API Key"},
                )
                ],
            }
        )

    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)

    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec")

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(app, ["run", "BTN-9-test", "--spec", str(spec_file)])

    assert result.exit_code == 0
    assert "Run paused - awaiting human review." in result.output
    assert "Run paused" in result.output
    assert "Invalid API Key" in result.output
    assert "Resume when ready: battalion resume " in result.output


def test_run_with_config_file(tmp_path, monkeypatch):
    """Test that run command loads config from YAML file."""
    import battalion.application as application_module
    
    captured_config = {}
    
    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        captured_config["llm_configs"] = llm_configs
        captured_config["base_dir"] = base_dir
        captured_config["prompts_dir"] = prompts_dir
        return RunState(
            schema_version="1.0",
            run_id="run-BTN-9-test",
            ticket_id="BTN-9-test",
            status=RunStatus.DONE,
            phase="done",
            write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
            retry_bound=2,
            budget=Budget(limit=100, used=5),
            reviewer_rejection_history=[],
            interrupt_log=[],
            manual_checkpoints=[],
        )
    
    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)
    
    # Create config file
    config_file = tmp_path / "battalion.config.yaml"
    config_file.write_text("""
models:
  architect:
    model: "gpt-4o"
  driver:
    model: "claude-3-5-sonnet"
budget_limit: 200
manual_checkpoints: ["reviewer"]
""")
    
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec")
    
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        runner.invoke(app, ["run", "BTN-9-test", "--spec", str(spec_file), "--config", str(config_file)])
    
    # Verify config was loaded
    assert "architect" in captured_config["llm_configs"]
    assert captured_config["llm_configs"]["architect"].model == "gpt-4o"
    assert captured_config["llm_configs"]["driver"].model == "claude-3-5-sonnet"
    assert captured_config["base_dir"] == "."
    assert captured_config["prompts_dir"] is None


def test_run_passes_configured_initial_state_to_graph_unchanged(tmp_path, monkeypatch):
    """BTN-27: the CLI-created RunState is the runtime's source of truth."""
    import battalion.application as application_module

    captured = {}

    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        captured["state"] = initial_state
        return initial_state.model_copy(update={"status": RunStatus.DONE, "phase": "done"})

    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)

    config_file = tmp_path / "battalion.config.yaml"
    config_file.write_text(
        """
budget_limit: 13
manual_checkpoints: ["driver_green"]
write_scope:
  architect: ["custom-plan.md"]
  driver: ["pkg/", "checks/"]
  reviewer: []
""",
        encoding="utf-8",
    )
    spec_file = tmp_path / "ticket-spec.md"
    spec_file.write_text("Caller supplied specification", encoding="utf-8")

    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["run", "BTN-27-test", "--spec", str(spec_file), "--config", str(config_file)],
        )

    assert result.exit_code == 0
    state = captured["state"]
    assert UUID(state.run_id).version == 4
    assert state.run_alias.startswith("BTN-27-test-")
    assert state.project_id is not None
    assert state.ticket_id == "BTN-27-test"
    assert state.spec == "Caller supplied specification"
    assert state.budget == Budget(limit=13, used=0)
    assert state.manual_checkpoints == ["driver_green"]
    assert state.write_scope == {
        "architect": ["custom-plan.md"],
        "driver": ["pkg/", "checks/"],
        "reviewer": [],
    }
    assert state.retry_bound == 2


def test_run_with_model_overrides(tmp_path, monkeypatch):
    """Test that CLI model flags override config file."""
    import battalion.application as application_module
    
    captured_config = {}
    
    def mock_run_ticket(initial_state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        captured_config["llm_configs"] = llm_configs
        return RunState(
            schema_version="1.0",
            run_id="run-BTN-9-test",
            ticket_id="BTN-9-test",
            status=RunStatus.DONE,
            phase="done",
            write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": []},
            retry_bound=2,
            budget=Budget(limit=100, used=5),
            reviewer_rejection_history=[],
            interrupt_log=[],
            manual_checkpoints=[],
        )
    
    monkeypatch.setattr(application_module, "run_ticket", mock_run_ticket)
    
    config_file = tmp_path / "battalion.config.yaml"
    config_file.write_text("""
models:
  architect:
    model: "gpt-4o"
""")
    
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Test Spec")
    
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        runner.invoke(app, [
            "run", "BTN-9-test",
            "--spec", str(spec_file),
            "--config", str(config_file),
            "--model-architect", "gpt-4o-mini",
            "--model-driver", "claude-3-haiku",
        ])
    
    # CLI overrides should win
    assert captured_config["llm_configs"]["architect"].model == "gpt-4o-mini"
    assert captured_config["llm_configs"]["driver"].model == "claude-3-haiku"


def test_resume_infers_target_from_interrupt(tmp_path, monkeypatch):
    """Test that resume infers target from interrupt context."""
    import battalion.application as application_module
    from battalion.graph import _infer_resume_target
    
    captured_state = {}
    
    def mock_resume_ticket(state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        # Capture the resume target that would be inferred
        captured_state["resume_target"] = _infer_resume_target(state)
        captured_state["phase"] = state.phase
        return state.model_copy(update={"status": RunStatus.DONE, "phase": "done"})
    
    monkeypatch.setattr(application_module, "resume_ticket", mock_resume_ticket)
    
    # Create state with interrupt context pointing to driver_green
    from battalion.state.models import InterruptLogEntry
    from datetime import datetime, timezone
    
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    state = make_paused_state("run-BTN-9-test")
    state.interrupt_log = [
        InterruptLogEntry(
            trigger="manual-checkpoint",
            timestamp=datetime.now(timezone.utc),
            resolution=None,
            context={"next_phase": "driver_green"},
        )
    ]
    save_state(state, state_dir / "run-BTN-9-test.json")
    
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        runner.invoke(app, ["resume", "run-BTN-9-test"])
    
    assert captured_state["resume_target"] == "driver_green"
    # Phase is the node that was running when interrupted (driver_red), not the status
    assert captured_state["phase"] == "driver_red"


def test_resume_infers_target_from_rejection(tmp_path, monkeypatch):
    """Test that resume infers target from last rejection checkpoint."""
    import battalion.application as application_module
    from battalion.graph import _infer_resume_target
    
    captured_state = {}
    
    def mock_resume_ticket(state, llm_configs, base_dir, prompts_dir, max_turns=50, **kwargs):
        # Capture the resume target that would be inferred
        captured_state["resume_target"] = _infer_resume_target(state)
        captured_state["phase"] = state.phase
        return state.model_copy(update={"status": RunStatus.DONE, "phase": "done"})
    
    monkeypatch.setattr(application_module, "resume_ticket", mock_resume_ticket)
    
    from battalion.state.models import RejectionRecord, CheckpointType
    
    state_dir = tmp_path / ".battalion" / "state"
    state_dir.mkdir(parents=True)
    state = make_paused_state("run-BTN-9-test")
    state.reviewer_rejection_history = [
        RejectionRecord(
            cause="Tests still failing",
            cycle_number=1,
            checkpoint=CheckpointType.GREEN_CHECK,
        )
    ]
    save_state(state, state_dir / "run-BTN-9-test.json")
    
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        runner.invoke(app, ["resume", "run-BTN-9-test"])
    
    # Should resume at driver_green for GREEN_CHECK rejection
    assert captured_state["resume_target"] == "driver_green"
    # Phase is the node that was running when interrupted (driver_red), not the status
    assert captured_state["phase"] == "driver_red"

"""CLI opt-in trace disclosure and node-associated output."""


import json
from typer.testing import CliRunner
from battalion.cli import app
from battalion.state.models import RunStatus
from battalion.state.persistence import save_state
from support.cli import (
    make_paused_state,
)


runner = CliRunner()


def test_trace_disclosure_precedes_file_creation_and_is_opt_in(tmp_path, monkeypatch):
    from battalion.cli import _open_trace_output
    from battalion.disclosure import DATA_HANDLING_URL

    trace_path = tmp_path / "private" / "trace.jsonl"
    notices = []

    def capture_notice(message, **kwargs):
        assert not trace_path.parent.exists()
        assert DATA_HANDLING_URL in message
        assert kwargs["err"] is True
        notices.append(message)

    monkeypatch.setattr("battalion.cli.typer.echo", capture_notice)
    with _open_trace_output(None) as (stream, path):
        assert stream is None and path is None
    assert notices == []
    with _open_trace_output(str(trace_path)) as (stream, path):
        assert len(notices) == 1
        assert path == trace_path.resolve()
        assert stream is not None
        assert trace_path.exists()


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
    assert result.output.index("Data handling:") < result.output.index("Trace output:")
    assert "without redaction" in result.output
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [(event["node"], event["kind"], event["content"]) for event in events] == [
        ("architect", "reasoning", "plan carefully"),
        ("architect", "token", "# Plan"),
    ]


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
    assert result.output.index("Data handling:") < result.output.index("Trace output:")
    assert "without redaction" in result.output
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [(event["node"], event["kind"], event["content"]) for event in events] == [
        ("driver_green", "reasoning", "implement now"),
    ]

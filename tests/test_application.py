"""Focused tests for the BTN-30 application command/query boundary."""

from uuid import UUID

import pytest

from battalion.application import (
    InspectRun,
    InvalidRunId,
    ResumeRun,
    RunAlreadyExists,
    RunIdentityChanged,
    RunNotFound,
    StartRun,
    StateReadFailed,
    create_initial_state,
    inspect_run,
    resume_run,
    start_run,
    state_path,
)
from battalion.actors import load_actor_registry
from battalion.config import BattalionConfig
from battalion.state.models import Budget, RunState, RunStatus


def make_state(
    run_id: str = "run-BTN-30-test",
    status: RunStatus = RunStatus.NOT_STARTED,
) -> RunState:
    return RunState(
        schema_version="1.0",
        run_id=run_id,
        ticket_id="BTN-30-test",
        spec="Application boundary test",
        status=status,
        phase="architect",
        write_scope={"architect": ["plan.md"], "driver": ["src/"]},
        retry_bound=2,
        budget=Budget(limit=10),
    )


def test_application_creates_canonical_new_run_identity(tmp_path):
    state = create_initial_state(
        "BTN-32", "Identity contract", BattalionConfig(base_dir=str(tmp_path))
    )

    assert UUID(state.run_id).version == 4
    assert state.run_alias.startswith("BTN-32-")
    assert UUID(state.project_id)
    assert (tmp_path / ".battalion" / "project.json").exists()


def test_start_run_returns_typed_identity_and_persists_graph_result(tmp_path):
    initial = make_state()
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return kwargs["initial_state"].model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        )

    result = start_run(
        StartRun(initial_state=initial, config=BattalionConfig()),
        state_dir=tmp_path,
        _execute=execute,
    )

    assert result.run_id == initial.run_id
    assert result.state_version == "1.0"
    assert result.state.status == RunStatus.DONE
    assert result.state_path == tmp_path / "run-BTN-30-test.json"
    assert result.state_path.exists()
    assert captured["initial_state"] is initial
    assert captured["llm_configs"] == BattalionConfig().models


def test_graph_execution_cannot_replace_canonical_run_identity(tmp_path):
    initial = make_state()

    with pytest.raises(RunIdentityChanged):
        start_run(
            StartRun(initial_state=initial, config=BattalionConfig()),
            state_dir=tmp_path,
            _execute=lambda **kwargs: kwargs["initial_state"].model_copy(
                update={"run_id": "different-run"}
            ),
        )


def test_start_run_requires_explicit_overwrite_authorization(tmp_path):
    initial = make_state()
    path = tmp_path / f"{initial.run_id}.json"
    path.write_text(initial.model_dump_json(), encoding="utf-8")

    with pytest.raises(RunAlreadyExists) as raised:
        start_run(
            StartRun(initial_state=initial, config=BattalionConfig()),
            state_dir=tmp_path,
            _execute=lambda **kwargs: kwargs["initial_state"],
        )

    assert raised.value.run_id == initial.run_id
    assert raised.value.path == path


def test_new_project_state_establishes_offline_local_actor(tmp_path):
    state = create_initial_state(
        "BTN-59",
        "Durable Actor identity",
        BattalionConfig(base_dir=str(tmp_path)),
    )

    registry = load_actor_registry(tmp_path)
    assert state.project_id == str(registry.project_id)
    assert registry.local_actor_id == registry.actors[0].actor_id
    assert registry.actors[0].display_name == "Local Operator"


def test_resume_run_loads_canonical_state_and_persists_result(tmp_path):
    paused = make_state(status=RunStatus.AWAITING_HUMAN)
    path = tmp_path / f"{paused.run_id}.json"
    path.write_text(paused.model_dump_json(), encoding="utf-8")
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return kwargs["state"].model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        )

    result = resume_run(
        ResumeRun(
            run_id=paused.run_id,
            config=BattalionConfig(base_dir=str(tmp_path)),
        ),
        state_dir=tmp_path,
        _execute=execute,
    )

    assert captured["state"].interrupt_log == paused.interrupt_log
    assert captured["state"].human_action_log[-1].kind == "interrupt-resolution"
    assert captured["state"].human_action_log[-1].target == "legacy-pause"
    assert result.warning is None
    assert result.state.status == RunStatus.DONE
    assert inspect_run(InspectRun(paused.run_id), state_dir=tmp_path).state == result.state


def test_resume_run_reports_non_paused_status_without_changing_policy(tmp_path):
    state = make_state(status=RunStatus.DONE)
    (tmp_path / f"{state.run_id}.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )

    result = resume_run(
        ResumeRun(
            run_id=state.run_id,
            config=BattalionConfig(base_dir=str(tmp_path)),
        ),
        state_dir=tmp_path,
        _execute=lambda **kwargs: kwargs["state"],
    )

    assert result.warning == "Run status is 'done', not 'awaiting-human'. Resuming anyway."


def test_inspect_run_returns_state_version_and_derived_costs(tmp_path):
    state = make_state(status=RunStatus.DONE)
    (tmp_path / f"{state.run_id}.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )

    result = inspect_run(InspectRun(state.run_id), state_dir=tmp_path)

    assert result.run_id == state.run_id
    assert result.state_version == state.schema_version
    assert result.costs == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "costs": [],
        "unknown_cost_calls": 0,
        "phases": [],
    }


def test_queries_expose_documented_domain_failures(tmp_path):
    with pytest.raises(RunNotFound):
        inspect_run(InspectRun("run-missing"), state_dir=tmp_path)

    malformed = tmp_path / "run-malformed.json"
    malformed.write_text("not json", encoding="utf-8")
    with pytest.raises(StateReadFailed) as raised:
        inspect_run(InspectRun("run-malformed"), state_dir=tmp_path)
    assert raised.value.path == malformed

    with pytest.raises(InvalidRunId):
        state_path("../outside", tmp_path)


def test_node_checkpoint_preserves_progress_if_worker_crashes(tmp_path):
    initial = make_state()
    progressed = initial.model_copy(
        update={"status": RunStatus.IN_PROGRESS, "phase": "driver_red"}
    )

    def crash_after_checkpoint(**kwargs):
        kwargs["on_state_checkpoint"](progressed)
        raise RuntimeError("simulated worker crash")

    with pytest.raises(RuntimeError, match="simulated worker crash"):
        start_run(
            StartRun(initial_state=initial, config=BattalionConfig()),
            state_dir=tmp_path,
            _execute=crash_after_checkpoint,
        )

    assert inspect_run(InspectRun(initial.run_id), state_dir=tmp_path).state == progressed
    create_initial_state,

"""BTN-173 regressions across role, graph, and persisted-resume boundaries."""
from dataclasses import replace
from datetime import datetime, timezone
import re

import pytest

from battalion import application, graph
from battalion.application import HumanActionRejected, ResumeRun, StartRun, resume_run, start_run
from battalion.config import BattalionConfig
from battalion.nodes.driver import InvalidModeOutput, run_driver
from battalion.nodes.reviewer import run_reviewer
from battalion.state.models import (
    InterruptLogEntry, ProgressStage, RunStatus, TestExecutionClassification as Classification,
)
from battalion.state.persistence import load_state, save_state
from support.state import make_llm_configs, make_run_state
from support.graph import patched_nodes, driver_advancing
from support.responses import json_response as response


def config_for(project):
    models = make_llm_configs()
    models["reviewer"] = replace(models["reviewer"], model="independent-review-model")
    return BattalionConfig(base_dir=str(project), models=models)


@pytest.mark.parametrize("mode", ["red", "green"])
def test_recursion_limit_preserves_typed_block_and_requires_new_authorization(tmp_path, mode):
    phase = f"driver_{mode}"
    state = make_run_state(
        status=RunStatus.AWAITING_HUMAN, phase="awaiting_human",
        interrupt_log=[InterruptLogEntry(
            trigger="manual-checkpoint", timestamp=datetime.now(timezone.utc),
            context={"next_phase": phase},
        )],
    )
    path = tmp_path / f"{state.run_id}.json"
    save_state(state, path)
    command = ResumeRun(state.run_id, config_for(tmp_path), resolution="Approve earlier checkpoint")

    def blocked(**kwargs):
        return run_driver(**kwargs, call_llm_fn=lambda *a, **kw: response({
            "files": {}, "result": {
                "kind": "blocked", "reason_code": "missing-context",
                "summary": "The requested interface is not supplied.",
            },
        }))

    with patched_nodes(driver=blocked):
        result = resume_run(command, state_dir=tmp_path,
                            _execute=lambda **kw: graph.resume_ticket(max_turns=1, **kw))
    assert result.state.status is RunStatus.BLOCKED
    assert result.state.phase == phase
    assert load_state(path) == result.state
    prior_action = result.state.human_action_log[0]
    attempt = result.state.execution_record.node_executions[-1]
    assert attempt.role_result.kind.value == "blocked"
    before = path.read_bytes()
    with pytest.raises(HumanActionRejected, match="must not be empty"):
        resume_run(replace(command, resolution=""), state_dir=tmp_path)
    assert path.read_bytes() == before

    calls = []
    with patched_nodes(driver=driver_advancing(calls)):
        completed = resume_run(replace(command, resolution="Interface supplied"), state_dir=tmp_path)
    assert completed.state.status is RunStatus.DONE
    assert calls[0] == phase
    assert len(completed.state.human_action_log) == 2
    assert completed.state.human_action_log[0] == prior_action
    authorization = completed.state.human_action_log[1]
    assert authorization.target == f"role-result:{attempt.execution_id}"
    assert authorization.detail == "Interface supplied"
    assert completed.state.execution_record.node_executions[0] == attempt


@pytest.mark.parametrize("crash_boundary", ["none", "rejection", "authorization"])
@pytest.mark.parametrize("retry_invalid", [False, True])
def test_correction_waits_for_budget_authorization_and_keeps_retry_context(
    tmp_path, monkeypatch, crash_boundary, retry_invalid,
):
    state = make_run_state(budget_limit=2)
    config = config_for(tmp_path)
    path = tmp_path / f"{state.run_id}.json"
    attempts = []
    events = []

    def invalid(**kwargs):
        attempts.append(kwargs["ticket_text"])
        raise InvalidModeOutput("RED must only produce tests", offending_paths=("widget.py",))

    class ProcessCrash(BaseException):
        pass

    def crash_after_rejection(saved, destination):
        save_state(saved, destination)
        if (saved.graph_progress is not None
                and saved.graph_progress.stage is ProgressStage.ATTEMPT_COMPLETED
                and saved.graph_progress.correction_context is not None):
            raise ProcessCrash()

    with patched_nodes(driver=invalid):
        if crash_boundary == "rejection":
            monkeypatch.setattr(application, "save_state", crash_after_rejection)
            with pytest.raises(ProcessCrash):
                start_run(StartRun(state, config), state_dir=tmp_path)
            monkeypatch.setattr(application, "save_state", save_state)
            # Continuing a saved rejection is not budget authorization.
            result = resume_run(ResumeRun(state.run_id, config), state_dir=tmp_path,
                                on_node_event=events.append)
        else:
            result = start_run(StartRun(state, config), state_dir=tmp_path,
                               on_node_event=events.append)
    assert len(attempts) == 1
    assert result.state.budget.used == 2
    assert result.state.status is RunStatus.AWAITING_HUMAN
    assert result.state.interrupt_log[-1].trigger == "budget-exceeded"
    assert result.state.interrupt_log[-1].context["next_phase"] == "driver_red"
    assert any(e["type"] == "interrupt" and e["trigger"] == "budget-exceeded" for e in events)
    saved = load_state(path)
    assert len(saved.execution_record.node_executions) == 2
    assert saved.graph_progress.correction_attempt == 1
    assert "widget.py" in saved.graph_progress.correction_context
    assert saved.human_action_log == []

    def corrected(**kwargs):
        attempts.append(kwargs["ticket_text"])
        return kwargs["state"].model_copy(update={"phase": "reviewer"})

    command = ResumeRun(state.run_id, config, resolution="Allow one correction")
    if crash_boundary == "authorization":
        def crash_after_authorization(saved, destination):
            save_state(saved, destination)
            if saved.resume_intent and not saved.resume_intent.completed:
                raise ProcessCrash()

        monkeypatch.setattr(application, "save_state", crash_after_authorization)
        with pytest.raises(ProcessCrash):
            resume_run(command, state_dir=tmp_path)
        assert len(attempts) == 1
        assert load_state(path).graph_progress == saved.graph_progress
        monkeypatch.setattr(application, "save_state", save_state)
    with patched_nodes(driver=invalid if retry_invalid else corrected):
        resumed = resume_run(command, state_dir=tmp_path)
    assert len(attempts) == 2
    assert "Battalion automatic correction" in attempts[-1]
    assert "widget.py" in attempts[-1]
    assert resumed.state.budget.used == 3
    assert resumed.state.status is RunStatus.AWAITING_HUMAN
    assert resumed.state.human_action_log[-1].detail == "Allow one correction"
    assert len(resumed.state.human_action_log) == 1
    assert resumed.state.execution_record.node_executions[:2] == saved.execution_record.node_executions
    if retry_invalid:
        assert resumed.state.interrupt_log[-1].trigger == "infra-failure"
        violation = resumed.state.execution_record.node_executions[-1].role_contract_violation
        assert violation.attempt_number == 2
        assert violation.resulting_disposition == "escalation"
    else:
        assert resumed.state.interrupt_log[-1].trigger == "budget-exceeded"
        assert resumed.state.interrupt_log[-1].context["next_phase"] == "reviewer_red"
    assert load_state(path) == resumed.state


@pytest.mark.parametrize("missing", ["module", "symbol", "collection-error"])
def test_red_prompt_example_through_real_driver_and_reviewer(tmp_path, missing):
    # Exercise the published example as a deterministic provider fixture, not
    # a claim that a live model will always follow its prompt.
    if missing == "symbol":
        (tmp_path / "widget.py").write_text("# increment is not implemented yet\n", encoding="utf-8")
    state = make_run_state(write_scope={
        "architect": ["plan.md"], "driver_red": ["tests/"],
        "driver_green": ["src/"], "reviewer": [],
    })

    def model(_role, _config, messages):
        prompt = messages[0]["content"]
        example = re.search(r"```python\n(.*?)```", prompt, flags=re.DOTALL)
        assert example is not None, "RED prompt must give a collection-safe missing-API example"
        content = example.group(1)
        if missing == "collection-error":
            content = "from widget import increment\n\ndef test_increment():\n    assert increment(1) == 2\n"
        return response({"files": {"test_widget.py": content}})

    def driver(**kwargs):
        return run_driver(**kwargs, call_llm_fn=model)

    def reviewer(**kwargs):
        return run_reviewer(**kwargs, call_llm_fn=lambda *a, **kw: pytest.fail("unexpected model review"))

    with patched_nodes(driver=driver, reviewer=reviewer):
        result = start_run(StartRun(state, config_for(tmp_path)), state_dir=tmp_path / "runs",
                           _execute=lambda **kw: graph.run_ticket(max_turns=3, **kw))
    review = result.state.execution_record.node_executions[-1]
    assert review.phase == "reviewer_red"
    if missing == "collection-error":
        assert review.test_execution.classification is Classification.PYTEST_ERROR
        assert result.state.status is RunStatus.AWAITING_HUMAN
        assert result.state.interrupt_log[-1].trigger == "infra-failure"
    else:
        assert review.test_execution.classification is Classification.TEST_FAILED
        assert review.test_execution.tests_collected == 1
        assert result.state.graph_progress.next_node == "driver_green"
        assert result.state.interrupt_log == []

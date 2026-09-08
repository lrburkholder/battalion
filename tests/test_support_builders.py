"""Builder contracts that protect test isolation and explicit failure setup."""
import pytest
from pydantic import ValidationError

from battalion.llm.litellm_client import NodeLLMConfig
from battalion.state.models import ExecutionRecord, RunStatus
from battalion.state.models import TestExecutionClassification as Classification
from battalion.state.persistence import load_state
from support.execution import make_interrupt, make_node_execution, make_test_result
from support.graph import patched_nodes
from support.state import make_llm_configs, make_run_state, persisted_checkpoint


def test_run_builder_isolates_nested_inputs_and_preserves_overrides():
    scope = {"driver": ["src/"]}
    execution = make_node_execution(role="driver", phase="driver_green")
    interrupt = make_interrupt("manual-checkpoint", context={"next_phase": "driver_green"})
    fields = dict(
        run_id="explicit-run", ticket_id="BTN-explicit", schema_version="1.1",
        status=RunStatus.AWAITING_HUMAN, phase="awaiting_human", write_scope=scope,
        budget={"limit": 9, "used": 4}, interrupt_log=[interrupt],
        execution_record=ExecutionRecord(node_executions=[execution]),
    )
    first = make_run_state(**fields)
    second = make_run_state(**fields)
    first.write_scope["driver"].append("other/")
    first.execution_record.node_executions[0].interrupt_ids.append(7)
    first.interrupt_log[0].context["next_phase"] = "done"
    first.budget.used = 8

    assert second.run_id == "explicit-run"
    assert second.schema_version == "1.1"
    assert second.status is RunStatus.AWAITING_HUMAN
    assert second.write_scope == scope == {"driver": ["src/"]}
    assert second.execution_record.node_executions[0].interrupt_ids == execution.interrupt_ids == []
    assert second.interrupt_log[0].context == interrupt.context == {"next_phase": "driver_green"}
    assert second.budget.used == fields["budget"]["used"] == 4


def test_run_builder_does_not_replace_explicit_empty_values_or_hide_invalid_state():
    assert make_run_state(write_scope={}).write_scope == {}
    default = make_run_state()
    default.write_scope["driver"].append("other/")
    assert make_run_state().write_scope["driver"] == ["src/"]
    with pytest.raises(ValidationError):
        make_run_state(status="invalid-status")


@pytest.mark.parametrize("builder, required, misspelled", [
    pytest.param(make_run_state, {}, "budegt", id="state"),
    pytest.param(make_node_execution, {"role": "driver", "phase": "driver_red"},
                 "outocme", id="node-execution"),
    pytest.param(make_interrupt, {"trigger": "manual-checkpoint"}, "contex", id="interrupt"),
    pytest.param(make_test_result, {"classification": Classification.PASSED,
                                   "output": "passed", "returncode": 0},
                 "test_collected", id="process-result"),
])
def test_builders_reject_misspelled_overrides(builder, required, misspelled):
    with pytest.raises(TypeError, match=misspelled):
        builder(**required, **{misspelled: None})


def test_config_builder_keeps_review_independent_and_rejects_unknown_roles():
    first = make_llm_configs(driver=NodeLLMConfig(model="chosen-driver"))
    second = make_llm_configs()
    assert first["driver"].model == "chosen-driver"
    assert second["driver"].model != second["reviewer"].model
    assert first["reviewer"] is not second["reviewer"]
    with pytest.raises(ValueError, match="Unknown model roles"):
        make_llm_configs(drivr=NodeLLMConfig(model="typo"))


def test_execution_builder_preserves_unfinished_attempts_and_validates_completion():
    attempt = make_node_execution(
        role="driver", phase="driver_red", outcome="in-progress", ended_at=None,
    )
    assert attempt.ended_at is None
    assert attempt.outcome == "in-progress"
    with pytest.raises(ValidationError, match="completion timestamp"):
        make_node_execution(role="driver", phase="driver_red", ended_at=None)


def test_process_result_overrides_reach_the_real_evidence_validator():
    result = make_test_result(Classification.PASSED, "é", 0)
    assert result.to_evidence().stdout_observed_bytes == 2
    malformed = make_test_result(Classification.PASSED, "passed", 0, tests_collected=0)
    with pytest.raises(ValidationError):
        malformed.to_evidence()


def test_checkpoint_builder_persists_independent_real_state(tmp_path):
    state = make_run_state(interrupt_log=[make_interrupt("manual-checkpoint")])
    path = persisted_checkpoint(tmp_path / "nested" / "state.json", state)
    assert load_state(path) == state
    state.interrupt_log.clear()
    assert len(load_state(path).interrupt_log) == 1


def test_graph_helper_rejects_misspelled_collaborators_before_invocation():
    with pytest.raises(ValueError, match="Unknown role runners"):
        with patched_nodes(drivr=lambda **kwargs: None):
            pytest.fail("invalid collaborator must be rejected before entering the scenario")

"""BTN-22 acceptance tests for post-completion Recon generation."""

from support.state import make_run_state
from support.responses import json_response

from datetime import datetime, timezone

import pytest

from battalion.intel.models import AcceptedInstinct
from battalion.llm.litellm_client import NodeLLMConfig
from battalion.nodes.recon import InvalidReconEvidence, ReconRequiresCompletedRun, run_recon
from battalion.state.models import ExecutionRecord, NodeExecution, RunState, RunStatus


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def test_recon_is_exported_without_hiding_existing_nodes():
    import battalion.nodes as nodes

    assert set(nodes.__all__) == {
        "run_architect", "run_driver", "run_recon", "run_refactorer", "run_reviewer"
    }


def _state(*, status=RunStatus.DONE, phase="done", executions=True):
    record = ExecutionRecord(node_executions=[NodeExecution(
        execution_id="exec-review-1",
        role="reviewer",
        phase="reviewer_green",
        model_identity="review-model",
        started_at=NOW,
        ended_at=NOW,
        outcome="succeeded",
        verdict="accepted",
    )] if executions else [])
    return make_run_state(
        run_id='run-22',
        ticket_id='BTN-22',
        status=status,
        phase=phase,
        execution_record=record,
        write_scope={},
        budget_limit=10,
    )


def _candidate(*, recommendation="Keep reviewer evidence durable.", run_id="run-22",
               node_id="exec-review-1", reference="execution_record.node_executions[0]"):
    return {
        "schema_version": "1.0", "instinct_id": "INS-DURABLE-REVIEW",
        "lifecycle": "candidate", "recommendation": recommendation,
        "evidence": [{"run_id": run_id, "node_execution_id": node_id,
                      "reference": reference, "description": "The reviewer accepted the evidence."}],
        "audience": ["reviewer"],
        "applicability": {"description": "Runs with review checkpoints", "include": [], "exclude": []},
        "tags": ["evidence"],
        "creation_provenance": {"originating_run_id": run_id,
            "originating_node_execution_ids": [node_id], "created_at": NOW.isoformat(),
            "created_by": "recon"},
    }


def _response(candidates):
    return json_response({"candidates": candidates})


@pytest.mark.parametrize("state", [
    _state(status=RunStatus.IN_PROGRESS, phase="reviewer"),
    _state(executions=False),
])
def test_recon_requires_a_completed_execution_record(state):
    called = False
    def fake_call(*args, **kwargs):
        nonlocal called
        called = True
    with pytest.raises(ReconRequiresCompletedRun):
        run_recon(state, [], NodeLLMConfig(model="recon-model"), call_llm_fn=fake_call,
                  system_prompt="prompt")
    assert not called


def test_recon_receives_only_durable_record_and_returns_valid_candidates():
    captured = {}
    def fake_call(role, config, messages):
        captured.update(role=role, messages=messages)
        return _response([_candidate()])

    result = run_recon(_state(), [], NodeLLMConfig(model="recon-model"),
                       call_llm_fn=fake_call, system_prompt="prompt")

    assert result == [result[0].model_validate(_candidate())]
    assert captured["role"] == "recon"
    assert "execution_record" in captured["messages"][1]["content"]
    assert "conversation" not in captured["messages"][1]["content"].lower()


def test_recon_rejects_evidence_not_in_completed_record():
    with pytest.raises(InvalidReconEvidence):
        run_recon(_state(), [], NodeLLMConfig(model="recon-model"),
                  call_llm_fn=lambda *args: _response([_candidate(node_id="invented")]),
                  system_prompt="prompt")


def test_recon_filters_candidate_duplicate_of_supplied_accepted_instinct():
    accepted = AcceptedInstinct.model_validate({
        **_candidate(recommendation="  KEEP reviewer evidence durable. "),
        "lifecycle": "accepted",
        "acceptance_provenance": {"accepted_at": NOW.isoformat(), "accepted_by": "operator"},
    })
    result = run_recon(_state(), [accepted], NodeLLMConfig(model="recon-model"),
                       call_llm_fn=lambda *args: _response([_candidate()]), system_prompt="prompt")
    assert result == []


def test_recon_zero_candidates_is_a_successful_result():
    result = run_recon(_state(), [], NodeLLMConfig(model="recon-model"),
                       call_llm_fn=lambda *args: _response([]), system_prompt="prompt")
    assert result == []

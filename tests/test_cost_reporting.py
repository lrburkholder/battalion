"""BTN-16 per-call cost capture and summary acceptance tests."""
from support.state import make_run_state

from support.execution import make_node_execution
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from battalion.cli import app
from battalion.execution import ExecutionCapture, summarize_costs
from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.state.models import (
    Budget,
    CostSource,
    LLMCallCost,
    NodeExecution,
    RunState,
    RunStatus,
)
from battalion.state.persistence import load_state, save_state


def _state() -> RunState:
    return make_run_state(
        run_id='run-BTN-16',
        ticket_id='BTN-16',
        status=RunStatus.IN_PROGRESS,
        write_scope={'architect': ['plan.md']},
        budget_used=7,
    )


def _execution(phase: str, role: str, *calls: LLMCallCost) -> NodeExecution:
    return make_node_execution(
        execution_id=f"node-{phase}",
        role=role,
        phase=phase,
        model_identity="configured-model",
        llm_calls=list(calls),
    )


def test_successful_llm_call_is_attached_to_active_node_and_persists(tmp_path):
    state = _state()
    capture = ExecutionCapture.start(state, "architect", "configured-model", tmp_path)

    response = call_llm(
        "architect",
        NodeLLMConfig(model="configured-model", max_retries=0),
        [{"role": "user", "content": "plan"}],
        completion_fn=lambda **kwargs: {
            "model": "provider/model-version",
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            "_hidden_params": {"response_cost": 0.0042},
        },
    )
    assert response["choices"][0]["message"]["content"] == "done"

    finished = capture.finish(state, state)
    call = finished.execution_record.node_executions[0].llm_calls[0]
    assert call.model == "provider/model-version"
    assert call.requested_model == "configured-model"
    assert call.response_model == "provider/model-version"
    assert call.backend is None
    assert call.endpoint_url is None
    assert call.inference_location == "unknown"
    assert call.input_tokens == 120
    assert call.output_tokens == 30
    assert call.cost == Decimal("0.0042")
    assert call.cost_currency == "USD"
    assert call.cost_source == CostSource.ESTIMATED

    path = tmp_path / "run.json"
    save_state(finished, path)
    assert load_state(path) == finished


def test_retry_records_only_the_completed_provider_call(tmp_path):
    state = _state()
    capture = ExecutionCapture.start(state, "architect", "model", tmp_path)
    attempts = 0

    def flaky(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return {
            "model": "model",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "_hidden_params": {"response_cost": 0.001},
        }

    call_llm(
        "architect",
        NodeLLMConfig(model="model", max_retries=1),
        [{"role": "user", "content": "plan"}],
        completion_fn=flaky,
        sleep_fn=lambda _: None,
    )
    finished = capture.finish(state, state)
    assert attempts == 2
    assert len(finished.execution_record.node_executions[0].llm_calls) == 1


def test_streaming_requests_and_records_final_usage_frame(tmp_path):
    state = _state()
    capture = ExecutionCapture.start(state, "architect", "model", tmp_path)

    def streaming(**kwargs):
        assert kwargs["stream_options"] == {"include_usage": True}
        return iter([
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(
                    content="ok", reasoning_content="why",
                ))],
                usage=None,
                _hidden_params={},
            ),
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=11, completion_tokens=3, cost=0.0015,
                ),
                model="router/resolved-model",
                _hidden_params={"response_headers": {
                    "X-Routed-Via": "stream-router",
                    "X-Routed-Model": "provider/routed-model",
                }},
            ),
        ])

    call_llm(
        "architect",
        NodeLLMConfig(model="model", max_retries=0),
        [{"role": "user", "content": "plan"}],
        completion_fn=streaming,
        on_stream=lambda _: None,
    )
    finished = capture.finish(state, state)
    call = finished.execution_record.node_executions[0].llm_calls[0]
    assert (call.input_tokens, call.output_tokens, call.cost) == (
        11, 3, Decimal("0.0015")
    )
    assert call.cost_source == CostSource.PROVIDER_REPORTED
    assert call.requested_model == "model"
    assert call.response_model == "router/resolved-model"
    assert call.routed_provider == "stream-router"
    assert call.routed_model == "provider/routed-model"
    assert finished.execution_record.schema_version == "1.8"
    execution = finished.execution_record.node_executions[0]
    assert execution.streamed_reasoning_characters == 3
    assert execution.streamed_content_characters == 2


def test_summary_is_broken_down_by_phase_with_stable_order():
    state = _state()
    record = state.execution_record.model_copy(update={
        "node_executions": [
            _execution(
                "driver_green", "driver",
                LLMCallCost(call_id="call-2", model="driver", input_tokens=20,
                            output_tokens=8, cost_usd=0.003),
            ),
            _execution(
                "architect", "architect",
                LLMCallCost(call_id="call-1", model="architect", input_tokens=10,
                            output_tokens=4, cost_usd=0.002),
            ),
        ]
    })

    summary = summarize_costs(record)
    assert summary == {
        "calls": 2,
        "input_tokens": 30,
        "output_tokens": 12,
        "streamed_reasoning_characters": 0,
        "streamed_content_characters": 0,
        "costs": [{
            "amount": "0.005", "currency": "USD",
            "sources": ["provider-reported"],
        }],
        "unknown_cost_calls": 0,
        "phases": [
            {"phase": "architect", "role": "architect", "models": ["architect", "configured-model"], "calls": 1,
             "input_tokens": 10, "output_tokens": 4,
             "streamed_reasoning_characters": 0, "streamed_content_characters": 0,
             "unknown_cost_calls": 0,
             "costs": [{"amount": "0.002", "currency": "USD",
                        "sources": ["provider-reported"]}]},
            {"phase": "driver_green", "role": "driver", "models": ["configured-model", "driver"], "calls": 1,
             "input_tokens": 20, "output_tokens": 8,
             "streamed_reasoning_characters": 0, "streamed_content_characters": 0,
             "unknown_cost_calls": 0,
             "costs": [{"amount": "0.003", "currency": "USD",
                        "sources": ["provider-reported"]}]},
        ],
    }


def test_unknown_cost_keeps_token_usage_without_inventing_zero(tmp_path):
    state = _state()
    capture = ExecutionCapture.start(state, "architect", "model", tmp_path)

    call_llm(
        "architect",
        NodeLLMConfig(model="model", max_retries=0),
        [{"role": "user", "content": "plan"}],
        completion_fn=lambda **kwargs: {
            "model": "model",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        },
    )

    call = capture.finish(state, state).execution_record.node_executions[0].llm_calls[0]
    assert (call.input_tokens, call.output_tokens) == (7, 2)
    assert call.cost is None
    assert call.cost_currency is None
    assert call.cost_source == CostSource.UNKNOWN


def test_cost_evidence_validation_and_decimal_serialization():
    call = LLMCallCost(
        call_id="call-exact", model="model", input_tokens=1, output_tokens=1,
        cost="0.1234567890123456789", cost_currency="CAD",
        cost_source="provider-reported",
    )
    assert call.cost == Decimal("0.1234567890123456789")
    assert '"cost":"0.1234567890123456789"' in call.model_dump_json()

    invalid = [
        {"cost": "1", "cost_currency": None, "cost_source": "estimated"},
        {"cost": "1", "cost_currency": "usd", "cost_source": "estimated"},
        {"cost": None, "cost_currency": "USD", "cost_source": "unknown"},
        {"cost": None, "cost_currency": None, "cost_source": "estimated"},
        {"cost": "NaN", "cost_currency": "USD", "cost_source": "estimated"},
    ]
    for evidence in invalid:
        with pytest.raises(ValidationError):
            LLMCallCost(
                call_id="invalid", model="model", input_tokens=1, output_tokens=1,
                **evidence,
            )


def test_legacy_cost_usd_remains_readable_as_provider_reported_evidence():
    call = LLMCallCost.model_validate({
        "call_id": "legacy", "model": "model", "input_tokens": 2,
        "output_tokens": 1, "cost_usd": 0.1,
    })
    assert call.cost == Decimal("0.1")
    assert call.cost_currency == "USD"
    assert call.cost_source == CostSource.PROVIDER_REPORTED
    assert call.requested_model is None
    assert call.response_model is None


def test_endpoint_and_router_identity_are_recorded_only_when_reported(tmp_path, monkeypatch):
    state = _state()
    capture = ExecutionCapture.start(state, "architect", "configured-model", tmp_path)
    monkeypatch.setenv("TEST_KEY", "test-key")

    call_llm(
        "architect",
        NodeLLMConfig(
            model="openai/requested-model", max_retries=0,
            endpoint_url="http://127.0.0.1:3001/v1", backend="freellmapi",
            inference_location="remote", api_key_env="TEST_KEY",
        ),
        [{"role": "user", "content": "plan"}],
        completion_fn=lambda **kwargs: {
            "model": "provider/resolved-model",
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "headers": {
                "X-Routed-Via": "free-router",
                "X-Routed-Model": "provider/routed-model",
            },
        },
    )

    evidence = capture.finish(state, state).execution_record.node_executions[0].llm_calls[0]
    assert evidence.requested_model == "openai/requested-model"
    assert evidence.response_model == "provider/resolved-model"
    assert evidence.backend == "freellmapi"
    assert evidence.endpoint_url == "http://127.0.0.1:3001/v1"
    assert evidence.inference_location == "remote"
    assert evidence.routed_provider == "free-router"
    assert evidence.routed_model == "provider/routed-model"


def test_local_inference_is_durable_typed_evidence_not_a_model_string(tmp_path):
    state = _state()
    capture = ExecutionCapture.start(state, "architect", "configured-model", tmp_path)

    call_llm(
        "architect",
        NodeLLMConfig(
            model="openai/qwen3", max_retries=0,
            endpoint_url="http://127.0.0.1:8000/v1", backend="vllm",
            inference_location="local", keyless=True,
        ),
        [{"role": "user", "content": "plan"}],
        completion_fn=lambda **kwargs: {
            "choices": [{"message": {"content": "done"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )

    evidence = capture.finish(state, state).execution_record.node_executions[0].llm_calls[0]
    assert evidence.requested_model == "openai/qwen3"
    assert evidence.response_model is None
    assert evidence.backend == "vllm"
    assert evidence.endpoint_url == "http://127.0.0.1:8000/v1"
    assert evidence.inference_location == "local"


def test_status_costs_queries_saved_run_without_changing_turn_budget(tmp_path, monkeypatch):
    state = _state()
    call = LLMCallCost(
        call_id="call-1", model="architect", input_tokens=10,
        output_tokens=4, cost_usd=0.002,
    )
    record = state.execution_record.model_copy(
        update={"node_executions": [_execution("architect", "architect", call)]}
    )
    state = state.model_copy(update={"execution_record": record})
    state_path = tmp_path / ".battalion" / "state" / "run-BTN-16.json"
    save_state(state, state_path)

    with monkeypatch.context() as scoped:
        scoped.chdir(tmp_path)
        result = CliRunner().invoke(app, ["status", "run-BTN-16", "--costs", "--human"])

    assert result.exit_code == 0
    assert (
        "architect [architect, configured-model]: 1 call(s), 10 in / 4 out, "
        "0 reasoning chars / 0 content chars, 0.002 USD"
    ) in result.output
    assert "Budget:      7 / 100" in result.output
    assert load_state(state_path).budget == Budget(limit=100, used=7)

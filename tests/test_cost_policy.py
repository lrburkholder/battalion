"""BTN-55 zero-cost inference admission and enforcement tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from battalion.application import InvalidInferencePolicy, ResumeRun, create_initial_state, resume_run
from battalion.config import BattalionConfig, load_config, save_config
from battalion.execution import ExecutionCapture
from battalion.llm.configuration import NodeLLMConfig
from battalion.llm.configuration import InferenceConfigurationError
from battalion.llm.cost_policy import CostPolicy, InferencePolicyError, cost_policy_context, validate_cost_policy
from battalion.llm.litellm_client import CostPolicyViolation, call_llm
from battalion.state.models import CostSource, RunStatus
from battalion.state.persistence import save_state
from support.graph import invoke_graph
from support.state import make_run_state


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _target(**overrides) -> NodeLLMConfig:
    fields = {
        "model": "ollama/qwen3",
        "endpoint_url": "http://127.0.0.1:11434",
        "inference_location": "local",
        "cost_classification": "local",
        "classification_source": "same-host operator verification",
        "classification_observed_at": NOW,
    }
    fields.update(overrides)
    return NodeLLMConfig(**fields)


def _models(target: NodeLLMConfig) -> dict[str, NodeLLMConfig]:
    return {role: target for role in ("architect", "driver", "reviewer", "refactorer")}


def test_local_only_admits_every_currently_verified_local_target():
    validate_cost_policy(_models(_target()), CostPolicy.LOCAL_ONLY, now=NOW)


def test_local_only_rejects_remote_target_before_any_provider_call():
    models = _models(_target(inference_location="remote", endpoint_url="https://models.example.test"))

    with pytest.raises(InferencePolicyError, match="local-only"):
        validate_cost_policy(models, CostPolicy.LOCAL_ONLY, now=NOW)


def test_free_only_admits_current_verified_free_remote_target():
    models = _models(_target(
        model="openai/gpt-free", endpoint_url="https://models.example.test",
        inference_location="remote", cost_classification="verified-free",
        classification_expires_at=NOW + timedelta(hours=1),
    ))

    validate_cost_policy(models, CostPolicy.FREE_ONLY, now=NOW)


def test_free_only_admits_mixed_local_and_verified_free_roles():
    free_remote = _target(
        model="openai/gpt-free", endpoint_url="https://models.example.test",
        inference_location="remote", cost_classification="verified-free",
        classification_expires_at=NOW + timedelta(hours=1),
    )
    models = {
        "architect": _target(), "driver": free_remote,
        "reviewer": _target(), "refactorer": free_remote,
    }

    validate_cost_policy(models, CostPolicy.FREE_ONLY, now=NOW)


def test_free_only_rejects_unknown_or_expired_evidence():
    unknown = _models(_target(cost_classification="unknown"))
    expired = _models(_target(
        cost_classification="verified-free",
        classification_expires_at=NOW + timedelta(seconds=1),
    ))

    with pytest.raises(InferencePolicyError, match="free-only"):
        validate_cost_policy(unknown, CostPolicy.FREE_ONLY, now=NOW)
    with pytest.raises(InferencePolicyError, match="free-only"):
        validate_cost_policy(expired, CostPolicy.FREE_ONLY, now=NOW + timedelta(minutes=1))


def test_zero_cost_policy_validates_optional_configured_roles_too():
    models = _models(_target())
    models["tactician"] = _target(
        model="openai/gpt-paid", endpoint_url="https://models.example.test",
        inference_location="remote", cost_classification="paid",
    )

    with pytest.raises(InferencePolicyError, match="tactician"):
        validate_cost_policy(models, CostPolicy.FREE_ONLY, now=NOW)


def test_changing_a_model_discards_target_specific_cost_evidence():
    changed = _target().with_model("ollama/qwen3.5")

    assert changed.cost_classification == "unknown"
    assert changed.classification_source is None
    assert changed.classification_observed_at is None


def test_target_configuration_rejects_provider_fallbacks():
    with pytest.raises(InferenceConfigurationError, match="extra_params"):
        _target(extra_params={"fallbacks": ["openai/paid-fallback"]})


def test_reported_nonzero_cost_is_recorded_then_stops_zero_cost_execution(tmp_path):
    state = make_run_state(run_id="run-BTN-55", ticket_id="BTN-55")
    capture = ExecutionCapture.start(state, "architect", "ollama/qwen3", tmp_path)

    with cost_policy_context(CostPolicy.FREE_ONLY):
        with pytest.raises(CostPolicyViolation, match="non-zero cost"):
            call_llm(
                "architect", _target(max_retries=2), [{"role": "user", "content": "plan"}],
                completion_fn=lambda **_: {
                    "model": "provider/model", "choices": [{"message": {"content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.01},
                },
            )

    call = capture.finish(state, state).execution_record.node_executions[0].llm_calls[0]
    assert call.cost_source is CostSource.PROVIDER_REPORTED
    assert call.cost_policy is CostPolicy.FREE_ONLY
    assert call.identity_contradiction == "Provider reported non-zero cost under free-only policy"


def test_unknown_runtime_cost_remains_unknown_under_free_only(tmp_path):
    state = make_run_state(run_id="run-BTN-55-unknown", ticket_id="BTN-55")
    capture = ExecutionCapture.start(state, "architect", "ollama/qwen3", tmp_path)

    with cost_policy_context(CostPolicy.FREE_ONLY):
        call_llm(
            "architect", _target(max_retries=0), [{"role": "user", "content": "plan"}],
            completion_fn=lambda **_: {
                "model": "provider/model", "choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    call = capture.finish(state, state).execution_record.node_executions[0].llm_calls[0]
    assert call.cost is None
    assert call.cost_source is CostSource.UNKNOWN
    assert call.cost_policy is CostPolicy.FREE_ONLY


def test_graph_pauses_after_recording_a_zero_cost_policy_violation(tmp_path):
    def paid_architect(state, spec_text, llm_config, base_dir, prompts_dir=None):
        call_llm(
            "architect", llm_config, [{"role": "user", "content": spec_text}],
            completion_fn=lambda **_: {
                "model": "provider/model", "choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.01},
            },
        )

    with cost_policy_context(CostPolicy.FREE_ONLY):
        final = invoke_graph(
            make_run_state(ticket_id="BTN-55"), tmp_path,
            configs=_models(_target(max_retries=2)), architect=paid_architect,
        )

    assert final["status"] is RunStatus.AWAITING_HUMAN
    assert final["interrupt_log"][-1].trigger == "infra-failure"
    call = final["execution_record"].node_executions[0].llm_calls[0]
    assert call.cost == Decimal("0.01")
    assert call.identity_contradiction == "Provider reported non-zero cost under free-only policy"


def test_policy_is_durable_on_new_runs_and_cannot_change_on_resume(tmp_path):
    models = _models(_target())
    config = BattalionConfig(base_dir=str(tmp_path), models=models, cost_policy=CostPolicy.LOCAL_ONLY)
    state = create_initial_state("BTN-55", "policy", config)

    assert state.cost_policy is CostPolicy.LOCAL_ONLY
    persisted = make_run_state(run_id="resume-BTN-55", ticket_id="BTN-55", cost_policy=CostPolicy.LOCAL_ONLY)
    save_state(persisted, tmp_path / "resume-BTN-55.json")
    with pytest.raises(InvalidInferencePolicy, match="cannot change"):
        resume_run(
            ResumeRun(
                "resume-BTN-55",
                BattalionConfig(base_dir=str(tmp_path), models=models, cost_policy=CostPolicy.FREE_ONLY),
            ),
            state_dir=tmp_path,
        )


def test_config_persists_user_selected_cost_policy(tmp_path):
    path = tmp_path / "battalion.config.yaml"
    save_config(
        {"default": {"model": "openai/gpt-4o-mini"}}, path,
        cost_policy=CostPolicy.FREE_ONLY,
    )

    assert load_config(path).cost_policy is CostPolicy.FREE_ONLY

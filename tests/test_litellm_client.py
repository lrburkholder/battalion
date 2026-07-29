"""Tests for battalion.llm.litellm_client — per-node model config + retry
handling that surfaces as a distinct InfraFailure, not a raw exception
(BTN-3, spec.md interrupt trigger #5)."""
import pytest

from battalion.llm.litellm_client import (
    InfraFailure,
    NodeLLMConfig,
    build_node_configs,
    call_llm,
)


def test_build_node_configs_gives_each_node_its_own_model():
    raw = {
        "architect": {"model": "claude-sonnet-4-6"},
        "driver": {"model": "gpt-4o"},
    }
    configs = build_node_configs(raw)
    assert configs["architect"].model == "claude-sonnet-4-6"
    assert configs["driver"].model == "gpt-4o"


def test_call_llm_succeeds_first_try_calls_completion_once():
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return {"content": "ok"}

    config = NodeLLMConfig(model="claude-sonnet-4-6", max_retries=2)
    result = call_llm(
        "architect", config, [{"role": "user", "content": "hi"}],
        completion_fn=fake_completion, sleep_fn=lambda s: None,
    )

    assert result == {"content": "ok"}
    assert len(calls) == 1
    assert calls[0]["model"] == "claude-sonnet-4-6"


def test_call_llm_retries_then_succeeds():
    attempts = {"n": 0}

    def flaky_completion(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient failure")
        return {"content": "recovered"}

    config = NodeLLMConfig(model="claude-sonnet-4-6", max_retries=3)
    result = call_llm(
        "driver", config, [{"role": "user", "content": "hi"}],
        completion_fn=flaky_completion, sleep_fn=lambda s: None,
    )

    assert result == {"content": "recovered"}
    assert attempts["n"] == 3


def test_call_llm_exhausts_retries_raises_infra_failure_not_raw_exception():
    def always_fails(**kwargs):
        raise RuntimeError("provider is down")

    config = NodeLLMConfig(model="claude-sonnet-4-6", max_retries=2)

    with pytest.raises(InfraFailure) as exc_info:
        call_llm(
            "reviewer", config, [{"role": "user", "content": "hi"}],
            completion_fn=always_fails, sleep_fn=lambda s: None,
        )

    assert exc_info.value.node_name == "reviewer"
    assert exc_info.value.attempts == 3  # initial attempt + 2 retries
    assert "provider is down" in str(exc_info.value.last_error)


def test_call_llm_sleeps_between_retries_but_not_after_final_failure():
    sleeps = []

    def always_fails(**kwargs):
        raise RuntimeError("nope")

    config = NodeLLMConfig(model="m", max_retries=2)

    with pytest.raises(InfraFailure):
        call_llm(
            "driver", config, [{"role": "user", "content": "hi"}],
            completion_fn=always_fails, sleep_fn=lambda s: sleeps.append(s),
        )

    # 3 total attempts (1 + 2 retries), sleeps only happen between attempts
    assert len(sleeps) == 2


def test_call_llm_passes_temperature_and_extra_params_through():
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return {"content": "ok"}

    config = NodeLLMConfig(
        model="m", temperature=0.7, extra_params={"max_tokens": 500}
    )
    call_llm(
        "architect", config, [{"role": "user", "content": "hi"}],
        completion_fn=fake_completion, sleep_fn=lambda s: None,
    )

    assert calls[0]["temperature"] == 0.7
    assert calls[0]["max_tokens"] == 500


def test_negative_max_retries_rejected():
    with pytest.raises(ValueError):
        NodeLLMConfig(model="m", max_retries=-1)


def test_litellm_is_actually_importable():
    """Smoke test only — doesn't call out to a real provider. Confirms
    litellm is a real dependency, not just referenced in a lazy import
    that would fail the first time it's actually used."""
    import litellm  # noqa: F401

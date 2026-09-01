"""Tests for battalion.llm.litellm_client — per-node model config + retry
handling that surfaces as a distinct InfraFailure, not a raw exception
(BTN-3, spec.md interrupt trigger #5)."""
import pytest

from battalion.llm.litellm_client import (
    InfraFailure,
    NodeLLMConfig,
    _silence_litellm_output,
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


# --- streaming (on_stream) ---

def _stream_chunk(content=None, reasoning=None):
    """A minimal litellm-style streamed chunk with .choices[0].delta."""
    from types import SimpleNamespace

    return SimpleNamespace(choices=[
        SimpleNamespace(delta=SimpleNamespace(
            content=content, reasoning_content=reasoning,
        ))
    ])


class TestCallLlmStreaming:
    def test_streaming_disables_litellm_background_logging_only_while_consuming(self):
        import litellm

        original = litellm.disable_streaming_logging
        observed = []

        def fake_completion(**kwargs):
            observed.append(("completion", litellm.disable_streaming_logging))

            def chunks():
                observed.append(("iteration", litellm.disable_streaming_logging))
                yield _stream_chunk(content="ok")

            return chunks()

        try:
            litellm.disable_streaming_logging = False
            result = call_llm(
                "driver", NodeLLMConfig(model="m", max_retries=0),
                [{"role": "user", "content": "hi"}],
                completion_fn=fake_completion,
                sleep_fn=lambda s: None,
                on_stream=lambda event: None,
            )

            assert result["choices"][0]["message"]["content"] == "ok"
            assert observed == [("completion", True), ("iteration", True)]
            assert litellm.disable_streaming_logging is False
        finally:
            litellm.disable_streaming_logging = original

    def test_streaming_restores_litellm_logging_setting_after_failure(self):
        import litellm

        original = litellm.disable_streaming_logging
        try:
            litellm.disable_streaming_logging = False

            def broken_completion(**kwargs):
                assert litellm.disable_streaming_logging is True
                raise RuntimeError("stream unavailable")

            with pytest.raises(InfraFailure):
                call_llm(
                    "driver", NodeLLMConfig(model="m", max_retries=0),
                    [{"role": "user", "content": "hi"}],
                    completion_fn=broken_completion,
                    sleep_fn=lambda s: None,
                    on_stream=lambda event: None,
                )

            assert litellm.disable_streaming_logging is False
        finally:
            litellm.disable_streaming_logging = original

    def test_on_stream_emits_tokens_and_returns_assembled_response(self):
        events = []

        def fake_completion(**kwargs):
            assert kwargs["stream"] is True
            return iter([
                _stream_chunk(content="Hel"),
                _stream_chunk(content="lo"),
                _stream_chunk(content=" world"),
                _stream_chunk(),  # finish/usage frame: no delta
            ])

        config = NodeLLMConfig(model="m", max_retries=0)
        result = call_llm(
            "architect", config, [{"role": "user", "content": "hi"}],
            completion_fn=fake_completion, sleep_fn=lambda s: None,
            on_stream=events.append,
        )

        assert events == [
            {"type": "token", "content": "Hel"},
            {"type": "token", "content": "lo"},
            {"type": "token", "content": " world"},
        ]
        # Assembled response must be extractable by the shared helpers.
        assert result["choices"][0]["message"]["content"] == "Hello world"

    def test_on_stream_forwards_reasoning_content_separately(self):
        events = []

        def fake_completion(**kwargs):
            return iter([
                _stream_chunk(reasoning="Let me think"),
                _stream_chunk(content="answer"),
            ])

        config = NodeLLMConfig(model="m", max_retries=0)
        result = call_llm(
            "driver", config, [{"role": "user", "content": "hi"}],
            completion_fn=fake_completion, sleep_fn=lambda s: None,
            on_stream=events.append,
        )

        assert events == [
            {"type": "reasoning", "content": "Let me think"},
            {"type": "token", "content": "answer"},
        ]
        assert result["choices"][0]["message"]["content"] == "answer"

    def test_on_stream_no_delta_frames_are_skipped_silently(self):
        events = []

        def fake_completion(**kwargs):
            return iter([
                _stream_chunk(),            # no content, no reasoning
                _stream_chunk(content="x"),
            ])

        config = NodeLLMConfig(model="m", max_retries=0)
        result = call_llm(
            "reviewer", config, [{"role": "user", "content": "hi"}],
            completion_fn=fake_completion, sleep_fn=lambda s: None,
            on_stream=events.append,
        )

        assert events == [{"type": "token", "content": "x"}]
        assert result["choices"][0]["message"]["content"] == "x"

    def test_on_stream_retries_then_raises_infra_failure(self):
        def always_fails(**kwargs):
            raise RuntimeError("stream broke mid-way")

        config = NodeLLMConfig(model="m", max_retries=1)
        with pytest.raises(InfraFailure):
            call_llm(
                "reviewer", config, [{"role": "user", "content": "hi"}],
                completion_fn=always_fails, sleep_fn=lambda s: None,
                on_stream=lambda e: None,
            )

    def test_without_on_stream_does_not_force_streaming(self):
        calls = []

        def fake_completion(**kwargs):
            calls.append(kwargs)
            return {"content": "ok"}

        config = NodeLLMConfig(model="m", max_retries=0)
        call_llm(
            "architect", config, [{"role": "user", "content": "hi"}],
            completion_fn=fake_completion, sleep_fn=lambda s: None,
        )

        assert "stream" not in calls[0]


def test_litellm_is_actually_importable():
    """Smoke test only — doesn't call out to a real provider. Confirms
    litellm is a real dependency, not just referenced in a lazy import
    that would fail the first time it's actually used."""
    import litellm  # noqa: F401


def test_litellm_spam_is_silenced():
    """Battalion suppresses litellm's per-attempt error-handling prints
    ("Give Feedback / Get Help", "Provider List") so a retry storm doesn't
    spam the terminal; the real provider error surfaces via InfraFailure +
    the CLI's pause message instead."""
    import litellm

    _silence_litellm_output()
    assert litellm.suppress_debug_info is True

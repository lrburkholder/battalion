"""Tests for battalion.nodes.architect — the first LLM-driven node (BTN-4)."""
import pytest

from battalion.llm.litellm_client import InfraFailure, NodeLLMConfig
from battalion.nodes.architect import (
    EmptyPlanContent,
    WriteScopeMisconfigured,
    extract_content,
    run_architect,
)
from battalion.state.models import RunStatus


from support.state import make_run_state
from support.responses import litellm_response as litellm_style_response


def make_state(write_scope=None, **overrides):
    return make_run_state(
        ticket_id="BTN-4-test", run_id="run-001",
        status=RunStatus.NOT_STARTED, phase="architect",
        write_scope=write_scope, **overrides,
    )


def test_extract_content_from_litellm_style_response():
    resp = litellm_style_response("# Plan\n\nDo the thing.")
    assert extract_content(resp) == "# Plan\n\nDo the thing."


def test_run_architect_writes_plan_md(tmp_path):
    def fake_completion(**kwargs):
        return litellm_style_response("# Plan\n\nStep one.")

    state = make_state()
    config = NodeLLMConfig(model="test-model")

    updated = run_architect(
        state,
        spec_text="Build a widget.",
        llm_config=config,
        base_dir=tmp_path,
        call_llm_fn=lambda node, cfg, msgs, **kw: fake_completion(),
    )

    assert (tmp_path / "plan.md").read_text() == "# Plan\n\nStep one."
    assert updated.phase == "driver"
    assert updated.status == RunStatus.IN_PROGRESS


def test_run_architect_passes_spec_text_and_node_name_to_llm():
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["node_name"] = node_name
        captured["config"] = config
        captured["messages"] = messages
        return litellm_style_response("plan content")

    state = make_state()
    config = NodeLLMConfig(model="test-model")

    run_architect(
        state,
        spec_text="Build a widget with a spinner.",
        llm_config=config,
        base_dir="/tmp/unused-in-this-test",
        call_llm_fn=fake_call_llm,
    )

    assert captured["node_name"] == "architect"
    assert captured["config"] is config
    joined = " ".join(m["content"] for m in captured["messages"])
    assert "Build a widget with a spinner." in joined


def test_run_architect_forwards_on_stream_to_the_llm_call():
    """on_stream must reach call_llm_fn so streamed tokens/reasoning can be
    surfaced live (BTN-13 CLI progress work)."""
    streamed = []

    def fake_call_llm(node_name, config, messages, **kwargs):
        on_stream = kwargs["on_stream"]
        on_stream({"type": "reasoning", "content": "thinking…"})
        on_stream({"type": "token", "content": "plan text"})
        return litellm_style_response("plan text")

    state = make_state()
    config = NodeLLMConfig(model="test-model")

    updated = run_architect(
        state,
        spec_text="spec",
        llm_config=config,
        base_dir="/tmp/unused-in-this-test",
        call_llm_fn=fake_call_llm,
        on_stream=streamed.append,
    )

    assert streamed == [
        {"type": "reasoning", "content": "thinking…"},
        {"type": "token", "content": "plan text"},
    ]
    assert updated.phase == "driver"


def test_run_architect_omits_on_stream_when_not_given():
    """The on_stream kwarg must not be forwarded when absent, so fixed-arity
    call_llm_fn fakes (no **kwargs) keep working."""
    captured = {}

    def fixed_arity_fake(node_name, config, messages):
        captured["node_name"] = node_name
        return litellm_style_response("plan")

    run_architect(
        make_state(),
        spec_text="spec",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir="/tmp/unused-in-this-test",
        call_llm_fn=fixed_arity_fake,
    )

    assert captured["node_name"] == "architect"


def test_run_architect_only_ever_receives_its_own_scope(tmp_path, monkeypatch):
    """Integration check on top of BTN-2's own tests: the node builds its
    tools from state.write_scope internally, so confirm it ends up with
    exactly {'plan.md'} even though the state carries other nodes' scopes
    too."""
    import battalion.nodes.architect as architect_module

    captured_scopes = {}
    real_build = architect_module.build_write_tools

    def spy_build_write_tools(node_name, write_scope, base_dir=".", on_violation=None):
        tools = real_build(node_name, write_scope, base_dir, on_violation)
        captured_scopes["node_name"] = node_name
        captured_scopes["tool_keys"] = set(tools.keys())
        return tools

    monkeypatch.setattr(architect_module, "build_write_tools", spy_build_write_tools)

    state = make_state()
    run_architect(
        state,
        spec_text="spec",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: litellm_style_response("content"),
    )

    assert captured_scopes["node_name"] == "architect"
    assert captured_scopes["tool_keys"] == {"plan.md"}


def test_run_architect_raises_clear_error_when_scope_missing_plan_md(tmp_path):
    state = make_state(write_scope={"architect": [], "driver": ["src/"], "reviewer": []})

    with pytest.raises(WriteScopeMisconfigured):
        run_architect(
            state,
            spec_text="spec",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: litellm_style_response("content"),
        )


def test_run_architect_propagates_infra_failure(tmp_path):
    def always_fails(**kwargs):
        raise RuntimeError("provider down")

    from battalion.llm.litellm_client import call_llm

    state = make_state()
    config = NodeLLMConfig(model="test-model", max_retries=1)

    with pytest.raises(InfraFailure):
        run_architect(
            state,
            spec_text="spec",
            llm_config=config,
            base_dir=tmp_path,
            call_llm_fn=lambda node, cfg, msgs, **kw: call_llm(
                node, cfg, msgs, completion_fn=always_fails, sleep_fn=lambda s: None
            ),
        )

    # Nothing should have been written given the LLM call never succeeded
    assert not (tmp_path / "plan.md").exists()


def test_run_architect_rejects_empty_llm_content(tmp_path):
    def fake_call_llm(node_name, config, messages, **kwargs):
        return litellm_style_response("   ")  # whitespace only

    state = make_state()

    with pytest.raises(EmptyPlanContent):
        run_architect(
            state,
            spec_text="spec",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=fake_call_llm,
        )

    assert not (tmp_path / "plan.md").exists()


def test_run_architect_system_prompt_override_takes_effect_over_file_default():
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return litellm_style_response("plan content")

    state = make_state()
    run_architect(
        state,
        spec_text="spec",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir="/tmp/unused-in-this-test",
        call_llm_fn=fake_call_llm,
        system_prompt="CUSTOM OVERRIDE PROMPT",
    )

    assert captured["system_content"] == "CUSTOM OVERRIDE PROMPT"


def test_run_architect_defaults_to_file_loaded_prompt_when_no_override():
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return litellm_style_response("plan content")

    state = make_state()
    run_architect(
        state,
        spec_text="spec",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir="/tmp/unused-in-this-test",
        call_llm_fn=fake_call_llm,
    )

    # Comes from battalion/prompts/architect.md, not a hardcoded Python string
    assert "Architect" in captured["system_content"]

"""Tests for battalion.nodes.driver — produces multi-file output (tests +
implementation) from one LLM call, per the scope decision flagged before
implementation: this ticket's AC doesn't require actually executing the
red-green-refactor cycle (real pytest runs), just producing scoped file
writes. See module docstring in driver.py for the deferred-capability note.
"""
import pytest

from battalion.nodes.driver import (
    EmptyDriverOutput,
    MalformedDriverOutput,
    extract_files,
    run_driver,
)
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.llm.litellm_client import InfraFailure, NodeLLMConfig, call_llm
from battalion.scope.tool_binding import ScopeViolationError
from battalion.state.models import Budget, RunState, RunStatus


def make_state(write_scope=None, **overrides):
    defaults = dict(
        schema_version="1.0",
        run_id="run-001",
        ticket_id="BTN-5-test",
        status=RunStatus.IN_PROGRESS,
        phase="driver",
        write_scope=write_scope if write_scope is not None else {
            "architect": ["plan.md"],
            "driver": ["src/"],
            "reviewer": [],
        },
        retry_bound=2,
        budget=Budget(limit=100, used=0),
    )
    defaults.update(overrides)
    return RunState(**defaults)


def files_response(files: dict) -> dict:
    import json
    return {"choices": [{"message": {"content": json.dumps({"files": files})}}]}


def fenced_files_response(files: dict) -> dict:
    import json
    body = json.dumps({"files": files})
    return {"choices": [{"message": {"content": f"```json\n{body}\n```"}}]}


def test_extract_files_parses_plain_json():
    resp = files_response({"src/module.py": "print('hi')"})
    assert extract_files(resp) == {"src/module.py": "print('hi')"}


def test_extract_files_parses_markdown_fenced_json():
    resp = fenced_files_response({"src/module.py": "print('hi')"})
    assert extract_files(resp) == {"src/module.py": "print('hi')"}


def test_extract_files_rejects_invalid_json():
    resp = {"choices": [{"message": {"content": "not json at all"}}]}
    with pytest.raises(MalformedDriverOutput):
        extract_files(resp)


def test_extract_files_rejects_missing_files_key():
    resp = {"choices": [{"message": {"content": '{"not_files": {}}'}}]}
    with pytest.raises(MalformedDriverOutput):
        extract_files(resp)


def test_extract_files_rejects_non_dict_files_value():
    resp = {"choices": [{"message": {"content": '{"files": ["not", "a", "dict"]}'}}]}
    with pytest.raises(MalformedDriverOutput):
        extract_files(resp)


def test_extract_files_rejects_non_string_content():
    resp = {"choices": [{"message": {"content": '{"files": {"module.py": 123}}'}}]}
    with pytest.raises(MalformedDriverOutput):
        extract_files(resp)


def test_extract_files_rejects_empty_string_path():
    resp = {"choices": [{"message": {"content": '{"files": {"": "content"}}'}}]}
    with pytest.raises(MalformedDriverOutput):
        extract_files(resp)


def test_run_driver_writes_multiple_files(tmp_path):
    files = {
        "test_module.py": "def test_x(): assert True",
        "module.py": "def x(): return True",
    }

    updated = run_driver(
        make_state(),
        ticket_text="Implement module.x()",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: files_response(files),
    )

    assert (tmp_path / "src" / "test_module.py").read_text() == files["test_module.py"]
    assert (tmp_path / "src" / "module.py").read_text() == files["module.py"]
    assert updated.phase == "reviewer"
    assert updated.status == RunStatus.IN_PROGRESS


def test_run_driver_only_ever_receives_its_own_scope(tmp_path, monkeypatch):
    import battalion.nodes.driver as driver_module

    captured = {}
    real_build = driver_module.build_write_tools

    def spy(node_name, write_scope, base_dir=".", on_violation=None):
        tools = real_build(node_name, write_scope, base_dir, on_violation)
        captured["node_name"] = node_name
        captured["tool_keys"] = set(tools.keys())
        return tools

    monkeypatch.setattr(driver_module, "build_write_tools", spy)

    run_driver(
        make_state(),
        ticket_text="ticket",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: files_response({"module.py": "x = 1"}),
    )

    assert captured["node_name"] == "driver"
    assert captured["tool_keys"] == {"src/"}


def test_run_driver_raises_clear_error_when_scope_missing_src(tmp_path):
    state = make_state(write_scope={"architect": ["plan.md"], "driver": [], "reviewer": []})

    with pytest.raises(WriteScopeMisconfigured):
        run_driver(
            state,
            ticket_text="ticket",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({"module.py": "x = 1"}),
        )


def test_run_driver_propagates_infra_failure(tmp_path):
    def always_fails(**kwargs):
        raise RuntimeError("provider down")

    with pytest.raises(InfraFailure):
        run_driver(
            make_state(),
            ticket_text="ticket",
            llm_config=NodeLLMConfig(model="test-model", max_retries=1),
            base_dir=tmp_path,
            call_llm_fn=lambda node, cfg, msgs, **kw: call_llm(
                node, cfg, msgs, completion_fn=always_fails, sleep_fn=lambda s: None
            ),
        )
    assert not (tmp_path / "src").exists()


def test_run_driver_rejects_empty_files_output(tmp_path):
    with pytest.raises(EmptyDriverOutput):
        run_driver(
            make_state(),
            ticket_text="ticket",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({}),
        )
    assert not (tmp_path / "src").exists()


def test_run_driver_validates_all_paths_before_writing_any():
    """A scope violation on a later file must not leave earlier files
    written — pre-validate the whole batch before writing any of it."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        files = {
            "module.py": "x = 1",
            "../escape.py": "malicious content",
        }
        with pytest.raises(ScopeViolationError):
            run_driver(
                make_state(),
                ticket_text="ticket",
                llm_config=NodeLLMConfig(model="test-model"),
                base_dir=tmp_path,
                call_llm_fn=lambda *a, **kw: files_response(files),
            )
        # module.py must NOT have been written, even though it was valid,
        # because the batch was invalid as a whole.
        assert not (tmp_path / "src" / "module.py").exists()
        assert not (tmp_path / "escape.py").exists()


def test_run_driver_default_prompt_loaded_from_file(tmp_path):
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return files_response({"module.py": "x = 1"})

    run_driver(
        make_state(),
        ticket_text="ticket",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=fake_call_llm,
    )

    assert "Driver" in captured["system_content"]


def test_run_driver_system_prompt_override_takes_effect(tmp_path):
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return files_response({"module.py": "x = 1"})

    run_driver(
        make_state(),
        ticket_text="ticket",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=fake_call_llm,
        system_prompt="CUSTOM DRIVER PROMPT",
    )

    assert captured["system_content"] == "CUSTOM DRIVER PROMPT"

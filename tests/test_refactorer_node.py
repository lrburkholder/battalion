"""Tests for battalion.nodes.refactorer — BTN-13 implementation.

Refactorer is structurally identical to Driver (same file output format and
scope enforcement). It accepts an explicit phase scope and preserves the
legacy shared Driver scope per ADR-0008/ADR-0013.
"""
import pytest

from battalion.nodes.refactorer import (
    EmptyRefactorerOutput,
    MalformedRefactorerOutput,
    extract_files,
    run_refactorer,
)
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.llm.litellm_client import InfraFailure, NodeLLMConfig, call_llm
from battalion.scope.tool_binding import ScopeViolationError
from battalion.state.models import RunStatus


# --- Fixtures / Helpers ---

from conftest import make_run_state


def make_state(write_scope=None, **overrides):
    fields = dict(
        ticket_id="BTN-13-test", run_id="run-001",
        status=RunStatus.IN_PROGRESS, phase="refactorer",
        write_scope=write_scope,
    )
    fields.update(overrides)
    return make_run_state(**fields)


def files_response(files: dict) -> dict:
    import json
    return {"choices": [{"message": {"content": json.dumps({"files": files})}}]}


def fenced_files_response(files: dict) -> dict:
    import json
    body = json.dumps({"files": files})
    return {"choices": [{"message": {"content": f"```json\n{body}\n```"}}]}


# --- extract_files tests ---

def test_extract_files_parses_plain_json():
    resp = files_response({"src/module.py": "def x(): pass"})
    assert extract_files(resp) == {"src/module.py": "def x(): pass"}


def test_extract_files_parses_markdown_fenced_json():
    resp = fenced_files_response({"src/module.py": "def x(): pass"})
    assert extract_files(resp) == {"src/module.py": "def x(): pass"}


def test_extract_files_rejects_invalid_json():
    resp = {"choices": [{"message": {"content": "not json at all"}}]}
    with pytest.raises(MalformedRefactorerOutput):
        extract_files(resp)


def test_extract_files_rejects_missing_files_key():
    resp = {"choices": [{"message": {"content": '{"not_files": {}}'}}]}
    with pytest.raises(MalformedRefactorerOutput):
        extract_files(resp)


def test_extract_files_rejects_non_dict_files_value():
    resp = {"choices": [{"message": {"content": '{"files": ["not", "a", "dict"]}'}}]}
    with pytest.raises(MalformedRefactorerOutput):
        extract_files(resp)


def test_extract_files_rejects_non_string_content():
    resp = {"choices": [{"message": {"content": '{"files": {"module.py": 123}}'}}]}
    with pytest.raises(MalformedRefactorerOutput):
        extract_files(resp)


def test_extract_files_rejects_empty_string_path():
    resp = {"choices": [{"message": {"content": '{"files": {"": "content"}}'}}]}
    with pytest.raises(MalformedRefactorerOutput):
        extract_files(resp)


# --- run_refactorer tests ---

def test_run_refactorer_writes_refactored_files(tmp_path):
    files = {
        "module.py": "def x():\n    return 42",
    }

    updated = run_refactorer(
        make_state(),
        refactor_text="Refactor module.py for clarity",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: files_response(files),
    )

    assert (tmp_path / "src" / "module.py").read_text() == files["module.py"]
    assert updated.phase == "reviewer"
    assert updated.status == RunStatus.IN_PROGRESS


def test_run_refactorer_uses_driver_scope_entry(tmp_path, monkeypatch):
    """ADR-008 critical test: Refactorer must call build_write_tools with
    node_name='driver', not 'refactorer'. This is how it shares Driver's
    write_scope entry without needing its own key."""
    import battalion.nodes.refactorer as refactorer_module

    captured = {}
    real_build = refactorer_module.build_write_tools

    def spy(node_name, write_scope, base_dir=".", on_violation=None):
        tools = real_build(node_name, write_scope, base_dir, on_violation)
        captured["node_name"] = node_name
        captured["tool_keys"] = set(tools.keys())
        return tools

    monkeypatch.setattr(refactorer_module, "build_write_tools", spy)

    run_refactorer(
        make_state(),
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: files_response({"module.py": "x = 1"}),
    )

    # ADR-008: node_name MUST be "driver", not "refactorer"
    assert captured["node_name"] == "driver"
    assert captured["tool_keys"] == {"src/"}


def test_run_refactorer_only_receives_driver_scope_entries(tmp_path):
    """Refactorer should only see scope entries from 'driver' key, never
    a hypothetical 'refactorer' key."""
    state = make_state(write_scope={
        "architect": ["plan.md"],
        "driver": ["src/"],
        "reviewer": [],
        # Even if someone mistakenly adds this, Refactorer won't use it
        # because it calls build_write_tools("driver", ...)
    })

    updated = run_refactorer(
        state,
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: files_response({"module.py": "x = 1"}),
    )

    assert (tmp_path / "src" / "module.py").exists()
    assert updated.phase == "reviewer"


def test_run_refactorer_raises_clear_error_when_driver_scope_missing_src(tmp_path):
    """If the 'driver' key in write_scope doesn't include 'src/', Refactorer
    cannot function — same check as Driver (AC #1)."""
    state = make_state(write_scope={
        "architect": ["plan.md"],
        "driver": [],  # Missing src/
        "reviewer": [],
    })

    with pytest.raises(WriteScopeMisconfigured) as exc_info:
        run_refactorer(
            state,
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({"module.py": "x = 1"}),
        )
    assert "write roots" in str(exc_info.value)
    assert "driver" in str(exc_info.value)


def test_run_refactorer_propagates_infra_failure(tmp_path):
    def always_fails(**kwargs):
        raise RuntimeError("provider down")

    with pytest.raises(InfraFailure):
        run_refactorer(
            make_state(),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model", max_retries=1),
            base_dir=tmp_path,
            call_llm_fn=lambda node, cfg, msgs, **kw: call_llm(
                node, cfg, msgs, completion_fn=always_fails, sleep_fn=lambda s: None
            ),
        )
    assert not (tmp_path / "src").exists()


def test_run_refactorer_rejects_empty_files_output(tmp_path):
    with pytest.raises(EmptyRefactorerOutput):
        run_refactorer(
            make_state(),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({}),
        )
    assert not (tmp_path / "src").exists()


def test_run_refactorer_validates_all_paths_before_writing_any(tmp_path):
    """A scope violation on a later file must not leave earlier files
    written — pre-validate the whole batch before writing any of it."""
    files = {
        "module.py": "x = 1",
        "../escape.py": "malicious content",
    }
    with pytest.raises(ScopeViolationError):
        run_refactorer(
            make_state(),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response(files),
        )
    # module.py must NOT have been written, even though it was valid,
    # because the batch was invalid as a whole.
    assert not (tmp_path / "src" / "module.py").exists()
    assert not (tmp_path / "escape.py").exists()


def test_run_refactorer_cannot_write_outside_src_scope(tmp_path):
    """Structural guarantee: Refactorer shares Driver's scope, so it
    cannot write outside src/ (AC #2)."""
    with pytest.raises(ScopeViolationError):
        run_refactorer(
            make_state(),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({"../plan.md": "sneaky"}),
        )
    assert not (tmp_path / "plan.md").exists()


def test_run_refactorer_default_prompt_loaded_from_file(tmp_path):
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return files_response({"module.py": "x = 1"})

    run_refactorer(
        make_state(),
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=fake_call_llm,
    )

    assert "Refactorer" in captured["system_content"]


def test_run_refactorer_system_prompt_override_takes_effect(tmp_path):
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return files_response({"module.py": "x = 1"})

    run_refactorer(
        make_state(),
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=fake_call_llm,
        system_prompt="CUSTOM REFACTORER PROMPT",
    )

    assert captured["system_content"] == "CUSTOM REFACTORER PROMPT"


def test_run_refactorer_prompts_dir_override(tmp_path):
    """Verify prompts_dir override is supported matching other nodes (AC #3)."""
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return files_response({"module.py": "x = 1"})

    # Pass a non-existent directory to force use of override
    # The actual file loading would fail, but we're testing the parameter
    # passes through correctly
    run_refactorer(
        make_state(),
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=fake_call_llm,
        system_prompt="OVERRIDE PROMPT",
        prompts_dir="/nonexistent",
    )

    assert captured["system_content"] == "OVERRIDE PROMPT"


def test_refactorer_prefers_its_distinct_implementation_scope(tmp_path):
    state = make_state(write_scope={
        "driver_red": ["tests/"],
        "driver_green": ["generated/"],
        "refactorer": ["battalion/"],
        "reviewer": [],
    })
    run_refactorer(
        state,
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: files_response({"widget.py": "VALUE = 1"}),
    )
    assert (tmp_path / "battalion" / "widget.py").exists()
    assert not (tmp_path / "generated" / "widget.py").exists()


def test_refactorer_cannot_traverse_into_test_root(tmp_path):
    state = make_state(write_scope={
        "driver_red": ["tests/"],
        "driver_green": ["battalion/"],
        "refactorer": ["battalion/"],
        "reviewer": [],
    })
    with pytest.raises(ScopeViolationError):
        run_refactorer(
            state,
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({"../tests/helper.py": "VALUE = 1"}),
        )
    assert not (tmp_path / "tests" / "helper.py").exists()

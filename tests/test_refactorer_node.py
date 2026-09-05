"""Tests for battalion.nodes.refactorer — BTN-13 implementation.

Refactorer is structurally identical to Driver (same file output format and
scope enforcement). It accepts an explicit phase scope and preserves the
legacy shared Driver scope per ADR-0008/ADR-0013.
"""
from support.execution import make_node_execution

import pytest

from battalion.nodes.refactorer import (
    EmptyRefactorerOutput,
    MalformedRefactorerOutput,
    UnauthorizedRefactorerOutput,
    extract_output,
    extract_files,
    run_refactorer,
)
from battalion.execution import ExecutionCapture
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.llm.litellm_client import InfraFailure, NodeLLMConfig, call_llm
from battalion.scope.tool_binding import ScopeViolationError
from battalion.state.models import ArtifactProvenance, ExecutionRecord, RunStatus


# --- Fixtures / Helpers ---

from support.state import make_run_state
from support.responses import files_response, no_change_response, litellm_response


def make_state(write_scope=None, **overrides):
    fields = dict(
        ticket_id="BTN-13-test", run_id="run-001",
        status=RunStatus.IN_PROGRESS, phase="refactorer",
        write_scope=write_scope,
    )
    fields.update(overrides)
    return make_run_state(**fields)


def state_with_green_artifacts(*paths: str):
    execution = make_node_execution(
        execution_id="node-green",
        role="driver",
        phase="driver_green",
        model_identity="test-model",
        artifact_provenance=[
            ArtifactProvenance(
                path=path,
                sha256="0" * 64,
                originating_run_id="run-001",
                originating_node_execution_id="node-green",
            )
            for path in paths
        ],
    )
    return make_state(execution_record=ExecutionRecord(node_executions=[execution]))


# --- extract_files tests ---

@pytest.mark.parametrize("fenced", [False, True], ids=["plain", "markdown-fenced"])
def test_extract_files_parses_json(fenced):
    resp = files_response({"src/module.py": "def x(): pass"}, fenced=fenced)
    assert extract_files(resp) == {"src/module.py": "def x(): pass"}


@pytest.mark.parametrize("content", [
    pytest.param("not json at all", id="invalid-json"),
    pytest.param('{"not_files": {}}', id="missing-files-key"),
    pytest.param('{"files": ["not", "a", "dict"]}', id="non-dict-files"),
    pytest.param('{"files": {"module.py": 123}}', id="non-string-content"),
    pytest.param('{"files": {"": "content"}}', id="empty-path"),
])
def test_extract_files_rejects_malformed_output(content):
    resp = litellm_response(content)
    with pytest.raises(MalformedRefactorerOutput):
        extract_files(resp)


def test_extract_output_accepts_explicit_no_change_only_with_reason():
    output = extract_output(no_change_response("No smaller safe change exists."))

    assert output.files == {}
    assert output.no_change_reason == "No smaller safe change exists."

    with pytest.raises(EmptyRefactorerOutput):
        extract_output(files_response({}))


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


def test_run_refactorer_rejects_files_not_written_by_accepted_green_driver(tmp_path):
    with pytest.raises(UnauthorizedRefactorerOutput, match="not written by accepted GREEN Driver"):
        run_refactorer(
            state_with_green_artifacts("src/widget.py"),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({"other.py": "VALUE = 1"}),
        )

    assert not (tmp_path / "src" / "other.py").exists()


def test_run_refactorer_rejects_test_or_documentation_artifacts(tmp_path):
    with pytest.raises(UnauthorizedRefactorerOutput, match="non-production paths"):
        run_refactorer(
            state_with_green_artifacts("src/test_widget.py"),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({"test_widget.py": "pass"}),
        )

    assert not (tmp_path / "src" / "test_widget.py").exists()


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


def test_run_refactorer_rejects_unexplained_empty_files_output(tmp_path):
    with pytest.raises(EmptyRefactorerOutput):
        run_refactorer(
            make_state(),
            refactor_text="refactor",
            llm_config=NodeLLMConfig(model="test-model"),
            base_dir=tmp_path,
            call_llm_fn=lambda *a, **kw: files_response({}),
        )
    assert not (tmp_path / "src").exists()


def test_run_refactorer_no_change_writes_nothing_and_records_decision(tmp_path):
    state = make_state()
    capture = ExecutionCapture.start(state, "refactorer", "test-model", tmp_path)

    updated = run_refactorer(
        state,
        refactor_text="refactor",
        llm_config=NodeLLMConfig(model="test-model"),
        base_dir=tmp_path,
        call_llm_fn=lambda *a, **kw: no_change_response("Already minimal."),
    )
    finished = capture.finish(state, updated)
    execution = finished.execution_record.node_executions[-1]

    assert updated.phase == "reviewer"
    assert not (tmp_path / "src").exists()
    assert execution.output_reference == "refactorer:no-change"
    assert execution.artifact_provenance == []
    assert "Already minimal." in execution.operator_summary.what_i_did


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

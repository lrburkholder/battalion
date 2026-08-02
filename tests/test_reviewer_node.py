"""Tests for battalion.nodes.reviewer (BTN-6).

Phase-agnostic by design: Reviewer's job is "run whatever tests currently
exist, from a clean copy, and report what happened" — it doesn't know or
care whether Driver is mid-RED or mid-GREEN. That's what lets it slot into
either checkpoint of the RED -> Reviewer -> GREEN -> Refactorer loop
without rework.
"""
import pytest

from battalion.llm.litellm_client import NodeLLMConfig
from battalion.nodes.errors import WriteScopeMisconfigured
from battalion.nodes.reviewer import (
    EmptyReviewContent,
    SourceTreeMissing,
    TestRunResult,
    make_clean_copy,
    run_reviewer,
    run_tests_via_subprocess,
)
from battalion.state.models import Budget, RejectionRecord, RunState, RunStatus


def make_state(write_scope=None, rejection_history=None, **overrides):
    defaults = dict(
        schema_version="1.0",
        run_id="run-001",
        ticket_id="BTN-6-test",
        status=RunStatus.IN_PROGRESS,
        phase="reviewer",
        write_scope=write_scope if write_scope is not None else {
            "architect": ["plan.md"],
            "driver": ["src/"],
            "reviewer": [],
        },
        reviewer_rejection_history=rejection_history or [],
        retry_bound=2,
        budget=Budget(limit=100, used=0),
    )
    defaults.update(overrides)
    return RunState(**defaults)


def litellm_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


# --- unit tests for run_reviewer, with fake test-execution/copy fns ---

def test_run_reviewer_accepts_when_tests_pass(tmp_path):
    (tmp_path / "src").mkdir()

    state = make_state()
    updated = run_reviewer(
        state,
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(passed=True, output="2 passed", returncode=0),
        call_llm_fn=lambda *a, **kw: litellm_response("unused on accept"),
    )

    assert updated.phase == "done"
    assert updated.status == RunStatus.DONE
    assert updated.reviewer_rejection_history == []


def test_run_reviewer_rejects_when_tests_fail_and_records_cause(tmp_path):
    (tmp_path / "src").mkdir()

    def fake_call_llm(node_name, config, messages, **kwargs):
        assert node_name == "reviewer"
        return litellm_response("AssertionError: off-by-one in module.add")

    state = make_state()
    updated = run_reviewer(
        state,
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(
            passed=False, output="FAILED test_x.py::test_add", returncode=1
        ),
        call_llm_fn=fake_call_llm,
    )

    assert updated.phase == "driver"
    assert updated.status == RunStatus.IN_PROGRESS
    assert len(updated.reviewer_rejection_history) == 1
    assert updated.reviewer_rejection_history[0].cause == "AssertionError: off-by-one in module.add"
    assert updated.reviewer_rejection_history[0].cycle_number == 1


def test_run_reviewer_increments_cycle_number_on_repeat_rejection(tmp_path):
    (tmp_path / "src").mkdir()

    state = make_state(
        rejection_history=[RejectionRecord(cause="prior cause", cycle_number=1)]
    )
    updated = run_reviewer(
        state,
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(passed=False, output="fail", returncode=1),
        call_llm_fn=lambda *a, **kw: litellm_response("second cause"),
    )

    assert len(updated.reviewer_rejection_history) == 2
    assert updated.reviewer_rejection_history[1].cycle_number == 2


def test_run_reviewer_never_writes_anything(tmp_path):
    """Reviewer's declared scope must be empty. Confirmed by asserting no
    write tool exists for it at all, matching BTN-2's structural pattern —
    not just 'this test happens not to call write()'."""
    import battalion.nodes.reviewer as reviewer_module

    captured = {}
    real_build = reviewer_module.build_write_tools

    def spy(node_name, write_scope, base_dir=".", on_violation=None):
        tools = real_build(node_name, write_scope, base_dir, on_violation)
        captured["tool_keys"] = set(tools.keys())
        return tools

    import pytest as _pytest
    (tmp_path / "src").mkdir()
    orig = reviewer_module.build_write_tools
    reviewer_module.build_write_tools = spy
    try:
        run_reviewer(
            make_state(),
            base_dir=tmp_path,
            llm_config=NodeLLMConfig(model="test-model"),
            make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
            run_tests_fn=lambda clean_dir: TestRunResult(passed=True, output="ok", returncode=0),
            call_llm_fn=lambda *a, **kw: litellm_response("unused"),
        )
    finally:
        reviewer_module.build_write_tools = orig

    assert captured["tool_keys"] == set()


def test_run_reviewer_raises_if_scope_misconfigured_non_empty(tmp_path):
    (tmp_path / "src").mkdir()
    state = make_state(write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": ["oops.md"]})

    with pytest.raises(WriteScopeMisconfigured):
        run_reviewer(
            state,
            base_dir=tmp_path,
            llm_config=NodeLLMConfig(model="test-model"),
            make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
            run_tests_fn=lambda clean_dir: TestRunResult(passed=True, output="ok", returncode=0),
            call_llm_fn=lambda *a, **kw: litellm_response("unused"),
        )


def test_run_reviewer_rejects_empty_cause_content(tmp_path):
    (tmp_path / "src").mkdir()
    state = make_state()

    with pytest.raises(EmptyReviewContent):
        run_reviewer(
            state,
            base_dir=tmp_path,
            llm_config=NodeLLMConfig(model="test-model"),
            make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
            run_tests_fn=lambda clean_dir: TestRunResult(passed=False, output="fail", returncode=1),
            call_llm_fn=lambda *a, **kw: litellm_response("   "),
        )


def test_run_reviewer_default_prompt_loaded_from_file(tmp_path):
    (tmp_path / "src").mkdir()
    captured = {}

    def fake_call_llm(node_name, config, messages, **kwargs):
        captured["system_content"] = messages[0]["content"]
        return litellm_response("cause")

    run_reviewer(
        make_state(),
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(passed=False, output="fail", returncode=1),
        call_llm_fn=fake_call_llm,
    )

    assert "Reviewer" in captured["system_content"]


# --- tests for the real default helpers (no mocking) ---

def test_make_clean_copy_is_independent_of_original(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.py").write_text("x = 1")

    clean = make_clean_copy(src)

    assert clean != src
    assert (clean / "module.py").read_text() == "x = 1"

    (src / "module.py").write_text("x = 2")
    assert (clean / "module.py").read_text() == "x = 1"  # unaffected by later edits


def test_run_tests_via_subprocess_detects_passing_tests(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "test_ok.py").write_text("def test_ok():\n    assert True\n")

    result = run_tests_via_subprocess(src)

    assert result.passed is True
    assert result.returncode == 0


def test_run_tests_via_subprocess_detects_failing_tests(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "test_bad.py").write_text("def test_bad():\n    assert False, 'expected failure'\n")

    result = run_tests_via_subprocess(src)

    assert result.passed is False
    assert "expected failure" in result.output


def test_run_reviewer_raises_clear_error_when_src_missing(tmp_path):
    # No src/ dir created at all
    state = make_state()

    with pytest.raises(SourceTreeMissing):
        run_reviewer(
            state,
            base_dir=tmp_path,
            llm_config=NodeLLMConfig(model="test-model"),
            make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
            run_tests_fn=lambda clean_dir: TestRunResult(passed=True, output="ok", returncode=0),
            call_llm_fn=lambda *a, **kw: litellm_response("unused"),
        )


def test_run_reviewer_cleans_up_temp_clean_copy_dir():
    """The real make_clean_copy default creates a temp dir — confirm
    run_reviewer removes it after the test run instead of leaking it."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        src = tmp_path / "src"
        src.mkdir()
        (src / "test_thing.py").write_text("def test_thing():\n    assert True\n")

        created_dirs = []
        real_make_clean_copy = make_clean_copy

        def spying_make_clean_copy(src_dir):
            result = real_make_clean_copy(src_dir)
            created_dirs.append(result)
            return result

        run_reviewer(
            make_state(),
            base_dir=tmp_path,
            llm_config=NodeLLMConfig(model="test-model"),
            call_llm_fn=lambda *a, **kw: litellm_response("unused"),
            make_clean_copy_fn=spying_make_clean_copy,
        )

        assert len(created_dirs) == 1
        assert not created_dirs[0].exists()


def test_run_reviewer_end_to_end_with_real_defaults(tmp_path):
    """Full pipeline, no mocked test execution: writes real files, uses
    the real clean-copy + subprocess pytest run, only the LLM call is faked."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "test_thing.py").write_text("def test_thing():\n    assert 1 + 1 == 2\n")

    updated = run_reviewer(
        make_state(),
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        call_llm_fn=lambda *a, **kw: litellm_response("unused on accept"),
    )

    assert updated.phase == "done"
    assert updated.status == RunStatus.DONE

"""Tests for battalion.nodes.reviewer (BTN-6, extended in BTN-12).

Reviewer's job is "run whatever tests currently exist, from a clean copy,
and report what happened" -- but which outcome counts as "accept" depends
on the checkpoint (BTN-12, ADR-007): RED-check expects tests to fail,
GREEN-check and REFACTOR-check expect them to pass. checkpoint also scopes
the rejection-cycle counter (ADR-009) and determines phase transitions.
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
from battalion.state.models import CheckpointType, RejectionRecord, RunStatus


from conftest import make_run_state


def make_state(write_scope=None, rejection_history=None, **overrides):
    fields = dict(
        ticket_id="BTN-12-test", run_id="run-001",
        status=RunStatus.IN_PROGRESS, phase="reviewer",
        write_scope=write_scope,
        rejection_history=rejection_history,
        schema_version="1.1",
    )
    fields.update(overrides)
    return make_run_state(**fields)


def litellm_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def fake_passed(tmp_path, **kw):
    state = kw.pop("state", None) or make_state(**kw.pop("state_overrides", {}))
    return run_reviewer(
        state,
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(passed=True, output="passed", returncode=0),
        call_llm_fn=lambda *a, **k: litellm_response("unused"),
        **kw,
    )


def fake_failed(tmp_path, cause="a cause", **kw):
    state = kw.pop("state", None) or make_state(**kw.pop("state_overrides", {}))
    return run_reviewer(
        state,
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(passed=False, output="failed", returncode=1),
        call_llm_fn=lambda *a, **k: litellm_response(cause),
        **kw,
    )


# --- expect_pass polarity per checkpoint (BTN-12 core behavior) ---

def test_green_check_accepts_on_pass(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_passed(tmp_path, checkpoint=CheckpointType.GREEN_CHECK)
    assert updated.reviewer_rejection_history == []


def test_green_check_rejects_on_fail(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, checkpoint=CheckpointType.GREEN_CHECK)
    assert len(updated.reviewer_rejection_history) == 1


def test_red_check_accepts_on_fail(tmp_path):
    """The whole point of BTN-12: a correctly-failing RED-check test must
    be ACCEPTED, not rejected -- this was the bug caught during the
    architecture pass before BTN-7."""
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, checkpoint=CheckpointType.RED_CHECK)
    assert updated.reviewer_rejection_history == []  # accepted, not rejected


def test_red_check_rejects_on_unexpected_pass(tmp_path):
    """If a RED-mode test unexpectedly passes, that's a rejection --
    something is wrong (the feature already existed, or the test is a
    no-op)."""
    (tmp_path / "src").mkdir()
    updated = fake_passed(tmp_path, checkpoint=CheckpointType.RED_CHECK)
    assert len(updated.reviewer_rejection_history) == 1


def test_refactor_check_accepts_on_pass(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_passed(tmp_path, checkpoint=CheckpointType.REFACTOR_CHECK)
    assert updated.reviewer_rejection_history == []


def test_refactor_check_rejects_on_fail(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, checkpoint=CheckpointType.REFACTOR_CHECK)
    assert len(updated.reviewer_rejection_history) == 1


# --- phase transitions depend on checkpoint + verdict ---

def test_red_check_accept_advances_to_driver_for_green(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, checkpoint=CheckpointType.RED_CHECK)
    # Was "driver" (generic) — now "driver_green", distinct from the reject
    # value "driver_red" below. The two used to collide (both "driver"),
    # which made the graph's RED_CHECK routing unable to tell accept from
    # reject: a rejected RED check was silently routed to Driver(GREEN) as
    # if it had passed.
    assert updated.phase == "driver_green"
    assert updated.status == RunStatus.IN_PROGRESS


def test_green_check_accept_advances_to_refactorer(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_passed(tmp_path, checkpoint=CheckpointType.GREEN_CHECK)
    assert updated.phase == "refactorer"
    assert updated.status == RunStatus.IN_PROGRESS


def test_refactor_check_accept_marks_done(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_passed(tmp_path, checkpoint=CheckpointType.REFACTOR_CHECK)
    assert updated.phase == "done"
    assert updated.status == RunStatus.DONE


def test_red_check_reject_retries_driver(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_passed(tmp_path, checkpoint=CheckpointType.RED_CHECK)  # unexpected pass = reject
    # Was "driver" (same as the accept value above); now "driver_red" so
    # the graph can actually route reject differently from accept.
    assert updated.phase == "driver_red"


def test_green_check_reject_retries_driver(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, checkpoint=CheckpointType.GREEN_CHECK)
    assert updated.phase == "driver"


def test_refactor_check_reject_retries_refactorer(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, checkpoint=CheckpointType.REFACTOR_CHECK)
    assert updated.phase == "refactorer"


# --- per-checkpoint rejection counters (ADR-009) ---

def test_rejection_cause_recorded_with_checkpoint_and_cycle_number(tmp_path):
    (tmp_path / "src").mkdir()
    updated = fake_failed(tmp_path, cause="off-by-one", checkpoint=CheckpointType.GREEN_CHECK)
    record = updated.reviewer_rejection_history[0]
    assert record.cause == "off-by-one"
    assert record.checkpoint == CheckpointType.GREEN_CHECK
    assert record.cycle_number == 1


def test_cycle_number_increments_within_same_checkpoint_type(tmp_path):
    (tmp_path / "src").mkdir()
    state = make_state(
        rejection_history=[
            RejectionRecord(cause="prior", cycle_number=1, checkpoint=CheckpointType.GREEN_CHECK)
        ]
    )
    updated = fake_failed(tmp_path, state=state, checkpoint=CheckpointType.GREEN_CHECK)
    green_records = [r for r in updated.reviewer_rejection_history if r.checkpoint == CheckpointType.GREEN_CHECK]
    assert len(green_records) == 2
    assert green_records[1].cycle_number == 2


def test_cycle_number_does_not_share_counter_across_checkpoint_types(tmp_path):
    """Core AC: a rejection during RED-check and one during GREEN-check
    must not share a counter, even on the same ticket."""
    (tmp_path / "src").mkdir()

    state = make_state(
        rejection_history=[
            RejectionRecord(cause="red cause", cycle_number=1, checkpoint=CheckpointType.RED_CHECK),
            RejectionRecord(cause="red cause 2", cycle_number=2, checkpoint=CheckpointType.RED_CHECK),
        ]
    )
    # A GREEN-check rejection now -- should be cycle 1 for GREEN_CHECK,
    # not cycle 3 (which a shared/global counter would produce).
    updated = fake_failed(tmp_path, state=state, checkpoint=CheckpointType.GREEN_CHECK)
    new_record = updated.reviewer_rejection_history[-1]
    assert new_record.checkpoint == CheckpointType.GREEN_CHECK
    assert new_record.cycle_number == 1


# --- pre-existing BTN-6 behaviors, retained ---

def test_run_reviewer_never_writes_anything(tmp_path):
    import battalion.nodes.reviewer as reviewer_module

    captured = {}
    real_build = reviewer_module.build_write_tools

    def spy(node_name, write_scope, base_dir=".", on_violation=None):
        tools = real_build(node_name, write_scope, base_dir, on_violation)
        captured["tool_keys"] = set(tools.keys())
        return tools

    (tmp_path / "src").mkdir()
    orig = reviewer_module.build_write_tools
    reviewer_module.build_write_tools = spy
    try:
        fake_passed(tmp_path, checkpoint=CheckpointType.GREEN_CHECK)
    finally:
        reviewer_module.build_write_tools = orig

    assert captured["tool_keys"] == set()


def test_run_reviewer_raises_if_scope_misconfigured_non_empty(tmp_path):
    (tmp_path / "src").mkdir()
    state = make_state(write_scope={"architect": ["plan.md"], "driver": ["src/"], "reviewer": ["oops.md"]})

    with pytest.raises(WriteScopeMisconfigured):
        fake_passed(tmp_path, state=state, checkpoint=CheckpointType.GREEN_CHECK)


def test_run_reviewer_rejects_empty_cause_content(tmp_path):
    (tmp_path / "src").mkdir()

    with pytest.raises(EmptyReviewContent):
        fake_failed(tmp_path, cause="   ", checkpoint=CheckpointType.GREEN_CHECK)


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
        checkpoint=CheckpointType.GREEN_CHECK,
        make_clean_copy_fn=lambda src: tmp_path / "clean-copy",
        run_tests_fn=lambda clean_dir: TestRunResult(passed=False, output="fail", returncode=1),
        call_llm_fn=fake_call_llm,
    )

    assert "Reviewer" in captured["system_content"]


def test_run_reviewer_raises_clear_error_when_src_missing(tmp_path):
    state = make_state()

    with pytest.raises(SourceTreeMissing):
        fake_passed(tmp_path / "missing-project", state=state, checkpoint=CheckpointType.GREEN_CHECK)


def test_reviewer_copies_configured_project_root_not_src(tmp_path):
    (tmp_path / "battalion").mkdir()
    (tmp_path / "tests").mkdir()
    captured = {}

    def capture_root(root):
        captured["root"] = root
        return tmp_path / "clean"

    run_reviewer(
        make_state(write_scope={
            "driver_red": ["tests/"],
            "driver_green": ["battalion/"],
            "refactorer": ["battalion/"],
            "reviewer": [],
        }),
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        checkpoint=CheckpointType.GREEN_CHECK,
        make_clean_copy_fn=capture_root,
        run_tests_fn=lambda clean: TestRunResult(passed=True, output="ok", returncode=0),
        call_llm_fn=lambda *a, **kw: litellm_response("unused"),
    )
    assert captured["root"] == tmp_path


def test_run_reviewer_cleans_up_temp_clean_copy_dir():
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
            checkpoint=CheckpointType.GREEN_CHECK,
            call_llm_fn=lambda *a, **kw: litellm_response("unused"),
            make_clean_copy_fn=spying_make_clean_copy,
        )

        assert len(created_dirs) == 1
        assert not created_dirs[0].exists()


# --- tests for the real default helpers (no mocking) ---

def test_make_clean_copy_is_independent_of_original(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.py").write_text("x = 1")

    clean = make_clean_copy(src)

    assert clean != src
    assert (clean / "module.py").read_text() == "x = 1"

    (src / "module.py").write_text("x = 2")
    assert (clean / "module.py").read_text() == "x = 1"


def test_make_clean_copy_excludes_local_runtime_metadata(tmp_path):
    (tmp_path / "battalion").mkdir()
    (tmp_path / "battalion" / "module.py").write_text("VALUE = 1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("local metadata")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "large.bin").write_text("generated")

    clean = make_clean_copy(tmp_path)

    assert (clean / "battalion" / "module.py").exists()
    assert not (clean / ".git").exists()
    assert not (clean / ".venv").exists()


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


def test_run_reviewer_end_to_end_with_real_defaults_green_check(tmp_path):
    """Full pipeline, no mocked test execution."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "test_thing.py").write_text("def test_thing():\n    assert 1 + 1 == 2\n")

    updated = run_reviewer(
        make_state(),
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        checkpoint=CheckpointType.GREEN_CHECK,
        call_llm_fn=lambda *a, **kw: litellm_response("unused on accept"),
    )

    assert updated.phase == "refactorer"
    assert updated.status == RunStatus.IN_PROGRESS


def test_run_reviewer_end_to_end_with_real_defaults_red_check(tmp_path):
    """Real subprocess execution of a genuinely failing test, checked as
    a RED-check -- should be accepted."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "test_thing.py").write_text("def test_thing():\n    assert False\n")

    updated = run_reviewer(
        make_state(),
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        checkpoint=CheckpointType.RED_CHECK,
        call_llm_fn=lambda *a, **kw: litellm_response("unused on accept"),
    )

    assert updated.phase == "driver_green"
    assert updated.reviewer_rejection_history == []


def test_run_reviewer_real_defaults_support_root_level_tests_layout(tmp_path):
    (tmp_path / "battalion").mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text("def test_thing():\n    assert True\n")

    updated = run_reviewer(
        make_state(write_scope={
            "driver_red": ["tests/"],
            "driver_green": ["battalion/"],
            "refactorer": ["battalion/"],
            "reviewer": [],
        }),
        base_dir=tmp_path,
        llm_config=NodeLLMConfig(model="test-model"),
        checkpoint=CheckpointType.GREEN_CHECK,
        call_llm_fn=lambda *a, **kw: litellm_response("unused on accept"),
    )

    assert updated.phase == "refactorer"

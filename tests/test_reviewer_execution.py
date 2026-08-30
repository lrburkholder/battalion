"""BTN-164: trusted pytest evidence, bounded lifecycle, and materialization."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from battalion.config import BattalionConfig, load_config
from battalion.desktop.presentation import render_execution
from battalion.graph import _make_reviewer_node, build_graph
from battalion.nodes.reviewer import run_reviewer
from battalion.reviewer_testing import (
    MAX_CAPTURE_BYTES,
    TestRunResult,
    _classify_pytest_result,
    make_clean_copy,
    run_tests_via_subprocess,
)
from battalion.state.models import (
    CheckpointType,
    RunState,
    RunStatus,
    TestExecutionClassification as Classification,
)
from battalion.state.persistence import load_state, save_state
from conftest import make_llm_configs, make_run_state


def _junit(path: Path, tests=1, failures=0, errors=0) -> Path:
    path.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" '
        f'errors="{errors}"><testcase name="test_example"/></testsuite></testsuites>',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("code", "tests", "failures", "errors", "expected"),
    [
        (0, 1, 0, 0, Classification.PASSED),
        (1, 1, 1, 0, Classification.TEST_FAILED),
        (2, 1, 0, 1, Classification.PYTEST_ERROR),
        (3, 0, 0, 0, Classification.PYTEST_ERROR),
        (4, 0, 0, 0, Classification.PYTEST_ERROR),
        (5, 0, 0, 0, Classification.NO_TESTS_COLLECTED),
        (0, 0, 0, 0, Classification.NO_TESTS_COLLECTED),
        (1, 1, 0, 1, Classification.PYTEST_ERROR),
        (1, 2, 1, 1, Classification.PYTEST_ERROR),
        (1, 1, 0, 0, Classification.PYTEST_ERROR),
        (0, 1, 1, 0, Classification.PYTEST_ERROR),
        (6, 1, 0, 0, Classification.INVALID_EXIT),
        (-9, 1, 0, 0, Classification.INVALID_EXIT),
    ],
)
def test_pytest_exit_matrix(tmp_path, code, tests, failures, errors, expected):
    report = _junit(tmp_path / "report.xml", tests, failures, errors)
    classification, collected, *_ = _classify_pytest_result(code, report)
    assert classification is expected
    assert collected == tests


@pytest.mark.parametrize("content", ["", "<broken", "<testsuite/>",
                                         '<testsuite tests="-1"/>'])
@pytest.mark.parametrize("code", [0, 1])
def test_malformed_structured_output_never_becomes_test_evidence(tmp_path, content, code):
    report = tmp_path / "report.xml"
    report.write_text(content, encoding="utf-8")
    result = _classify_pytest_result(code, report)
    assert result[0] is Classification.MALFORMED_OUTPUT
    assert result[-1]


def test_missing_structured_output_is_typed_as_malformed(tmp_path):
    assert _classify_pytest_result(0, tmp_path / "missing.xml")[0] is Classification.MALFORMED_OUTPUT


def test_real_no_tests_and_collection_error_are_not_red_evidence(tmp_path):
    empty = run_tests_via_subprocess(tmp_path)
    assert empty.classification is Classification.NO_TESTS_COLLECTED
    assert empty.returncode == 5
    (tmp_path / "test_syntax.py").write_text("def test_broken(:\n", encoding="utf-8")
    broken = run_tests_via_subprocess(tmp_path)
    assert broken.classification is Classification.PYTEST_ERROR
    assert broken.returncode == 2
    assert "SyntaxError" in broken.output


def test_fixture_setup_error_is_not_a_collected_assertion_failure(tmp_path):
    (tmp_path / "test_setup.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def broken():\n    raise RuntimeError('fixture infrastructure failed')\n"
        "def test_example(broken):\n    assert True\n",
        encoding="utf-8",
    )
    result = run_tests_via_subprocess(tmp_path)
    assert result.returncode == 1
    assert result.classification is Classification.PYTEST_ERROR
    assert result.errors == 1


def test_launch_failure_retains_command_and_directory(tmp_path, monkeypatch):
    def cannot_launch(*args, **kwargs):
        raise OSError("interpreter unavailable")

    monkeypatch.setattr("battalion.reviewer_testing.subprocess.Popen", cannot_launch)
    result = run_tests_via_subprocess(tmp_path)
    evidence = result.to_evidence()
    assert evidence.classification is Classification.PROCESS_LAUNCH_FAILED
    assert evidence.returncode is None
    assert evidence.command[:4] == [sys.executable, "-m", "pytest", "-q"]
    assert evidence.working_directory == str(tmp_path.resolve())
    assert evidence.detail == "interpreter unavailable"


def _is_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A terminated orphan can remain a zombie until the host's reaper runs.
    stat_path = Path(f"/proc/{pid}/stat")
    return not (stat_path.exists() and stat_path.read_text().split()[2] == "Z")


@pytest.mark.parametrize("cancel", [False, True])
def test_hanging_pytest_and_descendant_are_cleaned_up(tmp_path, cancel):
    child_pid = tmp_path / "child.pid"
    (tmp_path / "test_hang.py").write_text(
        "import pathlib, subprocess, sys, time\n"
        "def test_hang():\n"
        "    child = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])\n"
        "    pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "    time.sleep(120)\n",
        encoding="utf-8",
    )
    result = run_tests_via_subprocess(
        tmp_path,
        timeout_seconds=5,
        cancellation_requested=child_pid.exists if cancel else None,
    )
    assert result.classification is (Classification.CANCELLED if cancel else Classification.TIMED_OUT)
    assert result.duration_ms < 12_000
    assert result.cleanup_attempted
    assert result.cleanup_succeeded
    assert child_pid.exists(), result.output
    assert not _is_alive(int(child_pid.read_text()))
    assert result.to_evidence().cancelled is cancel


def test_prelaunch_cancellation_does_not_spawn_a_process(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "battalion.reviewer_testing.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("cancelled execution must not launch"),
    )
    result = run_tests_via_subprocess(tmp_path, cancellation_requested=lambda: True)
    assert result.classification is Classification.CANCELLED
    assert result.returncode is None
    assert not result.cleanup_attempted
    result.to_evidence()


@pytest.mark.parametrize("classification", [Classification.PASSED, Classification.TEST_FAILED])
def test_persisted_valid_outcomes_require_collected_test_evidence(classification):
    with pytest.raises(ValueError, match="requires collected tests"):
        _invalid_result(classification).to_evidence()


def test_stdout_and_stderr_are_bounded_with_truncation_evidence(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -s\n", encoding="utf-8")
    (tmp_path / "test_output.py").write_text(
        "import os\n"
        "def test_output():\n"
        "    os.write(1, b'x' * 100000)\n"
        "    os.write(2, b'y' * 100000)\n",
        encoding="utf-8",
    )
    result = run_tests_via_subprocess(tmp_path)
    evidence = result.to_evidence()
    assert result.classification is Classification.PASSED
    assert len(evidence.stdout) == MAX_CAPTURE_BYTES
    assert len(evidence.stderr) == MAX_CAPTURE_BYTES
    assert evidence.stdout_observed_bytes >= 100000
    assert evidence.stderr_observed_bytes == 100000
    assert evidence.stdout_truncated and evidence.stderr_truncated


def test_git_materialization_admits_tracked_and_nonignored_untracked_files(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    (tmp_path / ".gitignore").write_text("ignored.txt\ntracked.txt\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked input", encoding="utf-8")
    (tmp_path / "candidate.py").write_text("candidate = True", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("not admitted", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "generated.bin").write_bytes(b"generated")
    git("add", "-f", "tracked.txt", "dist/generated.bin")
    clean = make_clean_copy(tmp_path)
    try:
        assert (clean / "tracked.txt").read_text() == "tracked input"
        assert (clean / "candidate.py").is_file()
        assert not (clean / "ignored.txt").exists()
        assert not (clean / "dist").exists()
        assert not (clean / ".git").exists()
    finally:
        shutil.rmtree(clean)


@pytest.mark.parametrize("directory", ["build", "dist", ".battalion", ".venv", "venv",
                                       "node_modules", ".pytest_cache", "thing.egg-info"])
def test_non_git_materialization_excludes_generated_content(tmp_path, directory):
    (tmp_path / directory).mkdir()
    (tmp_path / directory / "generated.py").write_text("not_input = True", encoding="utf-8")
    (tmp_path / "project.py").write_text("input = True", encoding="utf-8")
    clean = make_clean_copy(tmp_path)
    try:
        assert (clean / "project.py").is_file()
        assert not (clean / directory).exists()
    finally:
        shutil.rmtree(clean)


def _invalid_result(classification: Classification) -> TestRunResult:
    return TestRunResult(
        classification=classification, command=("python", "-m", "pytest", "-q"),
        working_directory="clean-project-root", returncode=None,
        tests_collected=None, failures=None, errors=None,
        stdout="bounded evidence", stderr="harness unavailable",
        stdout_observed_bytes=16, stderr_observed_bytes=19,
        duration_ms=2, timeout_seconds=5,
        timed_out=classification is Classification.TIMED_OUT,
        cancelled=classification is Classification.CANCELLED,
    )


@pytest.mark.parametrize("checkpoint", list(CheckpointType))
@pytest.mark.parametrize("classification", [item for item in Classification if item not in {
    Classification.PASSED, Classification.TEST_FAILED,
}])
def test_invalid_execution_pauses_exact_checkpoint_and_persists_evidence(
    tmp_path, monkeypatch, checkpoint, classification,
):
    result = _invalid_result(classification)
    original = run_reviewer

    def reviewer(**kwargs):
        return original(
            **kwargs,
            make_clean_copy_fn=lambda root: tmp_path / "clean",
            run_tests_fn=lambda root: result,
            call_llm_fn=lambda *a, **kw: pytest.fail("harness failures are not LLM judgments"),
        )

    monkeypatch.setattr("battalion.nodes.reviewer.run_reviewer", reviewer)
    node = {
        CheckpointType.RED_CHECK: "reviewer_red",
        CheckpointType.GREEN_CHECK: "reviewer_green",
        CheckpointType.REFACTOR_CHECK: "reviewer_refactor",
    }[checkpoint]
    initial = make_run_state().model_copy(update={"resume_target": node, "phase": node})
    final = RunState.model_validate(
        build_graph(make_llm_configs(), base_dir=tmp_path).compile().invoke(initial)
    )
    assert final.status is RunStatus.AWAITING_HUMAN
    assert final.interrupt_log[-1].trigger == "infra-failure"
    assert final.interrupt_log[-1].context["next_phase"] == node
    assert final.reviewer_rejection_history == []
    execution = final.execution_record.node_executions[-1]
    assert execution.outcome == "interrupted"
    assert execution.attempt_disposition == "infra-failure"
    assert execution.test_execution.classification is classification
    assert execution.review_result.verdict == "unavailable"
    assert not execution.test_outcome.accepted
    assert execution.tool_activity[-1].outcome == "failed"
    rendered = render_execution(execution)
    assert f"Pytest classification: {classification.value}" in rendered
    assert "bounded evidence" in rendered
    assert "harness unavailable" in rendered
    state_path = tmp_path / "paused.json"
    save_state(final, state_path)
    assert load_state(state_path) == final


@pytest.mark.parametrize("checkpoint", list(CheckpointType))
@pytest.mark.parametrize("passed", [False, True])
def test_checkpoint_routing_uses_actual_execution_and_configured_timeout(
    tmp_path, monkeypatch, checkpoint, passed,
):
    classification = Classification.PASSED if passed else Classification.TEST_FAILED
    result = replace(
        _invalid_result(classification), returncode=0 if passed else 1,
        tests_collected=1, failures=0 if passed else 1, errors=0, timeout_seconds=17,
    )
    original = run_reviewer

    def reviewer(**kwargs):
        assert kwargs["test_timeout_seconds"] == 17
        return original(
            **kwargs,
            make_clean_copy_fn=lambda root: tmp_path / "clean",
            run_tests_fn=lambda root: result,
            call_llm_fn=lambda *a, **kw: {"choices": [{"message": {"content": "wrong result"}}]},
        )

    monkeypatch.setattr("battalion.nodes.reviewer.run_reviewer", reviewer)
    node = _make_reviewer_node(
        checkpoint, make_llm_configs(), str(tmp_path), reviewer_test_timeout_seconds=17,
    )
    final = node(make_run_state())
    execution = final.execution_record.node_executions[-1]
    accepted = passed == (checkpoint is not CheckpointType.RED_CHECK)
    assert execution.test_execution.classification is classification
    assert execution.test_execution.timeout_seconds == 17
    assert execution.test_outcome.passed is passed
    assert execution.test_outcome.accepted is accepted
    assert execution.review_result.verdict == ("accepted" if accepted else "rejected")
    expected_phase = {
        (CheckpointType.RED_CHECK, True): "driver_green",
        (CheckpointType.RED_CHECK, False): "driver_red",
        (CheckpointType.GREEN_CHECK, True): "refactorer",
        (CheckpointType.GREEN_CHECK, False): "driver",
        (CheckpointType.REFACTOR_CHECK, True): "done",
        (CheckpointType.REFACTOR_CHECK, False): "refactorer",
    }[checkpoint, accepted]
    assert final.phase == expected_phase


def test_reviewer_timeout_configuration_is_loaded_and_bounded(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("reviewer_test_timeout_seconds: 17\n", encoding="utf-8")
    assert load_config(path).reviewer_test_timeout_seconds == 17
    for value in [0, -1, 3601, float("inf"), float("nan")]:
        with pytest.raises(ValueError):
            BattalionConfig(reviewer_test_timeout_seconds=value)
        with pytest.raises(ValueError):
            run_tests_via_subprocess(tmp_path, timeout_seconds=value)


@pytest.mark.parametrize("checkpoint", list(CheckpointType))
def test_post_review_interrupt_preserves_the_actual_accepted_decision(
    tmp_path, monkeypatch, checkpoint,
):
    passed = checkpoint is not CheckpointType.RED_CHECK
    result = replace(
        _invalid_result(Classification.PASSED if passed else Classification.TEST_FAILED),
        returncode=0 if passed else 1, tests_collected=1,
        failures=0 if passed else 1, errors=0,
    )
    original = run_reviewer

    def reviewer(**kwargs):
        return original(
            **kwargs,
            make_clean_copy_fn=lambda root: tmp_path / "clean",
            run_tests_fn=lambda root: result,
        )

    monkeypatch.setattr("battalion.nodes.reviewer.run_reviewer", reviewer)
    next_phase = {
        CheckpointType.RED_CHECK: "driver_green",
        CheckpointType.GREEN_CHECK: "refactorer",
        CheckpointType.REFACTOR_CHECK: "done",
    }[checkpoint]
    initial = make_run_state().model_copy(update={"manual_checkpoints": [next_phase]})
    node = _make_reviewer_node(checkpoint, make_llm_configs(), str(tmp_path))
    paused = node(initial)
    execution = paused.execution_record.node_executions[-1]
    assert paused.status is RunStatus.AWAITING_HUMAN
    assert execution.outcome == "interrupted"
    assert execution.test_outcome.passed is passed
    assert execution.test_outcome.accepted
    assert execution.review_result.verdict == "accepted"


def test_rejection_model_failure_does_not_fabricate_a_review_verdict(tmp_path, monkeypatch):
    from battalion.llm.litellm_client import InfraFailure

    result = replace(
        _invalid_result(Classification.TEST_FAILED),
        returncode=1, tests_collected=1, failures=1, errors=0,
    )
    original = run_reviewer

    def failed_llm(*args, **kwargs):
        raise InfraFailure(
            "reviewer", "test-model", 1, RuntimeError("review explanation unavailable")
        )

    def reviewer(**kwargs):
        return original(
            **kwargs,
            make_clean_copy_fn=lambda root: tmp_path / "clean",
            run_tests_fn=lambda root: result,
            call_llm_fn=failed_llm,
        )

    monkeypatch.setattr("battalion.nodes.reviewer.run_reviewer", reviewer)
    node = _make_reviewer_node(CheckpointType.GREEN_CHECK, make_llm_configs(), str(tmp_path))
    paused = node(make_run_state())
    execution = paused.execution_record.node_executions[-1]
    assert execution.test_execution.classification is Classification.TEST_FAILED
    assert execution.review_result.verdict == "unavailable"
    assert execution.attempt_disposition == "infra-failure"
    assert execution.tool_activity[-1].outcome == "succeeded"

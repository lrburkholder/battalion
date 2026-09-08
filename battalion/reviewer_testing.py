"""Bounded pytest execution and explicit Reviewer workspace materialization."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from battalion.nodes.errors import RoleOutputError
from battalion.state.models import TestExecutionClassification, TestExecutionEvidence

DEFAULT_TEST_TIMEOUT_SECONDS = 300.0
MAX_CAPTURE_BYTES = 65_536
MAX_JUNIT_BYTES = 8_388_608
_JUNIT_FILENAME = ".battalion-reviewer-junit.xml"
_EXCLUDED_DIRECTORY_NAMES = frozenset({
    ".battalion", ".cache", ".env", ".git", ".hg", ".mypy_cache", ".nox", ".pytest_cache",
    ".ruff_cache", ".svn", ".tox", ".venv", "__pycache__", "build",
    "coverage", "dist", "env", "htmlcov", "node_modules", "target", "venv",
})
_EXCLUDED_FILE_NAMES = frozenset({".coverage", _JUNIT_FILENAME})
_EXCLUDED_SUFFIXES = (".egg-info", ".pyc", ".pyo")


@dataclass
class TestRunResult:
    __test__ = False  # tell pytest this isn't a test class despite the name
    classification: TestExecutionClassification
    command: tuple[str, ...]
    working_directory: str
    returncode: int | None
    tests_collected: int | None
    failures: int | None
    errors: int | None
    stdout: str
    stderr: str
    stdout_observed_bytes: int
    stderr_observed_bytes: int
    duration_ms: int
    timeout_seconds: float
    timed_out: bool = False
    cancelled: bool = False
    cleanup_attempted: bool = False
    cleanup_succeeded: bool | None = None
    detail: str | None = None

    @property
    def passed(self) -> bool:
        return self.classification is TestExecutionClassification.PASSED

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def to_evidence(self) -> TestExecutionEvidence:
        return TestExecutionEvidence(
            command=list(self.command),
            working_directory=self.working_directory,
            classification=self.classification,
            returncode=self.returncode,
            tests_collected=self.tests_collected,
            failures=self.failures,
            errors=self.errors,
            stdout=self.stdout,
            stderr=self.stderr,
            stdout_observed_bytes=self.stdout_observed_bytes,
            stderr_observed_bytes=self.stderr_observed_bytes,
            stdout_truncated=self.stdout_observed_bytes > MAX_CAPTURE_BYTES,
            stderr_truncated=self.stderr_observed_bytes > MAX_CAPTURE_BYTES,
            duration_ms=self.duration_ms,
            timeout_seconds=self.timeout_seconds,
            timed_out=self.timed_out,
            cancelled=self.cancelled,
            cleanup_attempted=self.cleanup_attempted,
            cleanup_succeeded=self.cleanup_succeeded,
            detail=self.detail,
        )


class ReviewerTestExecutionError(RoleOutputError):
    """Pytest could not produce trustworthy checkpoint evidence."""

    def __init__(self, result: TestRunResult) -> None:
        self.result = result
        detail = f": {result.detail}" if result.detail else ""
        super().__init__(
            "Reviewer test execution produced "
            f"{result.classification.value}{detail}"
        )


class WorkspaceMaterializationError(RoleOutputError):
    """Reviewer could not create its explicit clean project snapshot."""


def _is_admitted(relative: Path) -> bool:
    parts = relative.parts
    if any(
        part.lower() in _EXCLUDED_DIRECTORY_NAMES
        or part.lower().endswith(".egg-info")
        for part in parts
    ):
        return False
    name = relative.name.lower()
    return (
        name not in _EXCLUDED_FILE_NAMES
        and not name.endswith(_EXCLUDED_SUFFIXES)
    )


def _git_materialization_files(root: Path) -> list[Path] | None:
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if (root / ".git").exists():
            raise WorkspaceMaterializationError(
                f"could not inspect Git project inputs: {exc}"
            ) from exc
        return None
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        return None
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspaceMaterializationError(
            f"could not enumerate tracked and admitted untracked inputs: {exc}"
        ) from exc
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", errors="replace")[:1000]
        raise WorkspaceMaterializationError(
            f"git ls-files failed while materializing Reviewer inputs: {detail}"
        )
    return [Path(raw.decode("utf-8", errors="surrogateescape"))
            for raw in listed.stdout.split(b"\0") if raw]


def _materialization_files(root: Path) -> list[Path]:
    git_files = _git_materialization_files(root)
    candidates = git_files
    if candidates is None:
        candidates = []
        for directory, directories, files in os.walk(root):
            directories[:] = [
                name for name in directories
                if name.lower() not in _EXCLUDED_DIRECTORY_NAMES
                and not name.lower().endswith(".egg-info")
                and not (Path(directory) / name / "pyvenv.cfg").is_file()
            ]
            candidates.extend(
                (Path(directory) / name).relative_to(root) for name in files
            )
    return sorted(
        {path for path in candidates if _is_admitted(path)},
        key=lambda path: path.as_posix(),
    )


def make_clean_copy(src_dir: str | Path) -> Path:
    """Copy src_dir into a fresh temporary directory, independent of the
    original — this is the "clean tree" in clean-tree re-verification.

    Repository metadata, local environments, and generated caches are not
    project inputs and can make root-level copies prohibitively large.
    """
    root = Path(src_dir).resolve()
    dest = Path(tempfile.mkdtemp(prefix="battalion-clean-"))
    try:
        for relative in _materialization_files(root):
            source = root / relative
            resolved = source.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, target)
    except OSError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise WorkspaceMaterializationError(
            f"could not materialize Reviewer project inputs: {exc}"
        ) from exc
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def _bounded_output(handle) -> tuple[str, int]:
    handle.flush()
    observed = handle.seek(0, os.SEEK_END)
    handle.seek(0)
    content = handle.read(MAX_CAPTURE_BYTES)
    return content.decode("utf-8", errors="replace"), observed


def _terminate_process_tree(
    process: subprocess.Popen, grace_seconds: float = 2.0
) -> tuple[bool, bool | None]:
    if process.poll() is not None:
        return False, None
    tree_cleanup_succeeded = True
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=grace_seconds,
            )
            tree_cleanup_succeeded = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            tree_cleanup_succeeded = False
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
        # The group may outlive its leader; terminate remaining descendants
        # even when pytest itself responded to SIGTERM immediately.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        return True, False
    return True, tree_cleanup_succeeded and process.poll() is not None


def _junit_counts(path: Path) -> tuple[int, int, int]:
    if path.stat().st_size > MAX_JUNIT_BYTES:
        raise ValueError(f"JUnit output exceeds the {MAX_JUNIT_BYTES}-byte limit")
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError(f"unexpected JUnit root element {root.tag!r}")
    summaries = [root] if "tests" in root.attrib else list(root.findall("testsuite"))
    if not summaries:
        raise ValueError("JUnit summary does not contain a test suite")
    try:
        counts = (
            sum(int(item.attrib["tests"]) for item in summaries),
            sum(int(item.attrib.get("failures", "0")) for item in summaries),
            sum(int(item.attrib.get("errors", "0")) for item in summaries),
        )
        if any(value < 0 for value in counts) or counts[1] + counts[2] > counts[0]:
            raise ValueError("JUnit summary contains inconsistent test counts")
        return counts
    except (KeyError, ValueError) as exc:
        raise ValueError("JUnit summary is missing valid test counts") from exc


def _classify_pytest_result(
    returncode: int, junit_path: Path
) -> tuple[TestExecutionClassification, int | None, int | None, int | None, str | None]:
    counts: tuple[int, int, int] | None = None
    parse_error: str | None = None
    if junit_path.is_file():
        try:
            counts = _junit_counts(junit_path)
        except (ET.ParseError, OSError, ValueError) as exc:
            parse_error = str(exc)
    elif returncode in {0, 1}:
        parse_error = "pytest did not produce the required JUnit result"

    tests, failures, errors = counts or (None, None, None)
    if returncode == 5:
        return TestExecutionClassification.NO_TESTS_COLLECTED, tests, failures, errors, None
    if returncode in {2, 3, 4}:
        return TestExecutionClassification.PYTEST_ERROR, tests, failures, errors, None
    if returncode not in {0, 1}:
        return (
            TestExecutionClassification.INVALID_EXIT, tests, failures, errors,
            f"pytest returned unsupported exit code {returncode}",
        )
    if parse_error is not None:
        return TestExecutionClassification.MALFORMED_OUTPUT, tests, failures, errors, parse_error
    assert tests is not None and failures is not None and errors is not None
    if tests == 0:
        return TestExecutionClassification.NO_TESTS_COLLECTED, tests, failures, errors, None
    if returncode == 0 and failures == 0 and errors == 0:
        return TestExecutionClassification.PASSED, tests, failures, errors, None
    if returncode == 1 and failures > 0 and errors == 0:
        return TestExecutionClassification.TEST_FAILED, tests, failures, errors, None
    return (
        TestExecutionClassification.PYTEST_ERROR, tests, failures, errors,
        "pytest exit code and JUnit counts do not describe a valid pass or test failure",
    )


def run_tests_via_subprocess(
    clean_dir: str | Path,
    *,
    timeout_seconds: float = DEFAULT_TEST_TIMEOUT_SECONDS,
    cancellation_requested: Callable[[], bool] | None = None,
) -> TestRunResult:
    """Run pytest in clean_dir via subprocess. This is the real, un-mocked
    default — Reviewer never takes Driver's word for pass/fail.

    Uses sys.executable (the interpreter running Battalion) rather than the
    bare "python" from PATH, so the clean-tree re-run uses the same Python
    that has Battalion's test dependencies installed regardless of which
    venv the caller activated."""
    if not 0 < timeout_seconds <= 3600:
        raise ValueError("Reviewer test timeout must be greater than 0 and at most 3600 seconds")
    root = Path(clean_dir).resolve()
    junit_path = root / _JUNIT_FILENAME
    command = (
        sys.executable, "-m", "pytest", "-q",
        f"--junitxml={junit_path}",
    )
    started = time.monotonic()
    if cancellation_requested is not None and cancellation_requested():
        return TestRunResult(
            classification=TestExecutionClassification.CANCELLED,
            command=command, working_directory=str(root), returncode=None,
            tests_collected=None, failures=None, errors=None, stdout="", stderr="",
            stdout_observed_bytes=0, stderr_observed_bytes=0,
            duration_ms=0, timeout_seconds=timeout_seconds, cancelled=True,
            detail="cancellation was requested before pytest launch",
        )

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        popen_kwargs = {
            "cwd": str(root),
            "stdout": stdout_file,
            "stderr": stderr_file,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except OSError as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            return TestRunResult(
                classification=TestExecutionClassification.PROCESS_LAUNCH_FAILED,
                command=command, working_directory=str(root), returncode=None,
                tests_collected=None, failures=None, errors=None, stdout="", stderr="",
                stdout_observed_bytes=0, stderr_observed_bytes=0,
                duration_ms=duration_ms, timeout_seconds=timeout_seconds,
                detail=str(exc)[:2000],
            )

        deadline = started + timeout_seconds
        classification: TestExecutionClassification | None = None
        detail: str | None = None
        cleanup_attempted = False
        cleanup_succeeded: bool | None = None
        try:
            while process.poll() is None:
                if cancellation_requested is not None and cancellation_requested():
                    classification = TestExecutionClassification.CANCELLED
                    detail = "cancellation was requested while pytest was running"
                    cleanup_attempted, cleanup_succeeded = _terminate_process_tree(process)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    classification = TestExecutionClassification.TIMED_OUT
                    detail = f"pytest exceeded the {timeout_seconds:g}-second timeout"
                    cleanup_attempted, cleanup_succeeded = _terminate_process_tree(process)
                    break
                try:
                    process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue

        except KeyboardInterrupt:
            classification = TestExecutionClassification.CANCELLED
            detail = "pytest execution was cancelled by keyboard interrupt"
            cleanup_attempted, cleanup_succeeded = _terminate_process_tree(process)
        except BaseException:
            _terminate_process_tree(process)
            raise
        stdout, stdout_observed = _bounded_output(stdout_file)
        stderr, stderr_observed = _bounded_output(stderr_file)
        duration_ms = round((time.monotonic() - started) * 1000)
        if classification is None:
            classification, tests, failures, errors, detail = _classify_pytest_result(
                process.returncode, junit_path
            )
        else:
            tests = failures = errors = None
        try:
            junit_path.unlink(missing_ok=True)
        except OSError:
            pass
        return TestRunResult(
            classification=classification,
            command=command,
            working_directory=str(root),
            returncode=process.returncode,
            tests_collected=tests,
            failures=failures,
            errors=errors,
            stdout=stdout,
            stderr=stderr,
            stdout_observed_bytes=stdout_observed,
            stderr_observed_bytes=stderr_observed,
            duration_ms=duration_ms,
            timeout_seconds=timeout_seconds,
            timed_out=classification is TestExecutionClassification.TIMED_OUT,
            cancelled=classification is TestExecutionClassification.CANCELLED,
            cleanup_attempted=cleanup_attempted,
            cleanup_succeeded=cleanup_succeeded,
            detail=detail,
        )

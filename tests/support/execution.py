"""Execution evidence builders; role, phase and classification stay explicit."""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal

from battalion.reviewer_testing import TestRunResult
from battalion.state.models import InterruptLogEntry, NodeExecution
from battalion.state.models import TestExecutionClassification as Classification

FIXTURE_TIME = datetime(2026, 8, 1, tzinfo=timezone.utc)


def make_node_execution(
    *, role: Literal["architect", "driver", "reviewer", "refactorer"],
    phase: str, execution_id: str = "node-test", model_identity: str = "test-model",
    **overrides: Any,
) -> NodeExecution:
    unknown = overrides.keys() - NodeExecution.model_fields.keys()
    if unknown:
        raise TypeError(f"Unknown NodeExecution overrides: {sorted(unknown)}")
    fields = dict(
        execution_id=execution_id, role=role, phase=phase, model_identity=model_identity,
        started_at=FIXTURE_TIME, ended_at=FIXTURE_TIME, outcome="succeeded",
    )
    fields.update(overrides)
    return NodeExecution(**deepcopy(fields))


def make_interrupt(trigger: str, **overrides: Any) -> InterruptLogEntry:
    unknown = overrides.keys() - InterruptLogEntry.model_fields.keys()
    if unknown:
        raise TypeError(f"Unknown InterruptLogEntry overrides: {sorted(unknown)}")
    fields = dict(trigger=trigger, timestamp=FIXTURE_TIME)
    fields.update(overrides)
    return InterruptLogEntry(**deepcopy(fields))


def make_test_result(
    classification: Classification, output: str, returncode: int | None,
    **overrides: Any,
) -> TestRunResult:
    """Construct process evidence without running pytest or guessing a verdict."""
    collected = 1 if classification in {Classification.PASSED, Classification.TEST_FAILED} else None
    fields = dict(
        classification=classification, command=("python", "-m", "pytest"),
        working_directory="clean-project-root", returncode=returncode,
        tests_collected=collected, failures=1 if classification is Classification.TEST_FAILED else 0,
        errors=0, stdout=output, stderr="", stdout_observed_bytes=len(output.encode()),
        stderr_observed_bytes=0, duration_ms=1, timeout_seconds=300,
    )
    fields.update(overrides)
    return TestRunResult(**deepcopy(fields))

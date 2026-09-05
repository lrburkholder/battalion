"""Desktop rendering of durable execution evidence."""


from __future__ import annotations


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


from battalion.desktop.presentation import render_execution
from battalion.state.models import (
    CheckpointType,
    ReviewResult,
    RoleContractViolationEvidence,
    TestExecutionClassification as Classification,
    TestExecutionEvidence as ExecutionTestEvidence,
)
from support.desktop import (
    _execution,
)


def test_execution_inspector_exposes_provenance_verification_and_cost_semantics():
    rendered = render_execution(_execution())

    assert "Role: driver" in rendered
    assert "Phase: driver_green" in rendered
    assert "Model: provider/driver-model" in rendered
    assert "Contract version: driver/v1" in rendered
    assert f"Template hash (sha256): {'3' * 64}" in rendered
    assert f"Battalion revision: {'4' * 40}" in rendered
    assert f"Base revision (sha1): {'6' * 40}" in rendered
    assert "Dirty at end: yes" in rendered
    assert "Exact workspace reconstructable: no" in rendered
    assert "approved implementation context" in rendered
    assert "battalion/desktop/app.py · sha256" in rendered
    assert "Tests: green-check" in rendered
    assert "Review: green-check · accepted" in rendered
    assert "input=120 · output=45 · cost=0.125 USD · source=provider-reported" in rendered
    assert "unknown-call" in rendered and "cost=unknown · source=unknown" in rendered


def test_execution_inspector_exposes_reviewer_process_evidence():
    execution = _execution().model_copy(update={
        "role": "reviewer",
        "phase": "reviewer_green",
        "review_result": ReviewResult(
            checkpoint=CheckpointType.GREEN_CHECK,
            verdict="unavailable",
        ),
        "test_execution": ExecutionTestEvidence(
            command=["python", "-m", "pytest", "-q"],
            working_directory="clean-project-root",
            classification=Classification.TIMED_OUT,
            stdout="bounded evidence",
            stderr="harness unavailable",
            stdout_observed_bytes=16,
            stderr_observed_bytes=19,
            duration_ms=5000,
            timeout_seconds=5,
            timed_out=True,
            cleanup_attempted=True,
            cleanup_succeeded=True,
        ),
    })

    rendered = render_execution(execution)

    assert "Review: green-check · unavailable" in rendered
    assert "Pytest classification: timeout" in rendered
    assert "Working directory: clean-project-root" in rendered
    assert "Duration: 5000 ms; timeout: 5 s" in rendered
    assert "Cleanup attempted: yes; succeeded: yes" in rendered
    assert "bounded evidence" in rendered
    assert "harness unavailable" in rendered


def test_execution_inspector_exposes_nonblocking_contract_correction_evidence():
    execution = _execution().model_copy(update={
        "outcome": "rejected",
        "attempt_disposition": "corrected",
        "role_contract_violation": RoleContractViolationEvidence(
            reason_code="driver-mode-artifact",
            detail="GREEN mode must not produce test files",
            offending_paths=["tests/test_widget.py"],
            attempt_number=1,
            resulting_disposition="retry",
        ),
    })

    rendered = render_execution(execution)

    assert "Attempt disposition: corrected" in rendered
    assert "ROLE-CONTRACT CORRECTION" in rendered
    assert "Mutation applied: no" in rendered
    assert "Disposition: retry" in rendered
    assert "tests/test_widget.py" in rendered

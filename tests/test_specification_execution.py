"""Credential-free lifecycle coverage for BTN-228."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from battalion.specification_execution import (
    BlockResolutionKind, SpecificationExecutionError, SpecificationExecutionNotFound,
    SpecificationExecutionRepository,
    SpecificationOutcome, SpecificationPhase, UnresolvedSpecificationDependency,
    WorkflowBlock, WorkflowEvidence, WorkflowExecutionStatus, await_human_review,
    block_for_prerequisite, cancel_specification_execution, complete_specification_execution,
    fail_specification_execution, pause_specification_execution, recheck_blocked_prerequisite,
    record_execution_evidence, resume_specification_execution, set_specification_phase,
    start_specification_execution,
)
from battalion.specifications import (
    SpecificationNotFoundError,
    SpecificationRepository,
    SpecificationRevisionNotFoundError,
)


NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
SPEC_ID = UUID("00000000-0000-0000-0000-000000000001")
CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000002")


def _specification(tmp_path):
    repository = SpecificationRepository(tmp_path)
    try:
        return repository.get(SPEC_ID)
    except SpecificationNotFoundError:
        return repository.create(
            "work-item:BTN-228", specification_id=SPEC_ID, now=lambda: NOW
        )


def _candidate(tmp_path):
    _specification(tmp_path)
    repository = SpecificationRepository(tmp_path)
    try:
        return repository.get_revision(SPEC_ID, CANDIDATE_ID)
    except SpecificationRevisionNotFoundError:
        return repository.create_candidate(
            SPEC_ID, revision_id=CANDIDATE_ID, now=lambda: NOW
        )


def _execution(tmp_path):
    _specification(tmp_path)
    return start_specification_execution(SPEC_ID, project_root=tmp_path, now=lambda: NOW)


def _evidence():
    return (WorkflowEvidence(kind="operator", reference="human-action:1"),)


def _blocker():
    return WorkflowBlock(block_id=UUID("00000000-0000-0000-0000-000000000003"), reason_code="missing-evidence", description="Awaiting an authoritative source.", evidence=_evidence(), required_resolution_kind=BlockResolutionKind.EVIDENCE_AVAILABLE, created_at=NOW)


def test_normal_completion_preserves_recipe_candidate_and_persisted_history(tmp_path) -> None:
    execution = record_execution_evidence(
        set_specification_phase(_execution(tmp_path), SpecificationPhase.SYNTHESIZING),
        worker_evidence=(WorkflowEvidence(kind="model", reference="model:test-specifier"),),
        cost_evidence=(WorkflowEvidence(kind="cost", reference="usage:1"),),
    )
    execution = await_human_review(execution, _candidate(tmp_path).revision_id)
    completed = complete_specification_execution(execution, outcome=SpecificationOutcome.ACCEPTED, now=lambda: NOW)
    repository = SpecificationExecutionRepository(tmp_path)
    repository.save(completed)

    restored = repository.get(completed.execution.execution_id)

    assert restored.execution.status is WorkflowExecutionStatus.COMPLETED
    assert restored.execution.recipe_id == "specification"
    assert restored.candidate_revision_id == CANDIDATE_ID
    assert restored.outcome is SpecificationOutcome.ACCEPTED
    assert restored.execution.worker_evidence[0].reference == "model:test-specifier"
    assert restored.execution.cost_evidence[0].reference == "usage:1"


def test_pause_resume_preserves_the_resumable_phase(tmp_path) -> None:
    paused = pause_specification_execution(set_specification_phase(_execution(tmp_path), SpecificationPhase.SYNTHESIZING), _evidence())
    resumed = resume_specification_execution(paused)

    assert resumed.execution.status is WorkflowExecutionStatus.RUNNING
    assert resumed.phase is SpecificationPhase.SYNTHESIZING


def test_block_requires_prerequisite_recheck_not_an_arbitrary_unblock(tmp_path) -> None:
    blocked = block_for_prerequisite(_execution(tmp_path), _blocker())

    assert recheck_blocked_prerequisite(blocked, prerequisite_satisfied=False, resolution_evidence=()) == blocked
    with pytest.raises(SpecificationExecutionError, match="resolution evidence"):
        recheck_blocked_prerequisite(blocked, prerequisite_satisfied=True, resolution_evidence=())

    resumed = recheck_blocked_prerequisite(blocked, prerequisite_satisfied=True, resolution_evidence=_evidence(), now=lambda: NOW)
    assert resumed.execution.status is WorkflowExecutionStatus.RUNNING
    assert resumed.execution.block_history[0].resolution_evidence == _evidence()


def test_needs_resolution_is_completed_not_blocked_and_keeps_dependencies(tmp_path) -> None:
    dependency = UnresolvedSpecificationDependency(dependency_id=UUID("00000000-0000-0000-0000-000000000004"), description="Architecture must choose the storage boundary.", required_authority="architect", evidence=_evidence(), resolution_reference="architecture:decision")

    completed = complete_specification_execution(_execution(tmp_path), outcome=SpecificationOutcome.NEEDS_RESOLUTION, unresolved_dependencies=(dependency,), now=lambda: NOW)

    assert completed.execution.status is WorkflowExecutionStatus.COMPLETED
    assert completed.outcome is SpecificationOutcome.NEEDS_RESOLUTION
    assert completed.unresolved_dependencies == (dependency,)


def test_rejected_candidate_is_a_distinct_terminal_semantic_outcome(tmp_path) -> None:
    awaiting_human = await_human_review(_execution(tmp_path), _candidate(tmp_path).revision_id)

    rejected = complete_specification_execution(
        awaiting_human, outcome=SpecificationOutcome.REJECTED, now=lambda: NOW
    )

    assert rejected.execution.status is WorkflowExecutionStatus.COMPLETED
    assert rejected.outcome is SpecificationOutcome.REJECTED
    assert rejected.candidate_revision_id == CANDIDATE_ID


@pytest.mark.parametrize("operation", [cancel_specification_execution, fail_specification_execution])
def test_cancel_and_failure_are_terminal(tmp_path, operation) -> None:
    terminal = operation(_execution(tmp_path), _evidence(), now=lambda: NOW)

    assert terminal.execution.status in {WorkflowExecutionStatus.CANCELLED, WorkflowExecutionStatus.FAILED}
    with pytest.raises(SpecificationExecutionError, match="not currently running"):
        set_specification_phase(terminal, SpecificationPhase.SYNTHESIZING)


def test_stale_or_foreign_restart_fails_closed(tmp_path, tmp_path_factory) -> None:
    execution = _execution(tmp_path)
    repository = SpecificationExecutionRepository(tmp_path)
    repository.save(execution)

    with pytest.raises(SpecificationExecutionNotFound, match="not canonically persisted"):
        SpecificationExecutionRepository(tmp_path_factory.mktemp("other-project")).get(execution.execution.execution_id)


def test_stale_recipe_cannot_resume(tmp_path) -> None:
    paused = pause_specification_execution(_execution(tmp_path), _evidence())
    stale = paused.model_copy(update={"execution": paused.execution.model_copy(update={"recipe_version": "0.0"})})

    with pytest.raises(SpecificationExecutionError, match="stale or unavailable"):
        resume_specification_execution(stale)


def test_same_work_identity_can_have_independent_execution_identities(tmp_path) -> None:
    first = _execution(tmp_path)
    second = _execution(tmp_path)
    repository = SpecificationExecutionRepository(tmp_path)
    repository.save(first)
    repository.save(second)

    assert first.execution.execution_id != second.execution.execution_id
    assert repository.get(first.execution.execution_id).specification_id == repository.get(second.execution.execution_id).specification_id
    assert {
        item.execution.execution_id for item in repository.list_for_specification(SPEC_ID)
    } == {first.execution.execution_id, second.execution.execution_id}

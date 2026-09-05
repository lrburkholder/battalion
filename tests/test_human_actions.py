"""BTN-43 canonical human-action and intervention acceptance tests."""

from __future__ import annotations

from support.state import make_run_state

from datetime import datetime, timezone

import pytest
from unittest.mock import patch

from battalion.application import (
    CandidateReviewFailed,
    HumanActionRejected,
    QueueIntervention,
    ResumeRun,
    ReviewCandidate,
    queue_intervention,
    resume_run,
    review_candidate,
)
from battalion.actors import bootstrap_local_actor
from battalion.config import BattalionConfig
from battalion.identity import load_project_identity
from battalion.context import driver_context, reviewer_context
from battalion.graph import _deliver_interventions, _make_driver_node
from battalion.execution import ExecutionCapture
from battalion.intel import CandidateInstinct, ReviewAction
from battalion.intel.candidates import CandidateRepository
from battalion.state.models import (
    HumanActionRecord,
    HumanIntervention,
    InterventionDisposition,
    InterventionKind,
    InterventionTarget,
    InterruptLogEntry,
    RunState,
    RunStatus,
)
from battalion.state.persistence import load_state
from battalion.workers import WorkerRecord, WorkerStatus, _write_record
from battalion.llm.litellm_client import NodeLLMConfig


def _state(status: RunStatus = RunStatus.AWAITING_HUMAN) -> RunState:
    return make_run_state(
        run_id='run-BTN-43',
        ticket_id='BTN-43',
        spec='Desktop human actions',
        status=status,
        phase='awaiting_human',
        interrupt_log=[InterruptLogEntry(
            trigger="manual-checkpoint",
            timestamp=datetime(2026, 8, 20, tzinfo=timezone.utc),
            context={"next_phase": "driver_green"},
        )],
        write_scope={},
        budget_limit=10,
    )


def _candidate(instinct_id: str = "INS-BTN-43") -> CandidateInstinct:
    return CandidateInstinct.model_validate({
        "schema_version": "1.0",
        "instinct_id": instinct_id,
        "lifecycle": "candidate",
        "recommendation": "Keep human actions explicit.",
        "evidence": [{
            "run_id": "run-BTN-43",
            "node_execution_id": "node-driver-1",
            "reference": "execution_record.node_executions[0]",
            "description": "The durable record retained the action.",
        }],
        "audience": ["driver"],
        "applicability": {
            "description": "Human-directed runs.",
            "include": ["desktop actions"],
            "exclude": ["autonomous verdict override"],
        },
        "tags": ["human-action"],
        "creation_provenance": {
            "originating_run_id": "run-BTN-43",
            "originating_node_execution_ids": ["node-driver-1"],
            "created_at": "2026-08-20T12:00:00Z",
            "created_by": "recon",
        },
    })


def _actor_id(project_root):
    load_project_identity(project_root, create=True)
    return bootstrap_local_actor(project_root, "Human Operator").local_actor_id


def test_resume_records_actor_resolution_target_and_durable_state(tmp_path):
    state = _state()
    path = tmp_path / f"{state.run_id}.json"
    path.write_text(state.model_dump_json(), encoding="utf-8")
    actor_id = _actor_id(tmp_path)

    result = resume_run(
        ResumeRun(
            run_id=state.run_id,
            config=BattalionConfig(base_dir=str(tmp_path)),
            actor_id=actor_id,
            resolution="Proceed with the approved GREEN correction.",
        ),
        state_dir=tmp_path,
        _execute=lambda **kwargs: kwargs["state"],
    )

    assert result.state.interrupt_log[-1].resolution.startswith("Proceed")
    action = result.state.human_action_log[-1]
    assert (action.actor, action.actor_id, action.target, action.disposition) == (
        "Human Operator", actor_id, "interrupt:0", "applied"
    )
    assert load_state(path).human_action_log[-1] == action


def test_correction_is_queued_only_when_no_worker_is_active(tmp_path, monkeypatch):
    state = _state()
    path = tmp_path / f"{state.run_id}.json"
    path.write_text(state.model_dump_json(), encoding="utf-8")
    actor_id = _actor_id(tmp_path)
    result = queue_intervention(
        QueueIntervention(
            run_id=state.run_id,
            kind=InterventionKind.CORRECTION,
            target=InterventionTarget.DRIVER_GREEN,
            text="Reuse the existing application operation.",
            project_root=tmp_path,
            actor_id=actor_id,
        ),
        state_dir=tmp_path,
        worker_dir=tmp_path / "workers",
        _action_id="action-correction",
    )

    assert result.action.disposition == "queued"
    assert result.state.interventions[0].target is InterventionTarget.DRIVER_GREEN
    active = WorkerRecord(
        run_id=state.run_id,
        state_version="1.0",
        worker_id="worker-active",
        operation="resume",
        status=WorkerStatus.RUNNING,
        pid=42,
        started_at="2026-08-20T12:00:00+00:00",
        updated_at="2026-08-20T12:00:00+00:00",
        state_path=str(path),
    )
    worker_dir = tmp_path / "workers"
    _write_record(worker_dir / f"{state.run_id}.json", active)
    monkeypatch.setattr("battalion.workers._pid_exists", lambda pid: True)
    with pytest.raises(HumanActionRejected, match="in flight"):
        queue_intervention(
            QueueIntervention(
                run_id=state.run_id,
                kind=InterventionKind.DESIGN_DECISION,
                target=InterventionTarget.ARCHITECT,
                text="Use the accepted ADR.",
                project_root=tmp_path,
                actor_id=actor_id,
            ),
            state_dir=tmp_path,
            worker_dir=worker_dir,
        )


def test_reviewer_target_and_kind_mismatches_are_rejected(tmp_path):
    state = _state()
    (tmp_path / f"{state.run_id}.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )
    actor_id = _actor_id(tmp_path)
    with pytest.raises(HumanActionRejected):
        queue_intervention(
            QueueIntervention(
                run_id=state.run_id,
                kind=InterventionKind.CORRECTION,
                target="reviewer",  # type: ignore[arg-type]
                text="Override the verdict.",
                    project_root=tmp_path,
                    actor_id=actor_id,
            ),
            state_dir=tmp_path,
        )
    with pytest.raises(HumanActionRejected, match="Architect only"):
        queue_intervention(
            QueueIntervention(
                run_id=state.run_id,
                kind=InterventionKind.DESIGN_DECISION,
                target=InterventionTarget.DRIVER_RED,
                text="Wrong authority.",
                project_root=tmp_path,
                actor_id=actor_id,
            ),
            state_dir=tmp_path,
        )


def test_delivery_checkpoints_before_context_and_never_reaches_reviewer(tmp_path):
    queued = HumanIntervention(
        action_id="action-delivery",
        kind=InterventionKind.CORRECTION,
        target=InterventionTarget.DRIVER_RED,
        text="Start from the existing write-scope tests.",
        actor="human@example.com",
        requested_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    action = HumanActionRecord(
        action_id=queued.action_id,
        kind="correction",
        actor=queued.actor,
        occurred_at=queued.requested_at,
        target=queued.target.value,
        disposition="queued",
        detail=queued.text,
        resulting_state_version="1.0",
        resulting_status=RunStatus.AWAITING_HUMAN,
        resulting_phase="awaiting_human",
    )
    state = _state().model_copy(update={
        "interventions": [queued], "human_action_log": [action]
    })
    checkpoints = []
    capture = ExecutionCapture.start(state, "driver_red", "test-model", tmp_path)
    capture.execution_id = "node-attempt-43"
    state = capture.create_attempt(state)

    delivered = _deliver_interventions(
        state, "driver_red", "node-attempt-43", checkpoints.append
    )

    assert checkpoints == [delivered]
    assert delivered.interventions[0].disposition is InterventionDisposition.DELIVERED
    assert delivered.interventions[0].delivered_to_execution_id == "node-attempt-43"
    driver = driver_context(
        delivered, tmp_path, "red", node_execution_id="node-attempt-43"
    )
    assert "## Human intervention" in driver
    assert queued.text in driver
    assert queued.text not in reviewer_context(delivered)
    assert queued.text not in driver_context(
        delivered, tmp_path, "red", node_execution_id="later-attempt"
    )


def test_driver_attempt_checkpoints_delivery_before_model_generation(tmp_path):
    queued = HumanIntervention(
        action_id="action-attempt",
        kind=InterventionKind.CORRECTION,
        target=InterventionTarget.DRIVER_RED,
        text="Exercise the canonical application boundary.",
        actor="human@example.com",
        requested_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    action = HumanActionRecord(
        action_id=queued.action_id,
        kind="correction",
        actor=queued.actor,
        occurred_at=queued.requested_at,
        target=queued.target.value,
        disposition="queued",
        detail=queued.text,
        resulting_state_version="1.0",
        resulting_status=RunStatus.IN_PROGRESS,
        resulting_phase="driver_red",
    )
    state = _state(RunStatus.IN_PROGRESS).model_copy(update={
        "phase": "driver_red",
        "interventions": [queued],
        "human_action_log": [action],
    })
    events = []

    def checkpoint(value):
        events.append(("checkpoint", value))

    def run_driver(**kwargs):
        events.append(("model", kwargs["ticket_text"]))
        return kwargs["state"].model_copy(update={"phase": "reviewer"})

    with patch("battalion.nodes.driver.run_driver", side_effect=run_driver):
        node = _make_driver_node(
            "red",
            {"driver": NodeLLMConfig(model="driver-model")},
            str(tmp_path),
            on_state_checkpoint=checkpoint,
        )
        result = node(state)

    assert events[0][0] == "checkpoint"
    assert events[0][1].interventions[0].delivered_to_execution_id
    assert events[1][0] == "checkpoint"
    assert events[1][1].graph_progress.stage.value == "attempt-started"
    assert events[2][0] == "model"
    assert queued.text in events[2][1]
    execution = result.execution_record.node_executions[-1]
    references = [item for item in execution.input_references if queued.action_id in item.reference]
    assert len(references) == 1
    assert references[0].inclusion_reason == (
        "human-supplied correction for driver_red"
    )


def test_candidate_review_promotes_or_rejects_without_mutating_evidence(tmp_path):
    actor_id = _actor_id(tmp_path)
    candidate = _candidate()
    repository = CandidateRepository(tmp_path / ".battalion/recon/candidates")
    repository.store(candidate)
    original = repository.get(candidate.instinct_id)

    promoted = review_candidate(ReviewCandidate(
        project_root=tmp_path,
        candidate_id=candidate.instinct_id,
        action=ReviewAction.ACCEPT,
        actor_id=actor_id,
    ))

    assert promoted.disposition == "promoted"
    assert promoted.decision.decided_by_actor_id == actor_id
    assert repository.get(candidate.instinct_id) == original
    accepted = promoted.decision.accepted_instinct_id
    assert accepted is not None
    from battalion.intel.repository import IntelRepository
    assert (
        IntelRepository(tmp_path / ".battalion/intel")
        .get(accepted)
        .acceptance_provenance.accepted_by_actor_id
        == actor_id
    )
    rejected_candidate = _candidate("INS-BTN-43-REJECT")
    repository.store(rejected_candidate)
    rejected = review_candidate(ReviewCandidate(
        project_root=tmp_path,
        candidate_id=rejected_candidate.instinct_id,
        action=ReviewAction.REJECT,
        actor_id=actor_id,
    ))
    assert rejected.disposition == "rejected"
    assert repository.get(rejected_candidate.instinct_id) == rejected_candidate
    with pytest.raises(CandidateReviewFailed):
        review_candidate(ReviewCandidate(
            project_root=tmp_path,
            candidate_id=candidate.instinct_id,
            action=ReviewAction.REJECT,
            actor_id=actor_id,
        ))

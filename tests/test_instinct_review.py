"""BTN-23 acceptance tests for human review and Instinct promotion."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from battalion.intel import (
    CandidateInstinct,
    DecisionAlreadyRecordedError,
    IntelRepository,
    InstinctDecisionRepository,
    InstinctReviewWorkflow,
    ReviewAction,
)


def _candidate(instinct_id: str = "INS-SCOPED-WRITES") -> CandidateInstinct:
    return CandidateInstinct.model_validate({
        "schema_version": "1.0",
        "instinct_id": instinct_id,
        "lifecycle": "candidate",
        "recommendation": "Bind writing roles to declared project-relative roots.",
        "evidence": [{
            "run_id": "run-BTN-19",
            "node_execution_id": "node-driver-green-1",
            "reference": "execution_record.node_executions[3]",
            "description": "An undeclared write was rejected before execution.",
        }],
        "audience": ["driver", "refactorer"],
        "applicability": {
            "description": "Writing nodes operating on repository files.",
            "include": ["repositories with phase-specific write roots"],
            "exclude": ["read-only review nodes"],
        },
        "tags": ["write-scope", "safety"],
        "creation_provenance": {
            "originating_run_id": "run-BTN-19",
            "originating_node_execution_ids": ["node-driver-green-1"],
            "created_at": "2026-08-13T12:00:00Z",
            "created_by": "recon",
        },
    })


@pytest.fixture
def workflow(tmp_path):
    intel = IntelRepository(tmp_path / "intel")
    decisions = InstinctDecisionRepository(tmp_path / "decisions")
    return InstinctReviewWorkflow(intel, decisions), intel, decisions


def test_accept_promotes_candidate_and_records_operator_decision(workflow):
    review, intel, decisions = workflow
    candidate = _candidate()
    decided_at = datetime(2026, 8, 13, 13, tzinfo=timezone.utc)

    decision = review.accept(
        candidate, decided_by="operator@example.com", decided_at=decided_at
    )

    accepted = intel.get(candidate.instinct_id)
    assert accepted.recommendation == candidate.recommendation
    assert accepted.creation_provenance == candidate.creation_provenance
    assert accepted.acceptance_provenance.accepted_by == "operator@example.com"
    assert decision.action is ReviewAction.ACCEPT
    assert decision.candidate_id == candidate.instinct_id
    assert decision.accepted_instinct_id == candidate.instinct_id
    assert decision.decided_at == decided_at
    assert decisions.get(candidate.instinct_id) == decision


def test_edit_then_accept_changes_only_approved_content(workflow):
    review, intel, _ = workflow
    candidate = _candidate()
    original = candidate.model_copy(deep=True)

    decision = review.edit_then_accept(
        candidate,
        decided_by="operator@example.com",
        edits={
            "instinct_id": "INS-SCOPED-WRITES-EDITED",
            "recommendation": "Bind every write operation to its phase-specific roots.",
            "tags": ["write-scope", "operator-edited"],
        },
    )

    accepted = intel.get("INS-SCOPED-WRITES-EDITED")
    assert accepted.recommendation.startswith("Bind every write")
    assert accepted.tags == ["write-scope", "operator-edited"]
    assert accepted.creation_provenance == candidate.creation_provenance
    assert candidate == original
    assert decision.action is ReviewAction.EDIT_AND_ACCEPT
    assert decision.accepted_instinct_id == accepted.instinct_id


def test_edit_cannot_replace_recon_provenance(workflow):
    review, intel, decisions = workflow
    candidate = _candidate()

    with pytest.raises(ValueError, match="cannot edit"):
        review.edit_then_accept(
            candidate,
            decided_by="operator@example.com",
            edits={"creation_provenance": {"created_by": "operator"}},
        )

    assert intel.list_all() == []
    assert decisions.list_all() == []


def test_reject_records_decision_without_publishing_knowledge(workflow):
    review, intel, decisions = workflow
    candidate = _candidate()

    decision = review.reject(candidate, decided_by="operator@example.com")

    assert decision.action is ReviewAction.REJECT
    assert decision.accepted_instinct_id is None
    assert intel.list_all() == []
    assert decisions.get(candidate.instinct_id) == decision


def test_each_candidate_can_receive_only_one_immutable_decision(workflow):
    review, intel, decisions = workflow
    candidate = _candidate()
    review.reject(candidate, decided_by="first-operator")

    with pytest.raises(DecisionAlreadyRecordedError, match=candidate.instinct_id):
        review.accept(candidate, decided_by="second-operator")

    assert intel.list_all() == []
    assert len(decisions.list_all()) == 1

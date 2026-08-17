"""BTN-34 acceptance tests for durable Recon candidate persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from battalion.intel import (
    CandidateInstinct,
    CandidateDisposition,
    CandidateInbox,
    CandidateRepository,
    ImmutableCandidateError,
    InstinctDecisionRepository,
    InstinctReviewDecision,
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


def test_candidate_round_trips_as_human_readable_markdown(tmp_path):
    repository = CandidateRepository(tmp_path / "candidates")
    candidate = _candidate()

    repository.store(candidate)

    path = tmp_path / "candidates" / f"{candidate.instinct_id}.md"
    document = path.read_text(encoding="utf-8")
    assert document.startswith("---\n")
    assert f"# Recon candidate `{candidate.instinct_id}`" in document
    assert "## Recommendation" in document
    assert "confidence:" not in document
    assert repository.get(candidate.instinct_id) == candidate


def test_candidate_creation_is_create_only(tmp_path):
    repository = CandidateRepository(tmp_path / "candidates")
    original = _candidate()
    repository.store(original)

    with pytest.raises(ImmutableCandidateError, match=original.instinct_id):
        repository.store(original.model_copy(update={"recommendation": "changed"}))

    assert repository.get(original.instinct_id) == original


def test_candidate_identifier_cannot_traverse_repository(tmp_path):
    repository = CandidateRepository(tmp_path / "candidates")

    with pytest.raises(ValidationError):
        repository.get("../../outside")


def test_malformed_or_mismatched_markdown_fails_closed(tmp_path):
    root = tmp_path / "candidates"
    root.mkdir()
    path = root / "INS-BROKEN.md"
    path.write_text("---\nlifecycle: candidate\nconfidence: 0.9\n---\n# Broken\n")

    with pytest.raises(ValidationError):
        CandidateRepository(root).get("INS-BROKEN")


def test_partial_write_never_publishes_a_candidate(tmp_path, monkeypatch):
    repository = CandidateRepository(tmp_path / "candidates")

    def fail_after_partial_write(path: Path, payload: str) -> None:
        path.write_text(payload[:20], encoding="utf-8")
        raise OSError("simulated disk failure")

    monkeypatch.setattr(repository, "_write_temporary", fail_after_partial_write)

    with pytest.raises(OSError, match="disk failure"):
        repository.store(_candidate())

    assert list((tmp_path / "candidates").iterdir()) == []


def test_inbox_is_deterministic_and_projects_decisions_without_mutating_candidates(
    tmp_path,
):
    candidates = CandidateRepository(tmp_path / "candidates")
    decisions = InstinctDecisionRepository(tmp_path / "decisions")
    pending = _candidate("INS-Z-PENDING")
    promoted = _candidate("INS-A-PROMOTED")
    rejected = _candidate("INS-M-REJECTED")
    for candidate in (pending, promoted, rejected):
        candidates.store(candidate)
    original_rejected = (tmp_path / "candidates" / "INS-M-REJECTED.md").read_bytes()
    decisions.store(InstinctReviewDecision(
        candidate_id=promoted.instinct_id,
        action=ReviewAction.ACCEPT,
        decided_at="2026-08-17T12:00:00Z",
        decided_by="operator",
        accepted_instinct_id=promoted.instinct_id,
    ))
    decisions.store(InstinctReviewDecision(
        candidate_id=rejected.instinct_id,
        action=ReviewAction.REJECT,
        decided_at="2026-08-17T12:01:00Z",
        decided_by="operator",
    ))

    entries = CandidateInbox(candidates, decisions).list_all()

    assert [entry.candidate.instinct_id for entry in entries] == [
        "INS-A-PROMOTED", "INS-M-REJECTED", "INS-Z-PENDING",
    ]
    assert [entry.disposition for entry in entries] == [
        CandidateDisposition.PROMOTED,
        CandidateDisposition.REJECTED,
        CandidateDisposition.PENDING,
    ]
    assert (tmp_path / "candidates" / "INS-M-REJECTED.md").read_bytes() == original_rejected

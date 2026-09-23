"""Credential-free acceptance coverage for BTN-227 Specification persistence."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

import battalion.specifications as specifications
from battalion.specifications import (
    ImmutableSpecificationRevisionError,
    InvalidSpecificationTransition,
    ProjectionState,
    SpecificationProducerProvenance,
    SpecificationRepository,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
SPEC_ID = UUID("10000000-0000-4000-8000-000000000227")
CANDIDATE_ID = UUID("20000000-0000-4000-8000-000000000227")
ACCEPTED_ID = UUID("30000000-0000-4000-8000-000000000227")


def _repository(tmp_path):
    repository = SpecificationRepository(tmp_path)
    specification = repository.create("github:lrburkholder/battalion#346", specification_id=SPEC_ID, now=lambda: NOW)
    return repository, specification


def _candidate(repository, specification, **kwargs):
    return repository.create_candidate(
        specification.specification_id, revision_id=CANDIDATE_ID,
        outcomes=["Operators can inspect an exact accepted revision."],
        constraints=["Markdown is a projection only."], non_goals=["Provider comments are not canonical."],
        acceptance_criteria=["Acceptance records exact candidate provenance."],
        unresolved_product_decisions=["Choose Specifier recipe later."],
        producer_provenance=SpecificationProducerProvenance(producer="specifier", execution_id="spec-exec-1"),
        now=lambda: NOW, **kwargs,
    )


def test_create_revise_reload_and_immutable_candidate_history(tmp_path):
    repository, specification = _repository(tmp_path)
    original = _candidate(repository, specification)
    revised = repository.create_candidate(specification.specification_id, outcomes=["A distinct revision"], now=lambda: NOW)

    reloaded = SpecificationRepository(tmp_path)
    assert reloaded.get(specification.specification_id) == specification
    assert reloaded.get_revision(specification.specification_id, original.revision_id) == original
    assert revised.revision_id != original.revision_id
    with pytest.raises(ImmutableSpecificationRevisionError):
        repository._store_revision(original)


def test_accept_binds_exact_candidate_and_preserves_original_content(tmp_path):
    repository, specification = _repository(tmp_path)
    candidate = _candidate(repository, specification)

    accepted = repository.accept(specification.specification_id, candidate.revision_id,
                                 accepted_by="Local Operator", accepted_at=NOW, revision_id=ACCEPTED_ID)

    assert accepted.source_revision_id == candidate.revision_id
    assert accepted.acceptance_provenance.accepted_by == "Local Operator"
    assert repository.get_revision(specification.specification_id, candidate.revision_id) == candidate
    assert repository.get(specification.specification_id).current_accepted_revision_id == accepted.revision_id

    later_candidate = repository.create_candidate(
        specification.specification_id, outcomes=["A later authoritative revision"], now=lambda: NOW
    )
    later_accepted = repository.accept(
        specification.specification_id, later_candidate.revision_id, accepted_by="Local Operator", accepted_at=NOW
    )

    assert repository.get_revision(specification.specification_id, accepted.revision_id) == accepted
    assert repository.get(specification.specification_id).current_accepted_revision_id == later_accepted.revision_id


def test_reject_and_supersede_are_immutable_lifecycle_records(tmp_path):
    repository, specification = _repository(tmp_path)
    candidate = _candidate(repository, specification)
    rejected = repository.reject(specification.specification_id, candidate.revision_id, now=lambda: NOW)
    accepted = repository.accept(specification.specification_id, candidate.revision_id, accepted_by="operator", accepted_at=NOW)
    superseded = repository.supersede(specification.specification_id, accepted.revision_id, now=lambda: NOW)

    assert rejected.source_revision_id == candidate.revision_id
    assert superseded.source_revision_id == accepted.revision_id
    assert repository.get_revision(specification.specification_id, accepted.revision_id) == accepted
    assert repository.get(specification.specification_id).current_accepted_revision_id is None
    with pytest.raises(InvalidSpecificationTransition):
        repository.accept(specification.specification_id, accepted.revision_id, accepted_by="operator")


def test_projection_is_deterministic_stale_when_edited_and_never_canonical_input(tmp_path):
    repository, specification = _repository(tmp_path)
    candidate = _candidate(repository, specification)
    accepted = repository.accept(specification.specification_id, candidate.revision_id, accepted_by="operator", accepted_at=NOW)

    first = repository.render_markdown(specification.specification_id)
    path = repository.write_projection(specification.specification_id, now=lambda: NOW)
    assert path.read_text(encoding="utf-8") == first == repository.render_markdown(specification.specification_id)
    assert str(accepted.revision_id) in first
    path.write_text("operator notes", encoding="utf-8")

    assert repository.projection_status(specification.specification_id, now=lambda: NOW).state is ProjectionState.STALE
    assert repository.get_revision(specification.specification_id, accepted.revision_id) == accepted
    assert repository.render_markdown(specification.specification_id) == first


def test_projection_failure_is_recorded_without_invalidating_canonical_state(tmp_path, monkeypatch):
    repository, specification = _repository(tmp_path)
    candidate = _candidate(repository, specification)
    accepted = repository.accept(specification.specification_id, candidate.revision_id, accepted_by="operator", accepted_at=NOW)
    original_write = specifications._write_text_atomic

    def fail_markdown(path, text):
        if path.name == "specification.md":
            raise OSError("disk unavailable")
        original_write(path, text)

    monkeypatch.setattr(specifications, "_write_text_atomic", fail_markdown)

    with pytest.raises(OSError, match="disk unavailable"):
        repository.write_projection(specification.specification_id, now=lambda: NOW)

    assert repository.projection_status(specification.specification_id).state is ProjectionState.FAILED
    assert repository.get(specification.specification_id).current_accepted_revision_id == accepted.revision_id
    assert repository.get_revision(specification.specification_id, accepted.revision_id) == accepted

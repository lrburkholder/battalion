"""BTN-98 conservative identity reconciliation fixtures."""

from __future__ import annotations

from uuid import UUID

import pytest

from battalion.cartography.reconciliation import (
    CartographyEntityKind,
    ContinuityEvidence,
    ContinuityEvidenceKind,
    PriorEntity,
    ReconciliationCandidate,
    ReconciliationOutcome,
    reconcile_entities,
)


def _prior(entity_id: str = "symbol:run") -> PriorEntity:
    return PriorEntity(entity_id=entity_id, kind=CartographyEntityKind.SYMBOL)


def _evidence(kind: ContinuityEvidenceKind = ContinuityEvidenceKind.SEMANTIC_SIGNATURE):
    return ContinuityEvidence(
        kind=kind,
        reference="git:abc123",
        description="A bounded structural signature establishes continuity.",
    )


def _candidate(candidate_id: str, predecessors=(), evidence=(), **overrides):
    return ReconciliationCandidate(
        candidate_id=candidate_id,
        kind=CartographyEntityKind.SYMBOL,
        predecessor_ids=predecessors,
        continuity_evidence=evidence,
        **overrides,
    )


def _uuid_factory(*values: str):
    identifiers = iter(UUID(value) for value in values)
    return lambda: next(identifiers)


@pytest.mark.parametrize(
    ("candidate", "expected_outcome", "expected_id"),
    [
        (
            _candidate("unchanged", ("symbol:run",), (_evidence(),)),
            ReconciliationOutcome.PRESERVED,
            "symbol:run",
        ),
        (
            _candidate(
                "renamed-and-moved",
                ("symbol:run",),
                (_evidence(ContinuityEvidenceKind.EXPLICIT_REFACTOR),),
                path="battalion/application/operations.py",
                locator="run_operation",
            ),
            ReconciliationOutcome.PRESERVED,
            "symbol:run",
        ),
        (
            _candidate("new-symbol"),
            ReconciliationOutcome.NEW,
            "symbol:00000000000000000000000000000001",
        ),
    ],
)
def test_reconciliation_preserves_only_proven_one_to_one_identity(candidate, expected_outcome, expected_id):
    result = reconcile_entities(
        (_prior(),),
        (candidate,),
        uuid_factory=_uuid_factory("00000000-0000-0000-0000-000000000001"),
    )

    reconciled = result.candidates[0]
    assert reconciled.outcome is expected_outcome
    assert reconciled.entity_id == expected_id


def test_split_assigns_new_ids_and_keeps_possible_successors_inspectable():
    result = reconcile_entities(
        (_prior(),),
        (
            _candidate("first-half", ("symbol:run",), (_evidence(),)),
            _candidate("second-half", ("symbol:run",), (_evidence(),)),
        ),
        uuid_factory=_uuid_factory(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ),
    )

    assert [item.outcome for item in result.candidates] == [
        ReconciliationOutcome.SPLIT,
        ReconciliationOutcome.SPLIT,
    ]
    assert all(item.entity_id != "symbol:run" for item in result.candidates)
    assert all(item.identity_links[0].source_id == "symbol:run" for item in result.candidates)


def test_merge_keeps_prior_identities_and_assigns_a_new_successor_identity():
    result = reconcile_entities(
        (_prior("symbol:run"), _prior("symbol:resume")),
        (_candidate("unified", ("symbol:run", "symbol:resume"), (_evidence(),)),),
        uuid_factory=_uuid_factory("00000000-0000-0000-0000-000000000003"),
    )

    reconciled = result.candidates[0]
    assert reconciled.outcome is ReconciliationOutcome.MERGED
    assert reconciled.entity_id == "symbol:00000000000000000000000000000003"
    assert {link.source_id for link in reconciled.identity_links} == {"symbol:run", "symbol:resume"}


def test_deletion_new_and_path_only_ambiguity_are_explicit():
    weak = _candidate(
        "same-name-only",
        ("symbol:run",),
        (_evidence(ContinuityEvidenceKind.PATH_OR_NAME),),
        path="battalion/application.py",
        locator="run",
    )
    result = reconcile_entities(
        (_prior(), _prior("symbol:deleted")),
        (weak, _candidate("new-symbol")),
        uuid_factory=_uuid_factory(
            "00000000-0000-0000-0000-000000000004",
            "00000000-0000-0000-0000-000000000005",
        ),
    )

    by_candidate = {item.candidate_id: item for item in result.candidates}
    ambiguous = by_candidate["same-name-only"]
    new = by_candidate["new-symbol"]
    assert ambiguous.outcome is ReconciliationOutcome.AMBIGUOUS
    assert ambiguous.identity_links[0].kind == "possible_successor"
    assert new.outcome is ReconciliationOutcome.NEW
    assert result.deleted_entity_ids == ("symbol:deleted",)


def test_reconciliation_rejects_unknown_or_cross_kind_predecessors():
    with pytest.raises(ValueError, match="unknown predecessor"):
        reconcile_entities(
            (_prior(),),
            (_candidate("unknown", ("symbol:missing",), (_evidence(),)),),
        )

    domain_candidate = ReconciliationCandidate(
        candidate_id="domain-candidate",
        kind=CartographyEntityKind.DOMAIN,
        predecessor_ids=("symbol:run",),
        continuity_evidence=(_evidence(),),
    )
    with pytest.raises(ValueError, match="same kind"):
        reconcile_entities((_prior(),), (domain_candidate,))

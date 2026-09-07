"""BTN-98 authority-safe generated refresh assembly tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from battalion.cartography import (
    AuthorityClass,
    Domain,
    EvidenceReference,
    InstitutionalConstraint,
    MapRevision,
    Symbol,
    assemble_generated_refresh,
)
from battalion.cartography.refresh import GeneratedAuthorityViolation


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
PROJECT_ID = UUID("12b940e6-8d4c-4a7e-ae7c-fbf30b10d17b")
ACTOR_ID = UUID("4f3a1684-263d-4301-899f-9f1770fbab26")


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        reference="git:abc123",
        description="Observed by a bounded structural refresh.",
        observed_at=NOW,
    )


def _revision(revision_id: str, *, constraint: InstitutionalConstraint | None = None) -> MapRevision:
    domain = Domain(
        domain_id="domain:application",
        purpose="Own application operations.",
        path_scopes=("battalion",),
        evidence=(_evidence(),),
    )
    symbol = Symbol(
        symbol_id="symbol:run-new",
        domain_id=domain.domain_id,
        kind="function",
        responsibility="Run approved work after an ambiguous predecessor.",
        path="battalion/application/operations.py",
        locator="run",
        evidence=(_evidence(),),
    )
    return MapRevision(
        map_revision_id=revision_id,
        project_id=PROJECT_ID,
        repository_revision=revision_id,
        created_at=NOW,
        inspected_scope=("battalion",),
        domains=(domain,),
        symbols=(symbol,),
        constraints=() if constraint is None else (constraint,),
    )


def test_attributed_constraint_survives_generated_structural_change_and_ambiguity():
    human_constraint = InstitutionalConstraint(
        constraint_id="constraint:scoped-writes",
        statement="Do not bypass scoped writes.",
        applies_to=("symbol:run-old",),
        evidence=(_evidence(),),
        authority_class=AuthorityClass.ATTRIBUTED,
        asserted_by_actor_id=ACTOR_ID,
    )
    conflicting_generated_record = InstitutionalConstraint(
        constraint_id=human_constraint.constraint_id,
        statement="Generated replacement must not win.",
        applies_to=("symbol:run-new",),
        evidence=(_evidence(),),
    )

    assembled = assemble_generated_refresh(
        _revision("maprev:prior", constraint=human_constraint),
        _revision("maprev:generated", constraint=conflicting_generated_record),
    )

    assert assembled.revision.constraints == (human_constraint,)
    assert assembled.preserved_record_ids == ("constraint:scoped-writes",)
    assert assembled.revision.symbols[0].symbol_id == "symbol:run-new"


def test_generated_refresh_cannot_manufacture_attributed_or_governing_authority():
    generated_claim = InstitutionalConstraint(
        constraint_id="constraint:invented-authority",
        statement="A generated record cannot claim a human authority class.",
        applies_to=("symbol:run-new",),
        evidence=(_evidence(),),
        authority_class=AuthorityClass.ATTRIBUTED,
        asserted_by_actor_id=ACTOR_ID,
    )

    with pytest.raises(GeneratedAuthorityViolation, match="must remain derived"):
        assemble_generated_refresh(
            _revision("maprev:prior"),
            _revision("maprev:generated", constraint=generated_claim),
        )

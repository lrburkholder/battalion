"""BTN-98 logical Cartography contract tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from battalion.cartography import (
    AuthorityClass,
    Domain,
    EvidenceReference,
    InstitutionalConstraint,
    MapRevision,
    Relationship,
    Symbol,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
PROJECT_ID = UUID("12b940e6-8d4c-4a7e-ae7c-fbf30b10d17b")
ACTOR_ID = UUID("4f3a1684-263d-4301-899f-9f1770fbab26")


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        reference="git:abc123",
        description="Observed during a bounded structural refresh.",
        path="battalion/application.py",
        locator="run",
        observed_at=NOW,
    )


def _revision(**overrides) -> MapRevision:
    domain = Domain(
        domain_id="domain:application",
        purpose="Own transport-neutral run operations.",
        path_scopes=("battalion/application.py",),
        evidence=(_evidence(),),
    )
    symbol = Symbol(
        symbol_id="symbol:run-operation",
        domain_id=domain.domain_id,
        kind="function",
        responsibility="Runs one approved operation.",
        path="battalion/application.py",
        locator="run",
        evidence=(_evidence(),),
    )
    data = {
        "map_revision_id": "maprev:initial",
        "project_id": PROJECT_ID,
        "repository_revision": "abc123",
        "created_at": NOW,
        "inspected_scope": ("battalion",),
        "domains": (domain,),
        "symbols": (symbol,),
    }
    data.update(overrides)
    return MapRevision(**data)


def test_map_identity_is_separate_from_revision_specific_symbol_location():
    initial = _revision()
    moved = _revision(
        map_revision_id="maprev:moved",
        repository_revision="def456",
        symbols=(
            Symbol(
                symbol_id="symbol:run-operation",
                domain_id="domain:application",
                kind="function",
                responsibility="Runs one approved operation.",
                path="battalion/application/operations.py",
                locator="run",
                evidence=(_evidence(),),
            ),
        ),
    )

    assert initial.symbols[0].symbol_id == moved.symbols[0].symbol_id
    assert initial.symbols[0].path != moved.symbols[0].path


def test_map_rejects_dangling_graph_references():
    relationship = Relationship(
        relationship_id="relationship:invalid",
        source_id="domain:application",
        target_id="resource:missing",
        kind="reads",
        evidence=(_evidence(),),
    )

    with pytest.raises(ValidationError, match="relationship endpoints"):
        _revision(relationships=(relationship,))


def test_attributed_constraint_requires_durable_actor_identity():
    with pytest.raises(ValidationError, match="asserting Actor"):
        InstitutionalConstraint(
            constraint_id="constraint:no-direct-state-write",
            statement="Do not bypass the persistence boundary.",
            applies_to=("domain:application",),
            evidence=(_evidence(),),
            authority_class=AuthorityClass.ATTRIBUTED,
        )

    constraint = InstitutionalConstraint(
        constraint_id="constraint:no-direct-state-write",
        statement="Do not bypass the persistence boundary.",
        applies_to=("domain:application",),
        evidence=(_evidence(),),
        authority_class=AuthorityClass.ATTRIBUTED,
        asserted_by_actor_id=ACTOR_ID,
    )
    assert constraint.asserted_by_actor_id == ACTOR_ID

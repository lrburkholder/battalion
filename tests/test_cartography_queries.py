"""BTN-98 deterministic bounded Cartography query tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from battalion.cartography import (
    CartographyQueries,
    Domain,
    EvidenceReference,
    MapRevision,
    PathBinding,
    Relationship,
    Resource,
    Symbol,
)
from battalion.cartography.queries import CartographyEntityNotFound


def _map(*, changed: bool = False) -> MapRevision:
    observed = datetime(2026, 9, 6, tzinfo=timezone.utc)
    evidence = EvidenceReference(
        reference="git:abc123",
        description="Observed during a bounded refresh.",
        observed_at=observed,
    )
    domain = Domain(
        domain_id="domain:application",
        purpose="Changed purpose." if changed else "Own run operations.",
        path_scopes=("battalion",),
        evidence=(evidence,),
    )
    symbol = Symbol(
        symbol_id="symbol:run",
        domain_id=domain.domain_id,
        kind="function",
        responsibility="Start an approved run.",
        path="battalion/application.py",
        locator="run",
        evidence=(evidence,),
    )
    resource = Resource(
        resource_id="resource:run-state",
        kind="file",
        locator=".battalion/state",
        evidence=(evidence,),
    )
    return MapRevision(
        map_revision_id="maprev:changed" if changed else "maprev:initial",
        project_id=UUID("12b940e6-8d4c-4a7e-ae7c-fbf30b10d17b"),
        repository_revision="def456" if changed else "abc123",
        created_at=observed,
        inspected_scope=("battalion",),
        domains=(domain,),
        symbols=(symbol,),
        resources=(resource,),
        path_bindings=(PathBinding(
            matcher="battalion/*.py", entity_ids=(domain.domain_id, symbol.symbol_id), evidence=(evidence,)
        ),),
        relationships=(
            Relationship(
                relationship_id="relationship:contains-run",
                source_id=domain.domain_id,
                target_id=symbol.symbol_id,
                kind="contains",
                evidence=(evidence,),
            ),
            Relationship(
                relationship_id="relationship:reads-state",
                source_id=symbol.symbol_id,
                target_id=resource.resource_id,
                kind="reads",
                evidence=(evidence,),
            ),
        ),
    )


def test_explain_and_path_return_bounded_typed_evidence():
    queries = CartographyQueries(_map())

    explanation = queries.explain("symbol:run", depth=1)
    path = queries.path("domain:application", "resource:run-state")

    assert [entity.symbol_id for entity in explanation.entities if isinstance(entity, Symbol)] == ["symbol:run"]
    assert [edge.kind.value for edge in explanation.relationships] == ["contains", "reads"]
    assert path is not None
    assert path.entity_ids == ("domain:application", "symbol:run", "resource:run-state")
    assert [edge.relationship_id for edge in path.relationships] == [
        "relationship:contains-run", "relationship:reads-state"
    ]


def test_path_bindings_normalize_windows_paths_and_unknown_entities_fail_explicitly():
    queries = CartographyQueries(_map())

    assert queries.bindings_for_path("battalion\\application.py").bindings[0].matcher == "battalion/*.py"
    with pytest.raises(CartographyEntityNotFound, match="symbol:missing"):
        queries.get("symbol:missing")


def test_revision_diff_is_logical_and_does_not_depend_on_storage_encoding():
    initial = _map()
    changed = _map(changed=True)

    diff = CartographyQueries(changed).diff(initial)

    assert diff.prior_map_revision_id == "maprev:initial"
    assert diff.changed_entity_ids == ("domain:application",)
    assert diff.added_relationship_ids == ()

"""Candidate-neutral Cartography persistence benchmark contract.

The fixture models only the logical vocabulary proposed in RFC-0008. It is
benchmark infrastructure, not a production Cartography model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class MapRecord:
    record_id: str
    kind: Literal["domain", "symbol", "resource", "claim", "constraint", "path-binding"]
    data: dict[str, Any]
    provenance: str
    authored: bool = False


@dataclass(frozen=True)
class MapEdge:
    source_id: str
    target_id: str
    kind: str


@dataclass(frozen=True)
class MapFixture:
    fixture_id: Literal["BTN-86-persistence-v1"]
    revision_id: str
    repository_revision: str
    records: tuple[MapRecord, ...]
    edges: tuple[MapEdge, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PersistenceTrace:
    candidate: str
    retrieval_ids: tuple[str, ...]
    neighbor_ids: tuple[str, ...]
    diff: dict[str, tuple[str, ...]]
    recovered_revision: str
    generated_projection: bool


@dataclass(frozen=True)
class IntegrationSpikeTrace:
    """Evidence for the graph-canonical, Markdown-projection candidate."""

    before_lookup: tuple[str, ...]
    after_lookup: tuple[str, ...]
    added: tuple[str, ...]
    changed: tuple[str, ...]
    deleted: tuple[str, ...]
    preserved_authored: tuple[str, ...]
    projection_before_failure: str
    projection_after_failure: str


def build_fixture() -> MapFixture:
    """Return the exact provider-free control case used by every candidate."""
    return MapFixture(
        fixture_id="BTN-86-persistence-v1",
        revision_id="map-0001",
        repository_revision="a" * 40,
        records=(
            MapRecord(
                "domain:application", "domain",
                {"name": "application", "paths": ["battalion/application.py"],
                 "purpose": "transport-neutral commands and queries"},
                "derived:fixture",
            ),
            MapRecord(
                "domain:state", "domain",
                {"name": "state", "paths": ["battalion/state/"],
                 "purpose": "versioned durable state contract"},
                "derived:fixture",
            ),
            MapRecord(
                "symbol:application.run", "symbol",
                {"path": "battalion/application.py", "locator": "run",
                 "domain_id": "domain:application"},
                "derived:fixture",
            ),
            MapRecord(
                "resource:run-state", "resource",
                {"kind": "file", "locator": ".battalion/runs/<run>.json"},
                "derived:fixture",
            ),
            MapRecord(
                "constraint:application-boundary", "constraint",
                {"statement": "Presentation adapters do not mutate persisted state directly.",
                 "governing_artifact": "ADR-0022"},
                "governing_reference:ADR-0022", authored=True,
            ),
        ),
        edges=(
            MapEdge("domain:application", "symbol:application.run", "contains"),
            MapEdge("symbol:application.run", "resource:run-state", "writes"),
            MapEdge("constraint:application-boundary", "domain:application", "governs"),
        ),
    )


def fixture_with_generated_change() -> MapFixture:
    """Change a generated record while preserving the governing record."""
    original = build_fixture()
    records = tuple(
        MapRecord(
            item.record_id, item.kind,
            {**item.data, "purpose": "shared application commands and queries"}
            if item.record_id == "domain:application" else item.data,
            item.provenance, item.authored,
        )
        for item in original.records
    )
    return MapFixture(
        fixture_id=original.fixture_id,
        revision_id="map-0002",
        repository_revision="b" * 40,
        records=records,
        edges=original.edges,
    )


def build_integration_fixture() -> MapFixture:
    """A denser, path-and-provenance fixture for the pre-selection spike."""
    return MapFixture(
        fixture_id="BTN-86-persistence-v1",
        revision_id="map-0200",
        repository_revision="c" * 40,
        records=(
            MapRecord("domain:application", "domain", {"name": "application", "paths": ["battalion/application.py"], "purpose": "commands and queries"}, "derived:fixture"),
            MapRecord("domain:state", "domain", {"name": "state", "paths": ["battalion/state/"], "purpose": "durable state"}, "derived:fixture"),
            MapRecord("domain:scope", "domain", {"name": "scope", "paths": ["battalion/scope/"], "purpose": "write authority"}, "derived:fixture"),
            MapRecord("symbol:application.run", "symbol", {"path": "battalion/application.py", "locator": "run", "domain_id": "domain:application"}, "derived:fixture"),
            MapRecord("symbol:state.load", "symbol", {"path": "battalion/state/persistence.py", "locator": "load_state", "domain_id": "domain:state"}, "derived:fixture"),
            MapRecord("symbol:scope.enforce", "symbol", {"path": "battalion/scope/enforcement.py", "locator": "enforce_scope", "domain_id": "domain:scope"}, "derived:fixture"),
            MapRecord("resource:run-state", "resource", {"kind": "file", "locator": ".battalion/runs/<run>.json"}, "derived:fixture"),
            MapRecord("resource:legacy-event-log", "resource", {"kind": "file", "locator": ".battalion/events.json"}, "derived:fixture"),
            MapRecord("path:application", "path-binding", {"pattern": "battalion/application.py", "targets": ["domain:application", "symbol:application.run"]}, "derived:fixture"),
            MapRecord("constraint:application-boundary", "constraint", {"statement": "Presentation adapters do not mutate persisted state directly.", "governing_artifact": "ADR-0022"}, "governing_reference:ADR-0022", authored=True),
        ),
        edges=(
            MapEdge("domain:application", "symbol:application.run", "contains"),
            MapEdge("domain:state", "symbol:state.load", "contains"),
            MapEdge("domain:scope", "symbol:scope.enforce", "contains"),
            MapEdge("symbol:application.run", "resource:run-state", "writes"),
            MapEdge("symbol:application.run", "resource:legacy-event-log", "writes"),
            MapEdge("constraint:application-boundary", "domain:application", "governs"),
        ),
    )


def integration_fixture_after_repository_change() -> MapFixture:
    """Simulate a path rename, deleted legacy evidence, and new durable event path."""
    before = build_integration_fixture()
    records = tuple(
        MapRecord(record.record_id, record.kind, {**record.data, "path": "battalion/application/run.py"}, record.provenance, record.authored)
        if record.record_id == "symbol:application.run" else
        MapRecord(record.record_id, record.kind, {**record.data, "pattern": "battalion/application/run.py"}, record.provenance, record.authored)
        if record.record_id == "path:application" else record
        for record in before.records if record.record_id != "resource:legacy-event-log"
    ) + (
        MapRecord("resource:durable-observation", "resource", {"kind": "file", "locator": ".battalion/observations/<run>.json"}, "derived:fixture"),
    )
    edges = tuple(
        MapEdge(edge.source_id, "resource:durable-observation", edge.kind)
        if edge.target_id == "resource:legacy-event-log" else edge
        for edge in before.edges
    )
    return MapFixture(
        fixture_id=before.fixture_id,
        revision_id="map-0201",
        repository_revision="d" * 40,
        records=records,
        edges=edges,
    )
def validate_trace(trace: PersistenceTrace) -> None:
    """Reject candidates that skip a required logical operation."""
    if trace.retrieval_ids != ("domain:application", "symbol:application.run"):
        raise ValueError("retrieval must return the application domain and run symbol")
    if trace.neighbor_ids != ("symbol:application.run", "resource:run-state"):
        raise ValueError("two-hop traversal must be deterministic")
    expected_diff = {"changed": ("domain:application",), "unchanged": ("constraint:application-boundary",)}
    if trace.diff != expected_diff:
        raise ValueError("diff must distinguish generated change from governing record")
    if trace.recovered_revision != "map-0001":
        raise ValueError("failed publication must retain the previous completed revision")

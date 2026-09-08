"""Project-local canonical Cartography persistence.

The repository is the only filesystem-facing boundary for the initial
graph-adjacency JSON encoding.  Callers exchange validated logical revisions,
never mutable JSON dictionaries or storage handles.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from battalion.actors import load_actor_registry
from battalion.cartography.models import MapRevision
from battalion.cartography.projection import MarkdownProjector, ProjectionStatus
from battalion.cartography.queries import CartographyQueries, RevisionDiff
from battalion.identity import load_project_identity


CANONICAL_MAP = Path(".battalion/cartography/map.json")
CURRENT_CANONICAL_SCHEMA_VERSION = "1.1"


class CartographyPersistenceError(ValueError):
    """Base class for canonical map persistence failures."""


class CanonicalMapNotFound(CartographyPersistenceError, FileNotFoundError):
    """No completed Cartography revision has been published for this project."""


class MalformedCanonicalMap(CartographyPersistenceError):
    """A persisted canonical map fails JSON or schema validation."""


class CartographyProjectMismatch(CartographyPersistenceError):
    """A map revision is being published into the wrong project."""


class CartographyActorNotFound(CartographyPersistenceError):
    """A record claims durable Actor provenance absent from this project."""


class MapRevisionAlreadyPublished(CartographyPersistenceError):
    """A distinct immutable revision attempts to reuse a published identity."""


class UnsupportedCanonicalMapSchema(MalformedCanonicalMap):
    """The store uses a schema version this Battalion build cannot read."""


@runtime_checkable
class CartographyRepositoryPort(Protocol):
    """Backend-neutral logical persistence and query contract.

    JSON is merely the first physical encoding.  Application callers depend on
    this contract rather than a file, graph, or database handle, allowing a
    later backend replacement without changing Cartography's logical surface.
    """

    def publish(self, revision: MapRevision) -> None: ...

    def load(self) -> MapRevision: ...

    def load_revision(self, map_revision_id: str) -> MapRevision: ...

    def list_revisions(self) -> tuple[MapRevision, ...]: ...

    def diff_from(self, prior_map_revision_id: str) -> RevisionDiff: ...

    def queries(self, map_revision_id: str | None = None) -> CartographyQueries: ...


class CartographyRepository:
    """Atomic persistence boundary for immutable completed map revisions."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    @property
    def path(self) -> Path:
        return self.project_root / CANONICAL_MAP

    def publish(self, revision: MapRevision) -> None:
        """Atomically replace the completed canonical revision after validation."""

        if not isinstance(revision, MapRevision):
            raise TypeError("CartographyRepository publishes MapRevision instances only")
        project = load_project_identity(self.project_root)
        if revision.project_id != project.project_id:
            raise CartographyProjectMismatch(
                f"Map revision belongs to {revision.project_id}, not {project.project_id}."
            )
        _validate_actor_references(self.project_root, revision)
        if self.path.exists():
            current_revision_id, revisions = self._load_store()
            for persisted_revision in revisions.values():
                self._assert_project(persisted_revision)
            existing = revisions.get(revision.map_revision_id)
            if existing is not None:
                if existing == revision and current_revision_id == revision.map_revision_id:
                    return
                raise MapRevisionAlreadyPublished(
                    f"Map revision {revision.map_revision_id} is already immutable."
                )
        else:
            revisions = {}
        payload = _canonical_json(
            {**revisions, revision.map_revision_id: revision}, revision.map_revision_id
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> MapRevision:
        """Return the current validated revision without exposing its encoding."""

        current_revision_id, revisions = self._load_store()
        return self._assert_project(revisions[current_revision_id])

    def load_revision(self, map_revision_id: str) -> MapRevision:
        """Return one immutable completed revision by its durable identity."""

        _, revisions = self._load_store()
        try:
            return self._assert_project(revisions[map_revision_id])
        except KeyError as exc:
            raise CanonicalMapNotFound(
                f"No completed Cartography revision {map_revision_id} at {self.path}"
            ) from exc

    def publish_with_projection(
        self, revision: MapRevision, *, projector: MarkdownProjector | None = None
    ) -> ProjectionStatus:
        """Publish canonical state, then attempt its non-authoritative projection."""

        self.publish(revision)
        active_projector = projector or MarkdownProjector(self.project_root)
        try:
            return active_projector.publish(revision)
        except Exception:
            # The canonical revision is already durable.  Projection failure is
            # observable state, never grounds to invalidate the completed map.
            return active_projector.mark_stale(revision)

    def list_revisions(self) -> tuple[MapRevision, ...]:
        """List completed immutable revisions in deterministic creation order."""

        _, revisions = self._load_store()
        return tuple(
            self._assert_project(revision)
            for revision in sorted(
                revisions.values(), key=lambda item: (item.created_at, item.map_revision_id)
            )
        )

    def diff_from(self, prior_map_revision_id: str):
        """Diff the current revision from a persisted earlier revision."""

        return self.queries().diff(self.load_revision(prior_map_revision_id))

    def _load_store(self) -> tuple[str, dict[str, MapRevision]]:
        """Decode the canonical store, including its deterministic v1.0 migration."""

        if not self.path.is_file():
            raise CanonicalMapNotFound(f"No canonical Cartography map at {self.path}")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            current_revision_id, revisions = _decode_store(raw)
        except UnsupportedCanonicalMapSchema:
            raise
        except (OSError, json.JSONDecodeError, ValidationError, TypeError, KeyError, ValueError) as exc:
            raise MalformedCanonicalMap(
                f"Malformed canonical Cartography map at {self.path}: {exc}"
            ) from exc
        return current_revision_id, revisions

    def _assert_project(self, revision: MapRevision) -> MapRevision:
        project = load_project_identity(self.project_root)
        if revision.project_id != project.project_id:
            raise CartographyProjectMismatch(
                f"Canonical map belongs to {revision.project_id}, not {project.project_id}."
            )
        return revision

    def queries(self, map_revision_id: str | None = None) -> CartographyQueries:
        """Open the only supported deterministic query facade for the map."""

        revision = self.load() if map_revision_id is None else self.load_revision(map_revision_id)
        status = MarkdownProjector(self.project_root).status_for(revision)
        return CartographyQueries(revision, projection_freshness=status.freshness.value)


def _canonical_json(
    revisions: dict[str, MapRevision], current_revision_id: str
) -> str:
    """Return stable versioned graph-adjacency JSON for immutable revisions."""

    if current_revision_id not in revisions:
        raise ValueError("canonical store current revision must be persisted")
    revision_documents = {
        revision_id: _revision_document(revision)
        for revision_id, revision in sorted(revisions.items())
    }
    document = {
        "schema_version": CURRENT_CANONICAL_SCHEMA_VERSION,
        "current_revision_id": current_revision_id,
        "revisions": revision_documents,
        "adjacency": {
            revision_id: _adjacency(revision)
            for revision_id, revision in sorted(revisions.items())
        },
    }
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _revision_document(revision: MapRevision) -> dict:
    """Normalize producer ordering inside one logical revision."""

    revision_document = revision.model_dump(mode="json")
    for field, identity in (
        ("domains", "domain_id"),
        ("symbols", "symbol_id"),
        ("resources", "resource_id"),
        ("annotations", "annotation_id"),
        ("knowledge_claims", "claim_id"),
        ("constraints", "constraint_id"),
        ("relationships", "relationship_id"),
    ):
        revision_document[field].sort(key=lambda item: item[identity])
    revision_document["path_bindings"].sort(key=lambda item: item["matcher"])
    return revision_document


def _decode_store(raw: object) -> tuple[str, dict[str, MapRevision]]:
    """Read supported encodings and normalize legacy v1.0 to the v1.1 store."""

    if not isinstance(raw, dict):
        raise ValueError("canonical map must be a JSON object")
    version = raw.get("schema_version")
    if version == "1.0":
        revision = MapRevision.model_validate(raw["revision"])
        if raw["adjacency"] != _adjacency(revision):
            raise ValueError("canonical map adjacency does not match relationships")
        return revision.map_revision_id, {revision.map_revision_id: revision}
    if version != CURRENT_CANONICAL_SCHEMA_VERSION:
        raise UnsupportedCanonicalMapSchema(
            f"Unsupported canonical map schema version {version!r}; supported versions are 1.0 and {CURRENT_CANONICAL_SCHEMA_VERSION}."
        )
    revision_documents = raw["revisions"]
    adjacency = raw["adjacency"]
    current_revision_id = raw["current_revision_id"]
    if not isinstance(revision_documents, dict) or not isinstance(adjacency, dict):
        raise ValueError("canonical map revisions and adjacency must be objects")
    revisions = {
        revision_id: MapRevision.model_validate(document)
        for revision_id, document in revision_documents.items()
    }
    if not revisions or current_revision_id not in revisions:
        raise ValueError("canonical map must name one persisted current revision")
    for revision_id, revision in revisions.items():
        if revision.map_revision_id != revision_id:
            raise ValueError("canonical revision key must match its logical revision ID")
        if adjacency.get(revision_id) != _adjacency(revision):
            raise ValueError("canonical map adjacency does not match relationships")
    if set(adjacency) != set(revisions):
        raise ValueError("canonical adjacency must exist for every persisted revision")
    return current_revision_id, revisions


def _adjacency(revision: MapRevision) -> dict[str, list[str]]:
    """Derive deterministic outgoing relationship IDs for each graph node."""

    adjacency: dict[str, list[str]] = {}
    for relationship in sorted(
        revision.relationships,
        key=lambda item: (item.source_id, item.relationship_id),
    ):
        adjacency.setdefault(relationship.source_id, []).append(
            relationship.relationship_id
        )
    return adjacency


def _validate_actor_references(project_root: Path, revision: MapRevision) -> None:
    """Reject provenance claims that cannot be inspected in the Actor registry."""

    referenced = {revision.requesting_actor_id} if revision.requesting_actor_id else set()
    for record in (*revision.annotations, *revision.knowledge_claims, *revision.constraints):
        if record.asserted_by_actor_id is not None:
            referenced.add(record.asserted_by_actor_id)
    for record in (*revision.knowledge_claims, *revision.constraints):
        referenced.update(record.verified_by_actor_ids)
    for constraint in revision.constraints:
        referenced.update(constraint.disputed_by_actor_ids)
    if not referenced:
        return
    known = {actor.actor_id for actor in load_actor_registry(project_root).actors}
    missing = sorted(str(actor_id) for actor_id in referenced - known)
    if missing:
        raise CartographyActorNotFound(
            f"Cartography records reference unknown durable Actor {missing[0]}."
        )

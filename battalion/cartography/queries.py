"""Bounded deterministic queries over validated Cartography revisions."""

from __future__ import annotations

from collections import deque
from pathlib import PurePosixPath
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from battalion.cartography.models import (
    Annotation,
    Domain,
    InstitutionalConstraint,
    KnowledgeClaim,
    MapEntityId,
    MapRevision,
    PathBinding,
    Relationship,
    Resource,
    Symbol,
)


CartographyEntity: TypeAlias = (
    Domain
    | Symbol
    | Resource
    | Annotation
    | KnowledgeClaim
    | InstitutionalConstraint
)


class CartographyQueryError(ValueError):
    """Base class for bounded logical map query failures."""


class CartographyEntityNotFound(CartographyQueryError, KeyError):
    """A query named a stable entity absent from this revision."""


class _QueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    map_revision_id: str
    freshness: str
    projection_freshness: str


class EntityLookup(_QueryResult):
    entity: CartographyEntity


class Neighborhood(_QueryResult):
    """A bounded graph neighborhood with the evidence-bearing edges selected."""

    entities: tuple[CartographyEntity, ...]
    relationships: tuple[Relationship, ...]
    depth: int = Field(ge=0)
    truncated: bool


class PathResult(_QueryResult):
    """The shortest directed typed path, or explicit absence of one."""

    source_id: MapEntityId
    target_id: MapEntityId
    entity_ids: tuple[MapEntityId, ...]
    relationships: tuple[Relationship, ...]
    max_depth: int = Field(ge=1, le=10)


class PathBindingLookup(_QueryResult):
    path: str
    bindings: tuple[PathBinding, ...]


class RevisionDiff(_QueryResult):
    prior_map_revision_id: str
    added_entity_ids: tuple[MapEntityId, ...]
    removed_entity_ids: tuple[MapEntityId, ...]
    changed_entity_ids: tuple[MapEntityId, ...]
    added_relationship_ids: tuple[str, ...]
    removed_relationship_ids: tuple[str, ...]
    changed_relationship_ids: tuple[str, ...]


class CartographyQueries:
    """Read-only, deterministic query facade for one immutable map revision."""

    def __init__(
        self, revision: MapRevision, *, projection_freshness: str | None = None
    ) -> None:
        self._revision = revision
        self._projection_freshness = projection_freshness or revision.projection_freshness.value
        self._entities = _entity_index(revision)
        self._relationships = {item.relationship_id: item for item in revision.relationships}

    def get(self, entity_id: MapEntityId) -> EntityLookup:
        """Look up one entity by its Battalion-owned stable identity."""

        return EntityLookup(**self._result_fields(), entity=self._entity(entity_id))

    def bindings_for_path(self, path: str) -> PathBindingLookup:
        """Return deterministic bindings whose repository matcher covers ``path``."""

        normalized = path.replace("\\", "/")
        bindings = tuple(
            binding
            for binding in sorted(self._revision.path_bindings, key=lambda item: item.matcher)
            if PurePosixPath(normalized).match(binding.matcher)
        )
        return PathBindingLookup(**self._result_fields(), path=normalized, bindings=bindings)

    def neighborhood(
        self, seed_ids: tuple[MapEntityId, ...], *, depth: int = 1, limit: int = 100
    ) -> Neighborhood:
        """Expand an undirected evidence neighborhood within explicit fixed bounds."""

        if not 0 <= depth <= 5:
            raise CartographyQueryError("depth must be between 0 and 5")
        if not 1 <= limit <= 500:
            raise CartographyQueryError("limit must be between 1 and 500")
        for entity_id in seed_ids:
            self._entity(entity_id)

        discovered = set(seed_ids)
        queue = deque((entity_id, 0) for entity_id in sorted(seed_ids))
        selected_relationships: set[str] = set()
        truncated = False
        adjacency = _undirected_adjacency(self._revision.relationships)
        while queue:
            entity_id, current_depth = queue.popleft()
            if current_depth == depth:
                continue
            for relationship in adjacency.get(entity_id, ()):
                selected_relationships.add(relationship.relationship_id)
                neighbor = (
                    relationship.target_id
                    if relationship.source_id == entity_id
                    else relationship.source_id
                )
                if neighbor in discovered:
                    continue
                if len(discovered) >= limit:
                    truncated = True
                    continue
                discovered.add(neighbor)
                queue.append((neighbor, current_depth + 1))
        entities = tuple(self._entities[entity_id] for entity_id in sorted(discovered))
        relationships = tuple(
            self._relationships[relationship_id]
            for relationship_id in sorted(selected_relationships)
            if self._relationships[relationship_id].source_id in discovered
            and self._relationships[relationship_id].target_id in discovered
        )
        return Neighborhood(
            **self._result_fields(),
            entities=entities,
            relationships=relationships,
            depth=depth,
            truncated=truncated,
        )

    def explain(self, entity_id: MapEntityId, *, depth: int = 1, limit: int = 100) -> Neighborhood:
        """Return a bounded entity neighborhood including typed provenance edges."""

        return self.neighborhood((entity_id,), depth=depth, limit=limit)

    def path(
        self, source_id: MapEntityId, target_id: MapEntityId, *, max_depth: int = 5
    ) -> PathResult | None:
        """Find the shortest directed relationship path between known entities."""

        if not 1 <= max_depth <= 10:
            raise CartographyQueryError("max_depth must be between 1 and 10")
        self._entity(source_id)
        self._entity(target_id)
        if source_id == target_id:
            return PathResult(
                **self._result_fields(), source_id=source_id, target_id=target_id,
                entity_ids=(source_id,), relationships=(), max_depth=max_depth,
            )
        outgoing = _outgoing_adjacency(self._revision.relationships)
        queue = deque([(source_id, (), (source_id,))])
        visited = {source_id}
        while queue:
            current, edges, entity_ids = queue.popleft()
            if len(edges) == max_depth:
                continue
            for relationship in outgoing.get(current, ()):
                if relationship.target_id in visited:
                    continue
                next_edges = (*edges, relationship)
                next_entities = (*entity_ids, relationship.target_id)
                if relationship.target_id == target_id:
                    return PathResult(
                        **self._result_fields(), source_id=source_id, target_id=target_id,
                        entity_ids=next_entities, relationships=next_edges, max_depth=max_depth,
                    )
                visited.add(relationship.target_id)
                queue.append((relationship.target_id, next_edges, next_entities))
        return None

    def diff(self, prior: MapRevision) -> RevisionDiff:
        """Compare two typed revisions without depending on their JSON encoding."""

        prior_entities = _entity_index(prior)
        current_entities = self._entities
        prior_relationships = {item.relationship_id: item for item in prior.relationships}
        return RevisionDiff(
            **self._result_fields(),
            prior_map_revision_id=prior.map_revision_id,
            added_entity_ids=tuple(sorted(current_entities.keys() - prior_entities.keys())),
            removed_entity_ids=tuple(sorted(prior_entities.keys() - current_entities.keys())),
            changed_entity_ids=tuple(sorted(
                entity_id for entity_id in current_entities.keys() & prior_entities.keys()
                if current_entities[entity_id] != prior_entities[entity_id]
            )),
            added_relationship_ids=tuple(sorted(self._relationships.keys() - prior_relationships.keys())),
            removed_relationship_ids=tuple(sorted(prior_relationships.keys() - self._relationships.keys())),
            changed_relationship_ids=tuple(sorted(
                relationship_id
                for relationship_id in self._relationships.keys() & prior_relationships.keys()
                if self._relationships[relationship_id] != prior_relationships[relationship_id]
            )),
        )

    def _entity(self, entity_id: MapEntityId) -> CartographyEntity:
        try:
            return self._entities[entity_id]
        except KeyError as exc:
            raise CartographyEntityNotFound(f"Map entity {entity_id} was not found") from exc

    def _result_fields(self) -> dict[str, str]:
        return {
            "map_revision_id": self._revision.map_revision_id,
            "freshness": self._revision.freshness.value,
            "projection_freshness": self._projection_freshness,
        }


def _entity_index(revision: MapRevision) -> dict[MapEntityId, CartographyEntity]:
    return {
        **{item.domain_id: item for item in revision.domains},
        **{item.symbol_id: item for item in revision.symbols},
        **{item.resource_id: item for item in revision.resources},
        **{item.annotation_id: item for item in revision.annotations},
        **{item.claim_id: item for item in revision.knowledge_claims},
        **{item.constraint_id: item for item in revision.constraints},
    }


def _outgoing_adjacency(relationships: tuple[Relationship, ...]) -> dict[MapEntityId, tuple[Relationship, ...]]:
    adjacency: dict[MapEntityId, list[Relationship]] = {}
    for relationship in relationships:
        adjacency.setdefault(relationship.source_id, []).append(relationship)
    return {key: tuple(sorted(value, key=lambda item: item.relationship_id)) for key, value in adjacency.items()}


def _undirected_adjacency(relationships: tuple[Relationship, ...]) -> dict[MapEntityId, tuple[Relationship, ...]]:
    adjacency: dict[MapEntityId, list[Relationship]] = {}
    for relationship in relationships:
        adjacency.setdefault(relationship.source_id, []).append(relationship)
        adjacency.setdefault(relationship.target_id, []).append(relationship)
    return {key: tuple(sorted(value, key=lambda item: item.relationship_id)) for key, value in adjacency.items()}

"""Strict logical records for a durable Cartography map.

Repository paths and symbol locators are evidence for one map revision.  They
are deliberately not used as logical identities: those identities are assigned
by Battalion and survive a move or rename when later reconciliation proves
continuity.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


MapEntityId = Annotated[
    str,
    Field(pattern=r"^(domain|symbol|resource|claim|constraint|annotation):[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
MapRevisionId = Annotated[str, Field(pattern=r"^maprev:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
RelationshipId = Annotated[str, Field(pattern=r"^relationship:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AuthorityClass(str, Enum):
    DERIVED = "derived"
    ATTRIBUTED = "attributed"
    GOVERNING_REFERENCE = "governing_reference"


class Freshness(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class RelationshipKind(str, Enum):
    CONTAINS = "contains"
    DEPENDS_ON = "depends_on"
    INVOKES = "invokes"
    READS = "reads"
    WRITES = "writes"
    READ_WRITES = "read_writes"
    IMPLEMENTS_CONTRACT = "implements_contract"
    TESTS = "tests"
    GOVERNED_BY = "governed_by"
    ASSERTED_BY = "asserted_by"
    VERIFIED_BY = "verified_by"
    OWNED_BY = "owned_by"
    ASSIGNED_TO = "assigned_to"
    POSSIBLE_SUCCESSOR = "possible_successor"
    POSSIBLE_PREDECESSOR = "possible_predecessor"


class EvidenceReference(_Contract):
    """Revision-scoped inspectable evidence; never raw repository contents."""

    reference: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=2000)
    path: str | None = Field(default=None, max_length=1000)
    locator: str | None = Field(default=None, max_length=1000)
    observed_at: datetime


class _AttributedRecord(_Contract):
    """Common provenance rules for human and governing map records."""

    statement: str = Field(min_length=1, max_length=5000)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=50)
    authority_class: AuthorityClass = AuthorityClass.DERIVED
    asserted_by_actor_id: UUID | None = None

    @model_validator(mode="after")
    def _validate_attribution(self) -> "Annotation":
        if self.authority_class is AuthorityClass.ATTRIBUTED and self.asserted_by_actor_id is None:
            raise ValueError("attributed records require an asserting Actor")
        if self.authority_class is not AuthorityClass.ATTRIBUTED and self.asserted_by_actor_id is not None:
            raise ValueError("only attributed records may claim an asserting Actor")
        return self


class Annotation(_AttributedRecord):
    schema_version: Literal["1.0"] = "1.0"
    annotation_id: MapEntityId


class Domain(_Contract):
    schema_version: Literal["1.0"] = "1.0"
    domain_id: MapEntityId
    purpose: str = Field(min_length=1, max_length=5000)
    path_scopes: tuple[str, ...] = Field(min_length=1, max_length=100)
    owns: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    does_not_own: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=50)


class Symbol(_Contract):
    schema_version: Literal["1.0"] = "1.0"
    symbol_id: MapEntityId
    domain_id: MapEntityId
    kind: str = Field(min_length=1, max_length=100)
    responsibility: str = Field(min_length=1, max_length=5000)
    path: str = Field(min_length=1, max_length=1000)
    locator: str = Field(min_length=1, max_length=1000)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=50)


class Resource(_Contract):
    schema_version: Literal["1.0"] = "1.0"
    resource_id: MapEntityId
    kind: str = Field(min_length=1, max_length=100)
    locator: str = Field(min_length=1, max_length=1000)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=50)


class Relationship(_Contract):
    schema_version: Literal["1.0"] = "1.0"
    relationship_id: RelationshipId
    source_id: MapEntityId
    target_id: MapEntityId
    kind: RelationshipKind
    access_mode: Literal["read", "write", "read_write"] | None = None
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _validate_endpoints(self) -> "Relationship":
        if self.source_id == self.target_id:
            raise ValueError("a relationship cannot target its own source")
        return self


class KnowledgeClaim(_AttributedRecord):
    schema_version: Literal["1.0"] = "1.0"
    claim_id: MapEntityId
    applies_to: tuple[MapEntityId, ...] = Field(min_length=1, max_length=100)
    verified_by_actor_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)


class InstitutionalConstraint(_AttributedRecord):
    schema_version: Literal["1.0"] = "1.0"
    constraint_id: MapEntityId
    applies_to: tuple[MapEntityId, ...] = Field(min_length=1, max_length=100)
    governing_artifact_references: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    verified_by_actor_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)
    supersedes_id: MapEntityId | None = None
    disputed_by_actor_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=100)

    @model_validator(mode="after")
    def _validate_authority_evidence(self) -> "InstitutionalConstraint":
        if self.authority_class is AuthorityClass.GOVERNING_REFERENCE and not self.governing_artifact_references:
            raise ValueError("governing constraints require governing artifact references")
        return self


class PathBinding(_Contract):
    schema_version: Literal["1.0"] = "1.0"
    matcher: str = Field(min_length=1, max_length=1000)
    entity_ids: tuple[MapEntityId, ...] = Field(min_length=1, max_length=100)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=50)


class MapRevision(_Contract):
    """One immutable, validated canonical graph revision."""

    schema_version: Literal["1.0"] = "1.0"
    map_revision_id: MapRevisionId
    project_id: UUID
    repository_revision: str = Field(min_length=1, max_length=500)
    created_at: datetime
    freshness: Freshness = Freshness.CURRENT
    projection_freshness: Freshness = Freshness.CURRENT
    requesting_actor_id: UUID | None = None
    inspected_scope: tuple[str, ...] = Field(min_length=1, max_length=100)
    warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    domains: tuple[Domain, ...] = ()
    symbols: tuple[Symbol, ...] = ()
    resources: tuple[Resource, ...] = ()
    annotations: tuple[Annotation, ...] = ()
    knowledge_claims: tuple[KnowledgeClaim, ...] = ()
    constraints: tuple[InstitutionalConstraint, ...] = ()
    path_bindings: tuple[PathBinding, ...] = ()
    relationships: tuple[Relationship, ...] = ()

    @model_validator(mode="after")
    def _validate_graph(self) -> "MapRevision":
        ids = [
            *(item.domain_id for item in self.domains),
            *(item.symbol_id for item in self.symbols),
            *(item.resource_id for item in self.resources),
            *(item.annotation_id for item in self.annotations),
            *(item.claim_id for item in self.knowledge_claims),
            *(item.constraint_id for item in self.constraints),
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("map entity identifiers must be unique")
        known = set(ids)
        if any(symbol.domain_id not in known for symbol in self.symbols):
            raise ValueError("each Symbol must reference a persisted Domain")
        if any(edge.source_id not in known or edge.target_id not in known for edge in self.relationships):
            raise ValueError("relationship endpoints must reference persisted map entities")
        if any(entity_id not in known for binding in self.path_bindings for entity_id in binding.entity_ids):
            raise ValueError("PathBinding targets must reference persisted map entities")
        relationship_ids = [edge.relationship_id for edge in self.relationships]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("relationship identifiers must be unique")
        return self

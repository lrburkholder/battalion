"""Conservative identity reconciliation for successive Cartography refreshes.

Paths and symbol names are revision evidence, not identities.  This module
therefore never preserves an old Battalion identity solely because a candidate
has the same (or a renamed) location.  A unique one-to-one claim with stronger
evidence is required; every other continuity shape remains inspectable.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from battalion.cartography.models import MapEntityId


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CartographyEntityKind(str, Enum):
    DOMAIN = "domain"
    SYMBOL = "symbol"
    RESOURCE = "resource"
    CLAIM = "claim"
    CONSTRAINT = "constraint"
    ANNOTATION = "annotation"


class ContinuityEvidenceKind(str, Enum):
    """Whether a proof can justify carrying a Battalion identity forward."""

    SEMANTIC_SIGNATURE = "semantic_signature"
    EXPLICIT_REFACTOR = "explicit_refactor"
    HUMAN_ASSERTION = "human_assertion"
    PATH_OR_NAME = "path_or_name"


class ReconciliationOutcome(str, Enum):
    PRESERVED = "preserved"
    NEW = "new"
    DELETED = "deleted"
    SPLIT = "split"
    MERGED = "merged"
    AMBIGUOUS = "ambiguous"


class ContinuityEvidence(_Contract):
    """Inspectable proof relating a candidate to an earlier identity."""

    kind: ContinuityEvidenceKind
    reference: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=2000)

    @property
    def supports_identity_continuity(self) -> bool:
        return self.kind is not ContinuityEvidenceKind.PATH_OR_NAME


class PriorEntity(_Contract):
    entity_id: MapEntityId
    kind: CartographyEntityKind


class ReconciliationCandidate(_Contract):
    """A newly extracted concept before it is assigned a durable map ID."""

    candidate_id: str = Field(min_length=1, max_length=200)
    kind: CartographyEntityKind
    path: str | None = Field(default=None, min_length=1, max_length=1000)
    locator: str | None = Field(default=None, min_length=1, max_length=1000)
    predecessor_ids: tuple[MapEntityId, ...] = Field(default_factory=tuple, max_length=20)
    continuity_evidence: tuple[ContinuityEvidence, ...] = Field(
        default_factory=tuple, max_length=50
    )

    @model_validator(mode="after")
    def validate_predecessor_evidence(self) -> "ReconciliationCandidate":
        if self.predecessor_ids and not self.continuity_evidence:
            raise ValueError("claimed predecessors require reconciliation evidence")
        if len(set(self.predecessor_ids)) != len(self.predecessor_ids):
            raise ValueError("claimed predecessor identities must be unique")
        return self


class IdentityLink(_Contract):
    """An inspectable non-destructive possible-continuity link."""

    source_id: MapEntityId
    target_id: MapEntityId
    kind: str = Field(pattern=r"^possible_(successor|predecessor)$")
    evidence: tuple[ContinuityEvidence, ...] = Field(min_length=1, max_length=50)


class ReconciledCandidate(_Contract):
    candidate_id: str
    entity_id: MapEntityId
    outcome: ReconciliationOutcome
    predecessor_ids: tuple[MapEntityId, ...] = ()
    evidence: tuple[ContinuityEvidence, ...] = ()
    identity_links: tuple[IdentityLink, ...] = ()


class ReconciliationResult(_Contract):
    """Complete evidence record for one refresh's identity decisions."""

    candidates: tuple[ReconciledCandidate, ...]
    deleted_entity_ids: tuple[MapEntityId, ...]


def reconcile_entities(
    prior_entities: tuple[PriorEntity, ...],
    candidates: tuple[ReconciliationCandidate, ...],
    *,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ReconciliationResult:
    """Assign conservative durable identities to extracted refresh candidates.

    Only a one-to-one claim supported by semantic, explicit-refactor, or human
    evidence preserves the prior ID.  A split, merge, weak claim, or competing
    candidate receives a new identity and durable possible-continuity links.
    """

    prior_by_id = {item.entity_id: item for item in prior_entities}
    if len(prior_by_id) != len(prior_entities):
        raise ValueError("prior entity identities must be unique")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("reconciliation candidate identifiers must be unique")
    for candidate in candidates:
        unknown = set(candidate.predecessor_ids) - prior_by_id.keys()
        if unknown:
            raise ValueError(f"candidate {candidate.candidate_id} claims unknown predecessor {sorted(unknown)[0]}")
        incompatible = [
            entity_id
            for entity_id in candidate.predecessor_ids
            if prior_by_id[entity_id].kind is not candidate.kind
        ]
        if incompatible:
            raise ValueError("reconciliation may only relate entities of the same kind")

    claimed_by: dict[MapEntityId, list[ReconciliationCandidate]] = defaultdict(list)
    for candidate in candidates:
        for predecessor_id in candidate.predecessor_ids:
            claimed_by[predecessor_id].append(candidate)

    reconciled: list[ReconciledCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        predecessors = candidate.predecessor_ids
        has_strong_evidence = any(
            evidence.supports_identity_continuity for evidence in candidate.continuity_evidence
        )
        is_unique_pair = (
            len(predecessors) == 1
            and len(claimed_by[predecessors[0]]) == 1
            and has_strong_evidence
        )
        if is_unique_pair:
            reconciled.append(
                ReconciledCandidate(
                    candidate_id=candidate.candidate_id,
                    entity_id=predecessors[0],
                    outcome=ReconciliationOutcome.PRESERVED,
                    predecessor_ids=predecessors,
                    evidence=candidate.continuity_evidence,
                )
            )
            continue

        entity_id = _new_entity_id(candidate.kind, uuid_factory())
        outcome = _outcome(candidate, claimed_by, has_strong_evidence)
        links = tuple(
            IdentityLink(
                source_id=predecessor_id,
                target_id=entity_id,
                kind="possible_successor",
                evidence=candidate.continuity_evidence,
            )
            for predecessor_id in predecessors
        )
        reconciled.append(
            ReconciledCandidate(
                candidate_id=candidate.candidate_id,
                entity_id=entity_id,
                outcome=outcome,
                predecessor_ids=predecessors,
                evidence=candidate.continuity_evidence,
                identity_links=links,
            )
        )

    claimed_prior_ids = set(claimed_by)
    deleted = tuple(sorted(prior_by_id.keys() - claimed_prior_ids))
    return ReconciliationResult(candidates=tuple(reconciled), deleted_entity_ids=deleted)


def _outcome(
    candidate: ReconciliationCandidate,
    claimed_by: dict[MapEntityId, list[ReconciliationCandidate]],
    has_strong_evidence: bool,
) -> ReconciliationOutcome:
    if not candidate.predecessor_ids:
        return ReconciliationOutcome.NEW
    if len(candidate.predecessor_ids) > 1:
        return ReconciliationOutcome.MERGED
    if len(claimed_by[candidate.predecessor_ids[0]]) > 1:
        return ReconciliationOutcome.SPLIT
    if not has_strong_evidence:
        return ReconciliationOutcome.AMBIGUOUS
    return ReconciliationOutcome.AMBIGUOUS


def _new_entity_id(kind: CartographyEntityKind, identifier: UUID) -> MapEntityId:
    return f"{kind.value}:{identifier.hex}"
